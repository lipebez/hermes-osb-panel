# QA protocol

## Scope and truthfulness

This protocol verifies the repository, local fixture behavior, and one recorded private-staging check. It does **not** establish public-release availability, broad Hermes compatibility, a production OSB adapter, or an OSB release-version compatibility range. Hermes development/dashboard behavior was validated in local fixture QA against Hermes Agent v0.19.0 (2026.7.20).

One Windows PowerShell clean-host private-staging installation test achieved plugin discovery, loaded `/second-brain` with the default fail-closed no-local-data state, and then disabled and removed the plugin. This is one staging result, not a claim about other Windows systems, hosts, Hermes versions, or production OSB. No OSB installation or configuration was required for that default fail-closed validation.

The shareable data source is only `tests/fixtures/demo_snapshot_v1.json` through `FixtureSnapshotReader`. Never use a real vault for a public screenshot, issue attachment, pull request artifact, or report.

## Required static and unit gates

Run from the repository root:

```bash
node --check dashboard/dist/index.js
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -B -c 'import ast, pathlib; [ast.parse(pathlib.Path(path).read_text(encoding="utf-8"), filename=path) for path in ("__init__.py", "dashboard/plugin_api.py", "dashboard/snapshot_contract.py", "scripts/qa_dashboard_cdp.py", "scripts/qa_clean_install.py")]'
```

The suite covers normalization, fail-closed reader selection, redaction, fixture handling, plugin contract behavior, static asset egress checks, and release-scanner pure functions. Run it with bytecode disabled and confirm no bytecode artifacts were introduced.

## Isolated clean-install harness

After the candidate commit is available from GitHub at an immutable lowercase SHA, run from a checkout that has a compatible `hermes` executable on `PATH`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/qa_clean_install.py owner/repo --ref <40-character-lowercase-sha>
```

The harness creates disposable `HOME` and `HERMES_HOME` directories outside the repository, writes a random private `install_id` before Dashboard startup, removes OSB discovery and dashboard authentication/session variables from child environments, installs and validates the plugin, starts only its owned loopback Dashboard child, checks the no-OSB fail-closed API state, runs fixture CDP QA, then disables/removes the plugin and confirms absence. Every accepted clean-install API response is fetched through a new direct persistent HTTP/1.1 connection: `/api/status` must return that exact `install_id`, then the target is fetched on the same unchanged socket. Redirects, proxies, reconnects, connection replacement, missing identity, and oversized or non-JSON responses fail closed and retry the complete transaction under its deadline. Linux `/proc` process-group/listener checks remain an additional defense around API acceptance; unavailable or inconsistent evidence fails closed. These clean-install API identity and listener gates do not attest ownership of the separate CDP fixture-server flow. The harness terminates only the process it started and deletes all temporary state on success or failure. Do not run this command for an unpublished branch: installation intentionally resolves the exact GitHub ref and is not a working-tree test.

## Manual CDP matrix

The dashboard host must already be running. The CDP harness does not install, start, or expose a host service. Authenticated runs accept only strict `http`/`https` loopback URLs whose hostname is exactly `127.0.0.1`, `localhost`, or `::1`, without userinfo; this validation occurs before any authentication value is read. When host authentication is used, provide credentials only through an operator-controlled local environment mechanism; never print, save, or commit the password, cookie, or session token.

```bash
HERMES_WEBUI_ENV_FILE=local-webui.env \
python3 scripts/qa_dashboard_cdp.py \
  --url http://127.0.0.1:PORT/second-brain \
  --output /tmp/osb-panel-live-qa
