"""Tier 0 pipeline: windows -> detectors (orig, norm and speaker-joined views) -> projection ->
merge -> placeholders.

Speaker-joined view: consecutive turns by the same speaker, separated only by short
interjections from others ("mm-hmm", "go on"), are joined and scanned again, so a phone number
or email split across an interruption is caught. Spans found there are split back onto the
original fields.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import replace
from typing import Any

from offrecord.adapters._common import speaker_key
from offrecord.model import Document, Field, FieldId, Span
from offrecord.normalize.fields import Window, iter_windows, make_field
from offrecord.redact import detectors as det_registry
from offrecord.redact.config import Config, from_mapping
from offrecord.redact.merge import merge
from offrecord.redact.replace import Mapping, Stats, apply

INTERJECTION_MAX_WORDS = 6
LONG_INTERJECTION_WORDS = 15  # allowed when the speaker stopped mid-number or mid-email
UNFINISHED = re.compile(
    r"(?i)(?:\d|\b(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|at|dot))\W*$"
)
JOIN_MAX_TURNS = 3


def _project(span: Span, fields: dict[FieldId, Field]) -> Span:
    if span.view == "orig":
        return span
    f = fields[span.field]
    s, e = f.project(span.view, span.start, span.end)
    return replace(span, start=s, end=e, view="orig")


def _joined_fields(w: Window, doc: Document) -> Iterator[tuple[Field, list[tuple[Field, int]]]]:
    """Yield (joined field, [(part field, offset in joined text)]) for split-PII scanning."""
    text_fields = [f for f in (*w.context, *w.fields) if f.kind == "text" and f.turn is not None]
    keys = [speaker_key(doc.turns[f.turn].speaker or "") for f in text_fields if f.turn is not None]
    n = len(text_fields)
    for i in range(n):
        chain = [text_fields[i]]
        spk = keys[i]
        j = i + 1
        while j < n and len(chain) < JOIN_MAX_TURNS:
            f = text_fields[j]
            other = keys[j]
            if other == spk:
                chain.append(f)
                j += 1
                continue
            words = len(f.orig.split())
            unfinished = bool(UNFINISHED.search(chain[-1].orig))
            if words <= INTERJECTION_MAX_WORDS or (unfinished and words <= LONG_INTERJECTION_WORDS):
                j += 1
                continue
            break
        if len(chain) < 2:
            continue
        parts, offset, pieces = [], 0, []
        for f in chain:
            parts.append((f, offset))
            pieces.append(f.orig)
            offset += len(f.orig) + 1
        jf = make_field(("joined", str(w.index), str(i)), "text", None, " ".join(pieces))
        yield jf, parts


def _split_back(span: Span, jf: Field, parts: list[tuple[Field, int]]) -> list[Span]:
    s, e = (
        jf.project(span.view, span.start, span.end)
        if span.view != "orig"
        else (span.start, span.end)
    )
    out = []
    touched = []
    for f, off in parts:
        a, b = max(s, off), min(e, off + len(f.orig))
        if a < b:
            touched.append((f, a - off, b - off))
    # Matches inside a single turn are kept: the joined text gave them context from another
    # turn ("my account number is" / "mm-hmm" / "4471"). Except deny-list matches: names are
    # already matched per field, and a common-word brand must find its context in its own turn
    # (or the two before it), not in a neighbour joined on for split-PII scanning.
    if len(touched) < 2 and span.detector == "denylist":
        return []
    for f, a, b in touched:
        out.append(replace(span, field=f.id, start=a, end=b, view="orig",
                           reason=span.reason + "+joined"))  # fmt: skip
    return out


def speaker_names(doc: Document) -> tuple[str, ...]:
    seen, out = set(), []
    for t in doc.turns:
        if t.speaker and speaker_key(t.speaker) not in seen:
            seen.add(speaker_key(t.speaker))
            out.append(t.speaker)
    return tuple(out)


def detect_document(doc: Document, config: Config) -> list[Span]:
    cfg = replace(config, speaker_names=speaker_names(doc))
    detectors = det_registry.build(cfg)
    spans: list[Span] = []
    texts: dict[FieldId, str] = {}
    for w in iter_windows(doc, cfg.window_turns, cfg.lookback_turns):
        fmap = {f.id: f for f in (*w.context, *w.fields)}
        for f in w.fields:
            texts[f.id] = f.orig
        for d in detectors:
            for sp in d.detect(w):
                spans.append(_project(sp, fmap))
        for jf, parts in _joined_fields(w, doc):
            jw = Window(w.index, [jf], [])
            for d in detectors:
                if d.name in ("people", "ner", "llm"):
                    continue  # names were scanned per field; models are too costly to repeat
                for sp in d.detect(jw):
                    spans.extend(_split_back(sp, jf, parts))
    spans.extend(_propagate_model_names(spans, texts))
    return merge(spans, texts)


MODEL_NAME_CATEGORIES = {"PERSON", "ORG", "BRAND", "PRODUCT", "CODENAME", "LOCATION", "NAME"}


def _propagate_model_names(spans: list[Span], texts: dict[FieldId, str]) -> list[Span]:
    """A name a model found in one turn is redacted wherever it appears (models only see gated
    sentences, and may miss a repeat): whole word, any case, every field."""
    from offrecord.redact.wordlists import FUNCTION_WORDS

    found: dict[str, str] = {}
    for s in spans:
        if s.detector.startswith(("ner.", "llm.")) and s.category in MODEL_NAME_CATEGORIES:
            surface = texts.get(s.field, "")[s.start : s.end].strip()
            key = surface.casefold()
            if len(key) >= 2 and key not in FUNCTION_WORDS:
                found.setdefault(key, s.category)
    if not found:
        return []
    alts = sorted((re.escape(k) for k in found), key=len, reverse=True)
    rx = re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)", re.IGNORECASE)
    out = []
    for fid, text in texts.items():
        for m in rx.finditer(text):
            cat = found.get(m.group().casefold(), "NAME")
            out.append(Span(fid, m.start(), m.end(), cat, "propagate", 0.5, None, "model-name"))
    return out


def seed_mapping(mapping: Mapping, doc: Document, config: Config) -> None:
    """Register known names up front so every mention resolves to one placeholder."""
    for e in config.denylist:
        for v in e.variants():
            mapping.seed_alias(v, e.category, f"deny#{e.index}")
            if e.category == "PERSON":
                for tok in v.split():
                    if len(tok) >= 3:
                        mapping.seed_alias(tok, e.category, f"deny#{e.index}")
    names = speaker_names(doc)
    counts: dict[str, int] = {}
    for name in names:
        for tok in {t.lower() for t in name.split()}:
            counts[tok] = counts.get(tok, 0) + 1
    for tok, n in counts.items():
        if n > 1:
            mapping.mark_ambiguous(tok)
    for name in names:
        key = f"spk:{speaker_key(name)}"
        mapping.seed_alias(name, "PERSON", key)
        for tok in name.split():
            if len(tok) >= 3 and counts[tok.lower()] == 1:  # shared parts stay ambiguous
                mapping.seed_alias(tok, "PERSON", key)


def redact_document(
    doc: Document, config: Config, mapping: Mapping | None = None
) -> tuple[Document, list[Span], Mapping, Stats]:
    mapping = mapping or Mapping()
    spans = detect_document(doc, config)
    seed_mapping(mapping, doc, config)
    redacted, stats = apply(doc, spans, mapping)
    return redacted, spans, mapping, stats


SENTENCE_RE = re.compile(r"(?:[^.!?\n]|\.(?=\d))+(?:[.!?]+|$)")


def readability(doc: Document, spans: Sequence[Span]) -> tuple[int, int, int, int]:
    """How much of the turn text the redactions cover, as a readability check:
    (characters, redacted characters, sentences, sentences at least half redacted)."""
    by_turn: dict[int, list[tuple[int, int]]] = {}
    for sp in spans:
        if sp.action == "redact" and sp.view == "orig" and sp.field[::2] == ("turn", "text"):
            by_turn.setdefault(int(sp.field[1]), []).append((sp.start, sp.end))
    chars = red = sents = mostly = 0
    for t in doc.turns:
        covered = bytearray(len(t.text))
        for a, b in by_turn.get(t.index, []):
            covered[a:b] = b"\x01" * (b - a)
        chars += len(t.text)
        red += sum(covered)
        for m in SENTENCE_RE.finditer(t.text):
            n = len(m.group().strip())
            if n:
                sents += 1
                mostly += 2 * sum(covered[m.start() : m.end()]) >= n
    return chars, red, sents, mostly


def review_flags(spans: Sequence[Span], threshold: float) -> list[Span]:
    return [s for s in spans if s.action == "flag" or s.confidence < threshold]


def tier0_system(config: dict[str, Any]) -> Any:
    cfg = from_mapping(config)
    return lambda doc: detect_document(doc, cfg)
