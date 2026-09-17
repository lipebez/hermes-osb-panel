# Security policy

## Reporting a vulnerability

Use **GitHub private vulnerability reporting** for security reports. Do not put vulnerability details or sensitive reproduction material in a public issue.

If private reporting is unavailable, **do not open a public issue containing vulnerability details, vault data, paths, screenshots, credentials, tokens, cookies, session material, or reproduction data from a real vault**. Instead, contact `@lipebez` through GitHub and request a private reporting route. No security email address is published because none has been established for this project.

A useful private report contains the affected version or commit, a minimal sanitized reproduction, impact, and safe mitigation notes. Use the demo fixture rather than real-vault data whenever possible.

## Response targets

This is a community-maintained project with targets, not guarantees:

- acknowledgement target: within **7 days**;
- coordinated disclosure target: within **90 days** of acknowledgement, unless reporters and maintainers agree that a different timeline reduces harm.

Please do not disclose a vulnerability publicly before a coordinated fix or the agreed disclosure date.

## Supported versions

The supported source line is **3.1.x**, including its commits before or without a packaged release. Public tags and GitHub Releases, when available, are the authority for published artifacts in that line. Versions before 3.1 are unsupported. Support for a later minor line must be stated here rather than inferred.

## Security boundary and non-goals

Hermes OSB Panel is a privacy-first, read-only, independent community companion. It is not an official Hermes Agent or Open Second Brain component, and it does not provide a production OSB adapter.

The default reader is fail-closed: a vault is not read without explicit process-local opt-in. The optional direct reader is for a single authenticated owner-controlled dashboard only. It may display owner-visible titles and safe previews to authenticated viewers. It does **not** promise multi-user, multi-profile, team, tenant, or role isolation; do not deploy it as though it did.

The dashboard host owns authentication, session handling, network exposure, and access control. Reports involving those boundaries should identify the host configuration without sharing secrets.

## Data handling

Never submit real vault content, absolute paths, secrets, API keys, passwords, authentication headers, cookies, session tokens, environment files, or authenticated screenshots in an issue or report. The safe public/demo input is `tests/fixtures/demo_snapshot_v1.json`.

The shipped panel uses local/system fonts and has no panel-originated third-party telemetry, analytics, font, or asset egress. This statement does not cover the host, OSB, Hermes Agent, GitHub, or an operator's configuration.
