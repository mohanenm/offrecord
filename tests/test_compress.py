"""Compression: deletions only, placeholders always preserved. Synthetic data."""

from __future__ import annotations

import importlib.util
from collections import Counter

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from offrecord.compress import PLACEHOLDER, clean_text, compress
from offrecord.model import Document, Turn
from offrecord.tokens import APPROX, approx_count, get_counter


def doc(*turns: tuple[str, str]) -> Document:
    return Document("txt", tuple(Turn(i, s, t) for i, (s, t) in enumerate(turns)))


def test_fillers_and_stutters_removed():
    assert (
        clean_text("Um, so I I think, uh, the the <C1> deal is fine")
        == "so I think, the <C1> deal is fine"
    )


def test_false_start_removed():
    assert clean_text("I was- I am sending it") == "I am sending it"


def test_meaningful_phrases_kept():
    assert clean_text("I mean it, you know the answer") == "I mean it, you know the answer"


def test_backchannel_turns_dropped_and_turns_merged():
    d = doc(("A", "We closed <C1>."), ("B", "Mm-hmm."), ("A", "At <AMT1> total."), ("B", "yeah"))
    out, st_ = compress(d, "light")
    assert [t.text for t in out.turns] == ["We closed <C1>. At <AMT1> total."]
    assert st_.dropped_backchannel == 2 and st_.merged == 1


def test_backchannel_with_placeholder_kept():
    out, _ = compress(doc(("A", "Is it <C1>?"), ("B", "Yeah <C1>")), "light")
    assert any("<C1>" in t.text for t in out.turns if t.speaker == "B")


def test_medium_drops_small_talk_and_duplicates():
    d = doc(
        ("A", "Can you hear me okay?"),
        ("B", "Great, how was your weekend?"),
        ("A", "We need the forecast for <C1> by Friday please."),
        ("B", "Sure, I will send the forecast for <C1> by Friday please."),
        ("A", "We need the forecast for <C1> by Friday please."),
        ("B", "Thanks, bye!"),
    )
    out, s = compress(d, "medium")
    texts = [t.text for t in out.turns]
    assert not any("weekend" in t for t in texts)
    assert s.dropped_small_talk >= 2 and s.dropped_duplicates >= 1


def test_last_occurrence_of_placeholder_never_dropped():
    d = doc(("A", "Can you hear me, <P1>?"), ("B", "Yes"), ("A", "ok"))
    out, _ = compress(d, "medium")
    assert "<P1>" in " ".join(t.text for t in out.turns)


VOCAB = "um uh the the <C1> <AMT2> yeah I mean , deal you know so- okay <P3> mm-hmm weekend 42"
words = st.sampled_from(VOCAB.split())


@settings(max_examples=200)
@given(st.lists(st.tuples(st.sampled_from(["A", "B"]), st.lists(words, max_size=12)), max_size=10),
       st.sampled_from(["light", "medium"]))  # fmt: skip
def test_compression_preserves_placeholders_and_only_deletes(turns, level):
    d = doc(*[(s, " ".join(ws)) for s, ws in turns])
    out, _ = compress(d, level)
    before = Counter(p for t in d.turns for p in PLACEHOLDER.findall(t.text))
    after = Counter(p for t in out.turns for p in PLACEHOLDER.findall(t.text))
    assert set(before) == set(after)
    vocab = {w for t in d.turns for w in t.text.split()}
    for t in out.turns:
        for w in t.text.split():
            assert w.strip(",.") in {v.strip(",.") for v in vocab} or w in vocab


def test_compression_never_reintroduces_redacted_text():
    from pathlib import Path

    from offrecord import adapters
    from offrecord.limits import Limits
    from offrecord.redact.config import Config
    from offrecord.redact.pipeline import redact_document

    text = (Path(__file__).parent.parent / "samples" / "sales-call.txt").read_text()
    red, _spans, mapping, _ = redact_document(adapters.get("txt").parse(text, Limits()), Config())
    secrets = {o for e in mapping.entries.values() for o in e.occurrences if len(o) > 3}
    for level in ("light", "medium"):
        out, _ = compress(red, level)
        body = "\n".join(t.text + " " + (t.speaker or "") for t in out.turns)
        assert not [s for s in secrets if s in body]


def test_approx_counter():
    assert approx_count("Hello, world!") == 4
    assert approx_count("internationalization") == 5
    assert get_counter("approx") is APPROX
    assert APPROX.approximate


def test_tiktoken_counter_is_not_labelled_approximate(monkeypatch):
    import sys
    import types

    from offrecord import tokens

    class FakeEncoding:
        def encode(self, text, disallowed_special=()):
            return text.split()

    fake = types.ModuleType("tiktoken")
    fake.get_encoding = lambda name: FakeEncoding()
    monkeypatch.setitem(sys.modules, "tiktoken", fake)
    monkeypatch.setattr(tokens, "tiktoken_available", lambda encoding: True)
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "unused")
    counter = get_counter("tiktoken:cl100k_base")
    assert counter.name == "tiktoken:cl100k_base"
    assert counter.approximate is False
    assert counter.count("one two three") == 3


