"""Review mode: side-by-side original vs redacted text, with review flags and (for labelled
data) misses highlighted, plus suggested deny-list entries for missed names.

The output contains original, sensitive text by design: it is for the user's own terminal (or
an owner-only file). It is never logged.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from dataclasses import dataclass

from offrecord.evaluation.labels import Label
from offrecord.model import Document, Span, turn_field_id

MISS_OPEN, MISS_CLOSE = "[[MISS:", "]]"
FLAG_OPEN, FLAG_CLOSE = "{{", "}}"
NAME_CATEGORIES = {"PERSON", "CLIENT", "BRAND", "PRODUCT", "CODENAME", "ORG", "LOCATION"}


@dataclass
class Miss:
    turn: int
    field: str
    category: str
    text: str


def _mark(text: str, marks: list[tuple[int, int, str, str]]) -> str:
    out, pos = [], 0
    for s, e, o, c in sorted(marks):
        if s < pos:
            continue
        out.append(text[pos:s])
        out.append(o + text[s:e] + c)
        pos = e
    out.append(text[pos:])
    return "".join(out)


def find_misses(doc: Document, spans: Sequence[Span], labels: Sequence[Label]) -> list[Miss]:
    from offrecord.evaluation.runner import _covered, _intervals

    ivs = _intervals([s for s in spans if s.view == "orig"])
    misses = []
    for lb in labels:
        t = doc.turns[lb.turn]
        src = t.text if lb.field == "text" else (t.speaker or "")
        full, _ = _covered(ivs.get(turn_field_id(lb.turn, lb.field), []), lb.start, lb.end, src)
        if not full:
            misses.append(Miss(lb.turn, lb.field, lb.category, src[lb.start : lb.end]))
    return misses


def side_by_side(
    original: Document,
    redacted: Document,
    spans: Sequence[Span],
    labels: Sequence[Label] = (),
    width: int = 60,
    threshold: float = 0.6,
) -> str:
    """Two columns: original (flags as {{...}}, misses as [[MISS:...]]) | redacted."""
    marks: dict[int, list[tuple[int, int, str, str]]] = {}
    for s in spans:
        if (
            s.field[0] == "turn"
            and s.field[2] == "text"
            and (s.action == "flag" or s.confidence < threshold)
        ):
            marks.setdefault(int(s.field[1]), []).append((s.start, s.end, FLAG_OPEN, FLAG_CLOSE))
    for m in find_misses(original, spans, labels):
        if m.field == "text":
            t = original.turns[m.turn].text
            i = t.find(m.text)
            if i >= 0:
                marks.setdefault(m.turn, []).append((i, i + len(m.text), MISS_OPEN, MISS_CLOSE))
    lines = [f"{'ORIGINAL (sensitive)':<{width}} | REDACTED", "-" * (width * 2 + 3)]
    for o, r in zip(original.turns, redacted.turns, strict=True):
        left = f"{o.speaker}: " if o.speaker else ""
        left += _mark(o.text, marks.get(o.index, []))
        right = (f"{r.speaker}: " if r.speaker else "") + r.text
        lw = textwrap.wrap(left, width) or [""]
        rw = textwrap.wrap(right, width) or [""]
        for i in range(max(len(lw), len(rw))):
            a = lw[i] if i < len(lw) else ""
            b = rw[i] if i < len(rw) else ""
            lines.append(f"{a:<{width}} | {b}")
        lines.append("")
    return "\n".join(lines)


# Categories the tool uses that are guesses rather than gold categories: a draft marks them
# (and review flags) as unresolved, so the labeller has to decide each one.
UNRESOLVED = {"NAME", "INDIRECT"}


def draft_markup(doc: Document, spans: Sequence[Span]) -> str:
    """The transcript as inline markup, pre-filled with the tool's redactions, for a person to
    correct into gold labels. Uncertain ones are written ``[[CAT?:text]]``, which the markup
    parser rejects until they are resolved."""
    from offrecord.errors import UsageError

    marks: dict[tuple[int, str], list[tuple[int, int, str, str]]] = {}
    # Redactions first; a review flag is kept only where it overlaps no redaction (markup
    # cannot nest, and a flagged sentence often contains redacted values).
    for sp in sorted(spans, key=lambda x: x.action != "redact"):
        if sp.view != "orig" or sp.field[0] != "turn" or sp.end <= sp.start:
            continue
        unsure = sp.action == "flag" or sp.category in UNRESOLVED
        if sp.action != "redact" and not unsure:
            continue
        have = marks.setdefault((int(sp.field[1]), str(sp.field[2])), [])
        if sp.action != "redact" and any(a < sp.end and sp.start < b for a, b, _, _ in have):
            continue
        have.append((sp.start, sp.end, f"[[{sp.category}{'?' if unsure else ''}:", "]]"))
    lines = []
    for t in doc.turns:
        if "[[" in t.text or "]]" in t.text or "[[" in (t.speaker or ""):
            raise UsageError(f"turn {t.index} contains '[[' or ']]', which inline markup reserves")
        text = _mark(t.text, marks.get((t.index, "text"), []))
        if t.speaker:
            text = f"{_mark(t.speaker, marks.get((t.index, 'speaker'), []))}: {text}"
        lines.append(text)
    return "\n".join(lines) + "\n"


def denylist_suggestions(misses: Sequence[Miss]) -> str:
    """YAML snippet with missed names, for the user to review and add to their deny-list."""
    seen: dict[str, str] = {}
    for m in misses:
        if m.category in NAME_CATEGORIES and m.text.strip():
            seen.setdefault(m.text.strip(), m.category)
    if not seen:
        return "# no missed names to suggest\n"
    import yaml

    entries = [{"name": n, "category": c, "aliases": []} for n, c in seen.items()]
    return (
        "# Suggested deny-list entries (review before use; this file is sensitive)\n"
        + yaml.safe_dump({"denylist": entries}, sort_keys=False, allow_unicode=True)
    )
