# Changelog

All notable changes to Hermes OSB Panel will be documented in this file.

## Unreleased

### Added

- Public documentation for the independent-community status, read-only snapshot boundary, fail-closed direct-reader opt-in, privacy rules, manual QA, contribution guardrails, and vulnerability reporting process.
- Explicit documentation of the versioned companion contract `open-second-brain.dashboard.snapshot.v1` and the documented `o2b.metrics.v1` context.
- Recorded private-staging validation: a Windows PowerShell clean-host install achieved discovery, the `/second-brain` default fail-closed no-data state, disable, and removal after a narrow ReadOnly recovery.
- Added Windows-only removal-recovery documentation for `WinError 5` after disable: clear ReadOnly attributes only within the already-disabled plugin directory, then retry the official plugin removal command.

### Security

- Documented the single-user/direct-reader threat boundary and the prohibition on claiming multi-user isolation.
- Documented demo-fixture-only public artifacts, no panel-originated third-party egress, and archive-only release scanning.

### Compatibility notes

- Hermes development/dashboard behavior was validated in local fixture QA against Hermes Agent v0.19.0 (2026.7.20), plus one Windows PowerShell clean-host private-staging installation test.
- The private-staging archive gate passed. Private staging does not publish a release: `lipebez/hermes-osb-panel` still does not exist, and its install path remains post-publication instruction only.
- No OSB release-version compatibility range or production OSB adapter has been verified.

## Package metadata 3.1.0 (pre-publication)

`3.1.0` is current package metadata in the local public-release candidate. No public version has been published or released.