def test_unknown_level_rejected():
    with pytest.raises(ValueError):
        compress(doc(("A", "x")), "extreme")


def test_dedupe_scales_linearly():
    import time

    turns = [("A" if i % 2 else "B", f"turn number {i} talks about item {i * 7} and topic {i % 97}")
             for i in range(4000)]  # fmt: skip
    t0 = time.perf_counter()
    compress(doc(*turns), "medium")
    assert time.perf_counter() - t0 < 5.0


def test_lowmem_script_runs_small(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).parent.parent / "scripts" / "lowmem_check.py"
    out = subprocess.run([sys.executable, str(script), "300"], capture_output=True, text=True,
                         check=True, timeout=120)  # fmt: skip
    assert "peak_rss_mb=" in out.stdout


# A short reply to a question is its answer, not backchannel.
@pytest.mark.parametrize("level", ["light", "medium"])
@pytest.mark.parametrize(
    ("question", "answer"),
    [
        ("Can we ship the release on Friday?", "Absolutely."),
        ("legal signed off on the indemnity clause?", "Sure."),
        ("Is the budget approved?", "Right."),
        ("Do you object to the settlement?", "Okay."),
        ("Ready?", "Mhm."),
    ],
)
def test_answer_to_a_question_survives(question, answer, level):
    out, s = compress(doc(("Speaker 1", question), ("Speaker 2", answer)), level)
    assert [t.text for t in out.turns] == [question, answer]
    assert s.dropped_backchannel == 0


def test_backchannel_after_a_statement_still_dropped():
    out, _ = compress(doc(("A", "The budget is approved."), ("B", "Right.")), "light")
    assert [t.text for t in out.turns] == ["The budget is approved."]


def test_reply_to_a_dropped_question_is_not_protected():
    d = doc(("A", "How was your weekend?"), ("B", "Great."), ("A", "We need the <C1> forecast."))
    out, _ = compress(d, "medium")
    assert [t.text for t in out.turns] == ["We need the <C1> forecast."]


def test_tag_question_is_not_backchannel():
    out, _ = compress(doc(("A", "We ship on Friday."), ("B", "Right?"), ("A", "Yes.")), "light")
    assert [t.text for t in out.turns] == ["We ship on Friday.", "Right?", "Yes."]


@pytest.mark.parametrize(
    "text",
    [
        "She went to the ER after the fall",
        "worth about 5 MM",
        "a 5 mm gap",
        "pre- and post-merger numbers",
        "I know that that is wrong, he had had enough",
        "what it is is a pricing problem",
        "that's it, it works",
        "thank you, you were great",
    ],
)
def test_words_that_look_like_fillers_or_stutters_kept(text):
    assert clean_text(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("the bud- budget is fine", "the budget is fine"),
        ("Erm, we we agreed", "we agreed"),
        ("I, I think so", "I think so"),
    ],
)
def test_real_false_starts_and_stutters_removed(text, expected):
    assert clean_text(text) == expected


class _HashEmbedder:
    """Deterministic bag-of-words vectors, enough to drive the aggressive selection step."""

    def embed(self, texts):
        import numpy as np

        vecs = np.zeros((len(texts), 8))
        for i, t in enumerate(texts):
            for w in t.lower().split():
                vecs[i, sum(map(ord, w)) % 8] += 1
        return vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1)


QUESTION_WORDS = ("can we ship the release budget invoice clause legal team sign off approve "
                  "is are do you object to settlement contract on friday plan").split()  # fmt: skip
ONE_WORD_ANSWERS = ("Absolutely Sure Right Okay Yeah Yep Exactly Definitely Totally Perfect "
                    "Great Cool Mhm Mm Hmm Gotcha Fair True Alright").split()  # fmt: skip
needs_numpy = pytest.mark.skipif(
    importlib.util.find_spec("numpy") is None, reason="aggressive compression needs numpy"
)


@pytest.mark.parametrize(
    "level", ["light", "medium", pytest.param("aggressive", marks=needs_numpy)]
)
@settings(max_examples=100, deadline=None)
@given(
    st.lists(st.sampled_from(QUESTION_WORDS), min_size=1, max_size=10),
    st.sampled_from(ONE_WORD_ANSWERS),
    st.sampled_from(["", ".", "!", "..."]),
)
def test_one_word_answer_survives_every_level(level, qwords, answer, punct):
    d = doc(
        ("A", "Thanks everyone, the agenda has three items today."),
        ("B", "The vendor sent the revised numbers this morning."),
        ("A", " ".join(qwords) + "?"),
        ("B", answer + punct),
        ("A", "Then I will update the tracker and send it round."),
        ("B", "I can review the draft before the next call."),
    )
    out, _ = compress(d, level, _HashEmbedder() if level == "aggressive" else None)
    texts = [t.text for t in out.turns]
    q = next(i for i, t in enumerate(texts) if t.endswith("?"))
    assert texts[q + 1].startswith(answer)
