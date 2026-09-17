# Upstream data boundary

## Decision status

The UI contract is implemented and exercised with sanitized fixtures. A production OSB adapter is intentionally deferred until the maintainer chooses the canonical integration boundary. This document maps only surfaces documented in the inspected **Open Second Brain v1.56.0** checkout at observed commit `54bb28d9b760758446e494e3c6473f6534dfbdee`; it does not claim that OSB already emits the dashboard composite snapshot or that other OSB versions are compatible.

## Verified public surfaces

### Run-level metrics

`o2b.metrics.v1` is explicitly documented as the stable dashboard-facing on-disk contract:

- `docs/metrics.md`, under **Metrics layer - the dashboard data contract**, identifies `Brain/metrics/` as the stable consumer contract that avoids importing internals.
- `docs/metrics.md`, under **Record envelope**, defines `{schema, surface, run_at, payload}`, run-level records, torn-line tolerance, and bounded writes.
- `docs/metrics.md`, under **Surfaces and payload fields** and **Reading**, defines additive-optional fields plus newest-first/fail-soft consumer behavior.
- `docs/stability.md`, under **On-disk format schemas**, lists `o2b.metrics.v1` as a frozen schema and defines compatible evolution.

### Graph export

The public CLI exposes graph export:

- `docs/cli-reference.md`, in the `o2b brain` verb list, documents `o2b brain graph-export` as stable graph serialization available since v0.22.0.
- `docs/stability.md`, under **CLI verb tree**, freezes documented verbs, flags, exit behavior, and JSON response shapes under SemVer.
- `src/cli/command-manifest.ts` registers the `graph-export` command.
- `src/core/brain/portability/graph.ts` defines `GRAPH_VERSION`, `VaultGraphNode`, and `exportVaultGraph`: graph version `1`; node `id`, vault-relative `path`, `title`, sorted wikilinks, typed relations; Brain machinery exclusion; pure/read-only export; and deterministic node ordering.

The graph serializer is public and deterministic, but its graph version is not listed in the **On-disk format schemas** table in `docs/stability.md`. The maintainer should confirm whether dashboards may consume this format directly or should receive a supported composite facade.

## Field mapping and measured gaps

| Dashboard field | Documented OSB surface | Stability/read mode | Gap or owner decision |
| --- | --- | --- | --- |
| `metrics` run records | `o2b.metrics.v1` | Frozen on-disk schema; append-only JSONL; fail-soft direct read | Adapter ownership and host access policy remain to be chosen. |
| index/bridge/community/benchmark/tuning counters | named metrics surfaces | Additive-optional payloads | UI must render only present fields and ignore unknown fields. |
| user-page node ID/title | graph export v1 | Public deterministic CLI; `id`, relative `path`, `title` | Confirm direct graph-export consumption versus official facade. |
| wikilink edges | graph export `links` | Sorted, de-duplicated targets | Adapter must resolve targets/duplicates without inventing identity rules. |
| typed relation edges | graph export `relations` | Public typed relation vocabulary | Mapping into `{source,target,kind}` is straightforward after boundary approval. |
| Brain preferences/signals/log nodes | none in graph export | Graph export explicitly excludes Brain machinery | Unresolved. Do not silently reparse Markdown in a production adapter. |
| Active Memory preview | no composite snapshot surface verified | Prototype reader only | Unresolved owner decision. |
| node safe preview/body | graph export has title, links and relations, not body preview | Not available in verified graph surface | Unresolved privacy and access contract. |
| node modified/created timestamps and size | not present in verified graph export node | Not available | Unresolved; UI fails soft when absent. |
| provider availability/health | no single dashboard composite verified | Metrics indicate runs, not current host availability | Host/facade decision required. |
| hubs/orphans/broken-link summaries | can be deterministically derived from an approved graph snapshot | Derived UI data | Decide whether OSB or companion owns derivation. |
| activity timeline/recent log cards | no composite public surface verified | Prototype reader only | Unresolved. |
| Obsidian deep link vault identifier | not derived from filesystem basename | Optional explicit safe configuration | Omit action when identifier is absent. |

## Explicit non-implementation

This repository does not contain a production subprocess wrapper, private OSB import, export writer or `osb_public_adapter.py`. `DirectMarkdownPrototypeReader` remains local-prototype-only. `tests/fixtures/public_snapshot_v1.json` expresses the desired normalized UI boundary, not an assertion that current OSB emits it.

## Maintainer decision to request later

Choose one supported home and boundary:

1. the companion consumes graph export plus `o2b.metrics.v1` through an approved read-only host adapter;
2. OSB exposes an official read-only composite dashboard snapshot;
3. the companion lives inside OSB and calls a supported internal facade.

Repository placement and whether the two prepared review slices should be submitted separately or together are also maintainer decisions. No contact, remote change, push, publication or pull request is part of this readiness work.
