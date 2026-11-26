"""Regression tests for the optional model tiers and model-aware compression. Fake models;
synthetic data."""

from __future__ import annotations

import json

import pytest

from offrecord import adapters, models
from offrecord.compress import compress
from offrecord.errors import EnvironmentCheckError, UsageError
from offrecord.limits import Limits
from offrecord.model import Document, Span, Turn, turn_field_id
from offrecord.models.gliner import Entity
from offrecord.redact.config import from_mapping
from offrecord.redact.detectors import llm as llm_mod
from offrecord.redact.detectors import ner as ner_mod
from offrecord.redact.merge import merge
from offrecord.redact.pipeline import detect_document

np = pytest.importorskip("numpy")


def doc(text: str) -> Document:
    return adapters.get("txt").parse(text, Limits())


class AlwaysSimilar:
    """An embedder that thinks every pair of turns is identical (worst case)."""

    def embed(self, texts):
        return np.ones((len(texts), 4)) / 2.0


# Opposite decisions are never merged.
@pytest.mark.parametrize("level", ["medium", "aggressive"])
def test_opposite_decisions_survive(level):
    d = Document("txt", tuple(Turn(i, s, t) for i, (s, t) in enumerate([
        ("A", "Let's move the launch earlier."), ("B", "Why would we do that though?"),
        ("A", "Let's move the launch later."), ("B", "Okay, I can live with that plan."),
    ])))  # fmt: skip
    out, _ = compress(d, level, AlwaysSimilar())
    body = " ".join(t.text for t in out.turns)
    assert "earlier" in body and "later" in body


def test_medium_is_tier_independent():
    d = Document(
        "txt",
        tuple(Turn(i, "A" if i % 2 else "B", f"Item {i} needs work on step two") for i in range(6)),
    )
    a, _ = compress(d, "medium")
    b, _ = compress(d, "medium", AlwaysSimilar())
    assert [t.text for t in a.turns] == [t.text for t in b.turns]


# Model-found names propagate; short given names are gated.
class FakeNer:
    max_width = 12

    def predict(self, text, labels, threshold):
        out = []
        for word in ("Falcon", "Li", "Ed"):
            i = text.find(word)
            if i >= 0:
                out.append(
                    Entity(i, i + len(word), "organization" if word == "Falcon" else "person", 0.9)
                )
        return out


def test_model_names_propagate_to_ungated_mentions(monkeypatch):
    monkeypatch.setattr(ner_mod, "load_model", lambda name, threads=1: FakeNer())
    d = doc("A: The Falcon plan with Zorbatek is moving.\nB: ok so the falcon plan is still on.\n")
    spans = detect_document(d, from_mapping({"models": {"tier": 1, "ner": ["gliner-small-v2.1"]}}))
    t1 = d.turns[1].text
    covered = [t1[s.start : s.end].lower() for s in spans if s.field == turn_field_id(1, "text")]
    assert "falcon" in covered


@pytest.mark.parametrize("sentence", ["Li told me the deal is off.", "Ed and Bo said so."])
def test_short_names_are_gated(sentence):
    assert ner_mod.gate(sentence)


# Tier 2: original offsets, every occurrence, odd output tolerated.
class FakeLlm:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    def create_chat_completion(self, **kw):
        content, reason = self.outputs.pop(0) if self.outputs else ('{"spans": []}', "stop")
        return {"choices": [{"message": {"content": content}, "finish_reason": reason}]}


def run_llm(monkeypatch, text, outputs):
    """Run the Tier 2 detector alone (merging would relabel spans)."""
    from offrecord.normalize.fields import iter_windows

    monkeypatch.setattr(llm_mod, "load_llm", lambda name, threads: FakeLlm(outputs))
    d = doc(text)
    det = llm_mod.LlmDetector(
        from_mapping({"models": {"tier": 2, "ner": [], "llm": "qwen3-1.7b-q4"}})
    )
    spans = [sp for w in iter_windows(d) for sp in det.detect(w)]
    return d, spans


def test_llm_offsets_survive_case_folding_and_repeats(monkeypatch):
    weird = chr(0x130) * 4  # a capital dotted I lower-cases to two characters
    payload = json.dumps({"spans": [{"line": 1, "text": "the night porter", "kind": "person"}]})
    text = f"A: {weird} said the night porter would sign and The Night Porter agreed.\nA: ok\n"
    d, spans = run_llm(monkeypatch, text, [(payload, "stop")])
    t = d.turns[0].text
    assert [t[s.start : s.end] for s in spans] == ["the night porter", "The Night Porter"]


