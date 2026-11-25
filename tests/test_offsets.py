from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from offrecord.offsets import OffsetChain, OffsetMap, ViewBuilder, apply_edits


def test_identity():
    m = OffsetMap.identity(10)
    assert m.project(2, 5) == (2, 5)
    assert m.project_point(10) == 10


def test_replacement_expands_to_whole_source_run():
    src = "call five five five now"
    view, m = apply_edits(src, [(5, 19, "555")])
    assert view == "call 555 now"
    assert m.project(5, 8) == (5, 19)
    # Partial overlap with the replaced run pulls in the whole run (recall first).
    assert m.project(6, 7) == (5, 19)
    assert m.project(0, 4) == (0, 4)
    assert m.project(9, 12) == (20, 23)


def test_deletion():
    view, m = apply_edits("ab\u200bcd", [(2, 3, "")])
    assert view == "abcd"
    assert m.project(1, 3) == (1, 4)  # "bc" covers the deleted char too
    assert m.project(2, 3) == (3, 4)


def test_insertion_rejected():
    b = ViewBuilder("abc")
    with pytest.raises(ValueError):
        b.replace(1, 1, "x")


def test_out_of_order_rejected():
    b = ViewBuilder("abcdef")
    b.replace(3, 4, "x")
    with pytest.raises(ValueError):
        b.replace(1, 2, "y")


def test_bounds_checked():
    m = OffsetMap.identity(3)
    with pytest.raises(ValueError):
        m.project(2, 4)


def test_chain():
    src = "a  b"
    v1, m1 = apply_edits(src, [(1, 3, " ")])  # "a b"
    v2, m2 = apply_edits(v1, [(2, 3, "B!")])  # "a B!"
    chain = OffsetChain([m1, m2])
    assert v2 == "a B!"
    assert chain.project(2, 4) == (3, 4)
    assert chain.project(1, 2) == (1, 3)


@st.composite
def text_and_edits(draw):
    src = draw(st.text(alphabet="ab c\u200b5", min_size=0, max_size=40))
    edits = []
    pos = 0
    while pos < len(src) and draw(st.booleans()):
        start = draw(st.integers(pos, len(src) - 1))
        end = draw(st.integers(start + 1, len(src)))
        rep = draw(st.text(alphabet="xyz", max_size=5))
        edits.append((start, end, rep))
        pos = end
    return src, edits


@given(text_and_edits())
def test_projection_properties(case):
    src, edits = case
    view, m = apply_edits(src, edits)
    assert m.view_len == len(view)
    assert m.src_len == len(src)
    n = len(view)
    for a in range(n + 1):
        for b in range(a, min(n, a + 4) + 1):
            s, e = m.project(a, b)
            assert 0 <= s <= e <= len(src)
            if b > a:
                assert e > s  # a non-empty view range never maps to nothing
                # Monotone: extending the view range never shrinks the source range.
                if b < n:
                    s2, e2 = m.project(a, b + 1)
                    assert s2 == s and e2 >= e


@given(text_and_edits())
def test_unedited_chars_map_exactly(case):
    src, edits = case
    view, m = apply_edits(src, edits)
    for seg in m.segments:
        if seg.identity:
            for k in range(seg.view_start, seg.view_end):
                s, e = m.project(k, k + 1)
                assert src[s:e] == view[k]
