# Changelog

All notable changes. Format based on Keep a Changelog. Versions are unreleased until tagged.

## [Unreleased]

### Added
- `offrecord review --draft-labels -o FILE` writes a transcript as inline markup pre-filled
  with offrecord's redactions, for labelling your own transcripts privately
  (`docs/private-eval.md`).
- A sealed evaluation set (80 synthetic transcripts in new phrasing). `offrecord eval --set
  sealed` refuses to run without `--final-check`, and CI fails if the set changes.
- `CONTRIBUTING.md`, `SECURITY.md`, `AGENTS.md` and `docs/architecture.md`.
- CI: a test matrix on Python 3.10 to 3.14, a Tier 0 job that installs only the built wheel,
  type checking with mypy, a check of what the sdist and wheel contain, CodeQL, dependency
  review, Dependabot and a weekly run against the newest dependencies.
- Apache-2.0 licence (LICENSE, package metadata).
- `offrecord paste` / `offrecord unpaste`: redact the clipboard in place, then restore the
  LLM's copied answer with the most recent mapping (macOS, Wayland, X11).
- `--prompt "..."` on `redact` and `paste`: wraps the output in a ready-to-paste LLM prompt
  that keeps placeholders intact; the instruction itself is redacted with the same mapping.
- `offrecord init` (guided deny-list setup) and `offrecord denylist add|import|count`; a
  default config at `~/.config/offrecord/config.yaml` is used automatically.
- Per-category run summary ("redacted 42 item(s) (12 person, 5 client, ...); 3 flagged").
- `site/`: a self-contained one-page site for GitHub Pages.
- `tuning-hw` evaluation set (80 agent-written synthetic transcripts in inline markup) and
  `scripts/misses.py --hw/--neg`.
- Optional Tier 1: GLiNER + BERT-NER ensemble (ONNX Runtime, no PyTorch) on gated sentences,
  document-wide propagation of model-found names, MiniLM embeddings, `--compress aggressive`.
- Optional Tier 2: Phi-4-mini (Qwen3 1.7B fallback) via llama.cpp, asked only about candidate
  sentences for indirect identifiers ("the only pharmacy we serve in Alaska") that the Tier 0
  rules missed; grammar-constrained JSON located by code. New INDIRECT category: Tier 0 rules
  catch the common shapes; people are redacted, organisations flagged.
- `offrecord eval` reports 95% bootstrap intervals for PII recall and the PII-clean rate.
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

### Changed
- offrecord now protects personal data by default (`protect: pii`): names, contact details,
  identifiers, addresses and places, day-level dates and sentences about someone's health,
  family, legal or employment situation are redacted. Business context (amounts, percentages,
  quantities, organisations found automatically, business topics such as layoffs or pricing,
  quarters and fiscal years) is left readable and flagged for review. `protect: confidential`
  redacts it too; `offrecord init` asks. Deny-list entries are always redacted.
- `eval` leads with PII recall and the share of transcripts with no personal data left.
- New Tier 0 rules for personal data: last digits of cards and accounts, bare numbers of four
  or more digits, places listed after a place, spoken IP addresses and emails, multi-word
  passwords and read-back codes, URL paths, unit numbers and city/state tails of addresses,
  and given names that are ordinary words when they sit next to person words.
- Spoken URLs: "find it online at acme dot test slash help" is a URL, not an email, so its
  path is redacted too; path words joined by "dash", domain names spoken as two words and
  newer endings such as ".health" are handled. URL recall on the evaluation sets is now 100%.
- Place names at Tier 0: a capitalised name redacted in part is redacted whole ("Port St.
  Lucie", "Dana Point"); more words that end place names ("Gold Coast", "the Panhandle");
  places in text without capitals ("orange county", "salt lake city").
- Indirect identifiers at Tier 0: descriptions that single out one person ("the patient in bed
  twelve", "my husband") are redacted; ones that single out an organisation are flagged.
- A health, legal or family sentence is redacted only when it is about someone (a pronoun, a
  person word or a name, or an event that only happens to a person such as a divorce);
  otherwise it is business context and flagged, so "our cancer franchise grew" stays readable.
- `topics.mode` values are now redact, mixed (the default), flag and off; `default` is still
  accepted as the old name of mixed.
- Tier 0 finds more business context: rates said without "percent" ("attrition is nine", "up
  about four", "a fifth of your book"), places by shape and cue ("Route 9", "High Street", "in
  Fort Worth", "our Leeds store"), and sensitive topics said in plain words (lawyers, rumours,
  "keep this close", hours being cut, an owner looking to sell).
- Tier 1 also redacts sensitive sentences that use none of the topic keywords, by comparing
  each sentence with example sentences using the MiniLM model it already ships. It follows
  `topics.mode`.
- `eval` reports the clean-transcript rate (share of transcripts with nothing leaked), the
  share with no high-severity leak, leaks per transcript, and how much of the text is redacted.
  The run summary also says how much of the text was redacted.
- The original held-out set is now called `validation` (`--set heldout` still works). It was
  looked at during development, so it no longer counts as blind.
- Bad UTF-8 on the clipboard is now reported like bad UTF-8 in a file (exit code 3), and
  clipboard text gets the same line-ending normalisation and line-length limit.
- Token counts from tiktoken are labelled as such instead of "approx".
- Windows is not supported (the package metadata now says macOS and Linux).
- Tier 0 catches far more unseen phrasing (tuned on a new tuning set written in inline
  markup): spoken and weekday dates, quarters and fiscal years, spoken amounts, percentages
  and counts with rate tails and ranges, dictated identifiers and passwords, dictated emails,
  and organisation and place names with generic words ("Family Health", "and Sons").
  User-set recall 68.6% -> 79.0% (Tier 0).
- Fiscal quarters, bare years after a cue, codes/passwords and last-four digits now count as
  sensitive.

### Fixed
- "$300 baseline" no longer redacts part of "baseline"; negative token savings read "more".
- The embedding model used by `--compress aggressive` is now checksum-verified before loading,
  like the other models.
- A long chain of spoken numbers can no longer make the quantity rules take minutes.
- The key written by `offrecord keygen` is now used when no `--key-file`, `OFFRECORD_KEY_FILE`
  or `OFFRECORD_PASSPHRASE` is given, as the README's round trip assumed; before, it was
  written but never read. offrecord says on stderr when it uses it.
- Unknown config keys, at the top level, in `topics`, `detect` and `models` and in deny-list
  entries, are now an error instead of being ignored: `protekt: confidential` used to run
  with business context left readable. A near miss of a known key gets a suggestion.
