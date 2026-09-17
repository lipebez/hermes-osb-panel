# Architecture

## Status and boundary

Hermes OSB Panel is an **independent community companion** by Filipe Bezerra (`@lipebez`). It is not an official Hermes Agent or Open Second Brain (OSB) module; no affiliation, endorsement, maintenance, or upstream API status is implied.

The project owns a dashboard-side, versioned snapshot contract:

```text
approved reader -> normalize_snapshot() -> open-second-brain.dashboard.snapshot.v1 -> dashboard UI
```

The contract is read-only, deterministic, bounded, and sanitized. It does not establish a production OSB integration.

## Snapshot contract

`dashboard/snapshot_contract.py` owns `open-second-brain.dashboard.snapshot.v1`. Normalization whitelists browser-facing fields, removes local topology, redacts secret-like text and absolute paths, creates stable relative identifiers, sorts records deterministically, and represents unavailable optional surfaces explicitly.

The resulting snapshot may include graph nodes and typed edges, provider availability, counts and limits, activity, and metrics. These are dashboard fields, not a claim that OSB currently supplies a single composite snapshot.

## OSB compatibility: intentionally narrow

The documented OSB context is limited to the versioned `o2b.metrics.v1` on-disk surface and the documented graph-export context summarized in [`upstream-data-boundary.md`](upstream-data-boundary.md). `o2b.metrics.v1` is not the same thing as a verified panel adapter.

There is **no verified OSB release-version compatibility range**. There is also no production OSB adapter in this repository. It does not invoke `o2b`, import private OSB modules, write exports, or parse a vault as though its layout were a stable upstream API. A future adapter requires an approved, documented public boundary and its own compatibility evidence.

## Reader boundary and fail-closed default

`FixtureSnapshotReader` accepts sanitized mappings or JSON fixtures and is the reader for tests, demos, and shareable QA. It does not access a configured vault.

Without an explicit injected reader, the panel is fail-closed: it selects an unavailable reader unless `HERMES_OSB_PANEL_ENABLE_DIRECT_MARKDOWN` is enabled in the dashboard process environment. Strict true values are `1`, `true`, `yes`, and `on` after case and whitespace normalization. Any other value leaves vault access disabled. The process must be restarted after changing the environment.

When enabled, `DirectMarkdownPrototypeReader` is a read-only **local prototype**. It resolves the vault from `OPEN_SECOND_BRAIN_CONFIG` or `O2B_CONFIG` and the configured `vault` field; there is no named fallback vault. No HTTP input, browser control, query parameter, API route, or vault record can enable it. Explicit reader injection remains higher priority for tests.

## Direct-reader limitation and threat boundary

The direct reader is not a production adapter and has a deliberate single-user/direct-reader threat boundary:

- it is suitable only for an authenticated, owner-controlled local dashboard;
- it may display owner-visible titles and safe previews to authenticated dashboard viewers;
- it does not promise multi-user, multi-profile, team, tenant, or role isolation;
- it is not suitable for shared or publicly exposed deployments.

The dashboard host is responsible for authentication and access control. The panel must never be described as providing isolation that the host has not enforced.

## Privacy and egress

Normalization removes raw vault/config/Brain/memory-root fields, local paths, vault identifiers in direct-reader payloads, and secret-like values before browser delivery. Shareable materials are restricted to `tests/fixtures/demo_snapshot_v1.json`; real-vault captures and reports are local, ephemeral, and untracked.

The shipped UI uses local/system font stacks and has no panel-originated third-party font, telemetry, analytics, or asset egress. This does not make a claim about the network behavior of Hermes, OSB, the dashboard host, or a future GitHub installation.

## Renderer boundary

The default renderer is a dependency-free accessible 2D Canvas view. The optional 3D Canvas projection consumes the same normalized model and must preserve keyboard-equivalent graph navigation, selection, safe framing, reduced-motion behavior, and listener/animation cleanup. Neither renderer may read a vault directly or expand the snapshot contract.

## Public release boundary

`scripts/check_public_release.py` scans only `git archive HEAD`; it never treats a working-tree scan as equivalent. The scanner reports only `filename: category` and is intended to prevent environment artifacts, binary media, QA reports/screenshots, private paths, identity markers, and credential-shaped material from crossing the public boundary. A successful scan validates the exact committed archive but does not publish a tag, package, or GitHub Release.
