# QA protocol

## Scope and truthfulness

This protocol verifies the repository, local fixture behavior, and narrowly scoped upstream contracts. The validated matrix is Hermes Agent v0.21.3 at upstream commit `dfc28b61a0cfed58bcc200038c6bfec6f31adcd2` and Open Second Brain v1.56.0 at observed commit `54bb28d9b760758446e494e3c6473f6534dfbdee`. The OSB evidence is inspection of documented public surfaces, not a production adapter or a broader version range. At this documentation freeze, before the release merge, no `v3.1.0` tag or GitHub Release existed.

One separate Windows PowerShell staging installation achieved plugin discovery, loaded `/second-brain` with the default fail-closed no-local-data state, and then disabled and removed the plugin. This is retained as a narrow workflow result, not as compatibility evidence for another Hermes version, a Windows clean-install harness result, or a production OSB claim. No OSB installation or configuration was required for that default fail-closed validation.

The shareable data source is only `tests/fixtures/demo_snapshot_v1.json` through `FixtureSnapshotReader`. Never use a real vault for a public screenshot, issue attachment, pull request artifact, or report.

## Required static and unit gates

Run from the repository root:

```bash
node --check dashboard/dist/index.js
set -o pipefail
TEST_LOG="$(mktemp /tmp/hermes-osb-panel-tests.XXXXXX)"
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v 2>&1 | tee "$TEST_LOG"
TESTS_RUN="$(python3 -c 'import pathlib, re, sys; matches=re.findall(r"Ran (\d+) tests?", pathlib.Path(sys.argv[1]).read_text()); print(matches[-1] if matches else "")' "$TEST_LOG")"
test -n "$TESTS_RUN"
rm -f "$TEST_LOG"
PYTHONDONTWRITEBYTECODE=1 python3 -B -c 'import ast, pathlib, subprocess; [ast.parse(pathlib.Path(path).read_text(encoding="utf-8"), filename=path) for path in subprocess.check_output(["git", "ls-files", "*.py"], text=True).splitlines()]'
```

The discovery command runs the complete current suite without baking a test count into the documentation. It covers normalization, fail-closed reader selection, redaction, fixture handling, plugin contract behavior, static asset egress checks, and release-scanner pure functions. Run it with bytecode disabled and confirm no bytecode artifacts were introduced.

Validate against the exact Hermes checkout without loading or changing an active profile:

```bash
HERMES_CLI=/path/to/hermes-agent-v0.21.3/venv/bin/hermes
(
  QA_HOME="$(mktemp -d)"
  trap 'rm -rf "$QA_HOME"' EXIT
  HOME="$QA_HOME" HERMES_HOME="$QA_HOME/hermes" "$HERMES_CLI" plugins doctor . --ci
)
```

The executable must be built from `dfc28b61a0cfed58bcc200038c6bfec6f31adcd2`; a matching version string alone is insufficient. The subshell removes its temporary home even if the doctor check fails.

## Isolated clean-install harness

This real clean-install harness is **Linux-only**. Its ownership proof depends on Linux `/proc` process, session, socket-inode, and listener data; it must fail closed rather than be treated as a Windows or macOS clean-install procedure. The Windows staging result and recovery notes below are separate manual staging evidence, not execution of this harness.

