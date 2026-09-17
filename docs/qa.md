# QA protocol

## Scope and truthfulness

This protocol verifies the repository, local fixture behavior, and narrowly scoped upstream contracts. The validated matrix is Hermes Agent v0.21.3 at upstream commit `dfc28b61a0cfed58bcc200038c6bfec6f31adcd2` and Open Second Brain v1.56.0 at observed commit `54bb28d9b760758446e494e3c6473f6534dfbdee`. The OSB evidence is inspection of documented public surfaces, not a production adapter or a broader version range. Source metadata identifies version 3.1.0; public tags and GitHub Releases, when available, are the authority for published artifacts.

One separate Windows PowerShell staging installation achieved plugin discovery, loaded `/second-brain` with the default fail-closed no-local-data state, and then disabled and removed the plugin. This is retained as a narrow workflow result, not as compatibility evidence for another Hermes version, a Windows clean-install harness result, or a production OSB claim. No OSB installation or configuration was required for that default fail-closed validation.

The shareable data source is only `tests/fixtures/demo_snapshot_v1.json` through `FixtureSnapshotReader`. Never use a real vault for a public screenshot, issue attachment, pull request artifact, or report.

## Required exact-candidate gates

Commit the intended candidate, then run this single Bash block from its clean repository root with exclusive control of the repository and its sibling QA directory. Set `HERMES_AGENT_ROOT` to a clean Hermes Agent checkout at the validated commit whose own venv contains the `hermes` executable. The block aborts on every failed command or failed pipeline. It creates a disposable detached Git worktree for the exact candidate outside `/tmp`, creates one archive, and records that archive's SHA-256 in the sanitized operator summary. This procedure detects persistent drift before and after each gate; it is not a sandbox or a cryptographic defense against a hostile same-user process that mutates and restores files during a gate:

