"""Automatic WebSocket fallback for the managed rd.blambl.cz clients.

Run by the generator workflows against the checked-out RustDesk sources
(written for RustDesk 1.5.0 with hbb_common 229b904). Every replacement has
to match exactly once, otherwise the build stops so that an upstream change
is noticed instead of silently producing a client without the feature.

Runtime behaviour (inert unless the option allow-websocket-fallback=Y is set):
- controlled side: two unanswered UDP registrations (~6 s) switch the
  rendezvous connection to WebSocket; while in fallback a short TCP probe of
  the native port runs periodically and switches back when it succeeds;
- controlling side: a short native TCP attempt, then WebSocket; the same
  periodic probe returns to the native ports;
- the flag lives in process memory only and is never written to the config.
"""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')


def patch(path, marker, pairs):
    p = ROOT / path
    text = p.read_text(encoding='utf-8')
    if marker in text:
        print('wsFallback: already patched', path)
        return
    for old, new in pairs:
        n = text.count(old)
        if n != 1:
            raise SystemExit('wsFallback: expected exactly one match in %s, found %d: %r' % (path, n, old[:100]))
        text = text.replace(old, new, 1)
    p.write_text(text, encoding='utf-8')
    print('wsFallback: patched', path)


# ---------------------------------------------------------------- hbb_common
patch('libs/hbb_common/src/config.rs', 'ws_fallback_active', [
    (
        'pub fn use_ws() -> bool {\n'
        '    let option = keys::OPTION_ALLOW_WEBSOCKET;\n'
        '    option2bool(option, &Config::get_option(option))\n'
        '}\n',
        'pub fn use_ws() -> bool {\n'
        '    let option = keys::OPTION_ALLOW_WEBSOCKET;\n'
        '    option2bool(option, &Config::get_option(option)) || ws_fallback_active()\n'
        '}\n'
        '\n'
        '// Managed client: automatic WebSocket fallback while the native ports are unreachable.\n'
        '// Process-local runtime state, never written to the config file.\n'
        'pub static WS_FALLBACK_ACTIVE: std::sync::atomic::AtomicBool =\n'
        '    std::sync::atomic::AtomicBool::new(false);\n'
        '/// Native TCP attempt of the controlling side before falling back, in ms.\n'
        'pub const WS_FALLBACK_CONNECT_TIMEOUT: u64 = 4_000;\n'
        '/// Native port probe while the fallback is active, in ms.\n'
        'pub const WS_FALLBACK_PROBE_TIMEOUT: u64 = 3_000;\n'
        '/// Seconds between native port probes while the fallback is active.\n'
        'pub const WS_FALLBACK_PROBE_INTERVAL: u64 = 120;\n'
        '\n'
        'lazy_static::lazy_static! {\n'
        '    static ref WS_FALLBACK_LAST_PROBE: Mutex<Option<Instant>> = Mutex::new(None);\n'
        '}\n'
        '\n'
        'pub fn ws_fallback_enabled() -> bool {\n'
        '    let option = keys::OPTION_ALLOW_WEBSOCKET_FALLBACK;\n'
        '    option2bool(option, &Config::get_option(option))\n'
        '}\n'
        '\n'
        'pub fn ws_fallback_active() -> bool {\n'
        '    WS_FALLBACK_ACTIVE.load(std::sync::atomic::Ordering::SeqCst)\n'
        '}\n'
        '\n'
        'pub fn set_ws_fallback_active(active: bool) {\n'
        '    if WS_FALLBACK_ACTIVE.swap(active, std::sync::atomic::Ordering::SeqCst) != active {\n'
        '        crate::log::info!(\n'
        '            "websocket fallback {}",\n'
        '            if active { "activated" } else { "deactivated" }\n'
        '        );\n'
        '        *WS_FALLBACK_LAST_PROBE.lock().unwrap() = Some(Instant::now());\n'
        '    }\n'
        '}\n'
        '\n'
        '/// True at most once per WS_FALLBACK_PROBE_INTERVAL while the fallback is active.\n'
        'pub fn ws_fallback_probe_due() -> bool {\n'
        '    if !ws_fallback_active() {\n'
        '        return false;\n'
        '    }\n'
        '    let mut last = WS_FALLBACK_LAST_PROBE.lock().unwrap();\n'
        '    let due = last\n'
        '        .map(|t| t.elapsed().as_secs() >= WS_FALLBACK_PROBE_INTERVAL)\n'
        '        .unwrap_or(true);\n'
        '    if due {\n'
        '        *last = Some(Instant::now());\n'
        '    }\n'
        '    due\n'
        '}\n',
    ),
    (
        '    pub const OPTION_ALLOW_WEBSOCKET: &str = "allow-websocket";\n',
        '    pub const OPTION_ALLOW_WEBSOCKET: &str = "allow-websocket";\n'
        '    pub const OPTION_ALLOW_WEBSOCKET_FALLBACK: &str = "allow-websocket-fallback";\n',
    ),
])

