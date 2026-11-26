"""Optional tiers. Unit tests use fake models; real-model tests are skipped when the
models are not installed (CI has none)."""

from __future__ import annotations

import hashlib
import json

import pytest

from offrecord import adapters, models
from offrecord.errors import EnvironmentCheckError, UsageError
from offrecord.limits import Limits
from offrecord.model import Document, Turn
from offrecord.models.gliner import Entity
from offrecord.redact.config import from_mapping
from offrecord.redact.detectors import ner as ner_mod
from offrecord.redact.pipeline import detect_document


def doc(text: str) -> Document:
    return adapters.get("txt").parse(text, Limits())


# --- gating, config, manifest -------------------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "gated"),
    [("We met Zorbatek today.", True), ("It costs 40 a month.", True), ("Ask Amber.", True),
     ("yes that works for me", False), ("thanks for that", False)],
)  # fmt: skip
def test_gate(sentence, gated):
    assert ner_mod.gate(sentence) is gated


def test_models_config_validated():
    cfg = from_mapping({"models": {"tier": 1, "ner": ["gliner-small-v2.1"], "ner_threshold": 0.3}})
    assert cfg.tier == 1 and cfg.ner_models == ("gliner-small-v2.1",) and cfg.ner_threshold == 0.3
    for bad in ({"tier": 3}, {"ner_threshold": 2}, {"ner": "x"}):
        with pytest.raises(UsageError):
            from_mapping({"models": bad})


def test_default_tier_is_zero_and_ensemble_default():
    cfg = from_mapping({})
    assert cfg.tier == 0
    assert cfg.ner_models == models.TIER1_NER


def test_manifest_is_pinned():
    for spec in models.MANIFEST.values():
        assert len(spec.revision) == 40 and spec.license
        for f in spec.files:
            assert len(f.sha256) == 64 and f.size > 0


# --- setup: verification enforced ---------------------------------------------------------


@pytest.fixture
def fake_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFRECORD_HOME", str(tmp_path / "home"))
    data = b"synthetic model bytes"
    spec = models.ModelSpec("fake", 1, "gliner", "x/y", "0" * 40, "mit", 1.0, (
        models.ModelFile(
            "model.onnx", "onnx/model.onnx", hashlib.sha256(data).hexdigest(), len(data)
        ),
    ))  # fmt: skip
    monkeypatch.setitem(models.MANIFEST, "fake", spec)
    src = tmp_path / "src" / "onnx"
    src.mkdir(parents=True)
    return tmp_path / "src", src / "model.onnx", data


def test_install_from_dir_verifies(fake_model):
    from offrecord.models.setup import install

    src_dir, f, data = fake_model
    f.write_bytes(data)
    install("fake", str(src_dir))
    assert models.installed("fake", verify=True)


def test_install_rejects_tampered_file(fake_model):
    from offrecord.models.setup import install

    src_dir, f, data = fake_model
    f.write_bytes(data[:-1] + b"X")
    with pytest.raises(EnvironmentCheckError):
        install("fake", str(src_dir))
    assert not models.installed("fake")
    assert not list((models.model_path("fake")).glob("*.part"))


# --- detectors with fake models -----------------------------------------------------------


class FakeGliner:
    max_width = 12

    def predict(self, text, labels, threshold):
        out = []
        for word, label in (("Mark Brown", "person"), ("Zorbatek", "company"), ("I", "person")):
            i = text.find(word)
            if i >= 0:
                out.append(Entity(i, i + len(word), label, 0.9))
        return out


def test_ner_detector_adds_spans_and_skips_pronouns(monkeypatch):
    monkeypatch.setattr(ner_mod, "load_model", lambda name, threads=1: FakeGliner())
    d = doc("Orla: I spoke with Mark Brown at Zorbatek.\nOrla: ok\n")
    spans = detect_document(d, from_mapping({"models": {"tier": 1, "ner": ["gliner-small-v2.1"]}}))
    t = d.turns[0].text
    got = {
        (t[s.start : s.end], s.category)
        for s in spans
        if s.field[2] == "text" and s.field[1] == "0"
    }
    assert any("Mark Brown" in g for g, _ in got)
    assert not any(g == "I" for g, _ in got)


class SaleEmbedder:
    """Seeds and sentences mentioning a sale point the same way; everything else is orthogonal."""

    @property
    def seeds(self):
        return self.embed(list(ner_mod.TOPIC_SEEDS))

    def embed(self, texts):
        np = pytest.importorskip("numpy")
        vec = {True: [1.0, 0.0, 0.0], False: [0.0, 1.0, 0.0]}
        other_seed = [0.0, 0.0, 1.0]
        return np.array(
            [
                vec[True] if "sell" in t else other_seed if t in ner_mod.TOPIC_SEEDS else vec[False]
                for t in texts
            ]
        )


