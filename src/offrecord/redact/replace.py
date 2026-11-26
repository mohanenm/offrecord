"""Placeholder assignment and replacement.

- Short, consistent placeholders: ``<C417>``, ``<P283>``, ``<AMT605>``. The same entity always
  maps to the same placeholder across the document (and across documents with
  ``--mapping-scope project``, by reusing one Mapping).
- Numbers are drawn at random (100-999, wider if a category runs out), not counted from 1. A
  bare ``P417`` almost never occurs in ordinary writing, where ``P1`` and ``L2`` do, and the
  set of placeholders in an LLM answer identifies the mapping it came from, so restore can
  refuse a wrong mapping instead of filling in another call's names.
- Entity resolution: spans with an explicit entity (deny-list entry, speaker name) register
  their surface tokens as aliases, seeded up front from the config and speaker labels, so a
  later bare "Okafor" gets the same placeholder as "Bram Okafor".
- Escaping: text that already looks like a placeholder (``<C1>``) is escaped as ``<\\C1>`` so
  restore can never confuse it with a real one.

The Mapping holds original values: it is the most sensitive artefact. It is never logged; it is
written only encrypted.
"""

from __future__ import annotations

import re
import secrets
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from offrecord.model import Document, FieldId, Span, category

FORMAT_VERSION = 1
PLACEHOLDER_LIKE = re.compile(r"<\s*([A-Za-z]{1,6})\s*([0-9]+)\s*>")
_BS = re.escape(chr(92))
# Escaping is one-to-one: any "<", optional backslashes, then a placeholder body gets one more
# backslash; restore removes exactly one. Digits are ASCII only, as in restore's grammar: a
# Unicode digit class would escape text with Arabic-Indic digits that restore never unescapes.
ESCAPABLE = re.compile(
    f"(<|&lt;|{_BS}u003c)((?:{_BS})*\\s*[A-Za-z]{{1,6}}\\s*[0-9]+\\s*(?:>|&gt;|{_BS}u003e))"
)
_CANON_STRIP = re.compile(r"[\W_]+", re.UNICODE)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_NUMERIC_CATEGORIES = {"PHONE", "SSN", "CARD", "ACCOUNT"}
_RNG = secrets.SystemRandom()
# A title before a name: "Mr. Whitfield" may be the speaker Dana Whitfield's husband, so it never
# takes a speaker's identity (restore would write the speaker's name into the answer).
_TITLE = r"(?:mr|mrs|ms|mx|dr|miss|prof)\.?\s+"
_TITLED = re.compile(f"(?i)^{_TITLE}\\S")
_TITLE_BEFORE = re.compile(f"(?i)\\b{_TITLE}$")
NAME_CATEGORIES = {"PERSON", "NAME", "CLIENT", "BRAND", "PRODUCT", "CODENAME", "ORG", "LOCATION"}


def new_mapping_id() -> str:
    """Random, content-free identifier (128 bits, base32)."""
    import base64

    raw = secrets.token_bytes(16)
    return "or1-" + base64.b32encode(raw).decode("ascii").rstrip("=").lower()


def canonical(cat: str, surface: str) -> str:
    if cat in _NUMERIC_CATEGORIES:
        d = "".join(c for c in surface if c.isdigit())
        if d:
            return d
    return _CANON_STRIP.sub(" ", surface.casefold()).strip()


@dataclass
class Entry:
    category: str
    canonical: str
    key: str = ""
    occurrences: list[str] = field(default_factory=list)


