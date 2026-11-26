"""Property tests for the merger and restore."""

from __future__ import annotations

import itertools

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from offrecord.errors import MappingError
from offrecord.model import Span, turn_field_id
from offrecord.redact.merge import merge
from offrecord.restore import restore_text

FID = turn_field_id(0, "text")
CATS = ["PERSON", "NAME", "PHONE", "EMAIL", "CLIENT", "AMOUNT"]


@st.composite
def spans(draw):
    n = draw(st.integers(0, 12))
    out = []
    for _ in range(n):
        a = draw(st.integers(0, 60))
        b = draw(st.integers(a + 1, a + 15))
        out.append(Span(FID, a, b, draw(st.sampled_from(CATS)), "t",
                        draw(st.floats(0.1, 1.0)), draw(st.sampled_from([None, "e1", "e2"])),
                        action=draw(st.sampled_from(["redact", "redact", "flag"]))))  # fmt: skip
    return out


@settings(max_examples=300)
@given(spans(), st.text(alphabet="ab -.,", min_size=80, max_size=80))
def test_merge_never_drops_a_redacted_character(ss, text):
    out = merge(ss, {FID: text})
    red_in = {i for s in ss if s.action == "redact" for i in range(s.start, s.end)}
    red_out = {i for s in out if s.action == "redact" for i in range(s.start, s.end)}
    assert red_in <= red_out
    reds = sorted((s.start, s.end) for s in out if s.action == "redact")
    for (_a1, b1), (a2, _b2) in itertools.pairwise(reds):
        assert b1 <= a2  # no overlaps after merging
    # Extra coverage only ever bridges small punctuation/space gaps between spans.
    for i in red_out - red_in:
        assert text[i] in " -.,"


def payload(n: int) -> dict:
    return {
        "entries": {f"<C{i}>": {"category": "CLIENT", "canonical": f"org{i}",
                                "occurrences": [f"Org Number {i}"]} for i in range(1, n + 1)},
        "bare_collisions": [],
    }  # fmt: skip


@settings(max_examples=300)
@given(st.lists(st.one_of(st.integers(1, 12).map(lambda i: f"<C{i}>"),
                          st.sampled_from(["hello", " ", "world", ",", "q3", "x"])),
                max_size=20))  # fmt: skip
def test_restore_replaces_exactly_the_known_placeholders(parts):
    # With the mapping header, unknown placeholders are left and reported.
    p = payload(10)
    head = "# offrecord mapping=or1-x\n"
    text = head + "".join(parts)
    out, rep = restore_text(text, p, "or1-x")
    out = out.removeprefix(head)
    known = sum(1 for x in parts if x.startswith("<C") and int(x[2:-1]) <= 10)
    unknown = sum(1 for x in parts if x.startswith("<C") and int(x[2:-1]) > 10)
    assert rep.restored >= known
    assert sum(rep.unknown.values()) == unknown
    expected = "".join(
        f"Org Number {x[2:-1]}" if x.startswith("<C") and int(x[2:-1]) <= 10 else x for x in parts
    )
    assert out == expected


@settings(max_examples=200)
@given(st.lists(st.one_of(st.integers(1, 12).map(lambda i: f"<C{i}>"),
                          st.sampled_from(["hello", " ", "P1", "L2", "q3"])),
                max_size=20))  # fmt: skip
def test_headerless_text_with_a_foreign_placeholder_restores_nothing(parts):
    # An LLM answer has no header: one placeholder the mapping lacks means a wrong mapping.
    p = payload(10)
    text = "".join(parts)
    if any(x.startswith("<C") and int(x[2:-1]) > 10 for x in parts):
        with pytest.raises(MappingError):
            restore_text(text, p, "or1-x")
    else:
        out, _rep = restore_text(text, p, "or1-x")
        assert "P1" not in out or "P1" in text  # short bare tokens are never placeholders


@given(st.integers(1, 99))
def test_c1_never_restored_as_c10(n):
    p = payload(1)
    out, _rep = restore_text(f"# offrecord mapping=or1-x\n<C{n}>", p, "or1-x")
    assert out.endswith("Org Number 1") == (n == 1)
