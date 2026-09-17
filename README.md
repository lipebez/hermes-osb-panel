# Hermes OSB Panel — independent community companion

Hermes OSB Panel is a privacy-first, read-only dashboard companion for normalized Open Second Brain (OSB) snapshots in Hermes Agent. It is an independent community project by Filipe Bezerra (`@lipebez`): it is not an official Hermes Agent or Open Second Brain module and is not affiliated with, endorsed by, or maintained by either project.

> **Source and package status.** Current release: `3.1.0` is the version declared in source metadata, not a claim that a public artifact has already been published. At this documentation freeze, before the release merge, no `v3.1.0` tag, package artifact, or GitHub Release exists; this document therefore does not link to one. The public source repository is https://github.com/lipebez/hermes-osb-panel. Install only an immutable commit you have verified, or resolve and verify the tag after it is actually published.

## What it does

- normalizes a bounded, JSON-serializable snapshot under `open-second-brain.dashboard.snapshot.v1`;
- renders a dependency-free 2D Canvas graph by default, with an optional 3D Canvas projection;
- exposes read-only snapshot and health routes when mounted by a compatible dashboard host;
- uses sanitized fixture data for shareable demo and visual QA.

It does **not** index, mutate, delete, reconfigure, or create another memory store. It does not shell out to `o2b`, import private OSB modules, or ship a production OSB adapter.

## Compatibility and status

The narrow validated matrix is **Hermes Agent v0.21.3** at upstream commit `dfc28b61a0cfed58bcc200038c6bfec6f31adcd2` and **Open Second Brain v1.56.0** at observed commit `54bb28d9b760758446e494e3c6473f6534dfbdee`. Hermes validation covers the plugin admission/runtime contracts and local fixture QA. OSB validation covers the documented public data surfaces inspected at that exact checkout; it is not an end-to-end production adapter claim. No other Hermes or OSB version is claimed compatible.

The companion owns the versioned snapshot contract `open-second-brain.dashboard.snapshot.v1`. The only OSB surface described here is the documented `o2b.metrics.v1` surface together with the documented graph-export context in [`docs/upstream-data-boundary.md`](docs/upstream-data-boundary.md). There is no production OSB adapter. Missing data fails soft in the UI; it must not be filled by guessing or private-module access. A separate Windows PowerShell staging run verified discovery and disable/removal recovery, but it does not widen the exact compatibility matrix or replace the Linux-only clean-install harness.

## Prerequisites and authentication

Before installation, an operator needs:

1. a Hermes Agent installation with the dashboard available and access already protected by the host's dashboard authentication;
2. permission to install an independent, non-official community plugin from GitHub;
3. an OSB configuration **only** when using the strictly local prototype reader described below. No OSB installation or configuration is needed for the default fail-closed validation.

The panel does not add its own authentication layer and must not be exposed as a substitute for host authentication. Its direct reader is for a single-user, direct-reader setup only; it does **not** provide multi-user, multi-profile, team, or tenant isolation.

## GitHub installation

Hermes Agent v0.21.3 accepts a full 40-character commit SHA for `--ref`. After the intended commit is present in the public repository, install that exact revision:

```bash
hermes plugins install lipebez/hermes-osb-panel \
  --ref <40-character-lowercase-commit-sha> \
  --enable
hermes dashboard --no-open
```

Do not substitute a branch name for the SHA. Once `v3.1.0` actually exists, verify the tag in a trusted checkout, resolve it to its commit with `git rev-parse 'v3.1.0^{commit}'`, compare the resulting full SHA with the release evidence, and pass that SHA to `--ref`; Hermes v0.21.3 does not accept a tag name in `--ref`. At this documentation freeze the tag does not exist, so there is no tag-based install target to publish yet.

To turn the plugin off later:

```bash
hermes plugins disable hermes-osb-panel
hermes dashboard --stop
```

To remove it fully, stop the dashboard, disable the plugin, then run the official removal command:

```bash
hermes dashboard --stop
hermes plugins disable hermes-osb-panel
hermes plugins remove hermes-osb-panel
```

Confirm that the local plugin directory no longer exists before a clean reinstall. `hermes dashboard --stop` stops the local dashboard process; start it again with the appropriate local dashboard command when needed. Use `hermes dashboard --no-open` when a start without opening a browser is desired.

### Windows ReadOnly removal recovery

The Windows staging test disabled the plugin successfully, but the first removal encountered `WinError 5` because Git pack `.idx`, `.pack`, and `.rev` files inside the plugin directory were ReadOnly. This is a Windows host/CLI cleanup recovery, not a panel behavior failure. **Only after the plugin is already disabled**, and only when that specific Windows removal error occurs, set `$pluginDir` to that already-disabled `hermes-osb-panel` directory and clear the ReadOnly attribute from files below it:

```powershell
$pluginDir = Join-Path $env:LOCALAPPDATA 'hermes\plugins\hermes-osb-panel'
Get-ChildItem -LiteralPath $pluginDir -Recurse -File -Force | ForEach-Object { $_.IsReadOnly = $false }
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
PYTHONDONTWRITEBYTECODE=1 python3 -B -c 'import ast, pathlib, subprocess; [ast.parse(pathlib.Path(path).read_text(encoding="utf-8"), filename=path) for path in subprocess.check_output(["git", "ls-files", "*.py"], text=True).splitlines()]'
HERMES_CLI=/path/to/hermes-agent-v0.21.3/venv/bin/hermes
(
  QA_HOME="$(mktemp -d)"
  trap 'rm -rf "$QA_HOME"' EXIT
  HOME="$QA_HOME" HERMES_HOME="$QA_HOME/hermes" "$HERMES_CLI" plugins doctor . --ci
)
```

Use a Hermes executable built from the exact validated upstream commit above; the temporary `HOME`/`HERMES_HOME` keeps the doctor check away from active profiles and is removed even if the check fails. After committing the release documentation locally, run `PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/check_public_release.py`. It scans exactly `git archive HEAD`, not the working tree. A pass validates that committed archive only; it does not create a tag or GitHub Release.

## Contributing and license

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before proposing a change. The code and documentation are MIT licensed; see [`LICENSE`](LICENSE) and [`NOTICE.md`](NOTICE.md). This repository redistributes neither vault content nor source code from Hermes Agent or Open Second Brain.
