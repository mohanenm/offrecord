"""Core data model.

Turn and Document come out of adapters. Field/View are what detectors scan. Span is what
detectors return. All are immutable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from offrecord.offsets import OffsetChain, OffsetMap

_EMPTY: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Turn:
    index: int
    speaker: str | None
    text: str
    start: float | None = None
    end: float | None = None
    source_lines: tuple[int, int] | None = None
    extra: Mapping[str, str] = field(default=_EMPTY)


@dataclass(frozen=True, slots=True)
class Document:
    format: str
    turns: tuple[Turn, ...]
    metadata: Mapping[str, str] = field(default=_EMPTY)
    free_text_extras: frozenset[str] = frozenset()
    # Adapter-private structure needed to re-render the original format (e.g. the JSON
    # skeleton). Holds no scannable text: any text in the input is in turns or metadata.
    render_state: Any = None


FieldId = tuple[str, ...]
FieldKind = Literal["text", "speaker", "meta", "extra"]


def turn_field_id(turn: int, part: str, *rest: str) -> FieldId:
    return ("turn", str(turn), part, *rest)


def meta_field_id(key: str) -> FieldId:
    return ("meta", key)


Projector = OffsetMap | OffsetChain


@dataclass(frozen=True, slots=True)
class View:
    text: str
    to_orig: Projector


@dataclass(frozen=True, slots=True)
class Field:
    id: FieldId
    kind: FieldKind
    turn: int | None
    orig: str
    views: Mapping[str, View]

    def view(self, name: str) -> View:
        return self.views[name]

    def project(self, view: str, start: int, end: int) -> tuple[int, int]:
        if view == "orig":
            return start, end
        return self.views[view].to_orig.project(start, end)


@dataclass(frozen=True, slots=True)
class Span:
    field: FieldId
    start: int
    end: int
    category: str
    detector: str
    confidence: float
    entity: str | None = None
    reason: str = ""
    action: Literal["redact", "flag"] = "redact"
    view: str = "orig"

    def __post_init__(self) -> None:
        if not (0 <= self.start <= self.end):
            raise ValueError("span offsets out of order")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be in [0, 1]")


# --- Category registry -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Category:
    name: str
    prefix: str
    priority: int  # tie-break in the merger: higher wins


_CATEGORIES: dict[str, Category] = {}
_PREFIX_RE = re.compile(r"^[A-Z]{1,6}$")


def register_category(name: str, prefix: str, priority: int = 50) -> Category:
    if not _PREFIX_RE.match(prefix):
        raise ValueError("placeholder prefix must be 1-6 uppercase ASCII letters")
    existing = _CATEGORIES.get(name)
    if existing is not None:
        if existing.prefix != prefix:
            raise ValueError("category already registered with another prefix")
        return existing
    if any(c.prefix == prefix for c in _CATEGORIES.values()):
        raise ValueError("placeholder prefix already in use")
    cat = Category(name, prefix, priority)
    _CATEGORIES[name] = cat
    return cat


def category(name: str) -> Category:
    return _CATEGORIES[name]


def categories() -> Mapping[str, Category]:
    return MappingProxyType(_CATEGORIES)


# Priorities: validated, structured identifiers beat names, which beat business context.
for _name, _prefix, _prio in [
    ("SSN", "SSN", 90),
    ("CARD", "CC", 90),
    ("EMAIL", "EMAIL", 85),
    ("PHONE", "PH", 80),
    ("IP", "IP", 80),
    ("URL", "URL", 75),
    ("ACCOUNT", "ACCT", 70),
    ("PERSON", "P", 65),
    ("CLIENT", "C", 64),
    ("BRAND", "B", 63),
    ("PRODUCT", "PR", 62),
    ("CODENAME", "X", 61),
    ("ORG", "O", 60),
    ("LOCATION", "L", 55),
    ("DATE", "D", 40),
    ("AMOUNT", "AMT", 45),
    ("PERCENT", "PCT", 44),
    ("QUANTITY", "QTY", 43),
    ("NAME", "N", 30),  # unknown name-like word (Tier 0 heuristic)
    ("INDIRECT", "IND", 35),  # indirect identifier (Tier 2)
    ("TOPIC", "TOPIC", 10),
]:
    register_category(_name, _prefix, _prio)

# How much a leak hurts, for evaluation. High: identifies or harms a person directly (and the
# speaker labels, which are names). Low: hard to act on alone. Everything else is medium.
SEVERITY_HIGH = frozenset({"SSN", "CARD", "ACCOUNT", "EMAIL", "PHONE", "PERSON", "SPEAKER"})
SEVERITY_LOW = frozenset({"DATE", "PERCENT", "QUANTITY"})


def severity(category_name: str) -> str:
    if category_name in SEVERITY_HIGH:
        return "high"
    return "low" if category_name in SEVERITY_LOW else "medium"
