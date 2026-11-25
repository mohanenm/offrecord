# offrecord

Redact sensitive information from meeting and call transcripts, then compress them for LLM
analysis. Fully offline, on modest hardware, and reversible on an authorised machine.

> Status: all tiers built; Apache-2.0; not yet on PyPI. Recall on held-out / user set
> (synthetic): Tier 0 91.4% / 79.0%, Tier 1 94.2% / 82.8%, Tier 2 94.7% / 82.5%. The user set
> is the harder, more honest number (see [the numbers](#status-what-the-numbers-measure)). Read
> [Known limitations](#known-limitations) before relying on it.

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

## Pipeline

```
normalize -> redact -> compress -> output
```

1. Normalize: adapters (plain text, WebVTT, SRT, Whisper-style JSON) produce one turn model.
   Every field (turn text, speaker labels, headers, notes, cue ids, unknown JSON values) gets a
   verbatim view and a normalised view where spoken forms become canonical ("five five five
   one two one two" -> 5551212, "john at example dot com" -> john@example.com, "S as in Sam").
   Offset maps tie every normalised character back to the original.
2. Redact (Tier 0, rules only): regex + validators (email, phone, SSN, card with Luhn, IP,
   URL, account/order numbers, dates), your deny-list (fuzzy + phonetic matching, aliases,
   per-entry options for common-word brands), business context (amounts, percentages,
   quantities, sensitive topic sentences, codenames), name heuristics (speaker names, cue
   phrases, out-of-dictionary words). PII split across an interruption is caught by re-scanning
   one speaker's turns joined together. Spans are merged (union: nothing is dropped) and
   replaced with short consistent placeholders (`<C1>`, `<P2>`, `<AMT1>`).
3. Compress: `light` (default) removes fillers, stutters, false starts and backchannel turns
   and merges same-speaker turns; `medium` also drops opening/closing small talk, short
   contentless turns and near-duplicates (MinHash). Negations, decisions and numbers are never
   dropped, and no placeholder is ever lost.
4. Output: compact text (default: speaker legend with placeholders only) or the original
   format, with a header carrying the random mapping id.

Recall comes first: over-redaction is the preferred failure mode. `docs/architecture.md` has
the longer explanation.

## Tiers and hardware floors

| Tier | What | Floor | Status |
| --- | --- | --- | --- |
| 0 | Rules only, no models, no downloads | Python 3.10+, 2 GB RAM (Raspberry Pi 4 class) | built |
| 1 | NER ensemble (GLiNER small + BERT-NER, ONNX int8) + MiniLM embeddings | 4 GB RAM, CPU | built (`pip install -e '.[ner]'`, `offrecord setup models`) |
| 2 | Phi-4-mini 3.8B (GGUF, llama.cpp) for JSON labels only; Qwen3 1.7B fallback | 8 GB RAM, 4+ cores (fallback: 4 GB) | built (`pip install -e '.[llm]'`, `offrecord setup models --tier 2`) |

Select with `offrecord redact --tier 0|1|2|auto` (default 0; `auto` picks the highest tier your
RAM and installed models allow). `--compress aggressive` needs Tier 1.

Optional tiers only ever add redactions. Models arrive only through an explicit `setup`
command, with pinned versions and checksums.

## Status: what the numbers measure

All evaluation data is synthetic: generated conversations with invented names, companies and
numbers. Apart from the tuning templates, the sets were written by coding agents that saw only
the format description, never the detector code, so their phrasing is independent of the rules,
but none of it is real speech. `docs/eval-format.md` describes each set. In short:

- tuning and tuning-hw: the detectors were tuned on these;
- held-out (now called validation): written blind and frozen, but looked at often enough
  during development that it is no longer a blind test;
- user: eight agent-written transcripts in `eval/user_set/`, never tuned on, standing in until
  you add transcripts in your own phrasing.

Baselines on the same data: plain regexes 6.4% held-out recall; Microsoft Presidio with its
defaults 37.2% (its email/URL recognisers reject the reserved test domains used here, so those two
categories understate it).

Recall is strict: a label counts only if every letter and digit in it is redacted. Each speaker
counts once per transcript. Precision is measured in characters.

| Recall / precision | held-out (56 transcripts) | user set (8 transcripts) |
| --- | --- | --- |
| Tier 0 (rules) | 91.4% / 87.6% | 79.0% / 88.3% |
| Tier 1 (+ NER ensemble) | 94.2% / 82.7% | 82.8% / 83.3% |
| Tier 2 (+ Phi-4-mini) | 94.7% / 79.3% | 82.5% / 82.1% |
| Plain regexes | 6.4% | 3.6% |

The user set is the closest to unseen real phrasing; its weakest categories at Tier 0 are
topic sentences (47%), locations (44%) and percentages (59%).

Synthetic data cannot capture: real speech-recognition error patterns, real names and their
spelling variety, domain jargon, real cross-talk, long rambling turns, or the specific clients
and codenames in your deny-list. Expect real-world recall to be lower than the held-out number,
and use the review flags.

## Known limitations

- No tool catches everything. Review the flagged spans before sharing output.
- Tier 0 cannot recognise names that are ordinary dictionary words ("Amber", "Art") unless
  they are in the deny-list, a speaker label, or follow a cue ("my name is ..."); Tier 1 models
  catch most of them.
- Unknown organisations or products whose names are ordinary words are missed at Tier 0 and
  partly at Tier 1 (held-out PRODUCT: 64% / 84% / 89% at Tiers 0 / 1 / 2).
- Higher tiers trade precision for recall (held-out precision 87.6% / 82.7% / 79.3%), and are
  slower (Tier 2 is roughly 15-20 s per call transcript on a laptop CPU).
- `aggressive` compression is lossy by design (it drops low-information turns), though it never
  drops turns with placeholders, numbers, decisions or questions.
- Sensitive-topic sentences are found by keyword (layoffs, lawsuits, health conditions,
  divorce, fraud, "not public", ...): a sensitive sentence with none of those words is missed.
  Pricing/renewal sentences are only flagged by default, not redacted (configurable via
  `topics.redact` / `topics.flag`), because redacting every pricing sentence would blank out
  most of a sales call.
- Fiscal quarters ("Q3"), years after a cue ("in 2019"), codes and last-four digits are
  redacted; relative dates ("yesterday", "next Friday") are not.
- Python cannot wipe memory: decrypted mappings and transcript text live in process memory
  until garbage collected (docs/security.md R10).

## Privacy

- Nothing leaves the machine at run time: no telemetry, no update checks, no downloads.
  Every command except `setup` blocks network access and child processes (except the clipboard
  helper for `paste`/`unpaste`), and fails if anything
  tries.
- Transcript text is never logged or echoed; errors give positions and categories only.
- Your deny-list config is sensitive (it names clients and codenames): it is never echoed.
- Every file offrecord writes is owner-only; outputs are atomic and never overwritten without
  `--force`.
- Threat model: [docs/security.md](docs/security.md).

## Restore and the mapping file

- Each run creates a mapping (placeholder -> original value) with a random id. It is the most
  sensitive artefact: it is written only encrypted (Fernet; key from a passphrase via scrypt
  N=2^17 r=8 p=1, or from a key file via HKDF), by default to a private per-user store
  (`~/.local/share/offrecord/mappings/<id>.ormap`), never next to the output, and never next to
  the key file.
- Passphrases come from `OFFRECORD_PASSPHRASE` or a prompt, never from the command line.
- The redacted output carries the mapping id (a header line, a VTT NOTE, a zero-length first
  SRT cue, or a `_offrecord_mapping` JSON key). `restore` fails if the id does not match.
  Header-less text (an LLM's answer) cannot be verified, so `restore` asks you to confirm with
  `--assume-mapping`; it then checks every placeholder exists in the mapping and warns otherwise
  (`--strict` turns warnings into failure).
- `restore` tolerates `<c1>`, `<C 1>`, `<C1>'s`, `&lt;C1&gt;` and bare `C1`, but never restores
  anything ambiguous (bare tokens that also occur naturally in the redacted text are left as
  they are and reported).
- `--no-mapping` runs are one-way. `--mapping-scope project` (or `--mapping-in`) shares one
  mapping across several transcripts so the same client is the same placeholder everywhere.

## Docs

[docs/architecture.md](docs/architecture.md) · [docs/security.md](docs/security.md) ·
[docs/eval-format.md](docs/eval-format.md) · [CONTRIBUTING.md](CONTRIBUTING.md) ·
[CHANGELOG.md](CHANGELOG.md)

## Licence

Apache License 2.0: see [LICENSE](LICENSE). The bundled word lists come from SCOWL under their
own permissive licence ([src/offrecord/data/LICENSE-SCOWL.txt](src/offrecord/data/LICENSE-SCOWL.txt)).
Optional models are downloaded by `offrecord setup` from their publishers under their own
licences; none are redistributed here.
