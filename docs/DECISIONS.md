# Decisions

Each entry: the choice, alternatives, why, and how hard it is to reverse.
Big items that need sign-off are written up in `docs/proposals/` and linked here once decided.

---

### D-001 Package and CLI name: `offrecord` (2026-11-24)
- Alternatives: `transcript-redactor`, `redactly`.
- Why: matches the repo name; short to type.
- Reversibility: easy before first release.

### D-002 Dev toolchain: Homebrew Python 3.12, plus 3.10 for floor tests (2026-11-24)
- Context: the machine only had system Python 3.9.6; the spec needs 3.10+.
- Choice: `brew install python@3.12` (main venv `.venv`) and `python@3.10` (scratch venv to run
  the test suite on the floor version). Toolchain only, not project dependencies.
- Alternatives: pyenv, uv (not installed; would add tooling).
- Reversibility: easy.

### D-003 Git workflow: `main` plus one branch per phase, merged locally (2026-11-24)
- The repo had an unborn `master`; the stated main branch is `main`. Created `main` with an
  initial commit, then `phase-1-foundation`. At the end of each phase the branch is merged into
  `main` locally (`--no-ff`). Nothing is pushed (pushing needs user approval).
- Reversibility: easy.

### D-004 Packaging: setuptools, `src/` layout, no license field yet (2026-11-24)
- Alternatives: hatchling, flit (extra build deps).
- Why: setuptools is the most common and already present in every pip; `src/` layout stops tests
  importing the working tree by accident. License left out pending Q-003.
- Reversibility: easy.

### D-005 CLI framework: stdlib `argparse` (2026-11-24)
- Alternatives: click, typer (new dependencies, not pre-approved).
- Why: zero dependencies for Tier 0; enough for our subcommands.
- Reversibility: medium (rewrite of `cli.py` only).

### D-006 Run-time network guard in every command except `setup` (2026-11-24)
- Choice: patch the socket layer (connect, connect_ex, sendto, sendmsg, create_connection, DNS
  resolution) before any work. INET/INET6 blocked, Unix sockets allowed. Every attempt is counted;
  if the count is non-zero at exit the run fails with exit code 7, even if a library swallowed
  the exception. Error messages never include the address.
- Alternatives: rely on tests only; OS-level sandboxing (not portable).
- Why: defence in depth against a leaky dependency; makes the offline promise checkable.
- Limitation: native code bypasses Python-level patches (documented in security.md R3).
- Reversibility: easy.

### D-007 No-network test design (2026-11-24)
- All in-process tests run with the guard installed (session fixture). In addition, each CLI
  command runs in a subprocess with an **independent** tripwire loaded via `sitecustomize`
  (does not import offrecord, so it also catches import-time attempts and a broken guard).
  A meta-test checks the tripwire itself fires.
- Reversibility: easy.

### D-008 Content-free error reporting (2026-11-24)
- Our errors: code + position/size/category, built from fixed templates. Unexpected exceptions:
  type name + code location only, never the message (decode/JSON/regex errors can embed input).
  `--debug` adds frames (file:line function), never messages or locals. `sys.excepthook` and
  `warnings.showwarning` replaced with content-free versions.
- Lint: ruff `T20` (no `print`) is on everywhere except `cli.py` and `safe_errors.py`, so a stray
  debug print of transcript text fails CI.
- Reversibility: easy.

### D-009 Every file offrecord writes is owner-only (0600; dirs 0700) (2026-11-24)
- Includes the redacted output, not only the mapping and audit log, because redaction can miss
  things. Users can `chmod` deliberately.
- Alternative: respect umask for outputs. Rejected: recall-first spirit applied to permissions.
- Reversibility: easy.

### D-010 Atomic, no-clobber writes via hard link (2026-11-24)
- Temp file in the destination directory, `fsync`, then `os.link` (fails if the destination
  exists, race-free) or `os.replace` with `--force`. Falls back to check-then-rename on
  filesystems without hard links.
- Reversibility: easy.

### D-011 Dependency locks per Python version; Hypothesis pinned to 6.168.5 (2026-11-24)
- Hypothesis 6.170 dropped Python 3.10, and some transitive dev deps (e.g. `filelock` 4.2.1+,
  which fixes CVE-2026-112941) need 3.11+. So:
  - `requirements/dev.lock` (3.11+): audited by `pip-audit` in CI, currently clean.
  - `requirements/dev-py310.lock`: used only to run tests on the 3.10 floor. Contains
    `filelock==4.1.0` (the CVE above). Dev-only, pulled in by pip-audit's cache layer, never
    installed at run time.
- Runtime deps will be pinned in `pyproject.toml` and must support 3.10.
- Reversibility: easy.

