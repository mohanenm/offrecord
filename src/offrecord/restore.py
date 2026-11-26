"""Restore placeholders in redacted text (or an LLM's answer about it) using a mapping.

Tolerant of common LLM mangling: ``<c1>``, ``<C 1>``, ``<C1>'s``, ``&lt;C1&gt;`` (escaped, e.g.
from VTT output), markdown-escaped ``\\<C1>``, JSON-escaped angle brackets (backslash-u003c)
and bare ``C1``. Never restores anything ambiguous:

- One substitution pass over the input only: restored values are never scanned again.
- ASCII digits only. Bare forms (no brackets) must be the exact canonical token with at least
  three digits (upper case, no leading zero, e.g. ``P417``), so an answer's own ``P1`` or
  ``L2`` is never taken for a placeholder, and must not occur naturally in the redacted output
  (``bare_collisions``, computed on the final rendered output, metadata included).
- Placeholder-like tokens not in the mapping are left as they are and reported, including
  near misses (very long numbers).
- Source text escaped at redaction time (``<\\C1>``, ``&lt;\\C1&gt;``) is unescaped by exactly
  one backslash and never restored.
- Restored values are escaped for the input's format (JSON string escaping, VTT entities), so
  a value with quotes or angle brackets cannot break the document.

Wrong-mapping detection: if the input carries an offrecord header, its mapping id must match
(else MappingError). Header-less input (an LLM answer) is checked by its placeholders: if any
bracketed placeholder with a known prefix is missing from the mapping, nothing is restored
(MappingError). Placeholder numbers are random per mapping, so another call's answer almost
never fits.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from offrecord.errors import MappingError
from offrecord.model import categories

BS = re.escape(chr(92))  # a literal backslash in a regex
HEADER_RE = re.compile(
    r"(?:^(?:#|NOTE)?\s*offrecord\s+mapping=(?P<id>[\w-]+|none)\s*$)"
    r'|(?:"_offrecord_mapping"\s*:\s*"(?P<jid>[\w-]+|none)")',
    re.MULTILINE,
)
_D = "[0-9]"
_OPEN = f"(?:{BS}?<|&lt;|{BS}u003c)"
_CLOSE = f"(?:>|&gt;|{BS}u003e)"
TOKEN_RE = re.compile(
    # 1. escaped source text: one backslash is removed on restore; never restored
    f"(?P<esc>(?P<eo><|&lt;|{BS}u003c){BS}(?P<escrest>(?:{BS})*\\s*[A-Za-z]{{1,6}}\\s*{_D}+\\s*{_CLOSE}))"
    # 2. bracketed (tolerant)
    f"|(?P<br>{_OPEN}\\s*(?P<bp>[A-Za-z]{{1,6}})\\s*(?P<bn>{_D}+)\\s*{_CLOSE})"
    # 3. bare canonical upper-case token
    f"|(?P<bare>(?<![\\w<&]|{BS})(?P<rp>[A-Z]{{1,6}})(?P<rn>[1-9]{_D}{{2,5}})(?![\\w>]))"
)


@dataclass
class RestoreReport:
    restored: int = 0
    unknown: Counter[str] = field(default_factory=Counter)  # placeholder-like, not in mapping
    ambiguous: Counter[str] = field(default_factory=Counter)  # bare forms we refused
    header_id: str | None = None


def find_header_id(text: str) -> str | None:
    m = HEADER_RE.search(text[:8192])
    if not m:
        return None
    return m.group("id") or m.group("jid")


def sniff_format(text: str) -> str:
    head = text.lstrip("\ufeff \t\r\n")[:20]
    if head.startswith("WEBVTT"):
        return "vtt"
    if head[:1] in ("{", "["):
        try:
            json.loads(text)
        except ValueError:
            return "text"
        return "json"
    return "text"


def _escape_value(value: str, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(value, ensure_ascii=False)[1:-1]
    if fmt == "vtt":
        return html.escape(value, quote=False).replace("\n", " ")
    return value


def restore_text(
    text: str, payload: dict[str, Any], mapping_id: str, fmt: str | None = None
) -> tuple[str, RestoreReport]:
    rep = RestoreReport(header_id=find_header_id(text))
    if rep.header_id is not None and rep.header_id not in ("none", mapping_id):
        raise MappingError("this text was redacted with a different mapping (mapping id mismatch)")
    if rep.header_id == "none":
        raise MappingError("this text was redacted with --no-mapping; it cannot be restored")
    fmt = fmt or sniff_format(text)
    entries: dict[str, dict[str, Any]] = payload["entries"]
    lookup = {ph.upper(): e for ph, e in entries.items()}
    bare_blocked = {b.upper() for b in payload.get("bare_collisions", [])}

    if rep.header_id is None:
        prefixes = {c.prefix for c in categories().values()}
        prefixes |= {m.group("bp").upper() for ph in lookup if (m := TOKEN_RE.fullmatch(ph))}
        missing = sum(
            1
            for m in TOKEN_RE.finditer(text)
            if m.group("br")
            and m.group("bp").upper() in prefixes
            and len(m.group("bn")) <= 6
            and f"<{m.group('bp').upper()}{int(m.group('bn'))}>" not in lookup
        )
        if missing:
            raise MappingError(
                f"{missing} placeholder(s) in this text are not in this mapping, so it was "
                "probably redacted with a different one; nothing was restored"
            )

    def value(e: dict[str, Any]) -> str:
        occ = e.get("occurrences") or []
        return _escape_value(occ[0] if occ else e.get("canonical", ""), fmt)

    def sub(m: re.Match[str]) -> str:
        if m.group("esc"):
            return m.group("eo") + m.group("escrest")
        if m.group("br"):
            digits = m.group("bn")
            if len(digits) > 6:
                rep.unknown["<long placeholder-like token>"] += 1
                return m.group(0)
            key = f"<{m.group('bp').upper()}{int(digits)}>"
            e = lookup.get(key)
            if e is None:
                rep.unknown[key] += 1
                return m.group(0)
            rep.restored += 1
            return value(e)
        token = m.group("bare")
        key = f"<{token}>"
        if key not in lookup:
            return token  # just text that looks like an id ("Q3", "A4")
        if token in bare_blocked:
            rep.ambiguous[key] += 1
            return token
        rep.restored += 1
        return value(lookup[key])

    return TOKEN_RE.sub(sub, text), rep


def bare_collisions(rendered_texts: list[str], placeholders: list[str]) -> list[str]:
    """Bare tokens like ``C1`` that occur naturally in the final rendered output and would be
    ambiguous on restore (e.g. a room "P1" next to a real placeholder <P1>). Uses the same
    grammar as restore."""
    wanted = {p.strip("<>").upper() for p in placeholders}
    found = set()
    for t in rendered_texts:
        for m in TOKEN_RE.finditer(t):
            if m.group("bare") and m.group("bare") in wanted:
                found.add(m.group("bare"))
    return sorted(found)
