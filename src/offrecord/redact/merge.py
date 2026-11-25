"""Span merger.

Per field:
- Redaction spans that overlap are merged into one span covering their union (recall first:
  "longest wins" for the label, but no character of any input span is ever dropped). The merged
  span takes its category and entity from the longest member (ties: category priority, then
  confidence) and the *lowest* confidence of its members (for review flags).
- Adjacent spans of the same entity, separated only by whitespace/punctuation, merge.
- Flag-only spans are kept separately and never reduce redactions.
"""

from __future__ import annotations

import bisect
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace

from offrecord.model import FieldId, Span, category

_JOINABLE = set(" \t\n-.,'&/")
_MAX_GAP = 3
NAME_LIKE = {"PERSON", "NAME", "CLIENT", "BRAND", "PRODUCT", "CODENAME", "ORG", "LOCATION"}
NUMERIC = {"PHONE", "ACCOUNT", "CARD", "SSN"}
_EXPLICIT = ("deny#", "spk:")
CORRECTION = re.compile(
    r"(?i)\b(?:sorry|i mean|no wait|wait|scratch that|let me (?:redo|repeat|start) (?:that|again)|"
    r"correction|actually|rather|oops|my bad|no no|hang on)\b"
)
_CORRECTION_GAP = 40


def _explicit(s: Span) -> bool:
    return s.entity is not None and s.entity.startswith(_EXPLICIT)


def _joinable(prev: Span, s: Span, gap: str) -> bool:
    if prev.entity is not None and prev.entity == s.entity:
        return len(gap) <= _MAX_GAP and set(gap) <= _JOINABLE
    both_names = prev.category in NAME_LIKE and s.category in NAME_LIKE
    two_known = _explicit(prev) and _explicit(s)
    if both_names and gap.strip() == "" and len(gap) <= 2 and not two_known:
        # Adjacent name words become one name, unless they are two different known entities.
        return True
    if prev.category in NUMERIC and s.category in NUMERIC:
        # "five five five... sorry, five five six": the correction phrase goes with the number.
        return len(gap) <= _CORRECTION_GAP and bool(CORRECTION.search(gap))
    return False


def _rank(s: Span) -> tuple[int, int, float]:
    try:
        prio = category(s.category).priority
    except KeyError:
        prio = 0
    return (s.end - s.start, prio, s.confidence)


def _suffix_only(text: str | None, inner: Span, start: int, end: int) -> bool:
    """True if the cluster text outside ``inner`` is only org/place words ("Freight", "Inc")."""
    if text is None:
        return False
    from offrecord.redact.detectors.people import ORG_AFTER, PLACE_AFTER, PLACE_BEFORE

    extra = (text[start : inner.start] + " " + text[inner.end : end]).lower().split()
    words = [w.strip(".,'-") for w in extra if w.strip(".,'-")]
    return all(w in ORG_AFTER or w in PLACE_AFTER or w in PLACE_BEFORE for w in words)


def _cluster(spans: list[Span], text: str | None = None) -> Span:
    start = min(s.start for s in spans)
    end = max(s.end for s in spans)
    # A known entity (deny-list or speaker) keeps its identity when a longer model span only adds
    # an org/place word ("Quarnstead" + "Freight"). Otherwise the longest span wins, so "Ada
    # Brownfield" never takes the placeholder of a speaker called "Ada Quist".
    explicit = [s for s in spans if _explicit(s) and _suffix_only(text, s, start, end)]
    best = max(explicit or spans, key=_rank)
    return replace(
        best,
        start=start,
        end=end,
        confidence=min(s.confidence for s in spans),
        detector=best.detector if len(spans) == 1 else f"{best.detector}+{len(spans) - 1}",
    )


def merge(spans: Iterable[Span], texts: dict[FieldId, str] | None = None) -> list[Span]:
    """Merge spans (all in ``orig`` coordinates). ``texts`` (field id -> original text) enables
    joining same-entity spans across small punctuation/space gaps."""
    redact: dict[FieldId, list[Span]] = defaultdict(list)
    flags: list[Span] = []
    for s in spans:
        if s.view != "orig":
            raise ValueError("merge expects spans projected to orig coordinates")
        if s.end <= s.start:
            continue
        (redact[s.field] if s.action == "redact" else flags).append(s)

    out: list[Span] = []
    for fid, items in redact.items():
        items.sort(key=lambda s: (s.start, -s.end))
        clusters: list[list[Span]] = []
        cur_end = -1
        for s in items:
            if clusters and s.start < cur_end:
                clusters[-1].append(s)
                cur_end = max(cur_end, s.end)
            else:
                clusters.append([s])
                cur_end = s.end
        text = (texts or {}).get(fid)
        merged = [_cluster(c, text) for c in clusters]
        joined: list[Span] = []
        for s in merged:
            if joined and text is not None:
                prev = joined[-1]
                gap = text[prev.end : s.start]
                if _joinable(prev, s, gap):
                    keep = prev if _rank(prev) >= _rank(s) else s
                    if _explicit(s) and not _explicit(prev):
                        keep = s
                    joined[-1] = replace(
                        keep,
                        start=prev.start,
                        end=s.end,
                        confidence=min(prev.confidence, s.confidence),
                    )
                    continue
            joined.append(s)
        out.extend(joined)

    # Flags inside a redaction are redundant; keep the rest for human review.
    covered: dict[FieldId, list[tuple[int, int]]] = defaultdict(list)
    for s in out:
        covered[s.field].append((s.start, s.end))
    starts = {fid: [a for a, _ in sorted(ivs)] for fid, ivs in covered.items()}
    ends = {fid: [b for _, b in sorted(ivs)] for fid, ivs in covered.items()}
    for fl in flags:
        st = starts.get(fl.field, [])
        k = bisect.bisect_right(st, fl.start) - 1  # redactions never overlap after merging
        if not (k >= 0 and ends[fl.field][k] >= fl.end):
            out.append(fl)
    out.sort(key=lambda s: (s.field, s.start))
    return out
