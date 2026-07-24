# Upstream review slices

Both renderers remain product capabilities. The split exists to make review, rollback and performance risk explicit; it is not a proposal to discard 3D.

## Prepared local commit series

The readiness repository contains two unpushed local refs with an independently applicable parent chain:

- Slice A, `readiness/upstream-2d-foundation`: `417f672c3ebae51c3de51ba7c36cb67a7ff9ca80..eecca541dbd89ab3b57fb8ce41caf59ed0528c43`
- Slice B, `readiness/upstream-3d-enhancement`: `eecca541dbd89ab3b57fb8ce41caf59ed0528c43..0c6f83217c18a7c7823683d0d541a148dfa31d84`

Slice A is a complete 2D-only tree with `capabilities.graph_3d=false`, no 3D runtime, styling or control, 41 passing tests plus ten subtests and 102 passing synthetic CDP checks over four viewports. Slice B is its direct child, restores the optional real 3D renderer, and has the same tree as validated product commit `8e7f81a`. Neither ref has a remote or has been pushed.

## Slice A — portable contract and accessible 2D foundation

Contents:

- `open-second-brain.dashboard.snapshot.v1` normalization and privacy boundary;
- approved-reader injection plus sanitized public/demo fixtures;
- explicitly isolated direct-Markdown prototype reader;
- generic plugin metadata, MIT license, provenance and contribution docs;
- accessible dashboard shell with 2D selected by default;
- shared filters, inspector, activity, graph list and keyboard navigation;
- contract, privacy, static asset and 2D behavior tests.

Acceptance:

- fully useful with 3D disabled or unavailable;
- no local path, user, vault or secret metadata in browser payloads;
- no speculative production adapter;
- all unit/contract/privacy/static gates green;
- 2D passes the four-viewport CDP matrix.

## Slice B — optional dependency-free 3D enhancement

Contents:

- Canvas-projected 3D renderer over the exact Slice A node/edge model;
- optional capability/toggle with 2D remaining the initial mode;
- orbit, zoom, fit-all, fit-selection and selection/navigation parity;
- reduced-motion behavior and accurate orbit state;
- mobile-safe framing and controls;
- animation-frame/listener cleanup instrumentation;
- independent 3D CDP and performance acceptance probes.

Acceptance:

- no third-party 3D dependency;
- every projected node remains inside the safe padded canvas rectangle after settle, orbit, zoom recovery, fit-all and fit-selection;
- reduced-motion disables automatic/ambient orbit but not manual fit, zoom or selection;
- repeated renderer switching leaves one active loop and listener set;
- keyboard-equivalent graph list is identical to 2D;
- all four target viewports pass with zero console errors.

## Performance budget

The current renderer is designed for the bounded snapshot limits declared by the contract. Slice B must keep one requestAnimationFrame loop per mounted 3D scene, stop it on cleanup, avoid duplicate listeners, cap device-pixel work to the visible canvas and preserve responsive interaction at the fixture/node limits. Any future increase to graph caps requires a fresh measured budget rather than an unbounded default.

## Maintainer choices deferred

The maintainer may accept Slice A and Slice B independently, request both as one optional-module contribution, or choose a different canonical repository. The final OSB reader boundary is separately deferred as described in `docs/upstream-data-boundary.md`.
