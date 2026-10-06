"""Managed desktop updates: versioned downloads and an exact per-build allowlist."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

DESKTOP_ARCHES={'windows':{'x86_64'},'windows-x86':{'i686'},'macos':{'x86_64','aarch64'}}

def update_plan(link, platform, filename, arch):
    if platform not in DESKTOP_ARCHES or arch not in DESKTOP_ARCHES[platform]:
        raise ValueError('Unsupported updater platform/architecture')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}',filename):
        raise ValueError('Unsafe updater filename')
    parsed=urlsplit(link)
    if (parsed.scheme!='https' or not re.fullmatch(r'[A-Za-z0-9.-]+',parsed.netloc)
            or parsed.query or parsed.fragment):
        raise ValueError('Updater requires a plain HTTPS URL')
    match=re.fullmatch(r'/latest/'+re.escape(platform)+r'/(client|admin)/(.*)',parsed.path)
    if not match:raise ValueError('Updater link must identify its platform and variant')
    variant=match[1]
    expected='' if platform=='macos' else filename+'.exe'
    if match[2]!=expected:raise ValueError('Updater link does not match the installer filename')
    asset=filename+'-'+arch+'.dmg' if platform=='macos' else filename+'.exe'
    origin=parsed.scheme+'://'+parsed.netloc
    return dict(contract=1,platform=platform,variant=variant,arch=arch,filename=asset,
        latest_url=origin+'/latest/'+platform+'/'+variant+'/'+asset,
        feed_url=origin+'/updates/'+platform+'/'+variant+'.json',
        release_prefix=origin+'/releases/tag/'+platform+'/'+variant+'/',
        download_prefix=origin+'/files/'+platform+'/'+variant+'/')

def replace_once(source, old, new):
    if source.count(old)!=1:raise ValueError('Upstream update code changed: '+old[:80])
    return source.replace(old,new,1)

def rust_helpers(plan):
    # Only exact raw URLs are accepted, including the right variant and CPU.
    return '''
pub fn managed_update_version(version: &str) -> bool {
    version.len() <= 40 && version.split('.').count() >= 2
        && version.split('.').all(|part| !part.is_empty() && part.bytes().all(|c| c.is_ascii_digit()))
}

pub fn managed_update_url(version: &str) -> String {
    format!("PREFIX{}/FILENAME", version)
}

fn managed_update_filename(input: &str) -> Option<String> {
    let rest = input.strip_prefix("PREFIX")?;
    let (version, filename) = rest.split_once('/')?;
    if !managed_update_version(version) || filename != "FILENAME" { return None; }
    Some(format!("CACHEPREFIX{}-FILENAME", version))
}
'''.replace('CACHEPREFIX','rd-update-'+plan['platform']+'-'+plan['variant']+'-'+plan['arch']+'-').replace('PREFIX',plan['download_prefix']).replace('FILENAME',plan['filename'])

def patch_sources(root, plan):
    root=Path(root)
    common=(root/'src/common.rs').read_text(encoding='utf-8')
    common=replace_once(common,'''pub fn check_software_update() {
    if is_custom_client() {
        return;
    }''','''pub fn check_software_update() {
    // This managed custom build has its own verified release feed.''')
    common=replace_once(common,'''    let (request, url) =
        hbb_common::version_check_request(hbb_common::VER_TYPE_RUSTDESK_CLIENT.to_string());''',
        '    let url = '+json.dumps(plan['feed_url'])+'.to_owned();')
    if common.count('.post(&url).json(&request).send()')!=2:
        raise ValueError('Upstream update request changed')
    common=common.replace('.post(&url).json(&request).send()', '.get(&url).send()')
    common=replace_once(common,'    let response_url = resp.url;',
        '    let response_url = resp.url;\n'
        '    let release_version = response_url.strip_prefix('+json.dumps(plan['release_prefix'])+').unwrap_or("");\n'
        '    if !crate::updater::managed_update_version(release_version) {\n'
        '        *SOFTWARE_UPDATE_URL.lock().unwrap() = String::new();\n'
        '        return Ok(());\n    }')
    updater=(root/'src/updater.rs').read_text(encoding='utf-8')
    start=updater.index('pub fn get_update_download_file_from_url(url: &str) -> Option<PathBuf> {')
    end=updater.index('\nfn is_plain_update_filename',start)
    updater=updater[:start]+'''pub fn get_update_download_file_from_url(url: &str) -> Option<PathBuf> {
    managed_update_filename(url).map(|filename| std::env::temp_dir().join(filename))
}
'''+rust_helpers(plan)+updater[end:]
    start=updater.index('        #[cfg(target_os = "windows")]\n        let download_url = if cfg!(feature = "flutter") {')
    end=updater.index('        log::debug!("New version available:',start)
    updater=updater[:start]+'''        #[cfg(target_os = "windows")]
        let download_url = managed_update_url(version);
'''+updater[end:]
    updater=replace_once(updater,'    let update_msi = crate::platform::is_msi_installed()? && !crate::is_custom_client();',
        '    let update_msi = false; // Managed Windows updates use the custom EXE.')
    updater=replace_once(updater,'    let dmg_url = format!("{}/rustdesk-{}-{}.dmg", download_url, version, arch);',
        '    let dmg_url = managed_update_url(&version);')
    # Keep the upstream unit test aligned with this build's download policy.
    updater=replace_once(updater,'fn update_download_file_accepts_expected_github_asset_urls()',
        'fn update_download_file_accepts_expected_managed_asset_url()')
    updater=replace_once(updater,
        '"https://github.com/rustdesk/rustdesk/releases/download/1.4.0/rustdesk-1.4.0-x86_64.dmg"',
        json.dumps(plan['download_prefix']+'1.5.1/'+plan['filename']))
    updater=replace_once(updater,'.expect("valid GitHub release asset URL")',
        '.expect("valid managed release asset URL")')
    cache_name='rd-update-'+plan['platform']+'-'+plan['variant']+'-'+plan['arch']+'-1.5.1-'+plan['filename']
    updater=replace_once(updater,'Some("rustdesk-1.4.0-x86_64.dmg")','Some('+json.dumps(cache_name)+')')
    changed={'src/common.rs':common,'src/updater.rs':updater}
    if plan['platform']!='windows-x86':
        p='flutter/lib/desktop/widgets/update_progress.dart'
        dart=(root/p).read_text(encoding='utf-8')
        dart=replace_once(dart,"  downloadUrl = '$downloadUrl/$downloadFile';",
            "  downloadUrl = '"+plan['download_prefix']+"$version/"+plan['filename']+"';")
        changed[p]=dart
    replaced=0
    for p in ['flutter/lib/desktop/pages/desktop_home_page.dart','flutter/lib/mobile/pages/connection_page.dart','src/ui/index.tis']:
        source=(root/p).read_text(encoding='utf-8');replaced+=source.count('https://rustdesk.com/download')
        changed[p]=source.replace('https://rustdesk.com/download',plan['latest_url'])
    home='flutter/lib/desktop/pages/desktop_home_page.dart'
    changed[home]=replace_once(changed[home],'''    if (!bind.isCustomClient() &&
        updateUrl.isNotEmpty &&
        !isCardClosed &&
        bind.mainUriPrefixSync().contains('rustdesk')) {''',
        '''    if (updateUrl.isNotEmpty && !isCardClosed) {''')
    if plan['platform']=='windows-x86':
        sciter='src/ui/index.tis'
        changed[sciter]=replace_once(changed[sciter],
            "        var url = software_update_url + '.' + handler.get_software_ext();",
            '        var update_version = software_update_url.split("/");\n'
            '        var url = '+json.dumps(plan['download_prefix'])+' + update_version[update_version.length - 1] + '+json.dumps('/'+plan['filename'])+';')
    if not replaced:raise ValueError('Download button location changed upstream')
    for p,source in changed.items():(root/p).write_text(source,encoding='utf-8')
    return changed

if __name__=='__main__':
    plan=update_plan(os.environ['RD_UPDATE_LINK'],os.environ['RD_UPDATE_PLATFORM'],
        os.environ['RD_UPDATE_FILENAME'],os.environ['RD_UPDATE_TARGET'].split('-')[0])
    patch_sources('.',plan)
    (Path(os.environ['RUNNER_TEMP'])/'rd-update-contract.json').write_text(json.dumps(plan))
    # Exercise the exact Rust URL parser without compiling the entire app.
    tests=rust_helpers(plan)+'''\nfn main() {
    assert!(managed_update_filename(&managed_update_url("1.5.1")).is_some());
    assert_ne!(managed_update_filename(&managed_update_url("1.5.1")), managed_update_filename(&managed_update_url("1.5.2")));
    for bad in ["", "../1.5.1", "1..5", "1.5.1?x=1", "1.5.1/../../"] {
        assert!(managed_update_filename(&managed_update_url(bad)).is_none());
    }
    let valid=managed_update_url("1.5.1");
    assert!(managed_update_filename(&(valid.clone()+"?x=1")).is_none());
    assert!(managed_update_filename(&valid.replace("https://", "http://")).is_none());
    assert!(managed_update_filename(&valid.replace("/files/", "/latest/")).is_none());
    assert!(managed_update_filename(&valid.replace("/client/", "/other/").replace("/admin/", "/other/")).is_none());
}\n'''
    (Path(os.environ['RUNNER_TEMP'])/'rd-update-url-test.rs').write_text(tests)
    print('Managed update feed and exact variant/architecture download policy embedded')