### D-012 Input decoding: strict UTF-8 (BOM tolerated), line endings normalised first (2026-11-24)
- CRLF/CR → LF happens before any offsets are taken, so all offsets (and the audit log) refer to
  the decoded, LF-normalised text. Decode failures report the byte offset only.
- Alternatives: charset detection (new dep; guessing), `errors="replace"` (silently corrupts
  text, could break detection).
- Reversibility: medium; an `--encoding` flag can be added without changing the model.

### D-013 Default input limits (2026-11-24)
- 50 MiB file, 200,000 chars per line, 200,000 turns, 1,000,000 chars per turn, JSON depth 64,
  1,000 metadata fields. Configurable. Generous enough for multi-hour transcripts.
- Reversibility: easy.

### D-014 Audit log: off by default; when on, encrypted like the mapping (2026-11-24)
- Spec allows "off by default or encrypted"; we do both: `--audit` opt-in, always encrypted with
  the mapping's scheme, stored in the private store, never next to the output unencrypted.
  Encryption details depend on proposal 0002.
- Reversibility: easy.

### D-015 Exit codes (2026-11-24)
- 0 ok, 1 internal, 2 usage, 3 input, 4 output, 5 mapping, 6 environment, 7 network blocked,
  64 not implemented, 130 interrupted. Defined in `offrecord/errors.py`.
- Reversibility: easy before first release.

### D-016 CI: GitHub Actions, SHA-pinned actions (2026-11-24)
- Matrix 3.10 and 3.12; ruff lint + format check, pytest, pip-audit (3.12 lock).
  `persist-credentials: false`, `permissions: contents: read`. No remote exists yet, so CI has
  not run; see Q-004.
- Reversibility: easy.

### D-017 Task list lives in `docs/PROGRESS.md` (2026-11-24)
- Survives context resets and is visible in review. Reversibility: trivial.

### D-018 Phase 1 split around the interface sign-off (2026-11-24)
- Built everything in Phase 1 that does not depend on the core interfaces (docs, threat model,
  CLI skeleton, doctor, network guard, safe I/O, limits, CI), then stopped for approval of
  proposal 0001. Proposal 0002 (encryption) was asked at the same time so the user can answer
  both in one go.

### D-019 Low-RAM simulation via `OFFRECORD_SIMULATE_RAM_MB` (2026-11-24)
- Makes `doctor` and (later) tier auto-detection testable without a Pi. Phase 7 adds a real
  memory cap check (`resource.setrlimit`) for the Tier 0 run.
- Reversibility: easy.

### D-020 Core interfaces: proposal 0001 approved by the user (2026-11-24)
- `docs/proposals/0001-core-interfaces.md` is the contract for the turn model, fields/views,
  offset maps, span format, detector and adapter interfaces, category registry and plug-in
  loading. Changes to it are big items again.
- Reversibility: hard once detectors and adapters are built on it.

### D-021 Encryption and key handling: proposal 0002 approved by the user (2026-11-24)
- `docs/proposals/0002-encryption.md`: Fernet; scrypt N=2^17 r=8 p=1 with per-file salt; HKDF
  for key files; passphrase from prompt or env var only; private per-user store; refuse to run
  without a key unless `--no-mapping`; audit log opt-in and encrypted the same way.
- Reversibility: easy for parameters (stored in the header), medium for the file format.

### D-022 `Document.render_state`: adapter-private render structure (additive to 0001) (2026-11-24)
- The JSON adapter needs the original skeleton to re-render the format. Added an optional
  `render_state` field to `Document`. It must hold no scannable text: every text value lives in
  turns or metadata (the skeleton only holds references and known schema keys).
- Additive and adapter-private; no change to spans, fields or detectors, so not treated as a
  re-opening of the approved interface.
- Reversibility: easy.

### D-023 JSON adapter privacy rules (2026-11-24)
- Every string and number becomes turn text, a speaker, or a metadata field (scanned). Numbers
  are kept as raw text (no float rounding; no huge-int parsing cost).
- Object keys outside a small known schema are scanned as metadata too (a key can be a name).
- Metadata field names are opaque (`json:N`), never JSON paths, because paths contain keys.
- Word-level/token arrays (`words`, `tokens`, `word_segments`, ...) and integer arrays longer
  than 8 are dropped on render: they duplicate the text in a form detectors cannot see (token
  ids decode back to text). Whisper's top-level `text` is regenerated from turns.
- Alternatives: scan and redact word arrays word by word (splits PII across fields, so misses);
  keep keys verbatim (leaks names used as keys).
- Reversibility: easy.

### D-024 Speaker-label heuristics; rejected labels stay in the text (2026-11-24)
- `Name: text` is a speaker turn if the label is generic ("Speaker 1"), repeats, or the document
  is mostly labelled (at least half of lines/cues), and the label has <= 4 words with no function
  words ("Here is the thing:" is a sentence, not a speaker).
