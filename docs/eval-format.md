# Evaluation data formats

All evaluation data is synthetic. Never put real names, companies, numbers or transcripts in
any of these files.

There are three kinds of evaluation input:

1. Templates and pools (`eval/templates/<set>/*.yaml`, `eval/pools/<set>/pools.yaml`). The
   generator fills templates with invented entities, deterministically from a seed, and
   writes labelled transcripts in every supported format.
2. Labelled transcripts in inline markup (`eval/user_set/`, `eval/tuning_handwritten/`, or a
   private folder outside the repo): transcripts written as text, with sensitive spans marked
   inline.
3. Generated sets (`eval/generated/<set>/`): the generator's output, reproducible from the
   templates, pools and seed.

## The sets and how they were made

| Set | Source | Used for |
| --- | --- | --- |
| `tuning` | 12 templates written alongside the detectors, 72 transcripts | Tuning |
| `tuning-hw` | 80 agent-written transcripts in inline markup | Tuning |
| `validation` | 14 templates, 56 transcripts (formerly called `heldout`) | Checking that a change generalises |
| `validation2` | 20 templates, 80 transcripts: the first sealed set | Opened once for the first release; now a second validation set |
| `sealed` | 60 agent-written transcripts in inline markup, encrypted | Scored once, at the next release check |
| `user` | 8 agent-written transcripts in inline markup | Reported separately; not tuned on directly (see below) |

Everything except the `tuning` templates was written by coding agents that were given only
this format description and the category list. They did not see the detector code, the other
sets or each other's work. This keeps the phrasing independent of the rules being measured,
but it does not make the text real: all of it is generated, plausible conversation, and the
numbers should be read that way. The `user` set stands in for a set written in your own
phrasing until you add one (see `eval/user_set/README.md`). Its transcripts were never tuned
on, but its miss counts were checked after changes and decided what later tuning data
stressed, so treat it as a second validation set: its numbers are optimistic.

Most invented names in `validation` are not dictionary words, so the rule that treats an
unknown capitalised word as a name catches them almost by construction. Real names are often
ordinary words ("Grace Hill"), so expect lower recall on names than these sets show.

`validation` was the original held-out set. It has been looked at many times while the
detectors were developed, so it is no longer blind; it now serves to catch changes that help
the tuning sets but do not generalise. `sealed` replaced it as the blind set and was opened
once, at the end of the first release cycle; its result is in the README. It is no longer
blind either, so it is scored as `validation2` and the next release is scored on a new sealed
set.

The new `sealed` set was written by three separately briefed agents (ordinary business calls;
speech-to-text style, mostly lower case with recogniser errors; calls heavy in personal data),
20 transcripts each, 3,313 personal-data labels. They were told to use realistic names,
including names that are ordinary words, and real places, because invented names flattered
the earlier sets. About a third of its name words also occur in the tuning data, as common
names do. Only its parser and format checks were run, counting labels; nobody has read or
scored it. It is stored encrypted in `eval/sealed/` in offrecord's own format, with its key
held by the maintainer outside the repository, so looking at it takes a deliberate step.

The generated `validation` and `sealed` sets are frozen. Each folder has a `MANIFEST.sha256`
whose own hash is pinned in `src/offrecord/evaluation/sets.py`, and the `eval` command refuses
to run on a folder that does not match. A CI job also fails if any commit after the first one
touches either sealed set. `offrecord eval --set sealed` refuses to run without
`--final-check` and the key (`--key-file` or `OFFRECORD_SEALED_KEY`); it is meant to be used
once per release cycle, after all tuning is done.

A test, `tests/test_recall_floor.py`, scores Tier 0 on `tuning` and `validation` on every
test run. It fails if PII recall drops below 0.99 on tuning or 0.98 on validation, if any PII
category with at least 20 validation labels drops below 0.90, or if character precision on
validation drops below 0.85.

### Release check (fixed before the next sealed set is written)

The next release is scored once on a new sealed set, at every tier, with no changes in
between, and the result is published whether it passes or not. Tier 0 carries the claim:

