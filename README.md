# RDGen, a RustDesk client generator to use with your self-hosted RustDesk server

The client generator is currently hosted [here](https://rdgen.crayoneater.org).
If you would like to host the generator yourself, see [here](setup.md)

## Changes in this fork (Blambl420/rdgen)

This fork is the build backend for the managed RustDesk clients at
[rd.blambl.cz](https://rd.blambl.cz) and for the public generator at
[rdgen.blambl.cz](https://rdgen.blambl.cz). The generator web app itself
(`rdgenerator/`, `rdgen/`, Docker image) is **unchanged** from upstream; every
change lives under `.github/` and only affects what the GitHub Actions build
jobs do with the RustDesk sources. A daily `sync.yml` merges upstream
`bryangerlach/rdgen` into this fork.

### 1. Upstream sync and CI hardening (all builds)

- `.github/workflows/sync.yml`: scheduled merge of upstream `master`
  (GitHub "merge-upstream" API); the builder refuses to build while the fork
  is behind upstream.
- Third-party actions are pinned to commit SHAs, artifact actions use v4,
  and all workflows are validated with actionlint.
- `.github/actions/decrypt-secrets`: the decrypted build inputs are
  validated (allowed field names, no control or shell metacharacters,
  numeric port, valid base64 JSON config) before anything is written to
  `GITHUB_ENV`; secrets are never echoed into logs. The validator accepts the
  upstream `slug_appname` field and derives the same safe slug for older ZIPs.
- "Verify embedded server contract": after the sources are patched, the
  job asserts that the server, ports, public key, API server and custom
  config really ended up in the RustDesk sources (including both Android
  entry points for 1.5). A build that silently lost its configuration fails
  instead of producing a client pointing at the public RustDesk servers.
- Every job uploads a small `rd-receipt-*` artifact (run identity, versions,
  SHA-256 of the inputs and of the produced installers) so the publishing
  server can verify what it downloads against the actual CI run.

### 2. Managed in-app updater (only rd.blambl.cz builds)

Step "Embed managed desktop updater", applied only when the download link
starts with `https://rd.blambl.cz/latest/` (Windows, Windows x86, macOS).
The RustDesk updater is patched to read a per-platform/per-variant JSON feed
from rd.blambl.cz and to download only exact, versioned URLs from the same
origin (`.github/scripts/patch_update_links.py`). Clients never contact
GitHub or rustdesk.com for updates. Other builds keep the upstream updater.

### 3. Automatic WebSocket fallback (only rd.blambl.cz builds)

Step "Patch automatic WebSocket fallback", `.github/patches/wsFallback.py`,
applied only when the download link starts with `https://rd.blambl.cz/`.
Upstream RustDesk can use WebSocket (`wss://<id-server>/ws/id`,
`/ws/relay`, port 443) only as a static option (`allow-websocket=Y`, relay
only, no way back). The patch adds a runtime fallback:

- controlled side: when two UDP registrations to the ID server go
  unanswered (~6 s), the client switches to WebSocket; while in fallback it
  probes the native port every 2 minutes and switches back when it works;
- controlling side: a 4 s native TCP attempt, then WebSocket; the same
  periodic probe returns to the native ports;
- new option `enable-websocket-fallback` (on by default, set `N` to turn it
  off), shown as "Automatic WebSocket fallback" next to "Use WebSocket" in
  the desktop and mobile network settings;
- the fallback flag lives in process memory only and is never written to
  the client configuration.

Files touched in the RustDesk sources (written for RustDesk 1.5.0, exact
match, the build fails on upstream drift): `libs/hbb_common/src/config.rs`,
`src/rendezvous_mediator.rs`, `src/client.rs`, `flutter/lib/consts.dart`,
`flutter/lib/desktop/pages/desktop_setting_page.dart`,
`flutter/lib/mobile/pages/settings_page.dart`, `src/lang/en.rs`,
`src/lang/cs.rs`. The server needs `/ws/id` and `/ws/relay` reachable on
port 443 of the ID-server host, and the client needs the `api-server`
option set to an `https://` URL (rd.blambl.cz prefills it through
default-settings) so that the upstream code picks `wss` instead of `ws`.


## rustdesk api server integration

The rdgen client generator is integrated into my fork of the [rustdesk-api](https://github.com/bryangerlach/rustdesk-api). This is currently a work in progress, and might have bugs.

## rdgen local build alternative

If you would prefer to build rustdesk on your own computer, check out the [DVForge](https://github.com/VenimK/DVForge) project.

## Features

- Embed server and key into client
- Custom app name
- Custom icon/logo
- Set default settings for the client
- Support for rustdesk advanced settings (https://rustdesk.com/docs/en/self-host/client-configuration/advanced-settings/)

## Generate RustDesk clients from command line instead of using a web browser

Save your configuration from the rdgen web interface, or generate your own, then use that json file with [@AlekseyLapunov's rdgen-cli](https://github.com/AlekseyLapunov/rdgen-cli) to build from the command line on Windows, Linux, or MacOS like this: `python rdgen-cli -f my_config.json --set-version 1.4.5 --set-platform windows -s https://rdgen.crayoneater.org`

## Notes

- Icons should be square (256x256 recommended)
- Avoid special characters or non-English characters in app name and file name
- Build time is currently 30 - 45 minutes

