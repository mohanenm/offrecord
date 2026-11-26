# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: on the repository page, open the
Security tab and choose "Report a vulnerability". Don't open a public issue, and don't include
real transcripts or other sensitive data in the report; a synthetic example that reproduces the
problem is enough.

You can expect an acknowledgement within a week. Fixes for confirmed issues are released as
soon as they are ready, and the changelog credits the reporter unless they prefer otherwise.

## What counts

offrecord's job is to keep sensitive text from leaving your machine. Reports of any of these
are especially welcome:

- a run-time code path that opens a network connection or starts a process outside
  `offrecord setup` and the clipboard helpers;
- transcript text, deny-list entries or mapping contents appearing in errors, logs or files
  other than the intended outputs;
- weaknesses in how the mapping file is encrypted, or files written with permissions wider
  than owner-only;
- a way for crafted input to crash the tool, hang it, or make it skip text without scanning it.

A sensitive value that offrecord fails to redact is a quality bug, not a vulnerability, unless
it follows from one of the above. Please open a normal issue for those, with synthetic text.

## Supported versions

Only the latest release receives security fixes.

The threat model and the defence for each risk are in `docs/security.md`.
