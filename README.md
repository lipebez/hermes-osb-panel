# Hermes OSB Panel — independent community companion

Hermes OSB Panel is a read-only dashboard companion for normalized Open Second Brain (OSB) snapshots in Hermes Agent. It is an independent community project by Filipe Bezerra (`@lipebez`): it is not an official Hermes Agent or Open Second Brain module and is not affiliated with, endorsed by, or maintained by either project.

> **Pre-publication status.** Current release: `3.1.0` is local package metadata only; it is **unreleased** and not published or released. The intended repository `lipebez/hermes-osb-panel` does not exist yet. The future GitHub installation path below is instructional only; it has not been tested, and a clean-host Git installation is pending the first authorized commit and staging test.

## What it does

- normalizes a bounded, JSON-serializable snapshot under `open-second-brain.dashboard.snapshot.v1`;
- renders a dependency-free 2D Canvas graph by default, with an optional 3D Canvas projection;
- exposes read-only snapshot and health routes when mounted by a compatible dashboard host;
- uses sanitized fixture data for shareable demo and visual QA.

It does **not** index, mutate, delete, reconfigure, or create another memory store. It does not shell out to `o2b`, import private OSB modules, or ship a production OSB adapter.

## Compatibility and status

The Hermes development/dashboard behavior was validated against **Hermes Agent v0.19.0 (2026.7.20)** in local fixture QA. This is evidence for that local fixture setup only: no clean installation has been run and no compatibility claim is made for other Hermes versions, hosts, or dashboard configurations.

The companion owns the versioned snapshot contract `open-second-brain.dashboard.snapshot.v1`. The only OSB surface described here is the documented `o2b.metrics.v1` surface together with the documented graph-export context in [`docs/upstream-data-boundary.md`](docs/upstream-data-boundary.md). There is **no verified OSB release-version compatibility range** and no production OSB adapter. Missing data fails soft in the UI; it must not be filled by guessing or private-module access.

## Prerequisites and authentication

Before any future install, an operator needs:

1. a Hermes Agent installation with the dashboard available and access already protected by the host's dashboard authentication;
2. permission to install a community plugin from GitHub after this repository has been published;
3. an OSB configuration only if using the strictly local prototype reader described below.

The panel does not add its own authentication layer and must not be exposed as a substitute for host authentication. Its direct reader is for a single-user, direct-reader setup only; it does **not** provide multi-user, multi-profile, team, or tenant isolation.

## Future GitHub installation (post-publication only)

After an authorized first commit, a staging install, and publication of the intended repository, the expected Hermes CLI flow is:

```bash
hermes plugins install lipebez/hermes-osb-panel --enable
hermes dashboard --no-open
```

These commands are not a verified installation result. They document the intended post-publication path using the current Hermes CLI form `hermes plugins install <Git URL or owner/repo> --enable`. Do not run them until `lipebez/hermes-osb-panel` actually exists and a staging test has been authorized and completed.

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

## Fail-closed local direct-reader prototype

The default is fail-closed: without an explicitly supplied test reader or the local opt-in below, no vault is read. `FixtureSnapshotReader` is the supported reader for tests, demos, and shareable screenshots.

`DirectMarkdownPrototypeReader` is a read-only local prototype. It reads only the vault named by `OPEN_SECOND_BRAIN_CONFIG` or `O2B_CONFIG` through its `vault` setting; it has no named fallback vault. Enable it only for one authenticated owner-controlled dashboard process:

```bash
export HERMES_OSB_PANEL_ENABLE_DIRECT_MARKDOWN=true
hermes dashboard --stop
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

The archive release scanner is deliberately different: after an explicitly authorized first commit exists, run `PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/check_public_release.py`. It scans only `git archive HEAD` and fails closed when there is no `HEAD`; it must not be replaced with a working-tree scan. This candidate has no archive-validation result yet.

## Contributing and license

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before proposing a change. The code and documentation are MIT licensed; see [`LICENSE`](LICENSE) and [`NOTICE.md`](NOTICE.md). This repository redistributes neither vault content nor source code from Hermes Agent or Open Second Brain.
