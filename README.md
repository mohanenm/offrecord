# offrecord

Redact sensitive information from meeting and call transcripts before they reach an LLM, then
restore the originals in the LLM's answer. Fully offline, on modest hardware.

> offrecord reduces what you need to check before pasting a transcript into an LLM. It does
> not remove the need to check. On the synthetic validation set, Tier 0 catches 91% of
> sensitive items and leaves no high-severity leak (names, contact details, account and card
> numbers) in 96% of transcripts, but only 21% of transcripts come out with nothing leaked at
> all. On harder phrasing the numbers drop: see [Status](#status-what-the-numbers-measure).
> Apache-2.0; not yet on PyPI.

## Who it is for

People who need to send call or meeting transcripts to an LLM (or a colleague) without
exposing names, clients, contact details or business figures, and who cannot send the raw
transcript to a cloud service to clean it first.

## Install

```sh
pipx install "git+https://github.com/mohanenm/offrecord"                    # Tier 0
pipx install "offrecord[ner,llm] @ git+https://github.com/mohanenm/offrecord"  # + model tiers
offrecord doctor
```

macOS and Linux, Python 3.10 or later. Windows is not supported: offrecord relies on POSIX file
permissions to keep its outputs and mapping files private.

From a clone (development): `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' -c
requirements/dev.lock` (Python 3.10: `dev-py310.lock`).

## Everyday use: the LLM round trip

```sh
offrecord init                 # once: list your clients, products, codenames, colleagues
offrecord keygen               # once: key for reversible redaction
offrecord paste --prompt "Summarise the decisions and owners"
# ... paste into ChatGPT / Claude, copy its answer ...
offrecord unpaste              # placeholders in the copied answer -> real names again
```

- `init` writes your deny-list to `~/.config/offrecord/config.yaml` (owner-only); `redact`,
  `paste` and `review` use it automatically. Add names later with
  `offrecord denylist add "Quillfeather Analytics" --alias QFA`, import a CRM export with
  `offrecord denylist import clients.csv` (columns `name,category,aliases`), and check it with
  `offrecord denylist count` (counts only; names are never printed).
- `paste` redacts the clipboard in place; `unpaste` restores the clipboard using the most recent
  mapping (`--mapping` to choose one, `--stdout` to print instead). Clipboard helpers: macOS
  built in; Linux needs wl-clipboard, xclip or xsel. Without a clipboard, use files or stdin:
  `offrecord redact call.txt --prompt "..." > for-llm.txt`.
- `--prompt` puts your instruction first and tells the LLM to keep placeholders exactly as
  written, which is what makes restoring its answer reliable. Your instruction is redacted with
  the same mapping, so `Summarise the QFA call` becomes `Summarise the <C1> call`.
- Every run ends with a summary such as "redacted 42 item(s) (12 person, 5 client, ...); 3
  flagged for review". Review flagged items (`offrecord review`) before sharing.

## Quick try (synthetic samples)

```sh
offrecord redact samples/sales-call.txt --no-mapping
```

Reversible run (the mapping is encrypted with a key file; keep the key away from the mapping):

```sh
offrecord keygen                       # ~/.config/offrecord/keys/default.key
offrecord redact samples/support-call.vtt -o out.txt \
    --key-file ~/.config/offrecord/keys/default.key
# ... send out.txt to an LLM, save its answer as answer.txt ...
offrecord restore answer.txt --assume-mapping --mapping ~/.local/share/offrecord/mappings/<id>.ormap \
    --key-file ~/.config/offrecord/keys/default.key
```

`samples/` holds one synthetic transcript per supported format (txt, VTT, SRT, JSON).

## How it works

Adapters read plain text, WebVTT, SRT and Whisper-style JSON into one turn model, and a
normaliser adds a second view of each turn in which spoken forms ("five five five oh one two
three", "jane at example dot com") become written ones. Rule-based detectors find identifiers,
deny-list names, business figures, sensitive-topic sentences and name-like words in both views;
optional model tiers add to what the rules find. Every finding becomes a consistent
placeholder (`<P1>`, `<C2>`, `<AMT1>`), compression removes fillers and backchannels, and the
mapping from placeholders back to the originals is written encrypted.
[docs/architecture.md](https://github.com/mohanenm/offrecord/blob/main/docs/architecture.md) explains each step and why it works that way.

## Tiers and hardware floors

| Tier | What | Floor | Install |
| --- | --- | --- | --- |
| 0 | Rules only, no models, no downloads | Python 3.10+, 2 GB RAM (Raspberry Pi 4 class) | included |
| 1 | NER ensemble (GLiNER small + BERT-NER, ONNX int8) + MiniLM embeddings | 4 GB RAM, CPU | `[ner]` extra, then `offrecord setup models` |
| 2 | Phi-4-mini 3.8B (GGUF, llama.cpp) for JSON labels only; Qwen3 1.7B fallback | 8 GB RAM, 4+ cores (fallback: 4 GB) | `[llm]` extra, then `offrecord setup models --tier 2` |

The `[ner]` extra currently needs Python 3.10 to 3.13 (its pinned ONNX Runtime and NumPy have
no 3.14 wheels yet).

Select with `offrecord redact --tier 0|1|2|auto` (default 0; `auto` picks the highest tier your
RAM and installed models allow). `--compress aggressive` needs Tier 1.

Optional tiers add redactions; they never undo another detector's. Tier 2 also checks the
words Tier 0 redacted only because they were not in its dictionary (ones the LLM calls
ordinary words stay visible and are flagged for review; `models: {verify_guesses: false}`
turns this off), and reports output turns that no longer make sense. Models arrive only
through an explicit `setup`
command, with pinned versions and checksums.

## Status: what the numbers measure

All evaluation data is synthetic: generated conversations with invented names, companies and
numbers. Apart from the tuning templates, the sets were written by coding agents that saw only
the format description, never the detector code, so their phrasing is independent of the
rules, but none of it is real speech. [docs/eval-format.md](https://github.com/mohanenm/offrecord/blob/main/docs/eval-format.md) describes how
each set was made. Validation was written blind but has been looked at during development; the
user set is harder phrasing that was never tuned on. A sealed set is scored once per release
cycle (not yet opened). To measure offrecord on your own transcripts, see
[docs/private-eval.md](https://github.com/mohanenm/offrecord/blob/main/docs/private-eval.md).

Four numbers per run:

- recall: the share of sensitive items fully redacted (every letter and digit);
- clean transcripts: the share of transcripts in which nothing at all leaked;
- no high-severity leak: the share with no leaked name, contact detail, account, card or SSN;
- precision: the share of redacted characters that were actually sensitive.

| Validation (56) / user set (8) | Recall | Clean | No high-severity leak | Leaks per transcript, median (worst) | Precision |
| --- | --- | --- | --- | --- | --- |
| Tier 0 (rules) | 91.4% / 79.0% | 21% / 0% | 96% / 13% | 2 (5) / 8.5 (11) | 87.6% / 88.3% |
| Tier 1 (+ NER ensemble) | 94.2% / 82.8% | 34% / 0% | 98% / 25% | 1 (4) / 6.5 (10) | 82.7% / 83.3% |
| Tier 2 (+ Phi-4-mini) | 94.7% / 82.5% | 38% / 0% | 98% / 25% | 1 (4) / 6 (10) | 79.3% / 82.1% |
| Plain regexes | 6.4% / 3.6% | | | | |

Read the clean-transcript column first. Even on validation most transcripts still contain at
least one leaked item, usually a sensitive-topic sentence phrased without keywords or a
low-severity figure, so you still need to read the output before sharing it. What offrecord
changes is how much you have to find yourself. On the user set the weakest categories at
Tier 0 are topic sentences (47%), locations (44%) and percentages (59%).

Microsoft Presidio with its defaults reaches 37.2% recall on validation (its email and URL
recognisers reject the reserved test domains used here, so those two categories understate
it). Synthetic data cannot capture real speech-recognition errors, real names and their
spelling variety, domain jargon, cross-talk, or the clients and codenames in your deny-list.
Expect real-world recall to be lower.

## Known limitations

- No tool catches everything. Review the flagged spans before sharing output.
- Tier 0 cannot recognise names that are ordinary dictionary words ("Amber", "Art") unless
  they are in the deny-list, a speaker label, or follow a cue ("my name is ..."); Tier 1 models
  catch most of them.
- Unknown organisations or products whose names are ordinary words are missed at Tier 0 and
  partly at Tier 1 (validation PRODUCT: 64% / 84% / 89% at Tiers 0 / 1 / 2).
- Higher tiers trade precision for recall (see the table above), and are
  slower (Tier 2 is roughly 15-20 s per call transcript on a laptop CPU).
- `aggressive` compression is lossy by design (it drops low-information turns), though it never
  drops turns with placeholders, numbers, decisions or questions.
- Sensitive-topic sentences are found by keyword (layoffs, lawsuits, health conditions,
  divorce, fraud, "not public", ...): a sensitive sentence with none of those words is missed.
  Sentences about pricing, renewals, deals, budgets and strategy are redacted too, which blanks
  out a good part of a sales call. If you need those sentences analysed, set `topics: {mode:
  mixed}` in your config: they are then only flagged for review.
- Fiscal quarters ("Q3"), years after a cue ("in 2019"), codes and last-four digits are
  redacted; relative dates ("yesterday", "next Friday") are not.
- Python cannot wipe memory: decrypted mappings and transcript text live in process memory
  until garbage collected (docs/security.md R10).

## Privacy and the mapping file

- Nothing leaves the machine at run time: no telemetry, no update checks, no downloads.
  Every command except `setup` blocks network access and child processes (except the clipboard
  helper for `paste`/`unpaste`), and fails if anything tries.
- Transcript text and your deny-list are never logged or echoed; errors give positions and
  categories only. Every file offrecord writes is owner-only, written atomically, and never
  overwritten without `--force`.
- The mapping (placeholder to original value) can reverse the redaction, so it is written only
  encrypted, with a key file or a passphrase (never a command-line argument), to a private
  per-user store away from the output and the key.
- The redacted output carries the mapping's random id, and `restore` refuses a mismatched
  mapping. An LLM's answer has no header, so `restore --assume-mapping` asks you to confirm and
  checks every placeholder. Restore accepts common LLM mangling (`<c1>`, `&lt;C1&gt;`, bare
  `C1`) but never restores anything ambiguous.
- `--no-mapping` runs are one-way. `--mapping-scope project` shares one mapping across
  transcripts so the same client gets the same placeholder everywhere.

Threat model and the defence for each risk: [docs/security.md](https://github.com/mohanenm/offrecord/blob/main/docs/security.md).

## Docs

[docs/architecture.md](https://github.com/mohanenm/offrecord/blob/main/docs/architecture.md) · [docs/security.md](https://github.com/mohanenm/offrecord/blob/main/docs/security.md) ·
[docs/eval-format.md](https://github.com/mohanenm/offrecord/blob/main/docs/eval-format.md) · [docs/private-eval.md](https://github.com/mohanenm/offrecord/blob/main/docs/private-eval.md) · [CONTRIBUTING.md](https://github.com/mohanenm/offrecord/blob/main/CONTRIBUTING.md) ·
[CHANGELOG.md](https://github.com/mohanenm/offrecord/blob/main/CHANGELOG.md)

## Licence

Apache License 2.0: see [LICENSE](https://github.com/mohanenm/offrecord/blob/main/LICENSE). The bundled word lists come from SCOWL under their
own permissive licence ([src/offrecord/data/LICENSE-SCOWL.txt](https://github.com/mohanenm/offrecord/blob/main/src/offrecord/data/LICENSE-SCOWL.txt)).
Optional models are downloaded by `offrecord setup` from their publishers under their own
licences; none are redistributed here.
