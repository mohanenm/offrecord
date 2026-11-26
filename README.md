# offrecord

Remove personal data from meeting and call transcripts before they reach an LLM, then restore
the originals in the LLM's answer. Fully offline, on modest hardware.

> offrecord reduces what you need to check before pasting a transcript into an LLM. It does
> not remove the need to check. On the synthetic validation set, Tier 0 redacts 97% of the
> personal data (names, contact details, account and card numbers, addresses) and three in
> four transcripts come out with no personal data left. On harder phrasing the numbers drop:
> see [Status](#status-what-the-numbers-measure). Apache-2.0; not yet on PyPI.

## Who it is for

People who need to send call or meeting transcripts to an LLM (or a colleague) without
exposing the people in them, and who cannot send the raw transcript to a cloud service to
clean it first.

## What gets redacted

Personal data is redacted: names, email addresses, phone numbers, SSNs, card and account
numbers, codes, IP addresses, URLs, addresses and places, day-level dates (a birth date or an
appointment), and sentences about someone's health, family, legal or employment situation.
So is every name in your deny-list, whatever kind of name it is.

Business context is left readable and flagged for review by default: amounts, percentages,
quantities, quarters and fiscal years, organisations found automatically, and business topics
such as layoffs, acquisitions or pricing. To redact those as well, add `protect: confidential`
to your config (`offrecord init` asks).

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
three", "jane at example dot com") become written ones. Rule-based detectors find names, identifiers, places,
dates, deny-list entries, business figures and sensitive-topic sentences in both views;
optional model tiers add to what the rules find. Personal data becomes a consistent
placeholder (`<P1>`, `<EMAIL1>`, `<ACCT2>`) and business context becomes a review flag;
compression removes fillers and backchannels, and the mapping from placeholders back to the
originals is written encrypted.
[docs/architecture.md](https://github.com/mohanenm/offrecord/blob/main/docs/architecture.md) explains each step and why it works that way.

## Tiers and hardware floors

| Tier | What | Floor | Install |
| --- | --- | --- | --- |
| 0 | Rules only, no models, no downloads | Python 3.10+, 2 GB RAM (Raspberry Pi 4 class) | included |
| 1 | NER ensemble (GLiNER small + BERT-NER, ONNX int8) + MiniLM embeddings | 4 GB RAM, CPU | `[ner]` extra, then `offrecord setup models` |
| 2 | Phi-4-mini 3.8B (GGUF, llama.cpp) for indirect identifiers; Qwen3 1.7B fallback | 8 GB RAM, 4+ cores (fallback: 4 GB) | `[llm]` extra, then `offrecord setup models --tier 2` |

The `[ner]` extra currently needs Python 3.10 to 3.13 (its pinned ONNX Runtime and NumPy have
no 3.14 wheels yet).

Select with `offrecord redact --tier 0|1|2|auto` (default 0; `auto` picks the highest tier your
RAM and installed models allow). `--compress aggressive` needs Tier 1.

Optional tiers add redactions; they never undo another detector's. Tier 1 catches names the
rules miss, especially ordinary-word names; Tier 2 adds a narrow check for indirect
identifiers ("the only pharmacy we serve in Alaska") on the few sentences the rules did not
already cover. Models arrive only through an explicit `setup` command, with pinned versions
and checksums.

## Status: what the numbers measure

All evaluation data is synthetic: generated conversations with invented names, companies and
numbers. Apart from the tuning templates, the sets were written by coding agents that saw only
the format description, never the detector code, so their phrasing is independent of the
rules, but none of it is real speech. [docs/eval-format.md](https://github.com/mohanenm/offrecord/blob/main/docs/eval-format.md) describes how
each set was made. Validation was written blind but has been looked at during development; the
user set is harder phrasing that was never tuned on. To measure offrecord on your own
transcripts, see
[docs/private-eval.md](https://github.com/mohanenm/offrecord/blob/main/docs/private-eval.md).

### Sealed set: Tier 0 did not meet its targets

A sealed set of 80 transcripts, written blind and never opened during development, was scored
once at the end of this release cycle, against targets fixed before it was opened.

| Sealed set (80) | Target | Tier 0 | Tier 1 | Tier 2 |
| --- | --- | --- | --- | --- |
| PII recall | 95% | **93.4%** (91.7-94.9) | 96.4% (95.1-97.6) | 96.4% |
| PII-clean transcripts | 65% | **46%** (36-56) | 65% (55-75) | 65% |
| No name, contact, account, card or SSN leaked | 90% | 97.5% | 97.5% | 97.5% |
| Precision | 80% | 91.8% | 86.0% | 85.8% |

Tier 0 misses two targets, almost entirely because of places: 76 of its 78 misses are
locations, which it catches 84% of the time here against 96% on validation. Names, account
and card numbers, SSNs, IPs and URLs were all caught; one email and one phone number were
missed. Tier 1 halves the place misses. Brackets are 95% intervals. Place handling has since
been improved on new tuning data written for that purpose (Tier 0 place recall there went
from 85.9% to 89.1%), but that cannot be checked against this set: it now serves as a second
validation set, and the next release will be scored on a new sealed set.

The numbers below are for the default protection (personal data):

- PII recall: the share of names, contact details, account and card numbers, IPs, URLs and
  places that are fully redacted (every letter and digit);
- PII-clean transcripts: the share of transcripts with none of those left;
- business context redacted or flagged: the share of other labelled items (figures,
  organisations, business topics, dates) that are redacted or flagged for review (a flag only
  asks you to look);
- text redacted: how much of the transcript the LLM can no longer read.

| Validation (56) / user set (8) | PII recall | PII-clean transcripts | Business context redacted or flagged | Text redacted |
| --- | --- | --- | --- | --- |
| Tier 0 (rules) | 98.0% / 88.2% | 80% / 25% | 90% / 80% | 15% / 21% |
| Tier 1 (+ NER ensemble) | 98.6% / 89.5% | 86% / 25% | 97% / 82% | 17% / 23% |
| Tier 2 (+ Phi-4-mini) | 98.6% / 89.5% | 86% / 25% | 97% / 83% | 18% / 25% |
| Baseline: plain regexes | 14.2% / 7.2% | 0% / 0% | | |

The user set has only 8 transcripts, so its numbers are loose: the 95% interval for Tier 0
PII recall is 80.8-94.7% there (96.9-99.0% on validation), and its PII-clean rate could be
anywhere from 0% to 62%. `offrecord eval` prints these intervals.

Tier 2 does not change PII recall on these sets. What it adds is indirect identifiers: on 15
held-back transcripts written for that purpose, descriptions of a person ("our only
night-shift pharmacist") were redacted 63% of the time at Tier 0, 68% at Tier 1 and 78% at
Tier 2.

Two outside datasets, never tuned on and scored as aggregates only, give a feel for data
nobody here wrote. On Earnings-21 (44 real earnings calls, speech-recognition text) PII recall
is 85.7% at Tier 0 and 89.8% at Tier 1. On ai4privacy pii-masking-200k (43,501 short synthetic
English texts, not speech) it is 87.3% at Tier 0.

Read the PII-clean column first: in some transcripts a name or number still gets through, so
you still read the output before sharing it. What offrecord changes is how much you have to
find yourself. The misses that remain are mostly names that are ordinary words, people
referred to by role, and spoken or lower-case place names.

Synthetic data cannot capture real speech-recognition errors, real names and their spelling
variety, domain jargon, cross-talk, or the clients and codenames in your deny-list. Expect
real-world recall to be lower.

## Known limitations

- No tool catches everything. Review the flagged spans before sharing output.
- Tier 0 cannot recognise names that are ordinary dictionary words ("Amber", "Art") unless
  they are in the deny-list, a speaker label, or follow a cue ("my name is ..."); Tier 1 models
  catch most of them.
- Place names are Tier 0's weakest category: it caught 84% of them on the sealed set. Places
  that are ordinary words ("Mobile", "Reading") or that appear in text without capitals and
  without a cue ("in", "from", "near") are the usual misses; Tier 1 catches about half of
  those.
- Organisations and products are business context: unless they are in your deny-list or
  look like names to the rules, they are flagged rather than redacted. Put clients and
  codenames you need hidden in the deny-list.
- Higher tiers trade precision for recall (see the table above), and are
  slower (Tier 2 adds roughly 10 s per call transcript on a laptop CPU).
- `aggressive` compression is lossy by design (it drops low-information turns), though it never
  drops turns with placeholders, numbers, decisions or questions.
- Sentences about someone's health, family or legal situation are found by keyword at Tier 0
  ("diagnosed", "divorce", "arrested", ...) and also by meaning at Tier 1; a personal sentence
  that uses none of the keywords can still be missed. A keyword sentence that names no one and
  refers to no one ("our cancer franchise grew") is flagged rather than redacted.
- An individual's salary or debt is personal, but amounts are business figures by default:
  they are flagged, not redacted. Use `protect: confidential` if that matters to you.
- Day-level dates, codes and last-four digits are redacted. Quarters, fiscal years and a month
  or year on its own are flagged; relative dates ("yesterday", "next Friday") are left alone.
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
