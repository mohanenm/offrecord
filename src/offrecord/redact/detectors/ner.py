"""Tier 1: model-based NER (GLiNER, optionally an ensemble with a token-classification model).

- Gating: only sentences flagged by cheap, case-independent checks reach a model: a token that
  is not in the common-word list, a known given name, a digit, a currency sign, or a name/business
  cue word. Everything else is skipped, which keeps Tier 1 fast and flat in memory.
- Ensemble: every configured model runs on the gated sentences and their findings are united.
  Models can only add redactions (the merger never removes a span another detector found).
- Never on speaker labels (always redacted already), and pronouns are never taken as names.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import ClassVar

from offrecord.model import Field, Span
from offrecord.normalize.fields import Window
from offrecord.redact.config import Config
from offrecord.redact.wordlists import FUNCTION_WORDS, is_common, proper_names

GLINER_LABELS = {
    "person": "PERSON",
    "organization": "ORG",
    "company": "ORG",
    "brand": "BRAND",
    "product": "PRODUCT",
    "location": "LOCATION",
    "street address": "LOCATION",
    "project codename": "CODENAME",
}
TOKEN_LABELS = {"PER": "PERSON", "ORG": "ORG", "LOC": "LOCATION", "MISC": "NAME"}
SENTENCE_RE = re.compile(r"(?:[^.!?\n]|\.(?=\d))+(?:[.!?]+|$)")
WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")
CUE_RE = re.compile(
    r"(?i)\b(?:named|called|mr|mrs|ms|dr|client|customer|vendor|partner|company|team|project|"
    r"product|brand|store|office|account|firm|agency|bank|hospital|school)\b"
)
_MODEL_CACHE: dict[tuple[str, int], object] = {}


def gate(sentence: str) -> bool:
    if any(c.isdigit() for c in sentence) or any(c in sentence for c in "$\u20ac\u00a3@"):
        return True
    names = proper_names()
    for k, m in enumerate(WORD_RE.finditer(sentence)):
        w = m.group()
        lw = w.lower().removesuffix("'s")
        if lw in FUNCTION_WORDS:
            continue
        if len(lw) >= 2 and lw in names:  # given names of any length >= 2 ("Li", "Ed")
            return True
        if len(lw) >= 3 and not is_common(lw):
            return True
        if k > 0 and w[:1].isupper() and len(lw) >= 2:
            return True  # a capitalised word mid-sentence (extra signal; never required)
    return bool(CUE_RE.search(sentence))


def load_model(name: str, threads: int = 1):
    """Load (once per process) an installed Tier 1 model by manifest name."""
    from offrecord.errors import EnvironmentCheckError
    from offrecord.models import MANIFEST, installed, model_path, verify_for_load

    key = (name, threads)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    if name not in MANIFEST or not installed(name):
        raise EnvironmentCheckError(f"model {name} is not installed; run: offrecord setup models")
    if not verify_for_load(name):
        raise EnvironmentCheckError(
            f"model {name} failed its checksum; reinstall: offrecord setup models"
        )
    kind = MANIFEST[name].kind
    if kind == "gliner":
        from offrecord.models.gliner import Gliner

        m = Gliner(model_path(name), threads)
    elif kind == "token-ner":
        from offrecord.models.token_ner import TokenNer

        m = TokenNer(model_path(name), threads)
    else:
        raise EnvironmentCheckError(f"model {name} is not an NER model")
    _MODEL_CACHE[key] = m
    return m


class NerDetector:
    name: ClassVar[str] = "ner"
    categories: ClassVar[frozenset[str]] = frozenset(
        {"PERSON", "ORG", "BRAND", "PRODUCT", "LOCATION", "CODENAME", "NAME"}
    )

    def __init__(self, config: Config) -> None:
        self.models = [(n, load_model(n, config.model_threads)) for n in config.ner_models]
        self.threshold = config.ner_threshold
        self.gated = 0
        self.skipped = 0

    def detect(self, window: Window) -> Iterator[Span]:
        for f in window.fields:
            if f.kind == "speaker":
                continue
            yield from self._field(f)

    def _field(self, f: Field) -> Iterator[Span]:
        text = f.orig
        for m in SENTENCE_RE.finditer(text):
            sent = m.group()
            if not sent.strip():
                continue
            if not gate(sent):
                self.skipped += 1
                continue
            self.gated += 1
            for name, model in self.models:
                is_gliner = hasattr(model, "max_width")
                labels = list(GLINER_LABELS) if is_gliner else None
                for e in model.predict(sent, labels, self.threshold):
                    surface = sent[e.start : e.end]
                    if surface.strip().lower() in FUNCTION_WORDS or len(surface.strip()) < 2:
                        continue
                    cat = GLINER_LABELS.get(e.label) if is_gliner else TOKEN_LABELS.get(e.label)
                    if cat is None:
                        continue
                    s, t = m.start() + e.start, m.start() + e.end
                    yield Span(f.id, s, t, cat, f"ner.{name}", round(min(0.9, e.score), 2), None,
                               f"ner:{name}")  # fmt: skip
