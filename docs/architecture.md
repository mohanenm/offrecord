# How offrecord works

offrecord reads a transcript, finds the sensitive parts, replaces each one with a placeholder
such as `<P1>` or `<AMT2>`, and writes the redacted transcript plus an encrypted mapping. You
paste the redacted text into an LLM, and `offrecord restore` puts the original values back
into the answer. Everything runs locally.

## Design priorities

Two rules shape most of the code. The first is that a missed name is worse than a redacted
ordinary word, so when a trade-off comes up, the tool redacts. The unknown-word rule (any word
that is not in the dictionary is treated as a possible name) is the clearest example: it costs
precision, but in speech-recognition output most out-of-dictionary tokens really are names,
brands or places.

The second is that transcripts are sensitive, and so is everything derived from them. The tool
never opens a network connection after `offrecord setup`, never prints transcript text in
errors or logs, and writes every file it creates as owner-only.

## Pipeline

Adapters parse plain text, WebVTT, SRT and JSON (Whisper-style segments and similar
shapes) into one internal model: a document made of turns, each with a speaker, text and
timing, plus metadata fields. Adapters never drop text. Blocks that do not parse, VTT notes,
cue ids and unknown JSON keys all end up in a field that is scanned, and they are written back
on output. A JSON key can be a name, so unknown keys are scanned as well.

Normalisation builds a second view of each field next to the verbatim one. It turns spoken
forms into written ones ("five five five oh one two three" into digits, "jane at example dot
com" into an email address, spelled letters into a word), removes invisible characters and
folds look-alike letters. Each step keeps an offset map, so a match in the normalised view can
be projected back to exact characters in the original. Detectors scan both views, so
over-normalising only ever adds redactions.

Detectors return spans: a field, start and end offsets, a category and a confidence.
Tier 0 has four rule-based detectors:

- patterns: regular expressions with validators for emails, phone numbers, card numbers (Luhn),
  SSNs, IBANs (mod 97), IP addresses, URLs, account and order numbers, dates and dictated IDs;
- the deny-list: names you supply (clients, products, codenames, people), matched with fuzzy
  string similarity and a phonetic key, so speech-recognition misspellings still match;
- business context: amounts, percentages, quantities near business terms, and sentences that
  mention sensitive topics such as layoffs or litigation;
- people: cue phrases ("my name is", "Dr"), a bundled list of proper names, and the
  unknown-word rule.

Most detectors also run on a view that joins consecutive turns, so a phone number split by an
"mm-hmm" from the other speaker is still found.

The merger takes the union of all spans, so no character that any detector marked is
dropped. It joins adjacent name words into one placeholder and joins number fragments across
self-corrections ("five five five, sorry, five five six").

Placeholders are assigned per entity, not per mention. "Quillfeather Analytics", "QFA" and
"quill feather" become the same `<C1>` when the deny-list lists them as aliases. If the input
already contains text that looks like a placeholder, it is escaped first, so the mapping stays
one-to-one.

Compression is optional and conservative. The default level only removes fillers, stutters
and backchannels and merges consecutive turns from the same speaker, which cannot change what
was redacted. Higher levels drop small talk and near-duplicate turns. No level ever drops the
last occurrence of a placeholder.

## Restore and the mapping file

The mapping from placeholders to original values is encrypted with Fernet. The key comes from
a key file (via HKDF) or a passphrase (via scrypt with a per-file salt); there is no way to run
without one except `--no-mapping`. Each redacted output carries a short header with the mapping
id, so restore can tell when it is given the wrong mapping.

Restore is tolerant. LLMs rewrite placeholders (`<P1>`, `P1`, `&lt;P1&gt;`, `<p1>`), so restore
accepts the common variants. A bare `P1` is restored only when it is upper-case and never
appeared naturally in the redacted text. Unknown placeholders are left in place and reported.
Restore never guesses. Each placeholder restores to the first form seen for that entity, because
per-mention restore becomes unreliable once compression has merged or dropped turns.

## Optional tiers

Tier 1 adds two small named-entity models (GLiNER small and BERT-base-NER, int8 ONNX, no
PyTorch) and a sentence-embedding model (MiniLM). Tier 2 adds a small local LLM (Phi-4-mini, or
Qwen3 1.7B on machines with less memory) run through llama.cpp. Both tiers only add spans to
Tier 0's output; they never remove a redaction. Only sentences that might contain something the
rules missed are sent to a model: sentences with an unknown word, a known given name, a digit,
a currency sign or a business cue.

Tier 2 never writes text into the output. The model answers under a JSON grammar with the line
and the exact text it wants redacted, and code then looks for that text in that line. Anything
it cannot find is discarded. A prompt injection in a transcript can therefore at most stop
Tier 2 from adding redactions; it cannot change the rules, the NER models or the output.

Models are downloaded only by `offrecord setup models`, pinned by commit and SHA-256, and
verified again when loaded.

## Containment

Every command except `setup` installs a network guard before doing any work. It patches socket
connects, DNS lookups and process creation; any attempt is refused and counted, and the run
fails if the count is not zero at exit, even if a library swallowed the error. The clipboard
commands may start the platform clipboard helper by absolute path, and nothing else. Native
code can bypass a Python-level guard, so the guard is defence in depth. The real guarantee is
that no code path tries to connect. The test suite runs every command in a subprocess with an
independent tripwire to check this.

Errors are built from fixed templates that name positions, sizes and categories. Unexpected
exceptions are reported by type and code location only, because messages from decoders and
parsers can contain input text. Outputs are written atomically and never overwrite an existing
file unless `--force` is given.

## Evaluation

The evaluation sets are synthetic: generated conversations, not real transcripts, so they
measure the tool on plausible text rather than on yours. `docs/eval-format.md` describes the
formats, how each set was made and what it is used for.

Recall is strict: a label counts as caught only if every letter and digit in it is redacted.
Precision is measured in characters, so a system cannot raise it by emitting many tiny spans.

## Limits

offrecord reduces what you have to check before pasting a transcript into an LLM. It does not
remove the need to check. Names that are ordinary words with no cue ("I told Amber"),
identifying descriptions ("the only pharmacy chain we serve in Alaska") and unusual spoken
forms are the usual misses. The review flags and `offrecord review` exist to make that check
quick.
