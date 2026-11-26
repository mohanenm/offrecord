"""Audit log entries (opt-in, always encrypted).

Each entry records what was removed or replaced: input number, field, original offsets and
source lines, category, detector, confidence, reason, action, the placeholder, and the original
text. The log is sensitive (it contains the removed text): it is only ever written encrypted,
to the private per-user store.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from offrecord.model import Document, Span
from offrecord.redact.replace import new_mapping_id


def new_id() -> str:
    return new_mapping_id().replace("or1-", "oa1-")


def _source(doc: Document, span: Span) -> tuple[str, list[int] | None]:
    fid = span.field
    if fid[0] == "meta":
        return doc.metadata.get(fid[1], ""), None
    t = doc.turns[int(fid[1])]
    lines = list(t.source_lines) if t.source_lines else None
    if fid[2] == "text":
        return t.text, lines
    if fid[2] == "speaker":
        return t.speaker or "", lines
    return t.extra.get(fid[3], ""), lines


def entries(input_index: int, doc: Document, records: Sequence[tuple[Span, str]],
            flags: Sequence[Span]) -> list[dict[str, Any]]:  # fmt: skip
    out = []
    for span, ph in records:
        src, lines = _source(doc, span)
        out.append({
            "input": input_index, "field": list(span.field), "start": span.start, "end": span.end,
            "source_lines": lines, "category": span.category, "detector": span.detector,
            "confidence": span.confidence, "reason": span.reason, "action": "replaced",
            "placeholder": ph, "original": src[span.start : span.end],
        })  # fmt: skip
    for span in flags:
        if span.action != "flag":
            continue
        src, lines = _source(doc, span)
        out.append({
            "input": input_index, "field": list(span.field), "start": span.start, "end": span.end,
            "source_lines": lines, "category": span.category, "detector": span.detector,
            "confidence": span.confidence, "reason": span.reason, "action": "flagged",
            "placeholder": None, "original": src[span.start : span.end],
        })  # fmt: skip
    return out
