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
EMBED_MODEL = "minilm-l6-v2"
# Topic sentences said without any keyword are matched by meaning: a sentence close enough to
# one of these (cosine similarity of MiniLM embeddings) is redacted as TOPIC. Generic, invented
# examples, one or more per kind of sensitive topic; the threshold was chosen on the tuning sets.
TOPIC_SEEDS = (
    "We are going to cut jobs next quarter.",
    "Some people on the team will be let go after the reorganisation.",
    "Hours are being reduced and not everyone will keep their position.",
    "Our lawyers told us not to discuss the case.",
    "They are threatening to sue us over the contract.",
    "There is an investigation going on that we can't talk about.",
    "She has been off work since her diagnosis.",
    "He is in the hospital after a heart problem.",
    "I'm pregnant and haven't told anyone at work yet.",
    "My father passed away last month.",
    "I'm going through a divorce and need some time off.",
    "We are in talks to be acquired by a bigger company.",
    "The owner is quietly trying to sell the business.",
    "The company might not make payroll next month.",
    "We missed our numbers badly and the investors are unhappy.",
    "Please keep this between us for now.",
    "This hasn't been announced and nobody outside this room knows.",
    "Don't put any of this in writing.",
    "They are talking to a competitor and might not renew.",
    "If we raise the price again we will lose the account.",
    "Our biggest customer is planning to leave us.",
    "Someone on staff has been stealing from the store.",
    "There was a harassment complaint against the manager.",
    "We had a data breach and customers haven't been told.",
    "The regulator is looking into our safety reports.",
    "We plan to close the site at the end of the year.",
    "We are launching a new product our competitors don't know about.",
    "We plan to raise prices across the board in January.",
    "Our real floor on the discount is much lower than what we quoted.",
)
TOPIC_SIMILARITY = 0.40  # tuning-hw TOPIC 60 -> 71%, precision -3.6 points


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
    m: object
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


def load_embedder(threads: int = 1):
    """The Tier 1 sentence-embedding model, verified and loaded once per process; None if it
    is not installed."""
    from offrecord.errors import EnvironmentCheckError
    from offrecord.models import installed, model_path, verify_for_load

    key = (EMBED_MODEL, threads)
    if key not in _MODEL_CACHE:
        if not installed(EMBED_MODEL):
            return None
        if not verify_for_load(EMBED_MODEL):
            raise EnvironmentCheckError(
                f"model {EMBED_MODEL} failed its checksum; reinstall: offrecord setup models"
            )
        from offrecord.models.embed import Embedder

        _MODEL_CACHE[key] = Embedder(model_path(EMBED_MODEL), threads)
    return _MODEL_CACHE[key]


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
        self.embedder = load_embedder(config.model_threads)
        self.seeds = self.embedder.embed(list(TOPIC_SEEDS)) if self.embedder else None

    def detect(self, window: Window) -> Iterator[Span]:
        for f in window.fields:
            if f.kind == "speaker":
                continue
            yield from self._field(f)

    def _field(self, f: Field) -> Iterator[Span]:
        text = f.orig
        yield from self._topics(f, text)
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

    def _topics(self, f: Field, text: str) -> Iterator[Span]:
        if self.embedder is None or self.seeds is None:
            return
        sents = [m for m in SENTENCE_RE.finditer(text) if len(WORD_RE.findall(m.group())) >= 4]
        if not sents:
            return
        sims = (self.embedder.embed([m.group() for m in sents]) @ self.seeds.T).max(axis=1)
        for m, sim in zip(sents, sims, strict=True):
            if sim >= TOPIC_SIMILARITY:
                s = m.start() + len(m.group()) - len(m.group().lstrip())
                yield Span(f.id, s, m.end(), "TOPIC", "ner.topic", 0.5, None, "topic:similar")
