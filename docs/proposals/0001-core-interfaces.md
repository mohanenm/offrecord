# Proposal 0001: core interfaces (turn model, adapters, detectors, span format)

Status: **approved by the user, 2026-11-24** (D-020).

## Summary (the short version)

1. **Turn model**: `Document(format, turns, metadata)`; `Turn(index, speaker, text, start, end,
   source_lines, extra)`. Frozen dataclasses. Adapters never see anything but text.
2. **Fields**: every piece of scannable text (each turn's text, each speaker label, each metadata
   value, free-text adapter extras) becomes a `Field` with a stable id and one or more **views**:
   `orig` (verbatim) and `norm` (spoken forms canonicalised), each with an offset map back to
   `orig`.
3. **Spans live in original-field coordinates.** Detectors may emit spans against any view; the
   core projects them back to `orig` immediately, expanding to whole source tokens on partial
   overlap (recall first). Merger, replacement, audit and restore only ever see `orig`
   coordinates.
4. **Span** = `(field, start, end, category, detector, confidence)` + `entity` (canonical key
   so the same entity gets the same placeholder, including fragments split across turns),
   `reason` (content-free code) and `action` (`redact` | `flag`).
5. **Detectors** get turn-based `Window`s (N turns + k turns of read-only look-back for split
   PII), are instantiated per document (may keep state across windows), and only ever *add*
   spans. **Confidence never suppresses a redaction**; it only drives review flags.
6. **Plug-ins**: built-in registry, plus third-party adapters/detectors via entry points that
   load **only if named in the config** (an installed package cannot silently inject code into a
   sensitive pipeline).

## 1. Turn model

```python
@dataclass(frozen=True, slots=True)
class Turn:
    index: int                        # 0-based position in the document
    speaker: str | None               # raw label as parsed (sensitive; scanned as a field)
    text: str                         # turn text, markup stripped, lines joined with "\n"
    start: float | None = None        # seconds
    end: float | None = None
    source_lines: tuple[int, int] | None = None   # 1-based inclusive line range in the input
    extra: Mapping[str, str] = {}     # adapter-private round-trip data (cue id, VTT settings...)

@dataclass(frozen=True, slots=True)
class Document:
    format: str                       # adapter name
    turns: tuple[Turn, ...]
    metadata: Mapping[str, str]       # headers, NOTE blocks, JSON top-level strings, title
    free_text_extras: frozenset[str]  # keys of Turn.extra that may hold free text (scanned)
```

- Speaker unification happens in normalisation (`"JOHN SMITH"`, `"John Smith:"`, `"john smith"`
  → one speaker key; case- and punctuation-insensitive). The raw label stays on the turn.
- The input file is decoded once (strict UTF-8, CRLF→LF) before parsing; `source_lines` point
  into that decoded text, which is what the audit log references.

## 2. Fields and views (normalisation + offset map)

```python
FieldId = tuple[str, ...]    # ("meta","title") | ("turn","12","speaker") | ("turn","12","text")
                              # | ("turn","12","extra","cue_id")

@dataclass(frozen=True, slots=True)
class View:
    text: str
    to_orig: OffsetMap        # maps view index ranges -> orig index ranges

@dataclass(frozen=True, slots=True)
class Field:
    id: FieldId
    kind: Literal["text", "speaker", "meta", "extra"]
    turn: int | None
    orig: str
    views: Mapping[str, View]   # always "orig" (identity map) and "norm"
```

`OffsetMap` stores only the edited regions as sorted `(view_start, view_end, orig_start,
orig_end)` runs; identity elsewhere. O(edits) memory, O(log edits) lookup.

`norm` view rules (all case-independent; case is preserved, never relied upon):
- digit words → digits only in runs (≥3 number words, or ≥2 next to a trigger such as "number",
  "phone", "card", "account", "zip"); "oh"/"o" as zero only inside such runs; "double five" →
  "55"; "triple"; teens/tens ("fifty five") inside runs.
- "at"/"dot" → `@`/`.` only when the result forms a plausible email/URL shape.
- spelled letters ("S as in Sam", "s-m-i-t-h", "S M I T H") → joined letters.
- whitespace collapsed, Unicode NFKC, smart quotes/dashes folded.

Why two views and not one rewritten text: normalisation can be wrong ("one two three ideas").
Detectors that work better on verbatim text use `orig`; spoken-form detectors use `norm`; both
can run. Redacting the union is recall-first.

## 3. Span format

```python
@dataclass(frozen=True, slots=True)
class Span:
    field: FieldId
    start: int                 # half-open, in the given view's coordinates
    end: int
    category: str              # from the category registry (below)
    detector: str              # detector name, e.g. "regex.phone", "denylist"
    confidence: float          # 0..1; 1.0 = validated (Luhn ok, exact deny-list hit)
    entity: str | None = None  # canonical key, e.g. "5551212", "deny#12"; memory only, sensitive
    reason: str = ""           # content-free code, e.g. "luhn", "deny:fuzzy:88:#12"
    action: Literal["redact", "flag"] = "redact"
    view: str = "orig"         # detectors may emit against "norm"; core projects to "orig"
```

