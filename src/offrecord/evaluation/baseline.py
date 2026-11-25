"""Baseline: plain regular expressions only (no normalisation, no deny-list, no context).

This is the comparison point the spec asks for; it is deliberately naive.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from offrecord.model import Document, Span, turn_field_id

PATTERNS = {
    "EMAIL": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "PHONE": re.compile(
        r"(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}\b|\b\d{3}-\d{4}\b"
    ),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CARD": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "IP": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "URL": re.compile(r"\bhttps?://\S+"),
}


def regex_baseline(config: dict[str, Any]) -> Any:
    def system(doc: Document) -> Sequence[Span]:
        out = []
        for t in doc.turns:
            for part, text in (("text", t.text), ("speaker", t.speaker or "")):
                for cat, rx in PATTERNS.items():
                    for m in rx.finditer(text):
                        out.append(Span(turn_field_id(t.index, part), m.start(), m.end(), cat,
                                        "baseline.regex", 1.0))  # fmt: skip
        return out

    return system


def null_system(config: dict[str, Any]) -> Any:
    """Redacts nothing: a sanity floor (recall must be 0)."""
    return lambda doc: []