@pytest.mark.parametrize("content", ['[{"line": 1}]', '{"spans": null}', '{"spans": [5, "x"]}'])
def test_llm_odd_json_never_crashes(monkeypatch, content):
    _d, spans = run_llm(monkeypatch, "A: We met Zorbatek.\nA: ok\n", [(content, "stop")])
    assert spans == []


def test_llm_truncated_output_retried_in_halves(monkeypatch):
    payload = json.dumps({"spans": [{"line": 1, "text": "the night porter", "kind": "person"}]})
    text = "A: We met the night porter today. And their sole stockist called back later.\nA: ok\n"
    outputs = [("{", "length"), (payload, "stop"), ('{"spans": []}', "stop")]
    _d, spans = run_llm(monkeypatch, text, outputs)
    assert len(spans) == 1


# Aggressive keeps questions and the turns their replies depend on.
def test_aggressive_keeps_questions_and_context():
    turns = [Turn(i, "A" if i % 2 else "B", t) for i, t in enumerate([
        "Should we raise prices for the premium tier this spring", "Honestly I disagree with that.",
        "the weather was lovely", "random chatter about football", "more chatter about lunch",
        "and something about coffee", "chatter chatter chatter", "Anyway that's it.",
    ])]  # fmt: skip
    out, _ = compress(Document("txt", tuple(turns)), "aggressive", AlwaysSimilar())
    body = " ".join(t.text for t in out.turns)
    assert "raise prices" in body and "disagree" in body


# Graceful degradation: config typos and missing extras.
@pytest.mark.parametrize("bad", [{"llm": "qwen3-1.7b"}, {"ner": ["gliner-smal"]}])
def test_model_name_typos_rejected_clearly(bad):
    with pytest.raises(UsageError):
        from_mapping({"models": bad})


def test_missing_extras_drop_tier(monkeypatch, tmp_path):
    import importlib.util

    from offrecord.cli import resolve_tier

    real = importlib.util.find_spec
    blocked = ("onnxruntime", "llama_cpp")
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda n, *a: None if n in blocked else real(n, *a)
    )
    monkeypatch.setattr(models, "installed", lambda name, verify=False: True)
    cfg = from_mapping({})
    assert resolve_tier("auto", cfg) == 0
    with pytest.raises(EnvironmentCheckError):
        resolve_tier("1", cfg)


# A model span that only adds an org word keeps the deny-list identity; others do not adopt it.
def test_cluster_keeps_known_entity_for_suffix_words():
    fid = turn_field_id(0, "text")
    text = "Quarnstead Freight will sign."
    out = merge([Span(fid, 0, 10, "CLIENT", "denylist", 1.0, "deny#0"),
                 Span(fid, 0, 18, "ORG", "ner.x", 0.8)], {fid: text})  # fmt: skip
    assert out[0].entity == "deny#0" and (out[0].start, out[0].end) == (0, 18)
    text2 = "Ada Brownfield will sign."
    out2 = merge([Span(fid, 0, 3, "PERSON", "denylist", 1.0, "spk:ada quist"),
                  Span(fid, 0, 14, "PERSON", "ner.x", 0.8)], {fid: text2})  # fmt: skip
    assert out2[0].entity != "spk:ada quist"


# Swapped small files are caught at load time.
def test_load_time_verification_catches_swapped_file(tmp_path, monkeypatch):
    import hashlib

    monkeypatch.setenv("OFFRECORD_HOME", str(tmp_path / "home"))
    data = b'{"max_width": 12}'
    mf = models.ModelFile(
        "gliner_config.json", "gliner_config.json", hashlib.sha256(data).hexdigest(), len(data)
    )
    monkeypatch.setitem(
        models.MANIFEST,
        "fake",
        models.ModelSpec("fake", 1, "gliner", "x/y", "0" * 40, "mit", 1.0, (mf,)),
    )
    d = models.model_path("fake")
    d.mkdir(parents=True)
    (d / "gliner_config.json").write_bytes(data)
    assert models.verify_for_load("fake")
    (d / "gliner_config.json").write_bytes(b'{"max_width": 1}')  # same size, different content
    assert not models.verify_for_load("fake")