# Upstream picks ws:// whenever the api-server option is empty, although the
# default API is https and a self-hosted domain behind a TLS proxy answers 301
# to plain ws. RDGen only writes api-server into the client options for
# incoming-only builds, so the admin variant hit exactly that. Empty -> wss.
patch('libs/hbb_common/src/websocket.rs', 'api_server.is_empty() || api_server.starts_with("https")', [
    (
        '        let api_server = Config::get_option("api-server");\n'
        '        if api_server.starts_with("https") {\n'
        '            "wss"\n'
        '        } else {\n'
        '            "ws"\n'
        '        }\n',
        '        let api_server = Config::get_option("api-server");\n'
        '        if api_server.is_empty() || api_server.starts_with("https") {\n'
        '            "wss"\n'
        '        } else {\n'
        '            "ws"\n'
        '        }\n',
    ),
])

# ------------------------------------------------------- controlled side
patch('src/rendezvous_mediator.rs', 'ws_fallback', [
    (
        '                        if timeout {\n'
        '                            fails += 1;\n'
        '                            if fails >= MAX_FAILS2 {\n',
        '                        if timeout {\n'
        '                            fails += 1;\n'
        '                            if fails >= MAX_FAILS1\n'
        '                                && config::ws_fallback_enabled()\n'
        '                                && !config::ws_fallback_active()\n'
        '                            {\n'
        '                                log::info!("rendezvous server {} does not answer on the native ports, switching to websocket", host);\n'
        '                                config::set_ws_fallback_active(true);\n'
        '                                Self::restart();\n'
        '                                bail!("websocket fallback activated");\n'
        '                            }\n'
        '                            if fails >= MAX_FAILS2 {\n',
    ),
    (
        '                _ = timer.tick() => {\n'
        '                    if SHOULD_EXIT.load(Ordering::SeqCst) {\n'
        '                        break;\n'
        '                    }\n'
        '                    // https://www.emqx.com/en/blog/mqtt-keep-alive\n',
        '                _ = timer.tick() => {\n'
        '                    if SHOULD_EXIT.load(Ordering::SeqCst) {\n'
        '                        break;\n'
        '                    }\n'
        '                    if config::ws_fallback_probe_due() {\n'
        '                        if socket_client::connect_tcp_local(host.clone(), None, config::WS_FALLBACK_PROBE_TIMEOUT).await.is_ok() {\n'
        '                            log::info!("rendezvous server {} reachable on the native ports again, leaving websocket fallback", host);\n'
        '                            config::set_ws_fallback_active(false);\n'
        '                            Self::restart();\n'
        '                            bail!("websocket fallback deactivated");\n'
        '                        }\n'
        '                    }\n'
        '                    // https://www.emqx.com/en/blog/mqtt-keep-alive\n',
    ),
    (
        '        {\n'
        '            Self::start_tcp(server, host).await\n'
        '        } else {\n'
        '            Self::start_udp(server, host).await\n'
        '        }\n'
        '    }\n',
        '        {\n'
        '            let started = Instant::now();\n'
        '            let res = Self::start_tcp(server, host).await;\n'
        '            if res.is_err()\n'
        '                && config::ws_fallback_active()\n'
        '                && started.elapsed().as_millis() < (CONNECT_TIMEOUT + 2_000) as u128\n'
        '            {\n'
        '                // The websocket path did not come up either: retry the native transport first.\n'
        '                config::set_ws_fallback_active(false);\n'
        '            }\n'
        '            res\n'
        '        } else {\n'
        '            Self::start_udp(server, host).await\n'
        '        }\n'
        '    }\n',
    ),
])

