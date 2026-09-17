# Contributing

Thank you for helping improve an independent community companion. Hermes OSB Panel is not an official Hermes Agent or Open Second Brain project. Contributions must preserve that status, the read-only boundary, and the privacy model.

## Before you start

- Work only with sanitized fixtures, especially `tests/fixtures/demo_snapshot_v1.json`.
- Do not add a production OSB adapter, private OSB imports, `o2b` subprocess calls, background indexing, mutation, or a parallel memory store without a separately approved public contract.
- Do not claim support for a Hermes or OSB release unless a clean installation and the stated compatibility scope were actually verified.
- The public source repository is https://github.com/lipebez/hermes-osb-panel. Do not present its install command as broad compatibility validation or open a remote action without separate authorization.

## Privacy guardrails

Never commit, paste into an issue, or include in a pull request:

- vault notes, titles, previews, exports, or direct-reader output from a real vault;
- absolute paths, host topology, user names, credentials, cookies, session tokens, authentication headers, local environment files, or screenshots from authenticated real-vault QA;
- generated QA reports or screenshots, including sanitized-looking copies, unless a later privacy-reviewed baseline is expressly approved.

A demo must use `FixtureSnapshotReader` and `tests/fixtures/demo_snapshot_v1.json`. The direct reader is intentionally limited to local, authenticated, single-user use. Do not reword it into a multi-user or isolation guarantee.

## Scope rules

Keep all runtime behavior read-only. Preserve the default fail-closed behavior: no vault read occurs until the process has the strict local opt-in `HERMES_OSB_PANEL_ENABLE_DIRECT_MARKDOWN` set to an accepted true value and the dashboard process is restarted. That opt-in belongs only in an operator's local process environment; never place it in source, a fixture, documentation sample containing private config, or a browser-facing control.

The stable companion contract is `open-second-brain.dashboard.snapshot.v1`. The documented OSB surface is `o2b.metrics.v1`; there is no production OSB adapter. The only validated matrix is Hermes Agent v0.21.3 at `dfc28b61a0cfed58bcc200038c6bfec6f31adcd2` and Open Second Brain v1.56.0 at `54bb28d9b760758446e494e3c6473f6534dfbdee`, with the OSB claim limited to inspection of its documented public surfaces. Prefer fixture and contract improvements over a direct-vault workaround.

## Local checks

Run from the repository root before requesting review:

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

The Hermes executable must come from exact upstream commit `dfc28b61a0cfed58bcc200038c6bfec6f31adcd2`. The temporary home prevents the doctor check from loading or modifying an active profile and is removed even if the check fails.

Run the manual CDP matrix in [`docs/qa.md`](docs/qa.md) only against a host that is already running and only with the demo fixture for shareable captures. Place outputs outside the repository, inspect them locally, and delete the report and captures after review. Do not retain real-vault QA output.

After the intended documentation and source are committed locally, run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/check_public_release.py
```

This is an archive-only gate over `git archive HEAD`; it does not inspect uncommitted edits. Do not call a working tree archive-validated and do not substitute a working-tree scan. The scanner emits only `filename: category`; fix the artifact rather than broadening exclusions.

## Reviewable slices

Keep changes small and independently reviewable:

1. normalized snapshot/privacy boundary and accessible 2D foundation;
2. optional dependency-free 3D renderer with its own mobile, reduced-motion, lifecycle, and performance QA;
3. documentation or test-only changes that preserve the preceding boundaries.

Mention the fixture used, checks run, and any manual QA intentionally not run in the pull request description. If a report could contain sensitive data, summarize only the safe outcome; do not attach the report.

## Security reports

Do not file vulnerabilities as public issues. **GitHub private vulnerability reporting is enabled**; follow [`SECURITY.md`](SECURITY.md) to submit a private report. If it becomes unavailable, do not include sensitive details in an issue and contact `@lipebez` through GitHub instead.
