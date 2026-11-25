# Changelog

All notable changes. Format based on Keep a Changelog. Versions are unreleased until tagged.

## [Unreleased]

### Changed
- Tier 0 catches far more unseen phrasing (tuned on a new hand-written tuning set): spoken and
  weekday dates, quarters and fiscal years, spoken amounts/percentages/counts with rate tails
  and ranges, dictated identifiers and passwords, dictated emails, and organisation/place
  names with generic words ("Family Health", "and Sons"). User-set recall 68.6% -> 78.3%.
- Fiscal quarters, bare years after a cue, codes/passwords and last-four digits now count as
  sensitive (D-055).

### Added
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
