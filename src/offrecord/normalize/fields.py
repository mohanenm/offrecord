"""Build scannable Fields from a Document and group them into turn-based Windows."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from offrecord.adapters._common import speaker_key
from offrecord.model import Document, Field, FieldKind, View, meta_field_id, turn_field_id
from offrecord.normalize.spoken import normalize
from offrecord.offsets import OffsetMap


def make_field(fid: tuple[str, ...], kind: FieldKind, turn: int | None, text: str) -> Field:
    norm_text, chain = normalize(text)
    views = {
        "orig": View(text, OffsetMap.identity(len(text))),
        "norm": View(norm_text, chain),
    }
    return Field(fid, kind, turn, text, MappingProxyType(views))


def metadata_fields(doc: Document) -> list[Field]:
    return [make_field(meta_field_id(k), "meta", None, v) for k, v in doc.metadata.items() if v]


def turn_fields(doc: Document, index: int) -> list[Field]:
    t = doc.turns[index]
    out = []
    if t.speaker:
        out.append(make_field(turn_field_id(index, "speaker"), "speaker", index, t.speaker))
    if t.text:
        out.append(make_field(turn_field_id(index, "text"), "text", index, t.text))
    for k in sorted(doc.free_text_extras):
        v = t.extra.get(k)
        if v:
            out.append(make_field(turn_field_id(index, "extra", k), "extra", index, v))
    return out


def build_fields(doc: Document) -> list[Field]:
    """All fields of a document (metadata first, then turns in order). For small documents and
    tests; the pipeline uses :func:`iter_windows` so memory stays flat."""
    fields = metadata_fields(doc)
    for i in range(len(doc.turns)):
        fields.extend(turn_fields(doc, i))
    return fields


def speaker_keys(doc: Document) -> dict[int, str]:
    """Unified speaker key per turn index (case/punctuation/spacing-insensitive)."""
    return {t.index: speaker_key(t.speaker) for t in doc.turns if t.speaker}


@dataclass(frozen=True)
class Window:
    """A unit of detection: ``fields`` to scan plus read-only look-back ``context``."""

    index: int
    fields: Sequence[Field]
    context: Sequence[Field]


DEFAULT_WINDOW_TURNS = 50
DEFAULT_LOOKBACK_TURNS = 3


def iter_windows(
    doc: Document,
    size: int = DEFAULT_WINDOW_TURNS,
    lookback: int = DEFAULT_LOOKBACK_TURNS,
) -> Iterator[Window]:
    """Yield windows of ``size`` turns. Metadata fields go in window 0. Fields are built lazily
    per window, and only the look-back turns are carried over, so memory stays flat."""
    if size < 1 or lookback < 0:
        raise ValueError("window size must be >= 1 and lookback >= 0")
    n = len(doc.turns)
    carry: list[list[Field]] = []
    w = 0
    start = 0
    meta = metadata_fields(doc)
    if n == 0:
        if meta:
            yield Window(0, meta, [])
        return
    while start < n:
        end = min(n, start + size)
        per_turn = [turn_fields(doc, i) for i in range(start, end)]
        fields = [f for fs in per_turn for f in fs]
        if w == 0:
            fields = meta + fields
        context = [f for fs in carry for f in fs]
        yield Window(w, fields, context)
        carry = per_turn[-lookback:] if lookback else []
        start = end
        w += 1
