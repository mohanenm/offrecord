"""Extra evaluation metrics that need the full redact/restore/compress stack:
placeholder consistency, restore accuracy, token reduction per compression level, and a check
that compression never removed a placeholder."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from offrecord.compress import PLACEHOLDER, compress
from offrecord.evaluation.runner import EvalItem
from offrecord.output import render_compact
from offrecord.redact.config import from_mapping
from offrecord.redact.pipeline import redact_document
from offrecord.restore import restore_text
from offrecord.tokens import TokenCounter, get_counter


@dataclass
class Extras:
    name: str
    entities: int = 0
    consistent: int = 0
    turns: int = 0
    restored_exact: int = 0
    tokens_original: int = 0
    tokens: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    placeholders_lost: int = 0
    tokenizer: str = "approx"

    def to_json(self) -> dict[str, Any]:
        red = {lvl: (1 - n / self.tokens_original) if self.tokens_original else None
               for lvl, n in self.tokens.items()}  # fmt: skip
        return {
            "name": self.name,
            "placeholder_consistency": self.consistent / self.entities if self.entities else None,
            "restore_accuracy": self.restored_exact / self.turns if self.turns else None,
            "tokenizer": self.tokenizer,
            "tokens_original": self.tokens_original,
            "tokens_by_level": dict(self.tokens),
            "token_reduction_by_level": red,
            "placeholders_lost_by_compression": self.placeholders_lost,
        }


def _norm(s: str) -> str:
    return " ".join(s.casefold().split())


def _original_text(doc) -> str:
    return "\n".join(f"{t.speaker}: {t.text}" if t.speaker else t.text for t in doc.turns)


def run_extras(items: Sequence[EvalItem], config: dict[str, Any], name: str,
               counter: TokenCounter | None = None) -> Extras:  # fmt: skip
    cfg = from_mapping(config)
    counter = counter or get_counter("auto")
    ex = Extras(name, tokenizer=counter.name)
    for it in items:
        red, _spans, mapping, _ = redact_document(it.doc, cfg)
        # Consistency: every gold entity that was caught maps to exactly one placeholder.
        by_entity: dict[str, set[str]] = defaultdict(set)
        for lb in it.labels:
            t = red.turns[lb.turn]
            # Placeholders that replaced this label: find spans covering it in the redacted text
            # by re-locating through the mapping occurrences is fragile; use the label's surface.
            src = (
                it.doc.turns[lb.turn].text
                if lb.field == "text"
                else (it.doc.turns[lb.turn].speaker or "")
            )
            surface = src[lb.start : lb.end]
            phs = {ph for ph, e in mapping.entries.items() if surface in e.occurrences}
            target = t.text if lb.field == "text" else (t.speaker or "")
            phs = {p for p in phs if p in target}
            if phs:
                by_entity[lb.entity] |= phs
        for phs in by_entity.values():
            ex.entities += 1
            ex.consistent += int(len(phs) == 1)
        # Restore accuracy: redacted turn text restored with the mapping equals the original
        # (case- and whitespace-insensitive; restore uses each entity's first surface form).
        payload = mapping.to_payload()
        for orig, r in zip(it.doc.turns, red.turns, strict=True):
            restored, _rep = restore_text(r.text, payload, mapping.id)
            ex.turns += 1
            ex.restored_exact += int(_norm(restored) == _norm(re.sub(r"<\\", "<", orig.text)))
        # Tokens per compression level.
        ex.tokens_original += counter.count(_original_text(it.doc))
        before = {p for t in red.turns for p in PLACEHOLDER.findall(t.text)}
        for level in ("none", "light", "medium"):
            small, _ = compress(red, level)
            ex.tokens[level] += counter.count(render_compact(small, mapping.id))
            after = {p for t in small.turns for p in PLACEHOLDER.findall(t.text)}
            ex.placeholders_lost += len(before - after)
    return ex


def format_extras(rows: Sequence[Extras]) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x * 100:.1f}%"

    lines = [
        f"{'set':<10} {'consistency':>12} {'restore':>9} {'light':>8} {'medium':>8} {'lost':>5}"
    ]
    for r in rows:
        j = r.to_json()
        red = j["token_reduction_by_level"]
        lines.append(
            f"{r.name:<10} {pct(j['placeholder_consistency']):>12} {pct(j['restore_accuracy']):>9} "
            f"{pct(red.get('light')):>8} {pct(red.get('medium')):>8} {r.placeholders_lost:>5}"
        )
    lines.append(f"token reduction vs the original transcript ({rows[0].tokenizer}, estimate)")
    return "\n".join(lines)