1. PII recall at least 97%.
2. Every PII category with at least 20 labels at least 90% (one weak category, such as places
   on the first sealed set, cannot hide behind the total).
3. Names, contact details, accounts, cards and SSNs pooled at least 99%, and at least 95% of
   transcripts with none of them leaked.
4. At least 80% of transcripts PII-clean.
5. Character precision on turn text (`prec(text)`, speaker labels left out) at least 75%, so
   recall work cannot blank everything.
6. Tier 1 at least Tier 0 on PII recall and PII-clean transcripts.
7. Earnings-21 Tier 0 PII recall at least 85% (`scripts/earnings21_eval.py`): real speech,
   never tuned on, as a guard against fitting the synthetic phrasing.

---

## 1. Template files

One YAML file per conversation template.

```yaml
id: sales-renewal-01            # unique within the set
domain: sales                   # sales | support | internal | other
speakers:                       # speaker keys used in turns -> role description (free text)
  A: account manager at the vendor
  B: buyer at the client
turns:
  - A: "Hi {person#1.first}, can you hear me okay?"
  - B: "Yep, loud and clear."
  - A: "Great. So the {client#1} renewal comes up in {date}."
  - B: "Right. We did {amount} with you last year, about {percent} over plan."
  - B: "Honestly [[TOPIC: we are cutting the regional sales team in March]]."
  - split:                       # one value split across two turns with an interruption
      speaker: B
      slot: "phone#2"
      before: "my direct line is"
      interrupt: { speaker: A, text: "mm-hmm, go on" }
      after: "if you need me."
```

- `speakers` keys (`A`, `B`, ...) are placeholders: the generator gives each speaker a fake
  person name (labelled PERSON) or a generic label like `Speaker 1`.
- Each turn is either `- KEY: "text"` or a `split:` block.
- Write natural, varied speech: fillers ("um", "you know"), backchannels ("yeah", "mm-hmm"),
  small talk at the start and end, false starts, self-corrections. These matter for compression.

### Slots

`{type}` inserts a new random fake entity. `{type#k}` refers to entity `k` of that type within
the transcript (same value every time it appears). `{type#k.form}` picks a surface form.

| Type | Category | Forms (default first) |
| --- | --- | --- |
| `person` | PERSON | `full`, `first`, `last`, `asr` |
| `client` | CLIENT | `full`, `alias`, `asr`, `lower` |
| `brand` | BRAND | `full`, `alias`, `asr`, `lower` |
| `brand_common` | BRAND (form `word`: NOT sensitive) | `full`, `word` |
| `product` | PRODUCT | `full`, `alias`, `asr`, `lower` |
| `codename` | CODENAME | `full`, `alias`, `asr`, `lower` |
| `location` | LOCATION | `full` |
| `email` | EMAIL | `written`, `spoken` |
| `phone` | PHONE | `written`, `spoken`, `corrected` |
| `ssn` | SSN | `written`, `spoken` |
| `card` | CARD | `written`, `spoken` |
| `ip` | IP | `written` |
| `url` | URL | `written`, `spoken` |
| `account` | ACCOUNT | `written`, `spoken` |
| `order` | ACCOUNT | `written`, `spoken` |
| `date` | DATE | `written` |
| `amount` | AMOUNT | `written`, `spoken` |
| `percent` | PERCENT | `written`, `spoken` |
| `quantity` | QUANTITY | `written`, `spoken` (number only; put the unit in the text: `{quantity} stores`) |

- `asr`: a speech-recognition-style misspelling of the name (e.g. split into two common words,
  phonetic substitutions). Taken from the pool's `asr` list if present, otherwise generated.
- `alias`: one of the pool entry's aliases. `lower`: the name in lower case.
- `brand_common`: brands that are ordinary words. `{brand_common#1}` is the brand (sensitive);
  `{brand_common#1.word}` is the same word used in its ordinary sense (NOT sensitive, it tests
  precision), e.g. "the {brand_common#1.word} was busy this morning".
- `phone.corrected`: a self-correction such as "five five five... sorry, five five six, one two
  one two"; the whole utterance is labelled.
