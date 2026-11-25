"""Spoken and written business figures (synthetic phrases only)."""

import pytest

from offrecord import adapters
from offrecord.limits import Limits
from offrecord.model import turn_field_id
from offrecord.redact.config import from_mapping
from offrecord.redact.pipeline import detect_document


def covered(text: str) -> set[int]:
    """Characters of the first turn's text covered by business-detector spans."""
    doc = adapters.get("txt").parse(f"A: {text}\nA: ok\n", Limits())
    out: set[int] = set()
    for sp in detect_document(doc, from_mapping({})):
        if sp.detector.startswith("business.") and sp.field == turn_field_id(0, "text"):
            out.update(range(sp.start, sp.end))
    return out


@pytest.mark.parametrize(
    ("text", "fig"),
    [
        ("Rates are the blended $540 an hour?", "$540 an hour"),
        ("The band is $148,000 to $176,000, plus equity.", "$148,000 to $176,000"),
        ("We sent the offer at six eighty-five.", "six eighty-five"),
        ("Target is three point two million for the building.", "three point two million"),
        ("Usually sixty to ninety bucks total.", "sixty to ninety bucks"),
        ("Revenue is like forty-one k MRR and flat.", "forty-one k MRR"),
        (
            "Second, eight hundred and twelve dollars and forty cents.",
            "eight hundred and twelve dollars and forty cents",
        ),
        ("Drinks package at thirty-four a head.", "thirty-four a head"),
        ("About a dollar twenty per letter.", "a dollar twenty"),
        ("The mill rate went up about three and a half percent.", "three and a half percent"),
        ("Traffic was down one and a half points.", "one and a half points"),
        ("Equity is typically 0.05 to 0.1% over four years.", "0.05 to 0.1%"),
        ("Software margin is in the high eighties.", "high eighties"),
        ("We can split it 50/50.", "50/50"),
        ("Commission is two and a half on the buy side.", "two and a half"),
        ("Ready mix is sending eleven trucks.", "eleven"),
        ("We served three hundred and twelve young people.", "three hundred and twelve"),
        ("A group of four or five kids.", "four or five"),
        ("Sixty per store, the rest later.", "Sixty"),
    ],
)
def test_figures_found(text, fig):
    i = text.index(fig)
    assert set(range(i, i + len(fig))) <= covered(text)


@pytest.mark.parametrize(
    "text",
    [
        "Thursday works, they offered 4 pm for the call.",
        "Give me a minute, I'll be right back.",
    ],
)
def test_non_figures_left_alone(text):
    assert not covered(text)


def test_baseline_is_not_an_amount_tail():
    text = "We pay a $300 baseline fee."
    assert "baseline" not in "".join(text[i] for i in sorted(covered(text)))


@pytest.mark.parametrize(
    ("text", "fig"),
    [
        ("Attrition on my team is nine, which I'm watching.", "nine"),
        ("Basket was up like six and change.", "six and change"),
        ("Usage is down about forty since summer.", "forty"),
        ("They're what, a fifth of your book?", "a fifth"),
        ("The coordinator plus half of my time.", "half"),
        ("Thirty-day readmissions are at 14.1 now.", "14.1"),
    ],
)
def test_rates_without_a_percent_word(text, fig):
    i = text.index(fig)
    assert set(range(i, i + len(fig))) <= covered(text)


@pytest.mark.parametrize(
    "text",
    [
        "Attrition reviews run for ninety days.",
        "Meet at half past nine about attrition.",
        "Usage went up two floors.",
    ],
)
def test_durations_and_times_are_not_rates(text):
    for word in ("ninety", "half", "two"):
        if word in text:
            i = text.index(word)
            assert not set(range(i, i + len(word))) & covered(text)


@pytest.mark.parametrize(
    "text",
    [
        "It took a year and a half to ship.",
        "In the second half we hired.",
        "Call me at a quarter to nine.",
        "We booked a half-day session.",
        "There is a one-off fee for setup.",
        "Let me follow up one more time.",
    ],
)
def test_everyday_fractions_and_ones_are_not_figures(text):
    for word in ("a half", "half", "a quarter", "one"):
        i = text.find(word)
        if i >= 0:
            assert not set(range(i, i + len(word))) & covered(text)


def test_long_number_word_chains_stay_fast():
    import time

    t0 = time.perf_counter()
    covered("twenty and " * 2000 + "zz")
    assert time.perf_counter() - t0 < 5
