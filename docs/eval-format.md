# Evaluation data formats

All evaluation data is **synthetic**. Never put real names, companies, numbers or transcripts
in any of these files.

There are three kinds of evaluation input:

1. **Templates + pools** (`eval/templates/<set>/*.yaml`, `eval/pools/<set>/pools.yaml`): the
   synthetic generator fills templates with fake entities, deterministically from a seed, and
   writes labelled transcripts in every supported format.
2. **Labelled transcripts in inline markup** (`eval/user_set/`, or a private folder outside the
   repo): hand-written fake transcripts where sensitive spans are marked inline.
3. **Generated sets** (`eval/generated/<set>/`): output of the generator; reproducible, so they
   can be regenerated from templates, pools and seed.

Sets: `tuning` (detectors are tuned on it), `tuning-hw` (`eval/tuning_handwritten/`: 20
hand-written transcripts in inline markup by an independent agent; tuned on), `heldout` (frozen,
never tuned on; its templates and pools were written by a separate agent that never saw detector
code), `user` (the user's own hand-written set; never tuned on).

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
- `[[TOPIC: ...]]` marks a sensitive business topic (whole sentence).
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

## 2. Inline markup (user set and private hand-labelled sets)

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
