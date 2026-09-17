# Changelog

All notable changes to Hermes OSB Panel will be documented in this file.

## 3.1.0 - 2026-09-17

### Changed

- Localized all browser-visible dashboard interface strings and normalized vault-area labels to English, including navigation, filters, graph controls, inspectors, activity views, command palette, accessibility labels, empty/error states, and copy feedback. User-authored note content remains untouched.
- Updated behavioral CDP QA to exercise the English controls, preserve the same data hooks and privacy boundary, and validate heading typography only when the selected fixture renders that heading level.
- Clarified complete plugin removal and corrected Windows `WinError 5` recovery: stop the dashboard, disable the plugin, target the installed directory explicitly, and clear ReadOnly only on hidden files before retrying the official CLI removal.

### Added

- Public documentation for the independent-community status, read-only snapshot boundary, fail-closed direct-reader opt-in, privacy rules, manual QA, contribution guardrails, and vulnerability reporting process.
- Ordered platform-specific direct-reader commands: PowerShell for Windows and Bash/Zsh for Linux/macOS, each with matching fail-closed disable/restart sequences.
- Explicit documentation of the versioned companion contract `open-second-brain.dashboard.snapshot.v1` and the documented `o2b.metrics.v1` context.
- Recorded private-staging validation: a Windows PowerShell clean-host install achieved discovery, the `/second-brain` default fail-closed no-data state, disable, and removal after a narrow ReadOnly recovery.
- Added Windows-only removal-recovery documentation for `WinError 5` after disable: clear ReadOnly attributes only within the already-disabled plugin directory, then retry the official plugin removal command.

### Security

- Documented the single-user/direct-reader threat boundary and the prohibition on claiming multi-user isolation.
- Documented demo-fixture-only public artifacts, no panel-originated third-party egress, and archive-only release scanning.
- Hardened authenticated CDP QA: the harness rejects non-loopback `--url` values before it can read or attach dashboard authentication material.
- Added a browser-global exact-origin proxy for fixture Chromium runs, with CDP request interception and browser-API blocking retained as defense in depth. This is a QA egress boundary, not an operating-system sandbox.

### Compatibility notes

- Validated the plugin admission/runtime contracts and local fixture behavior against Hermes Agent v0.21.3 at exact upstream commit `dfc28b61a0cfed58bcc200038c6bfec6f31adcd2`.
- Inspected the documented OSB public-data boundary against Open Second Brain v1.56.0 at observed commit `54bb28d9b760758446e494e3c6473f6534dfbdee`. This does not claim a production adapter or compatibility with other OSB versions.
- Retained the separate Windows PowerShell staging result for discovery and disable/removal recovery without treating it as a broad compatibility claim or as execution of the Linux-only clean-install harness.
- Source metadata identifies version 3.1.0. Public tags and GitHub Releases, when available, are the authority for published artifacts; this changelog does not imply that one exists.
