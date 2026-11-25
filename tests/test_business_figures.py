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