Projection: a `norm` span maps to the smallest `orig` range covering every source character of
the view characters it touches; partial overlap with an edited run expands to the whole run.

Cross-turn / interrupted PII (`"my number is five five five"` / `B: "mm-hmm"` /
`A: "one two one two"`): detectors see look-back context, emit one span per field fragment, and
give all fragments the same `entity`. They share a placeholder; the mapping records each
occurrence's surface form so a headed transcript restores exactly.

Merger (Phase 3): union all spans per field; on overlap the longest span wins (ties: higher
confidence, then category priority); adjacent spans with the same entity merge. A span that wins
inherits the lowest confidence of the spans it absorbed, for review flagging.

`action="flag"`: the span is a review flag only (e.g. a topic sentence when topic mode is
`flag`). Default topic mode is to be decided in Phase 3 (recall-first suggests `redact`).

### Category registry (initial)

| Category | Placeholder | Category | Placeholder |
| --- | --- | --- | --- |
| PERSON | `<P1>` | EMAIL | `<EMAIL1>` |
| CLIENT | `<C1>` | PHONE | `<PH1>` |
| BRAND | `<B1>` | SSN | `<SSN1>` |
| PRODUCT | `<PR1>` | CARD | `<CC1>` |
| CODENAME | `<X1>` | IP | `<IP1>` |
| ORG (unknown, Tier 1) | `<O1>` | URL | `<URL1>` |
| LOCATION | `<L1>` | ACCOUNT (account/order/ID) | `<ACCT1>` |
| AMOUNT (currency) | `<AMT1>` | DATE (when configured) | `<D1>` |
| PERCENT / QUANTITY (business) | `<PCT1>` / `<QTY1>` | TOPIC (sentence) | `<TOPIC1>` |

Categories are strings in a registry so plug-ins can add new ones with their own prefix.

## 4. Detector interface

```python
@dataclass(frozen=True)
class Window:
    fields: Sequence[Field]     # fields to scan (this window's turns; metadata in window 0)
    context: Sequence[Field]    # previous k turns' fields, read-only look-back (default k=3)
    index: int                  # window number

class Detector(Protocol):
    name: ClassVar[str]
    categories: ClassVar[frozenset[str]]
    def detect(self, window: Window) -> Iterable[Span]: ...

class DetectorFactory(Protocol):
    def __call__(self, config: Mapping[str, Any]) -> Detector: ...   # one instance per document
```

Rules: detectors are pure functions of their input plus per-document state; never do I/O at
detect time (config/deny-lists are loaded in the factory); never log text. Default window is
50 turns (memory stays flat whatever the transcript length). Spans found inside `context` are
allowed (the merger dedupes). Tier 1/2 detectors implement the same interface and receive only
gated fields.

## 5. Adapter interface

```python
class Adapter(Protocol):
    name: ClassVar[str]                    # "txt", "vtt", "srt", "json"
    extensions: ClassVar[tuple[str, ...]]
    def sniff(self, head: str) -> float: ...                    # 0..1, for auto-detection
    def parse(self, text: str, limits: Limits) -> Document: ...
    def render(self, doc: Document) -> str: ...                 # doc with replaced text -> format
```

Adapters never open files (the core reads with size caps and strict decoding), must enforce
`limits` (turn count, turn length, JSON depth), and raise `InputFormatError` with a line or
offset, never content. `render` re-emits the original format from a `Document` whose texts,
speakers and metadata have already been replaced; compact plain text output is core code, not
an adapter.

## Alternatives considered

| Choice | Alternative | Why not (reversibility) |
| --- | --- | --- |
| Spans per field | One global coordinate space over the whole doc | Cross-turn spans would need splitting anyway for replacement and original-format output; global offsets complicate windowing. Hard to reverse later. |
| Two views (`orig` + `norm`) | Single normalised text | One wrong normalisation would hide PII from every detector. Medium to reverse. |
| Project to `orig` in core | Detectors handle offsets | Every detector would reimplement offset logic; bugs would leak. |
| Confidence only flags | Drop spans below a threshold | Lowers recall; contradicts recall-first. Could be added later as an opt-in. |
| Config-gated entry points | Load every installed plug-in | Silent code injection into a sensitive pipeline. Easy to relax later. |
| Frozen dataclasses | Dicts / pydantic | No extra dependency; typed; cheap. Easy to reverse. |
| Speaker labels always scanned as PERSON unless generic ("Speaker 1", "Agent") | Only scan via deny-list | Speaker labels are nearly always names; recall-first. Easy to tune. |