def test_topic_similarity_redacts_sentences_without_keywords(monkeypatch):
    pytest.importorskip("numpy")
    monkeypatch.setattr(ner_mod, "load_model", lambda name, threads=1: FakeGliner())
    monkeypatch.setattr(ner_mod, "load_embedder", lambda threads=1: SaleEmbedder())
    d = doc("Orla: The owner wants to sell the whole business soon.\nOrla: Lunch was good today.\n")
    spans = detect_document(d, from_mapping({"models": {"tier": 1, "ner": ["gliner-small-v2.1"]}}))
    topics = {s.field[1] for s in spans if s.category == "TOPIC" and s.reason == "topic:similar"}
    assert topics == {"0"}


class FakeLlm:
    def __init__(self, payload):
        self.payload = payload

    def create_chat_completion(self, **kw):
        return {"choices": [{"message": {"content": self.payload}}]}


@pytest.mark.parametrize(
    ("payload", "expect"),
    [
        (json.dumps({"spans": [{"line": 1, "text": "the only pharmacy chain we serve in Alaska",
                                "category": "indirect_identifier"}]}), True),
        # A paraphrase cannot be located in the line, so it is dropped.
        (json.dumps({"spans": [{"line": 1, "text": "a pharmacy we serve up north",
                                "category": "indirect_identifier"}]}), False),
        ("not json at all", False),  # malformed: nothing added, no crash
        (json.dumps({"spans": [{"line": 9, "text": "Alaska", "category": "location"}]}), False),
    ],
)  # fmt: skip
def test_llm_detector_only_adds_located_spans(monkeypatch, payload, expect):
    from offrecord.redact.detectors import llm as llm_mod

    monkeypatch.setattr(llm_mod, "load_llm", lambda name, threads: FakeLlm(payload))
    d = doc("Orla: We are the only pharmacy chain we serve in Alaska.\nOrla: ok\n")
    spans = detect_document(
        d, from_mapping({"models": {"tier": 2, "ner": [], "llm": "qwen3-1.7b-q4"}})
    )
    found = any(s.category == "INDIRECT" for s in spans)
    assert found is expect


def test_tier_requires_installed_models(tmp_path, monkeypatch):
    from offrecord.cli import resolve_tier

    monkeypatch.setenv("OFFRECORD_HOME", str(tmp_path / "empty"))
    cfg = from_mapping({})
    assert resolve_tier("auto", cfg) == 0
    with pytest.raises(EnvironmentCheckError):
        resolve_tier("1", cfg)


# --- aggressive compression ----------------------------------------------------------------


class FakeEmbedder:
    def embed(self, texts):
        import numpy as np

        vecs = []
        for t in texts:
            v = np.zeros(8)
            for w in t.lower().split():
                v[hash(w) % 8] += 1
            vecs.append(v / (np.linalg.norm(v) or 1))
        return np.array(vecs)


def test_aggressive_needs_embeddings_and_keeps_content():
    pytest.importorskip("numpy")
    from offrecord.compress import compress

    turns = [Turn(i, "A" if i % 2 else "B", t) for i, t in enumerate([
        "We need the forecast for <C1> by Friday.", "the weather was lovely and sunny all day long",
        "No, we will not sign that.", "my cat did something very silly this morning again",
        "The total is 42 units.", "anyway the coffee machine on floor three is broken again",
        "Send it to <EMAIL1> please.", "random chatter about the football game last night",
    ])]  # fmt: skip
    d = Document("txt", tuple(turns))
    with pytest.raises(ValueError):
        compress(d, "aggressive")
    out, st = compress(d, "aggressive", FakeEmbedder())
    body = " ".join(t.text for t in out.turns)
    for must in ("<C1>", "<EMAIL1>", "not sign", "42"):
        assert must in body
    assert st.turns_after < len(turns)


# --- real models (skipped unless installed) -------------------------------------------------


needs_gliner = pytest.mark.skipif(
    not models.installed("gliner-small-v2.1"), reason="model not installed"
)


@needs_gliner
def test_real_gliner_finds_common_word_names_lowercase():
    pytest.importorskip("onnxruntime")
    from offrecord.models.gliner import Gliner

    g = Gliner(models.model_path("gliner-small-v2.1"))
    ents = g.predict("i met mark brown from zandrelcorp yesterday", ["person", "company"], 0.25)
    assert any(e.label == "person" for e in ents)