After the release commit is available from GitHub at an immutable lowercase SHA, run from a checkout that has the exact compatible Hermes v0.21.3 executable on `PATH`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/qa_clean_install.py lipebez/hermes-osb-panel --ref <40-character-lowercase-sha>
```

The harness creates disposable `HOME` and `HERMES_HOME` directories outside the repository, writes a random private `install_id` before Dashboard startup, removes OSB discovery and dashboard authentication/session variables from child environments, installs and validates the plugin, starts only its owned loopback Dashboard child, checks the no-OSB fail-closed API state, runs fixture CDP QA, then disables/removes the plugin and confirms absence. Installed manifests and dashboard assets are opened through an `O_DIRECTORY|O_NOFOLLOW` descriptor tree, checked for safe type/link count/permissions, and read once. The asset bytes must equal the `dashboard/dist/index.js` and `dashboard/dist/style.css` Git blobs at the exact requested commit in this checkout. Those validated bytes cross into the CDP runner only through fully sealed anonymous Linux `memfd` descriptors; no asset pathname is reopened. Every accepted clean-install API response is fetched through a new direct persistent HTTP/1.1 connection: `/api/status` must return that exact `install_id`, then the target is fetched on the same unchanged socket. Redirects, proxies, reconnects, connection replacement, missing identity, and oversized or non-JSON responses fail closed and retry the complete transaction under its deadline. Linux `/proc` process-group/listener checks remain an additional defense around API acceptance; unavailable or inconsistent evidence fails closed. These clean-install API identity and listener gates do not attest ownership of the separate CDP fixture-server flow. The harness terminates only the process it started and deletes all temporary state on success or failure. Do not run this command for a commit that is not reachable from the public Git source: installation intentionally resolves the exact remote ref and is not a working-tree test. Because no v3.1.0 release ref existed at this documentation freeze, that final remote clean-install gate was not available yet.

## Manual CDP matrix

The dashboard host must already be running. The CDP harness does not install, start, or expose a host service. Authenticated runs accept only strict `http`/`https` loopback URLs whose hostname is exactly `127.0.0.1`, `localhost`, or `::1`, without userinfo; this validation occurs before any authentication value is read. Fixture Chromium runs route browser traffic through a browser-global exact-origin proxy. The proxy rejects CONNECT, non-exact origins, redirects, request bodies, unexpected response shapes, and other egress; any denial fails the run. Before the first navigation, request-stage CDP interception is enabled and retained through every assertion, while browser API blocking, resolver denial, and background-networking controls provide defense in depth. Fixture runs allow HTTP only to the exact generated `http://127.0.0.1:PORT` origin; another port, `localhost`, IPv6, HTTPS, WebSocket, redirects to another origin, `file:` and `chrome-extension:` are blocked. `data:` and `blob:` are the only non-egress schemes allowed because Chromium may use them for in-memory document resources. Only fixture runs redirect configurable Chromium service bases to that generated origin; GCM endpoints use dedicated `__qa_browser_internal` paths served only by the fixture and disjoint from dashboard assets and APIs. Root execution still requires Chromium's `--no-sandbox`: these proxy/CDP/flag controls are a QA egress boundary, **not** an operating-system sandbox. When host authentication is used, provide credentials only through an operator-controlled local environment mechanism; never print, save, or commit the password, cookie, or session token.

Chromium QA requires the `websocket-client` distribution (the imported module is `websocket`). Install it in a disposable venv outside the repository; do not add it to the Hermes runtime environment:

```bash
QA_VENV="$(mktemp -d /tmp/hermes-osb-panel-cdp-venv.XXXXXX)"
python3 -m venv "$QA_VENV"
"$QA_VENV/bin/python" -m pip install 'websocket-client==1.9.2'
```

```bash
HERMES_WEBUI_ENV_FILE=local-webui.env \
"$QA_VENV/bin/python" scripts/qa_dashboard_cdp.py \
  --url http://127.0.0.1:PORT/second-brain \
  --output /tmp/osb-panel-live-qa
```

Keep the same shell for both blocks, then delete the QA-only environment with `rm -rf "$QA_VENV"`. Do not install `websocket-client` into the Hermes runtime environment or add it as a runtime dependency.

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
  --candidate-sha "$(git rev-parse HEAD)" \
  --tested-hermes-version 0.21.3 \
  --tested-hermes-ref dfc28b61a0cfed58bcc200038c6bfec6f31adcd2 \
  --tested-osb-version 1.56.0 \
  --tested-osb-ref 54bb28d9b760758446e494e3c6473f6534dfbdee \
  --static-gate-passed true \
  --unit-gate-passed true \
  --archive-gate-passed true \
  --tests-run "$TESTS_RUN" \
  --tests-passed "$TESTS_RUN" \
  --tests-failed 0 \
  --output /tmp/hermes-osb-panel-release-evidence.json
```

`TESTS_RUN` comes from the successful complete-suite command above rather than a count frozen in documentation; keep the same shell or export the verified value explicitly. The builder accepts only semantic versions, immutable SHAs, booleans, and consistent non-negative test counts. It does not itself read Git, the repository, environment variables, host state, secrets, screenshots, reports, or raw logs; the shell resolves the explicit SHA and count arguments before invocation. Do not add sensitive materials to its arguments or output. The JSON has a fixed schema and canonical key ordering, so identical inputs produce identical bytes.

Review the generated file locally. Only its safe aggregate facts may be copied into release notes. Do not commit the JSON by default, and delete the `/tmp` file after the release review.

## Archive-only public release scanner

After the intended release documentation and source are committed locally, run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/check_public_release.py
```

The production gate scans exactly `git archive HEAD`; it does not see uncommitted edits. Do **not** use its result as a substitute for validating the intended committed release tree, and do not replace it with a working-tree scan. A pass does not imply that a tag or GitHub Release exists.

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
