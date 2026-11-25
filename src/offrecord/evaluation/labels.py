"""Gold labels and the inline-markup format used by hand-written evaluation sets.

Markup: ``[[CATEGORY:text]]``, ``[[CATEGORY#id:text]]``, ``[[NEG:text]]`` inside a plain-text
transcript (see docs/eval-format.md). Markup may appear in speaker labels.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from offrecord.errors import InputFormatError
from offrecord.model import categories

MARKUP_RE = re.compile(r"\[\[(?P<cat>[A-Z]+)(?:#(?P<id>[\w.-]+))?:(?P<text>.*?)\]\]", re.DOTALL)


@dataclass(frozen=True)
class Label:
    turn: int
    field: str  # "text" | "speaker"
    start: int
    end: int
    category: str
    entity: str
    form: str = ""
    known: bool | None = None  # in the deny-list? (None: not applicable)

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Label:
        return cls(**d)


@dataclass(frozen=True)
class Negative:
    turn: int
    field: str
    start: int
    end: int

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LabelledTranscript:
    """A transcript's turns (speaker, text) plus gold labels, independent of file format."""

    id: str
    speakers: list[str | None]
    texts: list[str]
    labels: list[Label] = field(default_factory=list)
    negatives: list[Negative] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def strip_markup(s: str, turn: int, field_name: str) -> tuple[str, list[Label], list[Negative]]:
    """Remove inline markup from ``s``; return clean text and labels with clean offsets."""
    out: list[str] = []
    labels: list[Label] = []
    negs: list[Negative] = []
    pos = 0
    clean_len = 0
    known = set(categories())
    for m in MARKUP_RE.finditer(s):
        out.append(s[pos : m.start()])
        clean_len += m.start() - pos
        text = m.group("text")
        cat = m.group("cat")
        start, end = clean_len, clean_len + len(text)
        if cat == "NEG":
            negs.append(Negative(turn, field_name, start, end))
        elif cat in known:
            ent = m.group("id") or f"{cat}:{text.casefold()}"
            labels.append(Label(turn, field_name, start, end, cat, f"{cat}#{ent}"))
        else:
            raise InputFormatError(f"unknown label category in markup (turn {turn})")
        out.append(text)
        clean_len = end
        pos = m.end()
    out.append(s[pos:])
    clean = "".join(out)
    if "[[" in clean and "]]" in clean:
        raise InputFormatError(f"unbalanced or malformed markup (turn {turn})")
    return clean, labels, negs