@pytest.mark.parametrize(
    ("mode", "expect"), [("off", set()), ("flag", {"flag"}), ("redact", {"redact"})]
)
def test_topic_similarity_follows_topic_settings(monkeypatch, mode, expect):
    pytest.importorskip("numpy")
    monkeypatch.setattr(ner_mod, "load_model", lambda name, threads=1: FakeGliner())
    monkeypatch.setattr(ner_mod, "load_embedder", lambda threads=1: SaleEmbedder())
    d = doc("Orla: The owner wants to sell the whole business soon.\nOrla: ok\n")
    cfg = {"models": {"tier": 1, "ner": ["gliner-small-v2.1"]}, "topics": {"mode": mode},
           "protect": "confidential"}  # fmt: skip
    spans = detect_document(d, from_mapping(cfg))
    assert {s.action for s in spans if s.reason == "topic:similar"} == expect


def _guess_spans():
    from offrecord.model import Span, turn_field_id

    fid = turn_field_id(0, "text")
    text = "We met Zorbleth about the frobnicate plan."
    spans = [
        Span(fid, 7, 15, "NAME", "people.unknown", 0.4, None, "unknown-word"),
        Span(fid, 26, 36, "NAME", "people.unknown", 0.4, None, "unknown-word"),
        Span(fid, 0, 2, "PERSON", "ner.x", 0.9, None, "ner:x"),
    ]
    return spans, {fid: text}


@pytest.mark.parametrize(
    ("payload", "actions"),
    [
        ('{"keep": [1]}', ["redact", "flag", "redact"]),  # "frobnicate" judged ordinary
        ('{"keep": [1, 2]}', ["redact", "redact", "redact"]),
        ("not json", ["redact", "redact", "redact"]),  # unusable answer: nothing changes
    ],
)
def test_verify_guesses_only_demotes_rejected_guesses(monkeypatch, payload, actions):
    from offrecord.redact.detectors import llm as llm_mod

    monkeypatch.setattr(llm_mod, "load_llm", lambda name, threads: FakeLlm(payload))
    spans, texts = _guess_spans()
    out = llm_mod.verify_guesses(spans, texts, from_mapping({}))
    assert [s.action for s in out] == actions
    assert len(out) == len(spans)  # never dropped


def test_coherence_report_returns_indices_only(monkeypatch):
    from offrecord.redact.detectors import llm as llm_mod

    payload = json.dumps({"unclear": [{"line": 2, "reason": "too_many_placeholders"},
                                      {"line": 9, "reason": "meaning_lost"}]})  # fmt: skip
    monkeypatch.setattr(llm_mod, "load_llm", lambda name, threads: FakeLlm(payload))
    cfg = from_mapping({})
    assert llm_mod.coherence_report(["Hi <P1>.", "<P2> <C1> <AMT1>.", "Ok."], cfg) == (
        [(1, "too_many_placeholders")],
        0,
    )
    monkeypatch.setattr(llm_mod, "load_llm", lambda name, threads: FakeLlm("{bad"))
    assert llm_mod.coherence_report(["Hi.", "Ok."], cfg) == ([], 2)  # counted as unchecked


def test_tier2_switches_must_be_booleans():
    with pytest.raises(UsageError):
        from_mapping({"models": {"verify_guesses": "yes"}})
    assert from_mapping({"models": {"coherence_check": False}}).coherence_check is False


def test_verifier_cannot_unredact_other_detectors(monkeypatch):
    """A rejecting (or prompt-injected) answer may only turn Tier 0 guesses into flags: a
    deny-list name merged with a guess stays redacted."""
    from offrecord.redact.detectors import llm as llm_mod

    monkeypatch.setattr(ner_mod, "load_model", lambda name, threads=1: FakeGliner())
    monkeypatch.setattr(llm_mod, "load_llm", lambda name, threads: FakeLlm('{"keep": []}'))
    monkeypatch.setattr(llm_mod.LlmDetector, "detect", lambda self, w: iter(()))
    d = doc("Orla: The Acme Quantrelix team called.\nOrla: Quantrelix again.\n")
    cfg = {"denylist": [{"name": "Acme", "category": "CLIENT"}],
           "models": {"tier": 2, "ner": ["gliner-small-v2.1"]}}  # fmt: skip
    spans = detect_document(d, from_mapping(cfg))
    t0 = d.turns[0].text
    red0 = [t0[s.start : s.end] for s in spans if s.field[1] == "0" and s.action == "redact"]
    assert any("Acme" in r for r in red0)
    flagged = {s.field[1] for s in spans if s.reason == "unknown-word:llm-no"}
    assert flagged == {"0", "1"}  # one decision for the word, applied everywhere
