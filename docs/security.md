# Security and privacy: threat model

offrecord processes transcripts that are, by assumption, sensitive. This document lists what we
protect, what can go wrong, and the defence for each. It is re-read against the code in the
hardening phase (Phase 7). Items marked _(planned)_ are not built yet; see `docs/DECISIONS.md`.

## What leaves the machine

**Nothing, at run time.** No telemetry, no update checks, no model downloads during a run.
The only command that may use the network is `offrecord setup`, which the user runs explicitly to
fetch optional model files and the tokenizer encoding (pinned versions, verified checksums).
Every other command installs a network guard (see below) before doing any work.

## Assets

| Asset | Why it is sensitive |
| --- | --- |
| Input transcripts | Raw PII, client names, business context |
| Redacted output | Should be safe, but no redactor is perfect: misses are possible |
| Mapping file | Placeholder → original value. Reverses the redaction. Highest value asset |
| Audit log | Contains the original text of every removed span |
| Deny-list config | Lists clients, products, people and codenames: sensitive on its own |
| Temp files | Intermediate copies of any of the above |
| Model files (Tier 1/2) | Integrity matters: a swapped model could change what is redacted |
| Keys / passphrases | Unlock the mapping and audit log |

## Risks and defences

### R1. Shared machine (other local users, backups, sync folders)
- All files offrecord creates (outputs, mapping, audit log, temp files) are created owner-only
  (`0600`, directories `0700`) regardless of umask. _(Decision D-009.)_
- The mapping and audit log are always encrypted at rest (Fernet; key derived by scrypt
  N=2^17 r=8 p=1 from a passphrase, or by HKDF from a 32-byte key file). Design approved:
  `docs/proposals/0002-encryption.md`.
- The tool refuses to write a mapping into the key file's directory, and refuses key files
  readable by other users. Passphrases are never command-line arguments; they come from a
  prompt or `OFFRECORD_PASSPHRASE`. (Setting that variable in a shared shell profile exposes it
  to anything running as you: prefer a key file or the prompt.)
- Encrypted mappings and audit logs go to a private per-user store
  (`~/.local/share/offrecord/mappings`, 0700) rather than next to the output.
- `redact` refuses to run without a passphrase/key unless `--no-mapping` is given.

### R2. Careless logging and error output
- No raw text in logs, exceptions, tracebacks or debug output. Errors carry a code, a position
  (field, turn, line, offset) and a category, never content.
- The CLI catches every exception at the top level. For our own errors it prints the code and
  position. For unexpected exceptions it prints only the exception *type* and the code location,
  never the exception message (third-party messages, e.g. decode errors, can embed input bytes).
- `--debug` prints stack frames (file, line, function) but never exception messages or locals.
- `sys.excepthook`, `threading.excepthook`, `sys.unraisablehook` and `warnings.showwarning`
  are replaced with content-free versions; logging's last-resort handler is removed and the
  root logger gets a `NullHandler`, so library log calls cannot print data.
- argparse usage errors are reduced to option names and the kind of error (file names often
  carry client names).
- There is no logging framework writing to disk. There is no telemetry.
- Canary tests: a unique marker string in input must never appear in stderr, for every command
  and every error path. _(partially built; extended per command)_

### R3. A leaky dependency
- Tier 0 runtime dependencies are minimal, pre-approved and pinned; dev tools are pinned via a
  lock file. `pip-audit` runs in CI.
- **Runtime network guard**: every command except `setup` installs it before work starts.
  A `sys.addaudithook` hook (fires from C, so direct `_socket` use is covered too) blocks
  creating INET/INET6 sockets, connect/bind/sendto on non-Unix sockets, every kind of name or
  address resolution, and starting child processes (which would run without the guard).
  Python-level patches on `socket` give clear errors on the common paths. Every attempt is
  counted and makes the command exit non-zero (exit code 7) even if a library swallowed the
  exception. Offline switches for ML libraries (`HF_HUB_OFFLINE` etc.) are set. Unix-domain
  sockets remain allowed (a local relay could forward traffic; out of scope).
- A test runs every CLI command in a subprocess with a second, independent guard installed via
  `sitecustomize` (so import-time attempts are caught too) and fails on any attempt.
- Limitation: a native extension that calls the OS socket API itself bypasses audit hooks.
  Tier 0 has no native networking deps. For Tier 1/2, document an OS-level option (firewall /
  network namespace) for high-assurance use.

### R4. Mangled restore (LLM output with distorted placeholders)
- Restore is tolerant of common mangling (`<c1>`, `<C 1>`, `<C1>'s`, `&lt;C1&gt;`, upper-case
  bare `C1`) but never restores anything ambiguous: bare tokens that also occur naturally in the
  redacted output, and unknown placeholders, are left as they are and reported (D-037).
- Source text that already looks like a placeholder is escaped one-to-one (`<C1>` -> `<\C1>`,
  `<\C1>` -> `<\\C1>`), and restore removes exactly one backslash.

