# Changelog

All notable changes. Format based on Keep a Changelog. Versions are unreleased until tagged.

## [Unreleased]

### Fixed
- "$300 baseline" no longer redacts part of "baseline"; negative token savings read "more".

### Changed
- Tier 0 catches far more unseen phrasing (tuned on a new hand-written tuning set): spoken and
  weekday dates, quarters and fiscal years, spoken amounts/percentages/counts with rate tails
  and ranges, dictated identifiers and passwords, dictated emails, and organisation/place
  names with generic words ("Family Health", "and Sons"). User-set recall 68.6% -> 79.0% (Tier 0).
- Fiscal quarters, bare years after a cue, codes/passwords and last-four digits now count as
  sensitive.

### Added
- Apache-2.0 licence (LICENSE, package metadata).
- `offrecord paste` / `offrecord unpaste`: redact the clipboard in place, then restore the
  LLM's copied answer with the most recent mapping (macOS, Wayland, X11).
- `--prompt "..."` on `redact` and `paste`: wraps the output in a ready-to-paste LLM prompt
  that keeps placeholders intact; the instruction itself is redacted with the same mapping.
- `offrecord init` (guided deny-list setup) and `offrecord denylist add|import|count`; a
  default config at `~/.config/offrecord/config.yaml` is used automatically.
- Per-category run summary ("redacted 42 item(s) (12 person, 5 client, ...); 3 flagged").
- `site/`: a self-contained one-page site for GitHub Pages.
- `tuning-hw` evaluation set (20 hand-written synthetic transcripts) and `scripts/misses.py
  --hw/--neg`.
- Optional Tier 1: GLiNER + BERT-NER ensemble (ONNX Runtime, no PyTorch) on gated sentences,
  document-wide propagation of model-found names, MiniLM embeddings, `--compress aggressive`.
- Optional Tier 2: Phi-4-mini (Qwen3 1.7B fallback) via llama.cpp returning grammar-constrained
  JSON labels located by code; new INDIRECT category for indirect identifiers.
- `setup models` (pinned revisions, SHA-256-verified, `--from` for air-gapped installs),
  load-time verification, `doctor` model checks, `redact --tier 0|1|2|auto`, `[ner]`/`[llm]` extras.
- Input adapters: plain text, WebVTT, SRT, Whisper-style/generic JSON; nothing in the input is
  dropped (notes, cue ids/settings, unknown JSON values and keys are scanned).
- Normalisation with offset maps: spoken digits, "oh", cardinals, spelled letters, NATO, spoken
  emails/URLs, invisible characters, Unicode look-alikes.
- Tier 0 redaction: patterns (email, phone, SSN, card+Luhn, IP, URL, IBAN, accounts, addresses,
  postcodes, dates and more), deny-list with fuzzy/phonetic matching and per-entry options,
  business context (amounts, percentages, quantities, topic sentences, codenames), name
  heuristics; split-PII detection across interruptions; consistent short placeholders.
- `redact` with compact or original-format output, review-flag counts and token estimates;
  `--mapping-scope project` / `--mapping-in` to share placeholders across transcripts.
- Encrypted mapping (Fernet; scrypt N=2^17 or key file) in a private per-user store; `restore`
  tolerant of LLM-mangled placeholders, with wrong-mapping detection; `keygen`.
- Compression: `light` (default) and `medium`; never drops or alters a placeholder.
- Opt-in encrypted audit log (`--audit`) and `audit` command; `review` side-by-side view with
  missed-label highlighting and deny-list suggestions.
- Evaluation: seeded synthetic generator, tuning set, frozen independently written held-out set,
  user-set folder, `eval` with per-category recall/precision, `--baseline`, `--extras`
  (placeholder consistency, restore accuracy, token reduction).
- `doctor`, `setup tokenizer` (the only networked command), run-time network and child-process
  guard, content-free error reporting, owner-only atomic outputs, input limits.
