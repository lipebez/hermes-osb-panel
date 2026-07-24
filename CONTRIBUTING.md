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

The stable companion contract is `open-second-brain.dashboard.snapshot.v1`. The documented OSB surface is `o2b.metrics.v1`; there is no verified OSB release-version compatibility range and no production OSB adapter. Prefer fixture and contract improvements over a direct-vault workaround.

## Local checks

Run from the repository root before requesting review:

```bash
node --check dashboard/dist/index.js
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -B -c 'import ast, pathlib; [ast.parse(pathlib.Path(path).read_text(encoding="utf-8"), filename=path) for path in ("__init__.py", "dashboard/plugin_api.py", "dashboard/snapshot_contract.py", "scripts/qa_dashboard_cdp.py")]'
```

Run the manual CDP matrix in [`docs/qa.md`](docs/qa.md) only against a host that is already running and only with the demo fixture for shareable captures. Place outputs outside the repository, inspect them locally, and delete the report and captures after review. Do not retain real-vault QA output.

After an explicitly authorized local commit exists, run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/check_public_release.py
```

This is an archive-only gate over `git archive HEAD`. Before the first commit it intentionally fails with a no-`HEAD` result; do not call an uncommitted tree archive-validated and do not substitute a working-tree scan. The scanner emits only `filename: category`; fix the artifact rather than broadening exclusions.

## Reviewable slices

Keep changes small and independently reviewable:

1. normalized snapshot/privacy boundary and accessible 2D foundation;
2. optional dependency-free 3D renderer with its own mobile, reduced-motion, lifecycle, and performance QA;
3. documentation or test-only changes that preserve the preceding boundaries.

Mention the fixture used, checks run, and any manual QA intentionally not run in the pull request description. If a report could contain sensitive data, summarize only the safe outcome; do not attach the report.

## Security reports

Do not file vulnerabilities as public issues. **GitHub private vulnerability reporting is enabled**; follow [`SECURITY.md`](SECURITY.md) to submit a private report. If it becomes unavailable, do not include sensitive details in an issue and contact `@lipebez` through GitHub instead.
