# Hermes OSB Panel — independent community companion

Hermes OSB Panel is a read-only dashboard companion for normalized Open Second Brain (OSB) snapshots in Hermes Agent. It is an independent community project by Filipe Bezerra (`@lipebez`): it is not an official Hermes Agent or Open Second Brain module and is not affiliated with, endorsed by, or maintained by either project.

> **Source and package status.** Current release: `3.1.0` is **unreleased** package metadata: no package artifact, tag, or GitHub Release is published. Private staging validation, including its archive gate, has passed, but private staging is not a public release. The public source repository is https://github.com/lipebez/hermes-osb-panel; use the GitHub installation command below only after verifying the repository and commit. Source availability alone does not publish a package release.

## What it does

- normalizes a bounded, JSON-serializable snapshot under `open-second-brain.dashboard.snapshot.v1`;
- renders a dependency-free 2D Canvas graph by default, with an optional 3D Canvas projection;
- exposes read-only snapshot and health routes when mounted by a compatible dashboard host;
- uses sanitized fixture data for shareable demo and visual QA.

It does **not** index, mutate, delete, reconfigure, or create another memory store. It does not shell out to `o2b`, import private OSB modules, or ship a production OSB adapter.

## Compatibility and status

The Hermes development/dashboard behavior was validated against **Hermes Agent v0.19.0 (2026.7.20)** in local fixture QA. One real Windows PowerShell clean-host installation from private staging also achieved plugin discovery, loaded `/second-brain` fail-closed with no local data, and completed disable/removal recovery. That staging result does not make a public-release claim or establish compatibility for other Hermes versions, hosts, or dashboard configurations.

The companion owns the versioned snapshot contract `open-second-brain.dashboard.snapshot.v1`. The only OSB surface described here is the documented `o2b.metrics.v1` surface together with the documented graph-export context in [`docs/upstream-data-boundary.md`](docs/upstream-data-boundary.md). There is **no verified OSB release-version compatibility range** and no production OSB adapter. Missing data fails soft in the UI; it must not be filled by guessing or private-module access.

## Prerequisites and authentication

Before any future install, an operator needs:

1. a Hermes Agent installation with the dashboard available and access already protected by the host's dashboard authentication;
2. permission to install a community plugin from GitHub after this repository has been published;
3. an OSB configuration **only** when using the strictly local prototype reader described below. No OSB installation or configuration is needed for the default fail-closed validation.

The panel does not add its own authentication layer and must not be exposed as a substitute for host authentication. Its direct reader is for a single-user, direct-reader setup only; it does **not** provide multi-user, multi-profile, team, or tenant isolation.

## GitHub installation

After verifying that the intended public repository is accessible, the expected Hermes CLI flow is:

```bash
hermes plugins install lipebez/hermes-osb-panel --enable
hermes dashboard --no-open
```

These commands use the current Hermes CLI form `hermes plugins install <Git URL or owner/repo> --enable`. Private staging validation passed, but verify the public repository page and commit before installing from `lipebez/hermes-osb-panel`.

To turn the plugin off later:

```bash
hermes plugins disable hermes-osb-panel
hermes dashboard --stop
```

To remove it:

```bash
hermes plugins remove hermes-osb-panel
```

`hermes dashboard --stop` stops the local dashboard process; start it again with the appropriate local dashboard command when needed. Use `hermes dashboard --no-open` when a start without opening a browser is desired.

### Windows ReadOnly removal recovery

The Windows staging test disabled the plugin successfully, but the first removal encountered `WinError 5` because Git pack `.idx`, `.pack`, and `.rev` files inside the plugin directory were ReadOnly. This is a Windows host/CLI cleanup recovery, not a panel behavior failure. **Only after the plugin is already disabled**, and only when that specific Windows removal error occurs, set `$pluginDir` to that already-disabled `hermes-osb-panel` directory and clear the ReadOnly attribute from files below it:

```powershell
$pluginDir = '<already-disabled hermes-osb-panel plugin directory>'
Get-ChildItem -LiteralPath $pluginDir -Recurse -File | ForEach-Object { $_.IsReadOnly = $false }
hermes plugins remove hermes-osb-panel
```