- Recall is unaffected either way: a rejected label stays in the turn text, which is scanned.
- Supports `[ts] Name: text`, `Name (ts): text`, `Name  ts` header lines (Otter style), VTT
  `<v Name>` voice tags and `Name:` payloads, SRT `NAME:` and `[Name]`.
- Reversibility: easy.

### D-025 Spoken-form normalisation is conservative and lives in a separate view (2026-11-24)
- The `norm` view converts digit runs (>= 3 digits, or >= 2 units after a trigger word such as
  "number"/"card"), cardinals only with scale words or money/quantity words after them, spelled
  letters (>= 3 letters, or an "X as in Word" unit), NATO runs of >= 3, and spoken emails/URLs
  (only when a TLD follows a spoken "dot").
- Over-normalisation (e.g. "met at acme dot com" -> "met@acme.com") is accepted: it only causes
  over-redaction, and detectors also scan the verbatim `orig` view.
- Unicode confusables (e.g. Cyrillic letters inside a Latin word) are NOT folded yet; noted for
  the Phase 7 hardening pass.
- Reversibility: easy (each pass is independent).

### D-026 Placeholders in VTT output are entity-escaped (`&lt;C1&gt;`) (2026-11-24)
- A literal `<C1>` in a VTT cue is a tag and would be stripped by players and parsers, losing
  the placeholder. Restore (Phase 4) must accept the escaped form.
- Reversibility: easy.

### D-027 Adapters never drop text (2026-11-24)
- Blocks that do not parse (VTT/SRT), NOTE/STYLE/REGION blocks, VTT header lines, cue ids and
  SRT lines before a timing line all end up in metadata or turn text, so they are scanned. They
  are re-emitted on render (VTT as NOTE blocks, SRT as zero-time cues at the end).
- Reversibility: easy.

### D-028 Python sources must be ASCII (2026-11-24)
- A test fails on any non-ASCII character in `src/` or `tests/`. Invisible characters (e.g. the
  zero-width characters the normaliser removes) must be written as `\u` escapes. Found after a
  tooling step silently turned escapes into literal invisible characters.
- Reversibility: trivial.

### D-029 Fixes from the Phase 1 outside review (2026-11-24)
- An independent reviewer (separate subagent, given the code, not my conclusions) found 11
  confirmed issues; all are fixed with regression tests (`tests/test_review_regressions.py`,
  `tests/test_netguard.py`, `tests/test_errors.py`, `tests/test_safeio.py`). Highlights:
  unscanned VTT cue settings / SRT timing tails (now scanned extras), quadratic regexes and
  loops (now linear, with timing tests), audit-hook network guard that also blocks child
  processes, extra stderr paths (threads, unraisable, logging), per-cluster NFKC, removal of all
  format characters, confusable folding, ASCII-only timestamps, nested token arrays.
- Not fixed, documented instead: Unix-socket relays; hard-link-less filesystems race; native
  extensions bypassing audit hooks.

### D-030 Held-out set is frozen with a checksum manifest (2026-11-24)
- Held-out templates and pools were written by a separate subagent that read only
  `docs/eval-format.md` (no detector code existed yet, and it was told not to open `src/`,
  `tests/` or the tuning templates). Generated once with seed 987654, n=56, committed with
  `MANIFEST.sha256`; a test fails if any file changes, and the generator refuses to regenerate
  it without an explicit flag.
- Tuning set: my own templates and pools, seed 1234, n=72.
- Reversibility: by design hard; any regeneration must be logged in eval/CORRECTIONS.md.

### D-031 Scoring semantics after the Phase 2 review (2026-11-24)
- Recall: strict (every character of a label redacted). Speaker labels count once per distinct
  speaker per transcript, as category SPEAKER, caught only if every occurrence is redacted.
- Precision: character-based (redacted characters inside any gold label / all redacted
  characters), overall and per predicted category. NEG hits (ordinary uses of common-word
  brands) are counted separately.
- Systems must return spans in `orig` coordinates; anything else is an error.
- Held-out freeze: the manifest's hash is pinned in code (`sets.HELDOUT_MANIFEST_SHA256`); the
  `eval` command fails (not warns) on a mismatch, also via `--dir`; a test regenerates both sets
  and compares them with the committed files.
- Reversibility: easy (scoring only), but changes must be logged in eval/CORRECTIONS.md.

### D-032 Synthetic value choices in the generator (2026-11-24)
- Phones: fictional 555-01xx lines. Cards: public test numbers only. SSNs: area 9xx (never
  issued) with a middle group 01-49 (not an ITIN range). IPs: documentation ranges and 10.x.
  Domains: `.example`, `.test`, `.invalid`.
