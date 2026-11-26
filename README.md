# offrecord

Remove personal data from meeting and call transcripts before they reach an LLM, then restore
the originals in the LLM's answer. Fully offline, on modest hardware.

> offrecord reduces what you need to check before pasting a transcript into an LLM. It does
> not remove the need to check. On the first sealed set (80 synthetic transcripts written
> blind, scored once and now used for validation), the rules alone (Tier 0) fully redact 94.6%
> of the personal data, and 54% of transcripts come out with none left; with the optional
> local models (Tier 1) that is 96.3% and 64%. On real earnings calls Tier 0 redacts 87.0%.
> On the easiest set it reaches 99%: see [Status](#status-what-the-numbers-measure).
> Apache-2.0; not yet on PyPI.

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
  the same mapping, and anything already redacted in the transcript gets the same placeholder,
  so `Summarise the QFA call` becomes `Summarise the <C417> call`.
- Every run ends with a summary such as "redacted 42 item(s) (12 person, 5 client, ...); 3
  flagged for review". Review flagged items (`offrecord review`) before sharing.

## Quick try (synthetic samples)

```sh
offrecord redact samples/sales-call.txt --no-mapping
```

Reversible run (the mapping is encrypted with a key file; keep the key away from the mapping):

```sh
offrecord keygen                       # ~/.config/offrecord/keys/default.key
offrecord redact samples/support-call.vtt -o out.txt
# ... send out.txt to an LLM, save its answer as answer.txt ...
offrecord restore answer.txt --assume-mapping --mapping ~/.local/share/offrecord/mappings/<id>.ormap
```

The key comes from `--key-file`, else `OFFRECORD_KEY_FILE`, else a passphrase in
`OFFRECORD_PASSPHRASE`, else the default key that `keygen` wrote (offrecord says when it uses
it), else a passphrase prompt. A mapping made with a passphrase asks for it even when the
default key exists.

`samples/` holds one synthetic transcript per supported format (txt, VTT, SRT, JSON).

## How it works

Adapters read plain text, WebVTT, SRT and Whisper-style JSON into one turn model, and a
normaliser adds a second view of each turn in which spoken forms ("five five five oh one two
three", "jane at example dot com") become written ones. Rule-based detectors find names, identifiers, places,
dates, deny-list entries, business figures and sensitive-topic sentences in both views;
optional model tiers add to what the rules find. Personal data becomes a consistent
placeholder (`<P417>`, `<EMAIL283>`, `<ACCT605>`) and business context becomes a review flag;
compression removes fillers and backchannel turns that don't answer a question, and the
mapping from placeholders back to the originals is written encrypted.
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
rules, but none of it is real speech, and the agents that wrote it are the same kind of model
that wrote the rules. [docs/eval-format.md](https://github.com/mohanenm/offrecord/blob/main/docs/eval-format.md) describes how
each set was made. Validation was written blind but has been looked at during development, and
most of its invented names are not dictionary words, which the name rules catch almost by
construction: it is the easiest set, not the most telling one. The user set was never tuned on
directly, but its miss counts were checked after changes and steered what later tuning data
covered, so treat it as a second validation set; its numbers are optimistic too. The sealed
set below and the two outside datasets are the better guide. To measure offrecord on your own
transcripts, see
[docs/private-eval.md](https://github.com/mohanenm/offrecord/blob/main/docs/private-eval.md).

### Sealed set: Tier 0 did not meet its targets

A sealed set of 80 transcripts, written blind and never opened during development, was scored
once at the end of the first release cycle, against targets fixed before it was opened.

| Sealed set (80), at opening | Target | Tier 0 | Tier 1 | Tier 2 |
| --- | --- | --- | --- | --- |
| PII recall | 95% | **93.4%** (91.7-94.9) | 96.4% (95.1-97.6) | 96.4% |
| PII-clean transcripts | 65% | **46%** (36-56) | 65% (55-75) | 65% |
| No name, contact, account, card or SSN leaked | 90% | 97.5% | 97.5% | 97.5% |
| Precision (speaker labels included) | 80% | 91.8% | 86.0% | 85.8% |

Tier 0 missed two targets, almost entirely because of places: 76 of its 78 misses were
locations, caught 84% of the time against 96% on validation. Tier 1 halved the place misses.
Brackets are 95% intervals. The set is now spent and is scored as `validation2`; place handling
improved afterwards on new tuning data (not on this set), and it now scores 94.6% at Tier 0.
The next release will be scored once on a new sealed set of 60 transcripts, stored encrypted,
against targets already fixed in [docs/eval-format.md](https://github.com/mohanenm/offrecord/blob/main/docs/eval-format.md).

### Current numbers

For the default protection (personal data):

- PII recall: the share of names, contact details, account and card numbers, IPs, URLs and
  places that are fully redacted (every letter and digit);
- PII-clean transcripts: the share of transcripts with none of those left.

| Set (transcripts) | Tier 0 PII recall | Tier 0 PII-clean | Tier 1 PII recall | Tier 1 PII-clean |
| --- | --- | --- | --- | --- |
| `validation2`, the first sealed set (80) | 94.6% | 54% | 96.3% | 64% |
| User set (8) | 88.9% | 25% | 90.2% | 25% |
| `validation` (56) | 99.3% | 93% | 99.8% | 98% |
| `validation`, no deny-list | 98.2% | 82% | | |
| Earnings-21, real earnings calls (44) | 87.0% | | 90.1% | |
| ai4privacy, short synthetic texts (43,501) | 88.2% | 78% | | |
| Baseline: plain regexes, `validation` | 14.2% | 0% | | |
| Baseline: Presidio defaults, `validation` | 62.7% | | | |

Without a deny-list (what a new user gets before `offrecord init`), `validation2` and the user
set barely move (94.5% and 88.9%); `validation` drops to 98.2% because its sets name clients and
people in their deny-lists. Earnings-21 and ai4privacy were never tuned on and are scored as
aggregates only (`scripts/earnings21_eval.py`, `scripts/ai4privacy_eval.py`); a whole earnings
call is about 8,000 words, so a PII-clean rate per call means little there. The Presidio row
comes from `eval/baselines/presidio.json` (default recognisers, no deny-list; its email and URL
recognisers reject the reserved test domains the sets use, so it is a rough comparison).

The cost of that recall, on `validation` (Tier 0 / Tier 1 / Tier 2):

- precision on turn text, the share of redacted characters that are labelled: 80% / 73% / 69%
  (on Earnings-21, whose labels cover fewer kinds of thing, 40% / 32%);
- text redacted: 15% / 16% / 17%;
- business context (figures, organisations, business topics): 45% / 47% / 47% redacted and a
  further 46% / 50% / 50% flagged for review, which leaves it readable;
- must-not-redact words redacted (ordinary uses of a word that is a name elsewhere): 32% on
  `tuning-hw` at Tier 0.

The intervals `offrecord eval` prints resample transcripts. `validation`'s 56 transcripts come
from 14 templates that share phrasing, so its intervals are narrower than they should be. The
user set has only 8 transcripts: its Tier 0 PII recall interval is 82-95% and its PII-clean
rate could be anywhere from 0% to 62%.

Tier 2 does not change PII recall on any of these sets and costs precision. What it is for is
indirect identifiers: on 15 held-back transcripts written for that purpose, descriptions of a
person ("our only night-shift pharmacist") were redacted 63% of the time at Tier 0, 68% at
Tier 1 and 78% at Tier 2. Those transcripts are not in the repository yet, so that figure
cannot be reproduced from it; it is opt-in for that reason.

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
- Higher tiers trade precision for recall (see above) and are slower: Tier 1 took about a
  minute on a 10,000-word call on a laptop CPU; Tier 2 adds about 10 s per short transcript and
  several minutes or more on a long call.
- `aggressive` compression is lossy by design (it drops low-information turns). Its selection
  step never drops turns with placeholders, numbers, decisions or questions, or the answer to a
  question, but like `medium` it removes near-duplicate turns, so a repeated mention can go.
  The last occurrence of each placeholder always stays.
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
  mapping. An LLM's answer has no header, so `restore` refuses it unless you pass
  `--assume-mapping` to say the mapping is the right one. Placeholder numbers are drawn at
  random for each mapping, so an answer about another call almost never fits: if the answer
  holds any placeholder the mapping lacks, `restore` and `unpaste` refuse and restore nothing.
  Restore accepts common LLM mangling (`<c417>`, `&lt;C417&gt;`, bare `C417`); a bare token
  needs three digits, so an answer's own `P1` or `L2` is never taken for a placeholder.
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
They are SCOWL 2020.12.07 en_US entries (size 35 for common words; proper names and capitalised
words up to size 70 and 50 for names), rebuilt exactly by `scripts/build_wordlists.py`.
Optional models are downloaded by `offrecord setup` from their publishers under their own
licences; none are redistributed here.