```bash
HERMES_AGENT_ROOT=/path/to/hermes-agent-v0.21.3
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
test -z "$(git status --porcelain=v1 --untracked-files=normal)"
CANDIDATE_SHA="$(git rev-parse HEAD)"
[[ "$CANDIDATE_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(git rev-parse HEAD)" = "$CANDIDATE_SHA"
git diff --quiet "$CANDIDATE_SHA" --
git diff --cached --quiet

QA_PARENT="$(dirname "$REPO_ROOT")"
QA_CONTAINER=""
QA_ROOT=""
EVIDENCE_TMP_ROOT=""
MAX_ARCHIVE_BYTES=$((8 * 1024 * 1024))
cleanup() {
  status=$?
  set +e
  cleanup_status=0
  if [[ -n "$QA_ROOT" ]]; then
    git -C "$REPO_ROOT" worktree remove --force "$QA_ROOT" >/dev/null 2>&1 || cleanup_status=1
  fi
  if [[ -n "$QA_CONTAINER" ]]; then
    rm -rf -- "$QA_CONTAINER" || cleanup_status=1
    [[ ! -e "$QA_CONTAINER" ]] || cleanup_status=1
  fi
  if [[ -n "$EVIDENCE_TMP_ROOT" ]]; then
    rm -rf -- "$EVIDENCE_TMP_ROOT" || cleanup_status=1
    [[ ! -e "$EVIDENCE_TMP_ROOT" ]] || cleanup_status=1
  fi
  if ((status != 0)); then
    exit "$status"
  fi
  exit "$cleanup_status"
}
trap cleanup EXIT
QA_CONTAINER="$(mktemp -d "$QA_PARENT/.hermes-osb-panel-release.XXXXXX")"
QA_ROOT="$QA_CONTAINER/candidate-worktree"
EVIDENCE_TMP_ROOT="$(mktemp -d /tmp/hermes-osb-panel-evidence.XXXXXX)"
git -C "$REPO_ROOT" worktree add --detach "$QA_ROOT" "$CANDIDATE_SHA"

ARCHIVE_ROOT="$QA_CONTAINER/extracted-source"
CANDIDATE_ARCHIVE="$QA_CONTAINER/candidate.tar"
TEST_LOG="$QA_CONTAINER/tests.log"
EVIDENCE_OUTPUT="$EVIDENCE_TMP_ROOT/release-evidence.json"
TRACKED_PYTHON_LIST="$QA_CONTAINER/tracked-python.zlist"
(cd "$REPO_ROOT" && git archive --format=tar "$CANDIDATE_SHA" > "$CANDIDATE_ARCHIVE")
CANDIDATE_ARCHIVE_SHA256="$(sha256sum "$CANDIDATE_ARCHIVE" | cut -d ' ' -f 1)"
[[ "$CANDIDATE_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]]
chmod 0444 "$CANDIDATE_ARCHIVE"

assert_candidate_worktree() {
  test "$(git -C "$QA_ROOT" rev-parse HEAD)" = "$CANDIDATE_SHA"
  test -z "$(git -C "$QA_ROOT" status --porcelain=v1 --untracked-files=normal)"
  git -C "$QA_ROOT" diff --quiet "$CANDIDATE_SHA" --
  git -C "$QA_ROOT" diff --cached --quiet
}
assert_archive_digest() {
  test -f "$CANDIDATE_ARCHIVE"
  test ! -L "$CANDIDATE_ARCHIVE"
  test "$(stat -c '%a' "$CANDIDATE_ARCHIVE")" = 444
  test "$(stat -c '%h' "$CANDIDATE_ARCHIVE")" = 1
  test "$(stat -c '%s' "$CANDIDATE_ARCHIVE")" -le "$MAX_ARCHIVE_BYTES"
  test "$(sha256sum "$CANDIDATE_ARCHIVE" | cut -d ' ' -f 1)" = "$CANDIDATE_ARCHIVE_SHA256"
}
assert_archive_parity() {
  tar --compare --file "$CANDIDATE_ARCHIVE" --directory "$ARCHIVE_ROOT"
  PYTHONDONTWRITEBYTECODE=1 python3 -B "$QA_ROOT/scripts/check_archive_parity.py" --archive "$CANDIDATE_ARCHIVE" --root "$ARCHIVE_ROOT"
}

STATIC_GATE_PASSED=false
UNIT_GATE_PASSED=false
ARCHIVE_GATE_PASSED=false
DOCTOR_GATE_PASSED=false

assert_candidate_worktree
(cd "$QA_ROOT" && node --check dashboard/dist/index.js)
assert_candidate_worktree

assert_candidate_worktree
git -C "$QA_ROOT" ls-files -z -- '*.py' > "$TRACKED_PYTHON_LIST"
mapfile -d '' TRACKED_PYTHON < "$TRACKED_PYTHON_LIST"
((${#TRACKED_PYTHON[@]} > 0))
PYTHONDONTWRITEBYTECODE=1 python3 -B - "$QA_ROOT" "${TRACKED_PYTHON[@]}" <<'PY'
import ast
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
for relative in sys.argv[2:]:
    path = root / relative
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY
assert_candidate_worktree
STATIC_GATE_PASSED=true

assert_candidate_worktree
(cd "$QA_ROOT" && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v) \
  2>&1 | tee "$TEST_LOG"
assert_candidate_worktree
DISCOVERED_TESTS="$(python3 -c 'import pathlib, re, sys; matches=re.findall(r"^Ran (\d+) tests? in [0-9.]+s$", pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"), re.MULTILINE); len(matches) == 1 or sys.exit(1); print(matches[0])' "$TEST_LOG")"
printf 'Ran %s tests\n' "$DISCOVERED_TESTS" >> "$TEST_LOG"
TESTS_RUN="$(python3 -c 'import pathlib, re, sys; matches=re.findall(r"^Ran (\d+) tests?$", pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"), re.MULTILINE); len(matches) == 1 or sys.exit(1); print(matches[0])' "$TEST_LOG")"
test -n "$TESTS_RUN"
TESTS_PASSED="$TESTS_RUN"
TESTS_FAILED=0  # reached only after the suite pipeline succeeded
UNIT_GATE_PASSED=true

assert_archive_digest
PYTHONDONTWRITEBYTECODE=1 python3 -B "$QA_ROOT/scripts/check_public_release.py" --archive "$CANDIDATE_ARCHIVE"
assert_archive_digest
ARCHIVE_GATE_PASSED=true

assert_archive_digest
mkdir -p "$ARCHIVE_ROOT"
PYTHONDONTWRITEBYTECODE=1 python3 -B "$QA_ROOT/scripts/check_archive_parity.py" \
  --archive "$CANDIDATE_ARCHIVE"
tar -xf "$CANDIDATE_ARCHIVE" -C "$ARCHIVE_ROOT"
assert_archive_digest
assert_archive_parity

HERMES_REF=dfc28b61a0cfed58bcc200038c6bfec6f31adcd2
test "$(git -C "$HERMES_AGENT_ROOT" rev-parse HEAD)" = "$HERMES_REF"
test -z "$(git -C "$HERMES_AGENT_ROOT" status --porcelain=v1 --untracked-files=normal)"
HERMES_CLI="$HERMES_AGENT_ROOT/venv/bin/hermes"
test -x "$HERMES_CLI"
QA_HOME="$QA_CONTAINER/hermes-home"
assert_archive_digest
HOME="$QA_HOME" HERMES_HOME="$QA_HOME/hermes" \
  "$HERMES_CLI" plugins doctor "$ARCHIVE_ROOT" --ci
DOCTOR_GATE_PASSED=true
assert_archive_digest
assert_archive_parity
assert_archive_digest

PYTHONDONTWRITEBYTECODE=1 python3 -B "$QA_ROOT/scripts/build_release_evidence.py" \
  --project-version 3.1.0 \
  --candidate-sha "$CANDIDATE_SHA" \
  --candidate-archive-sha256 "$CANDIDATE_ARCHIVE_SHA256" \
  --tested-hermes-version 0.21.3 \
  --tested-hermes-ref "$HERMES_REF" \
  --tested-osb-version 1.56.0 \
  --tested-osb-ref 54bb28d9b760758446e494e3c6473f6534dfbdee \
  --static-gate-passed "$STATIC_GATE_PASSED" \
  --unit-gate-passed "$UNIT_GATE_PASSED" \
  --archive-gate-passed "$ARCHIVE_GATE_PASSED" \
  --doctor-gate-passed "$DOCTOR_GATE_PASSED" \
  --tests-run "$TESTS_RUN" \
  --tests-passed "$TESTS_PASSED" \
  --tests-failed "$TESTS_FAILED" \
  --output "$EVIDENCE_OUTPUT"

assert_candidate_worktree
test "$(git -C "$REPO_ROOT" rev-parse HEAD)" = "$CANDIDATE_SHA"
test -z "$(git -C "$REPO_ROOT" status --porcelain=v1 --untracked-files=normal)"
git -C "$REPO_ROOT" diff --quiet "$CANDIDATE_SHA" --
git -C "$REPO_ROOT" diff --cached --quiet
python3 -B -m json.tool "$EVIDENCE_OUTPUT"
```