# ------------------------------------------------------ controlling side
patch('src/client.rs', 'ws_fallback', [
    (
        '        let mut start = Instant::now();\n'
        '        let mut socket = connect_tcp(&*rendezvous_server, CONNECT_TIMEOUT).await;\n'
        '        debug_assert!(!servers.contains(&rendezvous_server));\n',
        '        let mut start = Instant::now();\n'
        '        let mut socket = if config::ws_fallback_probe_due() {\n'
        '            // Fallback active: check the native ports again before using the websocket path.\n'
        '            match connect_tcp_local(&*rendezvous_server, None, config::WS_FALLBACK_PROBE_TIMEOUT).await {\n'
        '                Ok(s) => {\n'
        '                    log::info!("rendezvous server reachable on the native ports again, leaving websocket fallback");\n'
        '                    config::set_ws_fallback_active(false);\n'
        '                    Ok(s)\n'
        '                }\n'
        '                Err(_) => connect_tcp(&*rendezvous_server, CONNECT_TIMEOUT).await,\n'
        '            }\n'
        '        } else if config::ws_fallback_enabled() && !use_ws() {\n'
        '            let s = connect_tcp(&*rendezvous_server, config::WS_FALLBACK_CONNECT_TIMEOUT).await;\n'
        '            if s.is_err() {\n'
        '                log::info!("rendezvous server unreachable on the native ports, switching to websocket");\n'
        '                config::set_ws_fallback_active(true);\n'
        '                let s = connect_tcp(&*rendezvous_server, CONNECT_TIMEOUT).await;\n'
        '                if s.is_err() {\n'
        '                    config::set_ws_fallback_active(false);\n'
        '                }\n'
        '                s\n'
        '            } else {\n'
        '                s\n'
        '            }\n'
        '        } else {\n'
        '            connect_tcp(&*rendezvous_server, CONNECT_TIMEOUT).await\n'
        '        };\n'
        '        debug_assert!(!servers.contains(&rendezvous_server));\n',
    ),
    (
        '    fn is_force_relay(&self) -> bool {\n'
        '        self.get_lch().read().unwrap().force_relay\n'
        '    }\n',
        '    fn is_force_relay(&self) -> bool {\n'
        '        self.get_lch().read().unwrap().force_relay || config::ws_fallback_active()\n'
        '    }\n',
    ),
])

# ------------------------------------------------------------------ Flutter
patch('flutter/lib/consts.dart', 'kOptionAllowWebSocketFallback', [
    (
        'const String kOptionAllowWebSocket = "allow-websocket";\n',
        'const String kOptionAllowWebSocket = "allow-websocket";\n'
        'const String kOptionAllowWebSocketFallback = "allow-websocket-fallback";\n',
    ),
])

# Upstream serialises "off" for allow-* options as an empty value when the app
# name is RustDesk (defaultOptionNo), which deletes the option and brings the
# built-in default Y back. Store an explicit N for this key instead.
patch('flutter/lib/common.dart', 'kOptionAllowWebSocketFallback', [
    (
        "  } else if (option.startsWith('allow-') ||\n"
        '      option == kOptionStopService ||\n',
        "  } else if ((option.startsWith('allow-') &&\n"
        '          option != kOptionAllowWebSocketFallback) ||\n'
        '      option == kOptionStopService ||\n',
    ),
])

