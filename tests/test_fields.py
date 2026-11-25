from __future__ import annotations

from offrecord import adapters
from offrecord.limits import Limits
from offrecord.model import Span
from offrecord.normalize import build_fields, iter_windows, speaker_keys

TXT = (
    "\n".join(
        f"{'Orla Venkatesh' if i % 2 == 0 else 'BRAM OKAFOR'}: line {i} call five five five one two"
        for i in range(12)
    )
    + "\n"
)


def doc():
    return adapters.get("txt").parse(TXT, Limits())


def test_fields_have_orig_and_norm_views():
    fields = build_fields(doc())
    text_fields = [f for f in fields if f.kind == "text"]
    assert len(text_fields) == 12
    f = text_fields[0]
    assert f.views["orig"].text == f.orig
    assert "55512" in f.views["norm"].text
    a = f.views["norm"].text.index("55512")
    s, e = f.project("norm", a, a + 5)
    assert f.orig[s:e] == "five five five one two"


def test_speaker_fields_and_unification():
    d = doc()
    fields = build_fields(d)
    assert sum(1 for f in fields if f.kind == "speaker") == 12
    keys = speaker_keys(d)
    assert keys[0] == "orla venkatesh" and keys[1] == "bram okafor"


def test_windows_cover_every_field_once_with_lookback():
    d = doc()
    all_ids = [f.id for f in build_fields(d)]
    seen = []
    windows = list(iter_windows(d, size=5, lookback=2))
    assert len(windows) == 3
    for w in windows:
        seen.extend(f.id for f in w.fields)
    assert seen == all_ids
    assert windows[0].context == []
    # Look-back: previous 2 turns (speaker + text fields each).
    assert [f.turn for f in windows[1].context] == [3, 3, 4, 4]


def test_metadata_in_first_window():
    d = adapters.get("vtt").parse(
        "WEBVTT\n\nNOTE Quillfeather\n\n00:00:01.000 --> 00:00:02.000\nhi\n", Limits()
    )
    w0 = next(iter_windows(d))
    assert w0.fields[0].kind == "meta"


def test_span_validation():
    import pytest

    with pytest.raises(ValueError):
        Span(("turn", "0", "text"), 5, 2, "EMAIL", "x", 1.0)
    with pytest.raises(ValueError):
        Span(("turn", "0", "text"), 0, 2, "EMAIL", "x", 1.5)