- `[[TOPIC: ...]]` marks a sensitive business topic (whole sentence). Slots inside it are filled.
- `[[CATEGORY: literal]]` or `[[CATEGORY#id: literal]]` labels a phrase written exactly as
  given, for phrasings the slot forms do not cover ("up a third", "our Leeds store"). Any
  category from section 2 is allowed. `[[NEG: literal]]` marks text that must not be redacted.
- Business figures (`amount`, `percent`, `quantity`) should usually sit near trigger words
  ("ARR", "margin", "revenue", "units", "stores", "pipeline", "budget", "headcount"), but also
  sometimes appear without them.
- Literal braces in text: write `{{` and `}}`.

### Pools (`eval/pools/<set>/pools.yaml`)

```yaml
first_names: [Orla, Bram, ...]
last_names: [Venkatesh, Okafor, ...]
clients:
  - { name: Quillfeather Analytics, aliases: [Quillfeather, QFA], asr: [quill feather] }
brands:
  - { name: Corvanta, aliases: [], asr: [core vanta] }
brand_common:                   # brands that are ordinary words
  - { name: Lantern, word: lantern, context: [app, subscription, store, account] }
products:
  - { name: Ledgerline Pro, aliases: [Ledgerline], asr: [ledger line] }
codenames:
  - { name: Project Bluefin, aliases: [Bluefin], asr: [blue fin] }
locations: [Port Ellery, Halvard County, ...]
email_domains: [quillfeather.example, mailbox.test, ...]
url_hosts: [quillfeather.example, corvanta.test]
```

Use invented names only: they must not be real companies, products or well-known people.
Prefer `.example`, `.test` or `.invalid` domains.

The generator builds the set's deny-list from the pools (clients, brands, brand_common with
`common_word: true` and its `context` words, products, codenames, and a fraction of people), so
some people are "known" (in the deny-list) and others are unknown. Labels record which.

---

## 2. Inline markup (tuning-hw, user set and private sets)

A plain-text transcript (`.txt`) in any of the speaker-line shapes the txt adapter accepts,
with sensitive spans marked inline:

```text
[[PERSON:Orla Venkatesh]]: Morning. Is [[PERSON#2:Bram]] joining?
[[PERSON#2:Bram Okafor]]: I'm here. The [[CLIENT:Quillfeather]] deal closed at [[AMOUNT:$42,000]].
[[PERSON:Orla Venkatesh]]: Great. Their [[BRAND:Lantern]] app is down, but the [[NEG:lantern]] on my desk works.
```

- `[[CATEGORY:text]]`: a labelled span. Categories: PERSON, CLIENT, BRAND, PRODUCT, CODENAME,
  ORG, LOCATION, EMAIL, PHONE, SSN, CARD, IP, URL, ACCOUNT, DATE, AMOUNT, PERCENT, QUANTITY,
  TOPIC.
- `[[CATEGORY#id:text]]`: links mentions of the same entity (for placeholder-consistency
  checks). Without `#id`, identical text in the same category counts as the same entity.
- `[[NEG:text]]`: optional; marks text that must NOT be redacted (counts toward precision).
- Markup may appear in speaker labels.
- An optional `denylist.yaml` next to the transcripts is used as the deny-list for that folder
  (same format as the main config's deny-list section).

The eval runner strips the markup, computes offsets, and evaluates exactly like generated data.

---

## 3. Generated label files

For each generated transcript `name.<ext>` the generator writes `name.labels.json`:

```json
{
  "id": "tuning-0007", "set": "tuning", "seed": 1234, "template": "sales-renewal-01",
  "format": "vtt", "casing": "lower",
  "labels": [
    {"turn": 3, "field": "text", "start": 10, "end": 22, "category": "CLIENT",
     "entity": "client#1", "form": "asr", "known": true}
  ],
  "negatives": [{"turn": 5, "field": "text", "start": 4, "end": 11}]
}
```

Offsets are character offsets into the turn's text (or speaker label) as parsed by the adapter.
Label files contain synthetic text only.