- Deny-list per set: org names and aliases (never ASR variants) for about three quarters of
  orgs, and a fixed list of 60 known people; everyone else is "unknown", reported separately.
- Reversibility: easy.

### D-033 Tier 0 detector design (2026-11-24)
- Detectors: patterns (regex + validators), deny-list (rapidfuzz ratio on spaced and joined
  forms, Metaphone, per-entry match mode/score/context; common-word entries need their own
  context words or a generic brand cue within 6 tokens), business (amounts and percentages
  always; quantities with unit nouns or near trigger terms; topic sentences; "Project X"
  codenames), people (cue phrases, SCOWL proper names, unknown words as NAME, expansion over
  place/org words such as "Port", "Heights", "Freight").
- Views: every detector runs on `orig` and `norm`; all but people also run on a
  speaker-joined view to catch PII split across short interjections.
- Merger: union of overlapping spans (no character dropped), adjacent name words joined into
  one placeholder, number fragments joined across self-corrections ("... sorry, ...").
- Placeholders: per (prefix, entity key), aliases seeded from the deny-list and speaker labels.
- Unknown dictionary-less words are redacted by default (`detect.unknown_words: redact`), as
  NAME with confidence 0.4, so they appear in review flags. Rationale: in ASR output most
  out-of-dictionary tokens are names/brands/places; recall first.
- Common-word list and proper names come from SCOWL (permissive licence, bundled, ~120 KB).
  The US Census/SSA name lists were considered but their download was blocked from the dev
  machine; SCOWL's proper-name list (~5k names) is used instead.
- Reversibility: medium (each detector is independent; thresholds in config).

### D-034 Topic terms: strong terms redact the sentence, weak terms flag it (2026-11-24)
- Strong (acquisition, layoffs, litigation, settlement, restructuring, severance, price
  increase, "between us", ...) -> whole sentence replaced by `<TOPICn>`.
- Weak (pricing, renewal, discount, churn, budget, contract, deal, ...) -> review flag only,
  because redacting every pricing/renewal sentence would blank out most of a sales call.
- The spec lists "pricing" and "renewal" among topic terms; whether they should redact by
  default is a recall/usefulness trade-off for the user: Q-005.
- Reversibility: easy (`topics.mode: redact` in config).

### D-035 Fixes from the Phase 3 outside review (2026-11-24)
- Confirmed and fixed: common-word names after titles/cues ("Dr Smith", "Mark Brown"), generic
  speaker pattern swallowing names ("Agent Bob"), triggers in an earlier turn, corrected
  digits after "sorry", addresses/postcodes/IBAN/ID formats, longer interruptions mid-number,
  wrong-entity placeholder collisions, written vs spoken email placeholders, loose config
  typing, three quadratic scans, plurals of common-word entries, non-one-to-one escaping,
  JSON key collisions, VTT note placement, input names reused for `--out-dir` outputs.
- Regression tests: `tests/test_review3_regressions.py`.
- Recall after fixes: tuning 97.7%, held-out 90.6% (before: 97.6% / 89.5%).
- The proper-name list now includes SCOWL's capitalised "upper" lists (common given names such
  as Mark, Grace, John), minus months, weekdays and nationalities.

