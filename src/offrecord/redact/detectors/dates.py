"""Date patterns (used by the pattern detector when ``detect.dates`` is on).

Covers written and spoken forms: "March 3rd, 2027", "the third of March", "February, the
eleventh", "Wednesday the 2nd", "the 22nd" (a bare ordinal with "the", when not followed by a
noun), "11/27", "22/05/2027", "01.01.2027", ISO dates, quarters and fiscal years ("Q3", "FY26",
"the third quarter"), month or year after a cue ("in March", "since 2019", "August of 2021"),
"early January", "end of May", "the first week of February", "the spring of 2019", and ranges
("Feb 24 and 25", "July through September 2026").

Relative words ("yesterday", "next Friday") are not dates here.
Recall first: each match is a DATE span with confidence 0.8 (0.6 for the looser forms).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
MONTHS_FULL = (
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|apr|aug|sept?|oct|nov|dec"
)
_U = r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth"
_T = (
    r"tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|"
    r"nineteenth|twentieth|thirtieth"
)
ORD_WORD = rf"(?:(?:twenty|thirty)[\s-]?(?:{_U})|{_T}|{_U})"
ORD_NUM = r"(?:[12]?\d|3[01])(?:st|nd|rd|th)"
ORD = rf"(?:{ORD_NUM}|{ORD_WORD})"
DAY = rf"(?:{ORD}|(?:[12]?\d|3[01])(?![\d:.,]\d))"
YEAR4 = r"(?:19|20)\d{2}"
YEAR_TAIL = rf"(?:,?\s+(?:of\s+)?{YEAR4}\b|,\s+'?\d{{2}}\b)"
RANGE = rf"(?:\s*(?:-|to|and|through|thru|until|&)\s*(?:the\s+)?{DAY})"
WEEKDAY = (
    r"(?:(?:last|next|this)\s+)?(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?"
    r"(?:\s+(?:night|morning|afternoon|evening))?,?"
)

DATES: list[tuple[re.Pattern[str], float]] = [
    # Month first: "March 3rd, 2027", "February, the eleventh", "april nineteenth 2015",
    # "Feb 24 and 25", optionally after a weekday ("tuesday january twentieth").
    (
        re.compile(
            rf"(?i)\b(?:{WEEKDAY}\s+)?(?:{MONTHS})\.?,?\s+(?:the\s+)?{DAY}\b{RANGE}?{YEAR_TAIL}?"
        ),
        0.8,
    ),
    # Day first: "the third of November", "Eleventh of February", "3 March 2027", optionally
    # after a weekday ("Monday the eighteenth of January").
    (
        re.compile(
            rf"(?i)\b(?:{WEEKDAY}\s+)?(?:the\s+)?{DAY}{RANGE}?\s+(?:of\s+)?(?:{MONTHS})\b"
            rf"{YEAR_TAIL}?"
        ),
        0.8,
    ),
    # Weekday + ordinal: "Wednesday the 2nd", "LAST THURSDAY THE 19TH", "Saturday night, the
    # twenty-first".
    (re.compile(rf"(?i)\b{WEEKDAY}\s+(?:the\s+)?{ORD}\b"), 0.8),
    # Month + year: "March 2027", "August of 2021", "July through September 2026".
    (
        re.compile(
            rf"(?i)\b(?:(?:{MONTHS_FULL})\s+(?:through|thru|to|until|-|and)\s+)?"
            rf"(?:{MONTHS_FULL})\s+(?:of\s+)?{YEAR4}\b"
        ),
        0.8,
    ),
    # Numeric: 11/27/2026, 22/05/2027, 2026-11-24, 01.01.2027, and month/day "11/27".
    (re.compile(r"(?<![\d/])\d{1,2}/\d{1,2}/\d{2,4}(?![\d/])"), 0.8),
    (re.compile(r"(?<![\d-])\d{4}-\d{2}-\d{2}(?![\d-])"), 0.8),
    (re.compile(r"(?<![\d.])\d{1,2}\.\d{1,2}\.\d{4}(?!\d|\.\d)"), 0.8),
    (re.compile(r"(?<![\d/.])(?:1[0-2]|0?[1-9])/(?:3[01]|[12]\d|0?[1-9])(?![\d/]|\.\d)"), 0.6),
    # Quarters, halves, fiscal years: "Q3", "Q4 2026", "H2", "FY26", "third quarter".
    (re.compile(r"(?i)\b(?:Q[1-4]|H[12])(?:\s*(?:FY\s?)?'?(?:\d{4}|\d{2}))?\b"), 0.7),
    (re.compile(r"(?i)\bFY\s?'?(?:\d{4}|\d{2})\b"), 0.7),
    (
        re.compile(
            r"(?i)\b(?:first|second|third|fourth|last|1st|2nd|3rd|4th)\s+(?:fiscal\s+)?quarter(?![\w-])"
            rf"(?:\s+of\s+(?:{YEAR4}|this\s+year|next\s+year|last\s+year))?\b"
        ),
        0.6,
    ),
    # Qualified periods: "early January", "end of May", "the first week of February", "the week
    # of the 14th", "the spring of 2019", "mid-March 2027".
    (
        re.compile(
            r"(?i)\b(?:early|mid-?|late|(?:the\s+)?(?:end|start|beginning|middle)\s+of|"
            r"(?:the\s+)?(?:first|second|third|fourth|last)\s+(?:week|weekend)\s+(?:of|in)|"
            rf"(?:the\s+)?week\s+of)\s+(?:(?:{MONTHS_FULL})\b(?:\s+{YEAR4}\b)?|the\s+{ORD}\b)"
        ),
        0.8,
    ),
    (
        re.compile(rf"(?i)\b(?:the\s+)?(?:spring|summer|fall|autumn|winter)\s+(?:of\s+)?{YEAR4}\b"),
        0.8,
    ),
]

# A month or a year after a cue word: only the month/year itself is the date.
CUE_MONTH = re.compile(
    r"(?i)\b(?:in|since|by|until|till|before|after|during|from|through|for|of|last|next)\s+"
    rf"(?P<d>{MONTHS_FULL})\b(?!['-])"
)
CUE_YEAR = re.compile(
    r"(?i)\b(?:in|since|from|by|until|till|before|after|during|of|year|around|circa|"
    rf"class\s+of|back\s+in|early|late|mid-?)\s+(?P<d>{YEAR4})\b(?![\d,.]\d|\s*%)"
)
# "may" and "march" are also common words: only accepted after a cue when what follows is not
# a verb-like continuation ("in may be" never occurs; "by march we" is fine).
_MAY_VERB = re.compile(
    r"(?i)\s+(?:be|have|not|need|want|take|also|still|just|get|go|come|seem|help|well|or|i|we|"
    r"you|they|he|she|it)\b"
)

# A bare ordinal after "the" ("the 22nd", "the eighteenth") is a date unless a noun follows
# ("the third quarter", "the first time"). Words that may follow a date:
_THE_ORD = re.compile(rf"(?i)\bthe\s+(?:{ORD})(?![\w-])")
_FOLLOW_OK = frozenset(
    "at works work is was then if to through thru and or from by for instead would could should "
    "will still no sorry in that which we i you they he she it's its okay ok right yeah please "
    "but so because not with on maybe too as also is fine good great perfect then until "
    "latest earliest morning afternoon evening night the".split()
)
_OF_MONTH = re.compile(rf"(?i)\s+(?:(?:the|next|this)\s+month|{MONTHS})\b")
_NEXT_WORD = re.compile(r"\s*([A-Za-z']+)")


def _bare_ordinal_ok(text: str, end: int) -> bool:
    rest = text[end : end + 40]
    if not rest.strip() or rest.lstrip()[:1] in ".,;:!?)\"'-":
        return True
    m = _NEXT_WORD.match(rest)
    if not m:
        return True
    word = m.group(1).lower()
    if word == "of":  # "the first of March" is caught above; "the first of many" is not a date
        after = rest[m.end() :]
        return (
            re.match(rf"(?i)\s+(?:the\s+month|next\s+month|this\s+month|{MONTHS})\b", after)
            is not None
        )
    return word in _FOLLOW_OK


def find_dates(text: str) -> Iterator[tuple[int, int, float]]:
    """(start, end, confidence) for every date-like span in ``text``."""
    for rx, conf in DATES:
        for m in rx.finditer(text):
            yield m.start(), m.end(), conf
    for m in CUE_MONTH.finditer(text):
        word = m.group("d").lower()
        if word in ("may", "march") and _MAY_VERB.match(text, m.end()):
            continue
        yield m.start("d"), m.end("d"), 0.6
    for m in CUE_YEAR.finditer(text):
        yield m.start("d"), m.end("d"), 0.6
    for m in _THE_ORD.finditer(text):
        if _bare_ordinal_ok(text, m.end()):
            yield m.start(), m.end(), 0.6