Node syntax, tracked-file AST parsing, and the complete current suite run only in the disposable detached worktree. Its sibling location under the repository parent preserves the three path-sensitive contracts: a real Git object database and exact `HEAD`, repository `.gitignore` semantics, and a repository root outside `/tmp`. Exact-HEAD, status, unstaged-diff, and staged-diff checks run immediately before and after each Node, AST, and suite gate. The canonical checkout is required clean before worktree creation and checked again after all gates.

The scanner consumes the single tar directly. Its regular-file type, link count, read-only mode, SHA-256, and 8 MiB archive-size ceiling are checked before and after scanning, extraction, and Doctor. Before extraction, the bounded helper rejects raw unsafe paths, duplicate paths, names over 4096 UTF-8 bytes, members over 4 MiB logical size, archive types other than exact regular files and directories, and more than 100,000 entries. The public-release scanner applies the same archive, member, name, type, and entry bounds and reads at most 4 MiB plus one byte from each regular member. GNU tar checks content/type/mode parity, while the helper rejects extra paths, symbolic links, hard-linked regular files, and special extracted entries before and after Doctor. These checks establish stable candidate/archive identity for an operator-controlled run; they do not make pathnames immutable against a hostile concurrent process with the same privileges. Test and gate values become true only after their commands pass, and the summary records the candidate SHA, archive SHA-256, and explicit Doctor success. The EXIT trap is installed before either temporary directory allocation, preserves the original failing status, attempts every cleanup step, and reports cleanup failure after a successful run; it removes the registered worktree, sibling QA container, `/tmp` evidence directory, logs, archive, extraction, and isolated Hermes home.

## Isolated clean-install harness

This real clean-install harness is **Linux-only**. Its ownership proof depends on Linux `/proc` process, session, socket-inode, and listener data; it must fail closed rather than be treated as a Windows or macOS clean-install procedure. The Windows staging result and recovery notes below are separate manual staging evidence, not execution of this harness.