This narrow recovery does not delete files or target other plugins. Do not use it as a generic removal step.

## Fail-closed local direct-reader prototype

The default is fail-closed: without an explicitly supplied test reader or the local opt-in below, no vault is read. `FixtureSnapshotReader` is the supported reader for tests, demos, and shareable screenshots.

`DirectMarkdownPrototypeReader` is a read-only local prototype. It reads only the vault named by `OPEN_SECOND_BRAIN_CONFIG` or `O2B_CONFIG` through its `vault` setting; it has no named fallback vault. If neither variable is set, it uses the Open Second Brain default configuration path. Enable it only for one authenticated owner-controlled dashboard process.

**Choose exactly one shell block below. Run its commands from top to bottom in the same terminal that starts the dashboard. Do not use `export` in Windows PowerShell.**

### Windows PowerShell

```powershell
$env:HERMES_OSB_PANEL_ENABLE_DIRECT_MARKDOWN = 'true'
hermes dashboard --stop
hermes dashboard --no-open
```

The environment variable exists only in the current PowerShell window. Keep that window open while testing. To return the dashboard to its fail-closed default, run these commands in order:

```powershell
hermes dashboard --stop
Remove-Item Env:\HERMES_OSB_PANEL_ENABLE_DIRECT_MARKDOWN -ErrorAction SilentlyContinue
hermes dashboard --no-open
```

### Linux/macOS (Bash/Zsh)

```bash
export HERMES_OSB_PANEL_ENABLE_DIRECT_MARKDOWN=true
hermes dashboard --stop
hermes dashboard --no-open
```

To return the dashboard to its fail-closed default, run these commands in order:

```bash
hermes dashboard --stop
unset HERMES_OSB_PANEL_ENABLE_DIRECT_MARKDOWN
hermes dashboard --no-open
```

The accepted true values are `1`, `true`, `yes`, and `on` (case and surrounding whitespace are normalized). Every other value, including unset, disables the reader. **Restart the dashboard process after any change** to this environment variable. A query parameter, browser control, HTTP body, API route, or vault record cannot enable it.

This is not a production OSB adapter. It intentionally has a direct-reader limitation: authenticated viewers can see owner-visible titles and safe previews from the selected vault. Do not use it for shared, multi-profile, team, or publicly exposed deployments.

## Privacy and data handling

- Browser snapshots redact secret-like values and absolute filesystem paths; direct-reader payloads also omit vault identifiers and memory-root fields.
- Shareable demo material uses only [`tests/fixtures/demo_snapshot_v1.json`](tests/fixtures/demo_snapshot_v1.json). Never commit real-vault content, captures, paths, credentials, session data, or logs.
- The panel ships local/system font stacks and makes no panel-originated third-party font, telemetry, analytics, or asset request. Host-level networking and the future GitHub installation download are outside that runtime claim.
- Real-vault QA is local and ephemeral. Use the demo fixture for screenshots, issues, pull requests, and public documentation.

See [`docs/architecture.md`](docs/architecture.md) for the contract and threat boundary, [`docs/qa.md`](docs/qa.md) for manual QA and artifact retention, and [`SECURITY.md`](SECURITY.md) for vulnerability reporting.

## Local verification

Run from the repository root:

```bash
node --check dashboard/dist/index.js
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -B -c 'import ast, pathlib; [ast.parse(pathlib.Path(path).read_text(encoding="utf-8"), filename=path) for path in ("__init__.py", "dashboard/plugin_api.py", "dashboard/snapshot_contract.py", "scripts/qa_dashboard_cdp.py")]'
```

The archive release scanner is deliberately different: after an explicitly authorized first public-release commit exists, run `PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/check_public_release.py`. It scans only `git archive HEAD` and fails closed when there is no `HEAD`; it must not be replaced with a working-tree scan. The private staging archive gate passed; that result does not publish this candidate or replace the post-publication gate.

## Contributing and license

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before proposing a change. The code and documentation are MIT licensed; see [`LICENSE`](LICENSE) and [`NOTICE.md`](NOTICE.md). This repository redistributes neither vault content nor source code from Hermes Agent or Open Second Brain.
