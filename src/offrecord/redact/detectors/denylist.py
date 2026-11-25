"""Deny-list detector: user-maintained names with aliases, plus names from speaker labels.

Matching (all case-independent except ``case_sensitive`` entries):
- exact: same tokens (spacing and punctuation ignored, so "Quill-feather" == "Quill feather").
- fuzzy: rapidfuzz ratio on the spaced form (same word count) and on the joined form (word
  count +-1, so ASR splits like "quill feather" and merges like "quillfeather" match), plus a
  phonetic (Metaphone) match on the joined form. Thresholds favour recall.
- Very short variants (<= 4 characters) are exact-only; 5-6 characters need score >= 90.
- ``common_word`` entries ("Lantern", "Compass") default to exact matching and need one of
  their ``context`` words nearby (same turn or the two before it).

Speaker-label names become per-document PERSON entries (full name, last name, and first name
when it is not a common word), so participants mentioned in the text are caught.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import ClassVar

import jellyfish
from rapidfuzz import fuzz, process

from offrecord.adapters._common import GENERIC_SPEAKER_RE, speaker_key
from offrecord.model import Field, Span
from offrecord.normalize.fields import Window
from offrecord.redact.config import Config, DenyEntry
from offrecord.redact.wordlists import FUNCTION_WORDS, is_common

TOKEN_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)?")
MAX_WORDS = 5
PHONETIC_FLOOR = 70
CONTEXT_RADIUS = 12
GENERIC_RADIUS = 6
# Words that mark a common word as a brand when they sit right next to it ("Compass store",
# "calling Lantern", "Harbor card"). Used for common-word entries in addition to their own
# context words.
GENERIC_BRAND_CUES = frozenset(
    "support store stores app apps card cards account accounts customer customers team contract "
    "contracts deal rollout platform subscription subscriptions brand chain branch branches "
    "location locations calling partnership renewal pricing website site portal login product "
    "products service services plan license licence licenses rep reps inc corp ltd headquarters hq "
    "franchise outlet outlets membership loyalty rewards checkout invoice".split()
)


def _tokens(s: str) -> list[str]:
    return [t.lower().removesuffix("'s") for t in TOKEN_RE.findall(s)]


@dataclass(frozen=True)
class _Variant:
    entry: DenyEntry
    entity: str
    text: str  # original variant text (for case-sensitive matching)
    spaced: str
    joined: str
    words: int
    phon: str
    exact_only: bool
    min_score: int


def _ambiguous_tokens(names: Sequence[str]) -> set[str]:
    """Name parts shared by several speakers ("Wendell A." and "Wendell S."): never used as an
    alias for either, so a bare "Wendell" is not silently attributed to the wrong person."""
    seen: dict[str, str] = {}
    shared = set()
    for raw in names:
        key = speaker_key(raw)
        for tok in TOKEN_RE.findall(raw):
            t = tok.lower()
            if seen.get(t, key) != key:
                shared.add(t)
            seen.setdefault(t, key)
    return shared


def _speaker_entries(names: Sequence[str], start_index: int) -> list[tuple[DenyEntry, str]]:
    out = []
    shared = _ambiguous_tokens(names)
    for n, raw in enumerate(names):
        label = raw.strip()
        if not label or GENERIC_SPEAKER_RE.match(label):
            continue
        toks = TOKEN_RE.findall(label)
        aliases = []
        if len(toks) >= 2:
            # Every name part is an alias (common words included: "Sure, Mark."), except
            # function words and parts shared with another speaker. Common-word parts are
            # matched exactly, never fuzzily (see _Variant.exact_only).
            for tok in toks:
                t = tok.lower()
                if len(t) >= 2 and t not in FUNCTION_WORDS and t not in shared:
                    aliases.append(tok)
        elif len(toks) == 1 and toks[0].lower() in FUNCTION_WORDS:
            continue
        entry = DenyEntry(start_index + n, label, "PERSON", tuple(aliases), "fuzzy", 88)
        out.append((entry, f"spk:{speaker_key(label)}"))
    return out


class DenyListDetector:
    name: ClassVar[str] = "denylist"
    categories: ClassVar[frozenset[str]] = frozenset(
        {"CLIENT", "BRAND", "PRODUCT", "CODENAME", "PERSON", "ORG", "LOCATION"}
    )

    def __init__(self, config: Config) -> None:
        entries = []
        for e in config.denylist:
            if e.category == "PERSON":
                # Name parts of deny-listed people are aliases too ("Marigold" alone).
                parts = [t for v in e.variants() for t in TOKEN_RE.findall(v)
                         if len(t) >= 3 and t.lower() not in FUNCTION_WORDS]  # fmt: skip
                extra = tuple(p for p in parts if p not in e.aliases and p != e.name)
                e = DenyEntry(e.index, e.name, e.category, e.aliases + extra, e.match,
                              e.min_score, e.context, e.common_word)  # fmt: skip
            entries.append((e, f"deny#{e.index}"))
        entries += _speaker_entries(config.speaker_names, len(config.denylist))
        self.variants: list[_Variant] = []
        for e, ent in entries:
            for v in e.variants():
                toks = _tokens(v)
                if not toks:
                    continue
                joined = "".join(toks)
                exact_only = (
                    e.match != "fuzzy" or len(joined) <= 4 or (len(toks) == 1 and is_common(joined))
                )
                min_score = e.min_score if len(joined) > 6 else max(e.min_score, 90)
                self.variants.append(
                    _Variant(
                        e,
                        ent,
                        v,
                        " ".join(toks),
                        joined,
                        len(toks),
                        jellyfish.metaphone(joined),
                        exact_only,
                        min_score,
                    )
                )
        self.exact: dict[str, list[_Variant]] = defaultdict(list)
        self.by_phon: dict[str, list[_Variant]] = defaultdict(list)
        self.fuzzy: list[_Variant] = []
        for var in self.variants:
            self.exact[var.joined].append(var)
            if not var.exact_only:
                self.fuzzy.append(var)
                if len(var.phon) >= 3:
                    self.by_phon[var.phon].append(var)
        self.fuzzy_joined = [v.joined for v in self.fuzzy]
        self.max_words = min(MAX_WORDS, max((v.words for v in self.variants), default=0) + 1)
        self.min_cutoff = min((v.min_score for v in self.fuzzy), default=100)

    def detect(self, window: Window) -> Iterator[Span]:
        if not self.variants:
            return
        fields = list(window.fields)
        for idx, f in enumerate(fields):
            nearby = [fields[j] for j in range(max(0, idx - 2), idx)]
            for view in ("orig", "norm"):
                yield from self._scan(f, view, nearby)

    def _scan(self, f: Field, view: str, nearby: list[Field]) -> Iterator[Span]:
        text = f.views[view].text
        toks = [
            (m.start(), m.end(), m.group().lower().removesuffix("'s"))
            for m in TOKEN_RE.finditer(text)
        ]
        n = len(toks)
        for i in range(n):
            for k in range(1, self.max_words + 1):
                if i + k > n:
                    break
                words = [t[2] for t in toks[i : i + k]]
                joined = "".join(words)
                if len(joined) < 2:
                    continue
                s, e = toks[i][0], toks[i + k - 1][1]
                for v in self._match(words, joined, text[s:e]):
                    needs_ctx = v.entry.common_word or v.entry.context
                    if needs_ctx and not self._context_ok(v.entry, toks, i, k, nearby):
                        continue
                    conf = self._conf(v, words, joined)
                    reason = f"deny:{v.entry.match}:#{v.entry.index}"
                    yield Span(
                        f.id, s, e, v.entry.category, "denylist", conf, v.entity, reason, view=view
                    )

    def _match(self, words: list[str], joined: str, raw: str) -> list[_Variant]:
        k = len(words)
        out = []
        cands = list(self.exact.get(joined, ()))
        if not cands and len(joined) > 3:
            # Plurals and possessives of exact entries ("Lanterns").
            for stem in (joined[:-1] if joined.endswith("s") else None,
                         joined[:-2] if joined.endswith("es") else None):  # fmt: skip
                if stem:
                    cands.extend(self.exact.get(stem, ()))
        for v in cands:
            if v.entry.match == "case_sensitive" and raw.replace(" ", "") != v.text.replace(
                " ", ""
            ):
                continue
            if abs(v.words - k) <= 1:
                out.append(v)
        if out or not self.fuzzy or len(joined) < 5:
            return out
        best = process.extract(joined, self.fuzzy_joined, scorer=fuzz.ratio, limit=5,
                               score_cutoff=min(self.min_cutoff, PHONETIC_FLOOR))  # fmt: skip
        phon = None
        for _choice, score, idx in best:
            v = self.fuzzy[idx]
            if abs(v.words - k) > 1:
                continue  # try the next candidate rather than giving up
            spaced_ok = v.words == k and fuzz.ratio(" ".join(words), v.spaced) >= v.min_score
            if score >= v.min_score or spaced_ok:
                out.append(v)
                break
            if phon is None:
                phon = jellyfish.metaphone(joined)
            if phon and phon == v.phon:
                out.append(v)
                break
        return out

    @staticmethod
    def _conf(v: _Variant, words: list[str], joined: str) -> float:
        if joined == v.joined:
            return 1.0
        return round(min(0.95, fuzz.ratio(joined, v.joined) / 100), 2)

    @staticmethod
    def _context_ok(entry: DenyEntry, toks: list[tuple[int, int, str]], i: int, k: int,
                    nearby: list[Field]) -> bool:  # fmt: skip
        if entry.common_word:
            lo, hi = max(0, i - GENERIC_RADIUS), min(len(toks), i + k + GENERIC_RADIUS)
            if any(t[2] in GENERIC_BRAND_CUES for t in toks[lo:i] + toks[i + k : hi]):
                return True
        if not entry.context:
            return not entry.common_word
        ctx = set(entry.context)
        lo, hi = max(0, i - CONTEXT_RADIUS), min(len(toks), i + k + CONTEXT_RADIUS)
        if any(t[2] in ctx for t in toks[lo:i] + toks[i + k : hi]):
            return True
        return any(ctx & set(_tokens(f.orig)) for f in nearby)