```

Run both renderer modes at all required viewports:

- 1440×900
- 1280×577
- 1024×768
- 390×844

Check shell fit, internal scroll reachability, no horizontal residual scroll, no console errors, 2D canvas paint, keyboard navigation, reduced-motion behavior, 3D pointer/touch orbit, zoom, fit-all, fit-selected, selection parity, label-safe bounds, and repeated renderer lifecycle cleanup.

## Exact CDP artifact retention rule

`--output` must point outside the repository. The harness output (PNG captures and `report.json`) is review material only:

1. use the demo fixture for any shareable capture;
2. inspect all captures and the sanitized report locally;
3. record only a safe aggregate pass/fail summary if needed;
4. delete the complete output directory, including PNG files and `report.json`, immediately after inspection.

Do not commit, attach, upload, or retain CDP output in the repository. Real-vault CDP output is never shareable and must be deleted after local inspection. A later tracked visual baseline requires an explicit privacy review and a dedicated release-scanner-safe policy; none is approved here.

## Sanitized release-evidence summary

After the relevant gates have run, create the aggregate from explicit values only. Use immutable lowercase 40-character commit SHAs for the candidate and both tested refs, and write directly below `/tmp`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/build_release_evidence.py \
  --project-version 3.1.0 \
  --candidate-sha <40-character-candidate-sha> \
  --tested-hermes-version <tested-semver> \
  --tested-hermes-ref <40-character-hermes-sha> \
  --tested-osb-version <tested-semver> \
  --tested-osb-ref <40-character-osb-sha> \
  --static-gate-passed true \
  --unit-gate-passed true \
  --archive-gate-passed true \
  --tests-run <count> \
  --tests-passed <count> \
  --tests-failed <count> \
  --output /tmp/hermes-osb-panel-release-evidence.json
```

The builder accepts only semantic versions, immutable SHAs, booleans, and consistent non-negative test counts. It does not read Git, the repository, environment variables, host state, secrets, screenshots, reports, or raw logs. Do not add any of those materials to its arguments or output. The JSON has a fixed schema and canonical key ordering, so identical inputs produce identical bytes.

Review the generated file locally. Only its safe aggregate facts may be copied into release notes. Do not commit the JSON by default, and delete the `/tmp` file after the release review.

## Archive-only public release scanner

After a separately authorized first local commit exists, run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/check_public_release.py
```

The production gate scans exactly `git archive HEAD`. With no `HEAD`, it intentionally returns a nonzero fail-closed result stating that a committed release candidate is required. Do **not** run the archive-only production scanner as a substitute for an uncommitted candidate, and do not replace it with a working-tree scan.

The private-staging archive gate passed. That evidence is separate from public publication and does not replace the archive-only gate for an authorized public-release candidate.

The scanner prints only `filename: category` for findings. For manual scanner artifact retention, keep that terminal result only long enough to remove the offending material; do not commit scanner logs, reports, screenshots, or copies of matched content. A pass emits no artifact. The exact neutral binary fixture exception is `tests/fixtures/release_scanner_neutral.png`; do not add broad fixture, test, PII, or media exclusions.

## Manual inspection checklist

Before deleting demo output, confirm:

- no clipped toolbar, inspector, status bar, or bottom panel;
- no graph/inspector/activity overlap and no off-screen or unsafe labels;
- 2D is the default, and 3D remains optional and usable;
- compact mode exposes one reachable surface at a time;
- no real names, vault content, paths, tokens, credentials, cookies, session material, or host topology appears;
- no external font, telemetry, analytics, or panel asset request was introduced.

Record missing tools or intentionally omitted manual checks honestly. Do not convert an unavailable CDP run into a claim that the clean-install or live-host QA passed.

## Windows disable/remove recovery

For a Windows clean-host staging reproduction, first verify discovery and the `/second-brain` default fail-closed view without local data, then disable the plugin:

```powershell
hermes plugins disable hermes-osb-panel
```

In the recorded staging test, the initial official removal returned `WinError 5` because ReadOnly Git pack `.idx`, `.pack`, and `.rev` files remained inside the already-disabled plugin directory. Treat this as a Windows host/CLI cleanup condition, not as a panel failure. Only in that condition, set `$pluginDir` to the already-disabled `hermes-osb-panel` directory, clear ReadOnly on files recursively inside that single directory, then retry the official command:

```powershell
$pluginDir = Join-Path $env:LOCALAPPDATA 'hermes\plugins\hermes-osb-panel'
Get-ChildItem -LiteralPath $pluginDir -Recurse -File -Force | ForEach-Object { $_.IsReadOnly = $false }
hermes plugins remove hermes-osb-panel
```

Do not use generic recursive removal or apply this recovery to another plugin. The command changes only the ReadOnly file attribute below the supplied disabled-plugin directory; it does not delete files.