### D-036 Restore returns each entity's first surface form (2026-11-24)
- A placeholder restores to the first original form seen for that entity (e.g. "Quillfeather
  Analytics" even where the text said "QFA"). Exact per-occurrence restore is fragile once
  compression drops or merges turns. Measured restore accuracy (turn text identical,
  case/space-insensitive): tuning 93.2%, held-out 94.9%.
- Alternatives: occurrence-order restore for un-compressed headed transcripts (possible later).
- Reversibility: easy.

### D-037 Bare placeholder tokens restore only when upper-case and not naturally present (2026-11-24)
- Bare `C1` is restored only if written in upper case and if `C1` never occurred naturally in the
  redacted output (recorded in the mapping as `bare_collisions`). Lower-case bare tokens and
  unknown placeholders are left as they are and reported. Restore never guesses.
- Reversibility: easy.

### D-038 Compression defaults to `light`; `medium` is opt-in (2026-11-24)
- `light` only deletes fillers/stutters/backchannel and merges same-speaker turns: it cannot
  change what is redacted. `medium` drops small talk, short contentless turns and
  near-duplicates, which can remove context, so it is opt-in. Both never drop the last
  occurrence of a placeholder (enforced, property-tested).
- Original-format output after compression: VTT/SRT/txt re-render from the compressed turns;
  JSON falls back to a simple array (the original skeleton no longer matches the turns).
- Near-duplicate detection is an in-house MinHash with LSH banding (16x4); `datasketch` was not
  needed.
- Measured token reduction (approx counter, vs the original transcript): light ~10-12%,
  medium ~18%. Compact output also strips timestamps and cue numbers.
- Reversibility: easy.

### D-039 Token counting: approximate by default; tiktoken only from a local cache (2026-11-24)
- tiktoken would download its encoding on first use, which breaks the offline rule. We never
  call it unless the encoding file is already in our cache (`offrecord setup tokenizer`, or
  `--from FILE` for air-gapped machines, both SHA-256 verified). Otherwise an approximate
  counter is used. All counts are labelled estimates.
- The encoding checksums in `tokens.py` are pinned from tiktoken's published values; if they are
  wrong, `setup` refuses to install (fails safe).
- Reversibility: easy.

### D-040 `--out-dir` output names never reuse input names (2026-11-24)
- Outputs are `<NNN>-<mapping id>.<ext>`; stderr prints "input #N -> path". Input file names
  often carry client names.

### D-041 Audit log entries and command (2026-11-24)
- `--audit` writes `<oa1-id>.oraudit` to the private store, encrypted like the mapping. Each
  entry: input number, field, offsets, source lines, category, detector, confidence, reason,
  action (replaced/flagged), placeholder, original text. `offrecord audit FILE` decrypts it.

### D-042 Fixes from the Phases 4-6 outside review (2026-11-24)
- Restore: one substitution pass (no double substitution), ASCII digits only, canonical bare
  tokens only, symmetric escaping of every accepted form, near misses reported, values escaped
  for JSON/VTT input. Bare collisions are computed on the final rendered output (metadata too).
- Wrong mapping: SRT output gets a zero-length first cue and JSON a `_offrecord_mapping` key,
  so they are headed now. Header-less input (LLM answers) needs `--assume-mapping`: it cannot be
  verified, so the user must say so. Alternative considered: per-mapping salted placeholders
  (`<P1x7>`), rejected for token cost and LLM mangling; revisit if needed.
- Crypto: only KDF parameters this version writes are accepted (r=8, p=1, N in [2^14, 2^18]);
  key files opened with O_NOFOLLOW and checked on the descriptor (owner, mode); mapping refused
  anywhere under the key's directory; passphrase env var plus key file is an error; using the
  env passphrase prints a notice.
- Compression: linear token loop for stutters/false starts; negations, decisions, numbers and
  yes/no are never collapsed, deduplicated across or dropped ("Yes." is no longer backchannel);
  cleanup can never create a restorable placeholder form.
- Outputs: mapping written before the output and its destination checked up front (no orphaned
  outputs); `--out-dir` suffix from the detected format; `keygen` no longer chmods an existing
  directory; bounded reads for `setup --from`, `--mapping-in`, review labels; tiktoken cache
  checksum-verified before use.
- Regression tests: `tests/test_review4_regressions.py`.

### D-043 User decisions: weak topics stay flags, word list unchanged, Phase 8 approved (2026-11-24)
- Q-005: pricing, renewal, discount, budget and other weak topic terms remain review flags;
  strong terms redact the sentence (D-034 confirmed as the default).
- Q-006: keep the SCOWL level-35 common-word list (no recall trade-off for precision).
- Phase 8 start approved; model choices and new dependencies still need sign-off.

### D-044 Tier 1: GLiNER + BERT-NER ensemble by default; threshold 0.25 (2026-11-24)
- User asked whether several models should cross-check each other. Measured: in this design a
  model can only add redactions, so the union of models is a pure recall gain at some precision
  and time cost. On the tuning set GLiNER alone 98.4%, BERT-NER alone 98.5%, both 98.7%;
  held-out confirms (93.4% / 92.2% / 93.6%, products +4.5 points). Default `models.ner` is the
  ensemble; GLiNER alone is a config option for speed.
- GLiNER threshold 0.25 chosen on the tuning set (0.25 / 0.3 / 0.4 / 0.5 -> 98.4 / 98.2 / 98.2 /
  97.8% recall; precision 93.2 / 94.0 / 94.6 / 94.8%): recall first.
- GLiNER is run with in-house ONNX span decoding (the `gliner` package pulls in PyTorch). BERT-NER
  sub-word fragments are widened to whole words (it fails on lower-case text; GLiNER does not).
- Models: onnx-community/gliner_small-v2.1 (Apache-2.0, int8), Xenova/bert-base-NER (MIT, int8),
  sentence-transformers/all-MiniLM-L6-v2 (Apache-2.0, int8 per CPU architecture). Revisions
  pinned by commit hash, files by SHA-256.
- Reversibility: easy (config).

### D-045 Gating for model tiers (2026-11-24)
- A sentence reaches a model only if it has an out-of-dictionary token, a known given name, a
  digit, a currency sign/`@`, or a name/business cue noun. About 48% of tuning-set sentences
  pass. Given names are included (beyond the spec's list) because the models' main Tier 0 gain is
  names that are dictionary words; generic prepositions were removed from the cue list after a
  test showed they gated almost everything.
- Reversibility: easy.

### D-046 Tier 2: labels only, located by code, discarded if not found (2026-11-24)
- Default model Phi-4-mini-instruct Q4_K_M (MIT), fallback Qwen3-1.7B Q4_K_M (Apache-2.0) for
  machines under ~7.5 GB. Gated sentences are batched per window (<= 1,500 chars) into numbered
  lines; the model answers under a JSON-schema grammar with `{line, text, category}`; code finds
  `text` in that line (case-insensitive) and discards anything it cannot locate, so paraphrases
  and invented text never enter the output. Malformed output adds nothing. New category
  INDIRECT (`<IND1>`) for indirect identifiers.
- Per-segment keep/summarize/drop labels (in the spec) are deferred: compression keeps its
  deterministic rules; Tier 1 embeddings drive `aggressive`.
- Prompt injection in a transcript can at most reduce what Tier 2 adds; it cannot affect rules or
  NER, and model text is never emitted.
- llama.cpp native logging is silenced before the model loads; Metal is compiled in by the
  wheel but never used (`n_gpu_layers=0`, CPU-only as specified).
- Reversibility: easy.

### D-047 `aggressive` compression uses Tier 1 embeddings (2026-11-24)
- Medium + semantic near-duplicate removal (cosine >= 0.92, same negations and numbers) +
  extractive selection: within windows of 200 turns, the least central half of droppable turns is
  dropped (degree centrality). Turns with placeholders, numbers or negation/decision words are
  never droppable. It selects real turns only and never generates text.
- Reversibility: easy.

### D-048 `.gitignore` anchors `models/` to the repo root (2026-11-24)
- The earlier `models/` pattern also matched the source package `src/offrecord/models/`, which
  hid it from git and from ruff (ruff honours .gitignore). The commit-check script now also stops
  on lint failures (it previously only printed them).

### D-049 Fixes from the Phase 8 outside review (2026-11-24)
- Semantic dedupe merged "move the launch earlier" with "...later" (cosine 0.935) and changed
  `medium` whenever Tier 1 was on. Now `medium` is MinHash-only at every tier; `aggressive`'s
  semantic dedupe needs cosine >= 0.97 and identical content words; extraction never drops
  questions, questioned turns, decisions ("let's", "we'll", "decided") or turns whose reply
  carries meaning. `aggressive` is still lossy by design: documented.
- Names a model found are swept document-wide (models only see gated sentences); short given
  names ("Li", "Ed") and capitalised mid-sentence words now pass the gate.
- Tier 2: spans located with original offsets (lower() can change length), every occurrence,
  type-checked JSON, truncated or failing batches halved and retried, unusable batches counted.
- Graceful degradation: model names validated against the manifest (a typo used to break even
  Tier 0); `auto` steps down when the [ner]/[llm] extras are missing; explicit tiers say what to
  install; Tier 2 uses whichever model `setup` installed and warns below its RAM floor.
- Model files verified at load time: ONNX/JSON re-hashed on every load (< 0.5 s); GGUF
  re-hashed only when its (size, mtime, inode) stamp from setup/doctor changes.
- Merger: a deny-list/speaker entity keeps its placeholder when a model span only adds an
  org/place word ("Quarnstead" + "Freight"); otherwise the longest span wins (so "Ada
  Brownfield" never takes speaker "Ada Quist"'s placeholder).
- GLiNER chunks overlap; BERT splits sentences that would be truncated at 512 tokens; models are
  not re-run on the joined split-PII view; ONNX Runtime logs at error level.
- Measured memory (review): Tier 1 ~670 MB RSS; Tier 2 with Qwen3 1.7B ~2.6 GB. Phi-4-mini needs
  the 8 GB floor.
- Regression tests: `tests/test_review5_regressions.py`.

### D-050 Known advisory in the [llm] extra: diskcache PYSEC-2026-2447 (2026-11-24)
- `llama-cpp-python` depends on `diskcache` (<= 5.6.3, no fixed release), which deserialises
  with pickle: anyone who can write to its cache directory can run code when it is read.
- offrecord never enables llama-cpp-python's disk cache (`LlamaDiskCache`) and creates no such
  directory, so the vulnerable path is not reached. Recorded here and in security.md; re-check
  when a fixed release exists. The [ner] extra and Tier 0 dependencies audit clean.

### D-051 Off-the-shelf baseline: Presidio with defaults, in a throwaway venv (2026-11-24)
- User delegated Q-007 to my recommendation. Presidio (`presidio-analyzer` 2.2.366, spaCy
  `en_core_web_lg` 3.8) was installed only in a scratch virtualenv and scored with offrecord's
  runner (`scripts/presidio_baseline.py`, results in `eval/baselines/presidio.json`). It is not a
  project dependency.
- Results (strict recall / char precision): tuning 40.6% / 96.2%; held-out 37.2% / 91.6%.
  offrecord Tier 0: 97.7% / 95.2% and 90.6% / 89.6%.
- Where Presidio is weak on this data: spoken numbers/emails, amounts and percentages (0-4%),
  topics (0%), deny-list entities (clients 7%, brands 10% on held-out). Where it matches us:
  IPs (100%), dates (97%), people (81%; we are at 99-100%).
- Fairness caveat: its EMAIL (4%) and URL (0%) scores are artefacts of our synthetic reserved
  domains (`.example`, `.test`), which its recognisers reject as invalid TLDs. Real domains would
  score far higher. Presidio was also given no deny-list, by design ("defaults").

### D-052 Remaining open questions resolved by delegation (2026-11-24)
- Q-008: keep Qwen3 1.7B as the Tier 2 fallback (recall first) and document its precision cost.
- Q-003: no licence for now (private); choosing one only matters if the project is published.
- Q-004: GitHub Actions workflow stays ready; it runs once a remote exists, and pushing still
  needs the user's explicit go-ahead.

### D-053 User set seeded by an independent agent at the user's request (2026-11-24)
- The user asked for fake transcripts in `eval/user_set/`. To keep the set independent, a
  separate agent wrote them, reading only `docs/eval-format.md` and `eval/user_set/README.md`
  (no detector code, templates, pools, generated sets or baselines). 8 transcripts (sales,
  support/verification, finance, HR/legal, incident, vendor negotiation, board prep, customer
  success), 309 scored labels plus speaker labels, 16 NEG spans, and a deny-list that leaves some
  clients, brands, a product, a codename, all ORGs and most people out on purpose.
- Results are reported as a separate third column and never used for tuning: I did not inspect
  the individual misses. Improvements should come from new, independently written tuning data,
  not from this folder.
- The writer's labelling conventions differ from the generator's in places (quarters such as
  "Q3", birth and expiry dates labelled DATE; durations and times of day unlabelled). Labels were
  not changed; any future label correction goes in eval/CORRECTIONS.md.

### D-054 Hand-written tuning set, written by a fresh independent agent (2026-11-24)
- The user set showed weak categories (dates, topics, percentages, amounts, account numbers,
  emails) on unseen phrasing. To improve them without tuning on the user set, a new agent wrote
  `eval/tuning_handwritten/` (inline markup, set name `tuning-hw`). It read only
  `docs/eval-format.md` section 2 and the txt adapter (for speaker-line shapes); no detector
  code, user set, held-out data, templates or pools. It was told which categories to emphasise
  (aggregate weakness only, never user-set content or misses) and checked parsing with
  `--system null` only.
- Tuning is allowed on `tuning` and `tuning-hw` only (`sets.TUNING_SETS`); `scripts/misses.py
  --hw` shows misses on it. Held-out and user sets stay untouched and are re-measured after.
- Reversible: delete the folder and the set name.

### D-055 Wider labelling conventions accepted (user decision, 2026-11-24)
- From the hand-written tuning set's conventions, the user accepted: fiscal quarters ("Q3"),
  bare years and event months count as DATE; passwords, one-time codes, gate/authorisation codes
  and routing numbers count as ACCOUNT; a card's or SSN's last four digits count as CARD / SSN.
- Widens what is redacted (recall first); costs some precision. Detectors are tuned toward it on
  `tuning` and `tuning-hw` only.

### D-056 Business figures widened; unit nouns stay visible (2026-11-24)
- Tuned on `tuning-hw` (tuning set unchanged at 97.7%): money/negotiation trigger words for bare
  spoken amounts, rate/pay tails kept inside the amount ("$540 an hour", "41k MRR"), ranges,
  "and forty cents", spoken scale amounts ("three point two million") without triggers, spoken
  percentages ("two and a half percent", "up two points", "two thirds", "high eighties",
  "50/50"), spoken counts before unit nouns ("eleven trucks") and more unit nouns. Times of day
  ("4 pm") are no longer counted as figures near triggers.
- QUANTITY spans cover the number only; unit nouns stay visible so redacted output is still
  analysable ("<QTY1> stores"). tuning-hw labels were trimmed to match (eval/CORRECTIONS.md).
- Known misses left alone: lone figures with no cue ("Eighteen.", "Make it six"), clock-like
  price readings ("seven ten", "five oh one", which are also times).

### D-057 Dictated identifiers and emails (2026-11-24)
- Tuned on `tuning-hw`: a dictated-ID pattern covers runs of letters, digit groups, digit words,
  "dash", phonetic letters and symbol words when they hold at least 4 digits (or 2 digits plus
  3 letters over 6+ tokens, for dictated passwords); more ID trigger words (matter, parcel, MLS,
  PO, site, receipt, ...). Dictated emails the normaliser cannot rebuild (dash-joined domains,
  "plus" in the local part, split words) and spelled confirmations ("B DOT TREMAYNE") are
  matched on the original text; entity keys are canonical so written and spoken forms of the
  same address share a placeholder.
- ACCOUNT 65.5% -> 95.0%, EMAIL 84.0% -> 98.0% on tuning-hw; tuning set unchanged.

### D-058 Organisation and place names expand over generic words (2026-11-24)
- Tuned on `tuning-hw`: more org/place suffix words (brothers, sons, builders, lending, credit
  union, elementary, commons, wharf, centre, ...); up to two capitalised words may bridge to one
  ("Larchmere Family Health"); "and" joins names before any suffix ("Pratt Ivey and Sola",
  "Overbeck and Sons") but never two complete org names ("X Group and Y Group").
- ORG 51.9% -> 92.3%, LOCATION 67.6% -> 76.5% on tuning-hw; tuning set unchanged.

### D-059 Strong topic terms for personal and operational sensitivity (user approval, 2026-11-24)
- The user approved adding strong (sentence-redacting) topic terms beyond corporate events:
  health conditions and treatment, family/legal circumstances (divorce, custody, restraining
  orders, immigration status), wrongdoing and incidents (fraud, theft, unreported safety
  incidents, investigations), unannounced plans and secrets ("not public", "nobody below",
  "keep it off the minutes"), and customers at risk ("may not renew", "lose the account").
  Broad words that would blank whole calls ("doctor", "lawyer", "hearing") were left out.
- tuning-hw TOPIC 20.3% -> 89.2%; tuning set TOPIC unchanged (90.0%). Weak (flag-only) terms
  unchanged (D-043). Reversible per user via `topics.redact` in config.

### D-060 Publishing: one squashed commit on a branch; internal notes stay local (user, 2026-11-24)
- The public GitHub repo receives the project as one commit on branch `tuning-handwritten`, with
  no AI co-author trailer; nothing is pushed to `main` (the user creates it on GitHub). Local
  history is unchanged.
- `CLAUDE.md`, `docs/PROGRESS.md` and `docs/OPEN_QUESTIONS.md` are working notes: untracked and
  git-ignored, kept on the development machine only. Public docs point to this file instead.
- Final numbers after tuning on tuning-hw (held-out / user, recall / precision): Tier 0
  91.4/87.6 and 79.0/88.3; Tier 1 94.2/82.7 and 82.8/83.3; Tier 2 94.7/79.3 and 82.5/82.1
  (before: Tier 0 90.6/89.6 and 68.6/90.8). Held-out and user misses were not inspected.

### D-061 Clipboard commands may start the clipboard helper, nothing else (2026-11-25)
- `paste`/`unpaste` (user-requested usability features) need the platform clipboard helper.
  `netguard.allow_processes(paths)` temporarily admits exactly those absolute paths for
  Popen/posix_spawn audit events; no shell, reduced environment, timeout, size limit. The
  network block is untouched and all other child processes stay blocked. Threat model R11b.
- Alternatives rejected: tkinter (often missing, needs a display, heavier); a new dependency
  (needs approval, same subprocess underneath on Linux).

### D-062 Usability: default config, init, denylist, --prompt, run summary (2026-11-25)
- `redact`/`paste`/`review` load `~/.config/offrecord/config.yaml` (or `OFFRECORD_CONFIG`) when
  `--config` is not given, and say so on stderr (no content). Tests point OFFRECORD_CONFIG at a
  missing file so the developer's real deny-list is never used.
- `init` (interactive) and `denylist add/import/count` edit that file owner-only, merge aliases
  for an existing name instead of duplicating it, validate before writing, and never print
  names. YAML comments in the deny-list are not preserved when these commands rewrite it.
- `--prompt` redacts the instruction with the same mapping (deny-list, patterns, figures) but
  without the unknown-word heuristic, so the user's wording stays readable; the wrapper tells the
  LLM to copy placeholders exactly. The last mapping's path is remembered for `unpaste`.
- The run summary reports counts per category and the number of review flags; never text.

### D-063 Licence: Apache-2.0 (user decision, 2026-11-25)
- Resolves Q-003. Permissive with an explicit patent grant; compatible with the bundled SCOWL
  word lists. Model weights are not redistributed (users download them via `setup`), so their
  licences do not constrain the project's. LICENSE text taken verbatim from GitHub's licence API.