### R5. Wrong mapping file
- Each mapping has a random ID (128 bits, no content-derived bits) and a format version.
  Redacted output carries the ID (compact/txt header line, VTT NOTE, zero-length first SRT cue,
  `_offrecord_mapping` JSON key). Restore fails on an ID mismatch or on a `mapping=none` header.
  Header-less text (an LLM answer) needs `--assume-mapping`; then every placeholder must exist
  in the mapping or a warning is printed (`--strict` turns warnings into failure).
- Crafted mapping headers cannot demand expensive key derivation: only the parameters this
  version writes are accepted.
- The ID, format version and file kind are repeated inside the encrypted payload, so a tampered
  or swapped plaintext header is detected on decryption.

### R6. Sensitive deny-list config
- Documented as sensitive. Never echoed in output, logs or errors (config errors name the entry
  index and field only; tested with canary values). Reason codes refer to deny-list entries by
  index (`deny:fuzzy:#12`).
- `.gitignore` excludes `*.local.yaml`; the repo ships only a synthetic example config.

### R6b. Adapters must not drop or hide text
- Every piece of text in an input ends up in a scanned field: turn text, speaker labels,
  metadata (headers, NOTE blocks, unparsed blocks, unknown JSON values and keys) or free-text
  extras that are re-emitted (VTT cue ids and settings, SRT text after the timing).
- Only known caption tags are stripped; other `<...>` text stays. Second and later voice names in
  a VTT cue are kept inline. Duplicate JSON keys are kept (renamed).
- JSON word/token arrays and number-only arrays (also nested) are dropped on render, because
  they can decode back to text the detectors never saw.

### R7. Temp files
- Written only inside a private temp directory (`0700`) created per run, removed on success,
  error, `KeyboardInterrupt` and `SIGTERM`. Outputs are written atomically (temp file in the
  destination directory, `fsync`, rename), so a crash never leaves a half-written output.
- Existing files are never overwritten without `--force`; the no-overwrite path uses a
  link-based create that is race-free (filesystems without hard links fall back to a
  check-then-rename, which has a small race).
- Private directories are created 0700 including missing parents; a symlinked or foreign-owned
  directory is refused. SIGTERM and SIGHUP are turned into a clean shutdown.

### R8. Hostile or malformed input
- Caps on file size, line length, turn count and JSON nesting depth, with clear errors that
  name the limit and position. _(limits built; enforced per adapter in Phase 1)_
- Strict UTF-8 decoding by default; a decode failure reports the byte offset only.
- Inputs are opened non-blocking and checked with `fstat` on the open descriptor (no
  check-then-open race; a FIFO cannot hang a run). JSON with lone surrogates is rejected.
- Parsers are linear-time on hostile input: regexes only see short line prefixes, payload
  sizes are checked before any regex runs, and normalisation passes skip failed runs.
  Timing regression tests cover the inputs found in review.
- Parsers never evaluate input: `json` (stdlib) and hand-written VTT/SRT parsers; YAML config
  only via `yaml.safe_load`.

### R9. Tampered or swapped model files (Tier 1/2)
- Models come only from `offrecord setup models` (Hugging Face, revision pinned by commit hash)
  or `setup models --from DIR` (air-gapped copy). Every file is size- and SHA-256-verified while
  it streams to a temp file, then atomically renamed into the private model store (0700/0600).
  `doctor` re-verifies the hashes. At run time only presence and size are checked (hashing 3 GB
  on every run is too slow); run `doctor` after copying models around.
- Model output can only *add* redactions: model spans go through the same union merger. Tier 2
  output is JSON labels whose text must be found verbatim in the sentence, or it is discarded.
- Known advisory: `diskcache` (a dependency of the [llm] extra) unpickles its cache files; offrecord
  never enables llama-cpp-python's disk cache, so it is not reached (D-050).
- No model package is imported at Tier 0; the network guard and child-process block stay on
  for Tier 1/2; llama.cpp native logs are silenced before the model loads.

### R10. Residual in-memory exposure
- Python cannot reliably zero strings; decrypted mappings and transcript text live in process
  memory until garbage-collected and could reach swap or a core dump. Documented limitation;
  the tool does not write core dumps itself. High-assurance users should use encrypted swap.

### R11. Outputs that contain original text by design
- `review` prints original text side by side with the redacted text, and `audit` prints the
  decrypted audit log. Both write to stdout (the user's own terminal) or an owner-only file with
  `-o`. Never pipe them into anything that leaves the machine.
- `restore` output is original text again: written owner-only with `-o`.

### R12. The one networked command
- `offrecord setup tokenizer` downloads a token-encoding file from a fixed HTTPS URL, verifies
  its SHA-256 before writing it, and sends nothing else. `--from FILE` installs from a local
  copy (air-gapped). `setup models` (Tier 1/2) is not built and needs approval (Phase 8).

## Known limitations

- No redactor catches everything. Over-redaction is preferred, but misses are possible:
  review the flagged spans before sharing output.
- Synthetic evaluation data cannot capture every real-world phrasing; see the README Status.
