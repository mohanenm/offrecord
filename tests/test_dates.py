"""Date patterns (synthetic phrases only)."""

import pytest

from offrecord.redact.detectors.dates import find_dates


def covered(text: str) -> set[int]:
    out: set[int] = set()
    for s, e, _, _ in find_dates(text):
        out.update(range(s, e))
    return out


@pytest.mark.parametrize(
    ("text", "date"),
    [
        ("Service was the 22nd, so we filed.", "the 22nd"),
        ("Okay, the eighteenth works.", "the eighteenth"),
        ("They moved it from the twenty fifth.", "twenty fifth"),
        ("Rebar inspection is WEDNESDAY THE SECOND, pour after.", "WEDNESDAY THE SECOND"),
        (
            "It happened Saturday night, the twenty-first, we were away.",
            "Saturday night, the twenty-first",
        ),
        ("Yeah, February, the eleventh. Not November.", "February, the eleventh"),
        ("date of birth april nineteenth 2015 grade five", "april nineteenth 2015"),
        ("Eleventh of February, 1979.", "Eleventh of February, 1979"),
        ("Inspection is Monday the eighteenth of January.", "Monday the eighteenth of January"),
        ("We sent the offer Thursday, 11/19, at noon.", "11/19"),
        ("New prices from 01.01.2027.", "01.01.2027"),
        ("Welcome to the Q3 update.", "Q3"),
        ("Spend for FY26 is flat.", "FY26"),
        ("Two open in the third quarter of 2027.", "third quarter of 2027"),
        ("I've been there since August of 2021.", "August of 2021"),
        ("My bonus pays out in March.", "March"),
        ("A prior claim in 2023, hail.", "2023"),
        ("Mediation target early January, then status.", "early January"),
        ("Honestly, it's more like end of May.", "end of May"),
        ("Ovens arrive the week of the 14th.", "week of the 14th"),
        ("That was the spring of 2019.", "spring of 2019"),
        ("Venue is booked Feb 24 and 25.", "Feb 24 and 25"),
        ("Q3, so July through September 2026.", "July through September 2026"),
        ("So for the 22nd the venue hire is fixed.", "the 22nd"),
    ],
)
def test_dates_found(text, date):
    i = text.index(date)
    assert set(range(i, i + len(date))) <= covered(text)


@pytest.mark.parametrize(
    "text",
    [
        "That was the first time we tried it.",
        "The third quarter-inch bolt is loose.",
        "The second point is about staffing.",
        "This may be harder than it looks.",
        "We will march over there after lunch.",
        "Give me two minutes and 2000 more units.",
    ],
)
def test_non_dates_left_alone(text):
    assert not covered(text)
