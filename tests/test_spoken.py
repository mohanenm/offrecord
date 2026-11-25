"""Spoken-form normalisation. All examples are synthetic."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from offrecord.normalize.spoken import normalize


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("my number is five five five one two one two", "my number is 5551212"),
        ("MY NUMBER IS FIVE FIVE FIVE ONE TWO ONE TWO", "MY NUMBER IS 5551212"),
        ("eight hundred five five five one two one two", "8005551212"),
        ("five five five dash one two one two", "5551212"),
        ("zip is nine oh two one oh", "zip is 90210"),
        ("double five triple two", "55222"),
        (
            "card is four one one one one one one one one one one one one one one one",
            "card is 4111111111111111",
        ),
        ("account number four four, oh two", "account number 4402"),
        ("email john dot smith at example dot com thanks", "email john.smith@example.com thanks"),
        ("JOHN AT EXAMPLE DOT COM", "JOHN@example.com"),
        ("mail jane underscore doe at example dot org", "mail jane_doe@example.org"),
        ("the site is www dot quill-feather dot co dot uk", "the site is www.quill-feather.co.uk"),
        ("spell it S as in Sam, M, I, T, H", "spell it SMITH"),
        ("s-m-i-t-h", "smith"),
        ("J O H N", "JOHN"),
        ("alpha bravo charlie", "abc"),
        ("fifty thousand dollars", "50000 dollars"),
        ("two point five million in ARR", "2500000 in ARR"),
        ("a hundred and twenty units", "120 units"),
        ("smart \u201cquotes\u201d", 'smart "quotes"'),
        ("zero\u200bwidth", "zerowidth"),
    ],
)
def test_examples(src, expected):
    assert normalize(src)[0] == expected


@pytest.mark.parametrize(
    "src",
    [
        "Oh, one more thing",
        "five five, that's it",
        "we should talk about one of them",
        "john@example.com already literal",
        "nothing to see here",
    ],
)
def test_left_alone(src):
    assert normalize(src)[0] == src


def test_self_correction_keeps_both_numbers():
    view, _ = normalize("it's five five five... sorry, five five six")
    assert "555" in view
    assert "556" in view


def test_projection_back_to_original():
    src = "call five five five one two one two now"
    view, chain = normalize(src)
    a = view.index("5551212")
    s, e = chain.project(a, a + 7)
    assert src[s:e] == "five five five one two one two"


def test_partial_projection_expands_to_whole_phrase():
    src = "call five five five one two one two now"
    view, chain = normalize(src)
    a = view.index("5551212")
    s, e = chain.project(a + 2, a + 3)
    assert src[s:e] == "five five five one two one two"


words = st.sampled_from(
    ["five", "oh", "one", "two", "at", "dot", "com", "s", "as", "in", "sam", "hello",
     "thousand", "dollars", "point", ",", "-", "\u2019", "\u200b", "x", "double", "J"]
)  # fmt: skip


@given(st.lists(words, max_size=25), st.sampled_from([" ", "  ", ", "]))
def test_projection_always_in_bounds(ws, sep):
    src = sep.join(ws)
    view, chain = normalize(src)
    for a in range(len(view)):
        s, e = chain.project(a, a + 1)
        assert 0 <= s < e <= len(src)
    s, e = chain.project(0, len(view))
    if view:
        assert s <= len(src) and e <= len(src)


@given(st.text(alphabet=st.characters(codec="utf-8"), max_size=60))
def test_never_crashes_and_maps_whole_view(src):
    view, chain = normalize(src)
    if view:
        s, e = chain.project(0, len(view))
        assert 0 <= s <= e <= len(src)