patch('flutter/lib/desktop/pages/desktop_setting_page.dart', 'kOptionAllowWebSocketFallback', [
    (
        '                    kOptionAllowWebSocket),\n',
        '                    kOptionAllowWebSocket),\n'
        '              if (!hideWebSocket) divider,\n'
        '              if (!hideWebSocket)\n'
        '                switchWidget(\n'
        '                    Icons.alt_route,\n'
        "                    'Automatic WebSocket fallback',\n"
        "                    translate('websocket_fallback_tip'),\n"
        '                    kOptionAllowWebSocketFallback),\n',
    ),
])

MOBILE_TILE = (
    '          if (!disabledSettings && !_hideNetwork && !_hideWebSocket)\n'
    '            SettingsTile.switchTile(\n'
    "              title: Text(translate('Use WebSocket')),\n"
    '              initialValue: _allowWebSocket,\n'
    '              onToggle: isOptionFixed(kOptionAllowWebSocket)\n'
    '                  ? null\n'
    '                  : (v) async {\n'
    '                      await mainSetBoolOption(kOptionAllowWebSocket, v);\n'
    '                      final newValue =\n'
    '                          await mainGetBoolOption(kOptionAllowWebSocket);\n'
    '                      setState(() {\n'
    '                        _allowWebSocket = newValue;\n'
    '                      });\n'
    '                    },\n'
    '            ),\n'
)
patch('flutter/lib/mobile/pages/settings_page.dart', 'kOptionAllowWebSocketFallback', [
    (
        '  var _allowWebSocket = false;\n',
        '  var _allowWebSocket = false;\n'
        '  var _allowWebSocketFallback = false;\n',
    ),
    (
        '    _allowWebSocket = mainGetBoolOptionSync(kOptionAllowWebSocket);\n',
        '    _allowWebSocket = mainGetBoolOptionSync(kOptionAllowWebSocket);\n'
        '    _allowWebSocketFallback =\n'
        '        mainGetBoolOptionSync(kOptionAllowWebSocketFallback);\n',
    ),
    (
        MOBILE_TILE,
        MOBILE_TILE
        + '          if (!disabledSettings && !_hideNetwork && !_hideWebSocket)\n'
        '            SettingsTile.switchTile(\n'
        "              title: Text(translate('Automatic WebSocket fallback')),\n"
        "              description: Text(translate('websocket_fallback_tip')),\n"
        '              initialValue: _allowWebSocketFallback,\n'
        '              onToggle: isOptionFixed(kOptionAllowWebSocketFallback)\n'
        '                  ? null\n'
        '                  : (v) async {\n'
        '                      await mainSetBoolOption(kOptionAllowWebSocketFallback, v);\n'
        '                      final newValue =\n'
        '                          await mainGetBoolOption(kOptionAllowWebSocketFallback);\n'
        '                      setState(() {\n'
        '                        _allowWebSocketFallback = newValue;\n'
        '                      });\n'
        '                    },\n'
        '            ),\n',
    ),
])

# ---------------------------------------------------------------- languages
patch('src/lang/en.rs', 'websocket_fallback_tip', [
    (
        '        ("websocket_tip", "When using WebSocket, only relay connections are supported."),\n',
        '        ("websocket_tip", "When using WebSocket, only relay connections are supported."),\n'
        '        ("websocket_fallback_tip", "If the server cannot be reached on the native ports, switch to WebSocket (port 443, relay only) automatically and switch back once the native ports work again."),\n',
    ),
])
patch('src/lang/cs.rs', 'websocket_fallback_tip', [
    (
        '        ("Use WebSocket", "Použít WebSocket"),\n',
        '        ("Use WebSocket", "Použít WebSocket"),\n'
        '        ("Automatic WebSocket fallback", "Automatický přechod na WebSocket"),\n'
        '        ("websocket_fallback_tip", "Pokud se nepodaří spojit se serverem přes běžné porty, klient se automaticky přepne na WebSocket (port 443, pouze relay) a po obnovení dostupnosti portů se vrátí zpět."),\n',
    ),
])

print('wsFallback: done')