@dataclass
class Mapping:
    id: str = field(default_factory=new_mapping_id)
    version: int = FORMAT_VERSION
    scope: str = "document"
    entries: dict[str, Entry] = field(default_factory=dict)
    _by_key: dict[tuple[str, str], str] = field(default_factory=dict)
    _alias: dict[str, tuple[str, str]] = field(default_factory=dict)  # surface -> (cat, key)
    _used: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    _ambiguous: set[str] = field(default_factory=set)  # name parts shared by several people

    def mark_ambiguous(self, surface: str) -> None:
        c = canonical("PERSON", surface)
        self._ambiguous.add(c)
        self._alias.pop(c, None)

    def seed_alias(self, surface: str, cat: str, key: str) -> None:
        c = canonical(cat, surface)
        if c and c not in self._alias and c not in self._ambiguous:
            self._alias[c] = (cat, key)

    def resolve(self, span: Span, surface: str, before: str = "") -> tuple[str, str]:
        """(category, entity key) for a span.

        - Explicit entities (deny-list entry, speaker) are used as they are. Their aliases come
          only from the entry's own variants (seeded up front), never from the matched surface.
        - Name-like spans adopt a known entity only if the whole surface is an alias, or every
          distinctive word of it points at the same entity ("Ada Brownfield" does not become
          the speaker "Ada Quist").
        - Pattern categories key on the detector's canonical value, so a written and a spoken
          form of the same email/phone share one placeholder.
        """
        cat = span.category
        c = canonical(cat, surface)
        titled = bool(_TITLED.match(surface) or _TITLE_BEFORE.search(before))
        if titled and cat in NAME_CATEGORIES and not (span.entity or "").startswith("deny#"):
            return cat, "titled:" + c
        if span.entity and span.entity.startswith(("deny#", "spk:")):
            return cat, span.entity
        if cat in NAME_CATEGORIES:
            hit = self._alias.get(c)
            if hit is None:
                toks = [t for t in _TOKEN.findall(surface) if len(t) >= 3]
                hits = [self._alias.get(canonical(cat, t)) for t in toks]
                if toks and all(h is not None for h in hits) and len(set(hits)) == 1:
                    hit = hits[0]
            if hit is not None:
                return hit
            return cat, c
        return cat, span.entity or c

    def placeholder(self, cat: str, key: str, surface: str) -> str:
        prefix = category(cat).prefix
        ph = self._by_key.get((prefix, key))
        if ph is None:
            ph = f"<{prefix}{self._draw(prefix)}>"
            self._by_key[(prefix, key)] = ph
            self.entries[ph] = Entry(cat, canonical(cat, surface), key)
        e = self.entries[ph]
        if surface not in e.occurrences:
            e.occurrences.append(surface)
        return ph

    def _draw(self, prefix: str) -> int:
        used = self._used[prefix]
        lo, hi = 100, 999
        while hi - lo + 1 - sum(lo <= n <= hi for n in used) < 100:
            lo, hi = hi + 1, hi * 10 + 9  # 1000-9999, then 10000-99999
        while True:
            n = _RNG.randint(lo, hi)
            if n not in used:
                used.add(n)
                return n

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "scope": self.scope,
            "entries": {
                ph: {
                    "category": e.category,
                    "canonical": e.canonical,
                    "key": e.key,
                    "occurrences": e.occurrences,
                }
                for ph, e in self.entries.items()
            },
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> Mapping:
        m = cls(
            id=str(data["id"]),
            version=int(data["version"]),
            scope=str(data.get("scope", "document")),
        )
        for ph, e in data["entries"].items():
            m.entries[ph] = Entry(
                e["category"], e["canonical"], e.get("key", ""), list(e["occurrences"])
            )
            mt = PLACEHOLDER_LIKE.fullmatch(ph)
            if mt:
                prefix, n = mt.group(1), int(mt.group(2))
                m._used[prefix].add(n)
                m._by_key[(prefix, e.get("key") or e["canonical"])] = ph
        return m


@dataclass
class Stats:
    replaced: int = 0
    escaped: int = 0
    by_category: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # (span, placeholder) for every replacement: feeds the (encrypted, opt-in) audit log.
    records: list[tuple[Span, str]] = field(default_factory=list)


def escape_placeholder_like(text: str) -> tuple[str, int]:
    n = 0

    def esc(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return m.group(1) + chr(92) + m.group(2)

    return ESCAPABLE.sub(esc, text), n


def apply_to_text(text: str, spans: Sequence[Span], mapping: Mapping, stats: Stats) -> str:
    """Replace redaction spans (orig coordinates, non-overlapping) in ``text``."""
    out: list[str] = []
    pos = 0
    for s in sorted((s for s in spans if s.action == "redact"), key=lambda s: s.start):
        if s.start < pos:
            continue  # merged spans never overlap; defensive
        seg, n = escape_placeholder_like(text[pos : s.start])
        stats.escaped += n
        out.append(seg)
        surface = text[s.start : s.end]
        cat, key = mapping.resolve(s, surface, text[max(0, s.start - 8) : s.start])
        ph = mapping.placeholder(cat, key, surface)
        out.append(ph)
        stats.records.append((s, ph))
        stats.replaced += 1
        stats.by_category[cat] += 1
        pos = s.end
    seg, n = escape_placeholder_like(text[pos:])
    stats.escaped += n
    out.append(seg)
    return "".join(out)


def apply(doc: Document, spans: Iterable[Span], mapping: Mapping) -> tuple[Document, Stats]:
    """Return a redacted copy of ``doc``."""
    by_field: dict[FieldId, list[Span]] = defaultdict(list)
    for s in spans:
        by_field[s.field].append(s)
    stats = Stats()

    def red(fid: FieldId, text: str | None) -> str | None:
        if text is None:
            return None
        return apply_to_text(text, by_field.get(fid, []), mapping, stats)

    meta = {k: red(("meta", k), v) or "" for k, v in doc.metadata.items()}
    turns = []
    for t in doc.turns:
        i = str(t.index)
        extra = dict(t.extra)
        for k in doc.free_text_extras:
            if k in extra:
                extra[k] = red(("turn", i, "extra", k), extra[k]) or ""
        turns.append(
            replace(
                t,
                speaker=red(("turn", i, "speaker"), t.speaker),
                text=red(("turn", i, "text"), t.text) or "",
                extra=MappingProxyType(extra),
            )
        )
    return replace(doc, turns=tuple(turns), metadata=MappingProxyType(meta)), stats