After the release commit is available from GitHub at an immutable lowercase SHA, run from a checkout that has the exact compatible Hermes v0.21.3 executable on `PATH`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/qa_clean_install.py lipebez/hermes-osb-panel --ref <40-character-lowercase-sha>
```

The harness creates disposable `HOME` and `HERMES_HOME` directories outside the repository, writes a random private `install_id` before Dashboard startup, removes OSB discovery and dashboard authentication/session variables from child environments, installs and validates the plugin, starts only its owned loopback Dashboard child, checks the no-OSB fail-closed API state, runs fixture CDP QA, then disables/removes the plugin and confirms absence. Installed manifests and dashboard assets are opened through an `O_DIRECTORY|O_NOFOLLOW` descriptor tree, checked for safe type/link count/permissions, and read once. The asset bytes must equal the `dashboard/dist/index.js` and `dashboard/dist/style.css` Git blobs at the exact requested commit in this checkout. Those validated bytes cross into the CDP runner only through fully sealed anonymous Linux `memfd` descriptors; no asset pathname is reopened. Every accepted clean-install API response is fetched through a new direct persistent HTTP/1.1 connection: `/api/status` must return that exact `install_id`, then the target is fetched on the same unchanged socket. Redirects, proxies, reconnects, connection replacement, missing identity, and oversized or non-JSON responses fail closed and retry the complete transaction under its deadline. Linux `/proc` process-group/listener checks remain an additional defense around API acceptance; unavailable or inconsistent evidence fails closed. These clean-install API identity and listener gates do not attest ownership of the separate CDP fixture-server flow. The harness terminates only the process it started and deletes all temporary state on success or failure. Do not run this command for a commit that is not reachable from the public Git source: installation intentionally resolves the exact remote ref and is not a working-tree test.

## Manual CDP matrix

The dashboard host must already be running. The CDP harness does not install, start, or expose a host service. Authenticated runs accept only strict `http`/`https` loopback URLs whose hostname is exactly `127.0.0.1`, `localhost`, or `::1`, without userinfo; this validation occurs before any authentication value is read. Fixture Chromium runs route browser traffic through a browser-global exact-origin proxy. The proxy rejects CONNECT and non-exact request origins, but forwards same-origin 3xx responses; request-stage CDP interception checks the subsequent destination and blocks it if it leaves the one allowed origin. Request bodies, unexpected response shapes, and other egress are also denied, and any denial fails the run. Before the first navigation, interception is enabled and retained through every assertion, while browser API blocking, resolver denial, and background-networking controls provide defense in depth. Fixture runs allow HTTP only to the exact generated `http://127.0.0.1:PORT` origin; another port, `localhost`, IPv6, HTTPS, WebSocket, redirects to another origin, `file:` and `chrome-extension:` are blocked. `data:` and `blob:` are the only non-egress schemes allowed because Chromium may use them for in-memory document resources. Only fixture runs redirect configurable Chromium service bases to that generated origin; GCM endpoints use dedicated `__qa_browser_internal` paths served only by the fixture and disjoint from dashboard assets and APIs. Root execution still requires Chromium's `--no-sandbox`: these proxy/CDP/flag controls are a QA egress boundary, **not** an operating-system sandbox. When host authentication is used, provide credentials only through an operator-controlled local environment mechanism; never print, save, or commit the password, cookie, or session token.

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

The exact-candidate block builds the aggregate only after Node, AST, the complete suite, archive scanning, archive/extraction parity, and Hermes Doctor have succeeded. The builder rejects any false gate, requires the lowercase 64-hex candidate archive SHA-256, and emits `evidence_kind: sanitized_operator_summary.v1`. It does not inspect Git, recompute the digest, authenticate the operator, or read logs; therefore the JSON is a deterministic sanitized summary of explicit operator-supplied results, not a signature or independent attestation. Its safe aggregate facts may be copied into release notes only together with the reviewed workflow context.

## Archive-only public release scanner

The production gate above passes the already-created candidate tar to `scripts/check_public_release.py --archive`; it never reconstructs the candidate from the extracted working directory. With no `--archive`, the scanner retains its convenience behavior of scanning `git archive HEAD`, but that standalone mode is not a substitute for the single-archive release procedure. A pass does not imply that a tag or GitHub Release exists.

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
