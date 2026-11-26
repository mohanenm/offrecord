"""Regex + validator detectors: email, phone, SSN, card (Luhn), IP, URL, account/order, dates.

Run on both the ``orig`` and ``norm`` views of every field (``norm`` turns spoken digits and
spoken emails into canonical forms). Recall first: unvalidated matches are still redacted,
with lower confidence so they show up as review flags.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterator
from typing import ClassVar

from offrecord.model import Field, Span
from offrecord.normalize.fields import Window
from offrecord.redact.config import Config
from offrecord.redact.detectors.dates import find_dates

TLDS = (
    "com|org|net|io|co|edu|gov|mil|int|uk|us|de|fr|ca|au|in|biz|info|ai|app|dev|me|ly|nl|es|it|"
    "jp|ch|se|no|eu|tv|xyz|cloud|tech|example|test|invalid|local|internal|corp|ie|nz|sg|za|br|mx"
)
EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PHONE_SEP = re.compile(
    r"(?<![\w+])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])\d{3,4}[\s.-]?\d{3,4}(?!\w)"
)
PHONE7 = re.compile(r"(?<![\w-])\d{3}[\s.-]\d{4}(?![\w-])")
SSN = re.compile(r"(?<![\d-])\d{3}[\s-]\d{2}[\s-]\d{4}(?![\d-])")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
DIGIT_RUN = re.compile(r"(?<![\w.,])\d{7,19}(?![\w]|[.,]\d)")
IPV4 = re.compile(
    r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d]|\.\d)"
)
IPV6 = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+")
DOMAIN = re.compile(rf"(?i)(?<![\w@.-])(?:[a-z0-9-]+\.)+(?:{TLDS})\b(?:/[^\s<>\"']*)?")
ACCOUNT_CODE = re.compile(r"(?i)(?<![\w-])(?:acct|ac|ord|po|inv|ref|tkt|id)[-#:]?\d{4,}[\w-]*")
HASH_ID = re.compile(r"(?<![\w#])#\s?[A-Za-z]{0,3}\d{3,}[\w-]*")
ALNUM_ID = re.compile(r"(?<![\w-])[A-Za-z]{1,4}-?\d{5,}[A-Za-z0-9-]*(?![\w-])")
NUMBER_TRIGGER = re.compile(
    r"(?i)\b(?:account|acct|order|ticket|reference|ref|invoice|case|claim|policy|member|"
    r"confirmation|tracking|store|routing|customer|id|serial|number|no\.?|licen[cs]e|passport|"
    r"vin|iban|swift|bic|national\s+insurance|ni|tax|nhs|medicare|employee|badge)\b"
    r"(?:\s+(?:number|no\.?|id|is|was|#))*[\s#:.-]*(?P<num>[A-Za-z]{0,3}[\d][\d\s-]{2,}\d)"
)
PHONE_TRIGGER = re.compile(
    r"(?i)\b(?:phone|cell|mobile|line|extension|ext|direct|number|call(?:\s+me)?(?:\s+(?:at|on))?|"
    r"reach\s+me(?:\s+(?:at|on))?|text\s+me(?:\s+(?:at|on))?)\b[^.\d]{0,24}?(?P<num>\d[\d\s-]{1,}\d|\d)"
)
LAST_FOUR = re.compile(
    r"(?i)\b(?:last\s+(?:four|4)|ending\s+in|ends\s+in)\b\D{0,12}(?P<num>\d{4})\b"
)
SSN_CONTEXT = re.compile(r"(?i)\b(?:social|ssn|ss#|security\s+number|tax\s+id|itin|tin)\b")
CORRECTION_CUE = re.compile(
    r"(?i)\b(?:sorry|i mean|no wait|wait|scratch that|let me (?:redo|repeat) that|correction|"
    r"actually|oops|my bad|rather|make that)\b"
)
DIGIT_GROUP = re.compile(r"\d[\d \t-]{0,40}\d|\d")
LOOK = 40  # characters searched on each side of a correction cue
STREET_WORDS = (
    "street|st|road|rd|avenue|ave|lane|ln|drive|dr|way|boulevard|blvd|court|ct|place|pl|"
    "terrace|close|crescent|square|sq|parkway|pkwy|highway|hwy|row|mews|grove|gardens|circle|"
    "cir|trail|walk|hill|park|alley|plaza|loop|run|path|pike"
)
ADDRESS = re.compile(
    rf"(?i)\b\d{{1,6}}[a-z]?\s+(?:[a-z][\w'-]*\s+){{0,3}}(?:{STREET_WORDS})\b\.?"
    r"(?:,?\s+(?:flat|apt|apartment|suite|unit|floor|fl|#)\.?\s*\w{1,5})?"
)
UNIT = re.compile(
    r"(?i)\b(?:flat|apt|apartment|suite|unit|po\s+box|p\.o\.\s+box)\.?\s*#?\d{1,6}\w?\b"
)
US_STATES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|"
    "NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY"
)
POSTCODES = [
    re.compile(r"(?i)(?<![\w])[a-z]{1,2}\d[a-z\d]?\s*\d[a-z]{2}(?![\w])"),  # UK
    re.compile(rf"(?<![\w])(?:{US_STATES})\.?\s+\d{{5}}(?:-\d{{4}})?(?!\d)", re.IGNORECASE),  # US
    re.compile(r"(?<!\d)\d{5}-\d{4}(?!\d)"),  # ZIP+4
    re.compile(r"(?i)(?<![\w])[a-z]\d[a-z]\s?\d[a-z]\d(?![\w])"),  # Canada
]
POSTCODE_TRIGGER = re.compile(
    r"(?i)\b(?:zip|zipcode|zip\s+code|postcode|post\s+code|postal\s+code)\b"
    r"\W{0,12}(?P<num>[a-z0-9][a-z0-9 -]{2,9}[a-z0-9])"
)
# What may follow a street address: unit, then ", City", ", State", ZIP or postcode, in order.
ADDRESS_TAIL = re.compile(
    r"(?:,?\s+(?:flat|apt|apartment|suite|ste|unit|floor|fl|#)\.?\s*#?\w{1,6}\b)?"
    r"(?:,\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,2}){0,2}"
    rf"(?:,?\s+(?:(?:{US_STATES})\.?\s+)?\d{{5}}(?:-\d{{4}})?(?!\d)"
    r"|,?\s+[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b)?"
)
INTL_PHONE = re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s.-]?\(?\d{1,5}\)?){2,6}(?!\w)")
IBAN = re.compile(r"(?i)\b[a-z]{2}\d{2}(?:\s?[a-z0-9]{4}){2,7}(?:\s?[a-z0-9]{1,4})?\b")
MIXED_ID = re.compile(r"(?<![\w-])(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{6,40}(?![\w-])")
UK_NI = re.compile(r"(?i)\b[a-z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[a-d]\b")
SORT_CODE = re.compile(r"(?<![\d-])\d{2}-\d{2}-\d{2}(?![\d-])")
MAC = re.compile(r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b")
EXPIRY = re.compile(
    r"(?i)\b(?:exp(?:iry|ires|iration)?|valid\s+(?:thru|through|until))\b\D{0,12}(?P<num>(?:0?[1-9]|1[0-2])\s?/\s?\d{2,4})"
)
# A dictated identifier: a run of letters, digit groups, digit words, "dash", phonetic letters
# and symbol words ("B O L dash seven seven four one nine", "SO 4471 9930", "two six whiskey
# lima, zero zero four", "capital T as in tango, lowercase r, ..."). Kept only with enough digits.
_DIGW = r"zero|oh|one|two|three|four|five|six|seven|eight|nine|double|triple"
_NATOW = (
    r"alpha|alfa|bravo|charlie|delta|echo|foxtrot|golf|hotel|india|juliett?|kilo|lima|mike|"
    r"november|oscar|papa|quebec|romeo|sierra|tango|uniform|victor|whiske?y|xray|x-ray|yankee|zulu"
)
_SYMW = (
    r"dash|hyphen|capital|lowercase|uppercase|exclamation(?:\s+(?:mark|point))?|underscore|hash|"
    r"pound|star|asterisk|at\s+sign|dollar\s+sign|ampersand|slash"
)
_ID_TOK = (
    rf"(?:\d[\dA-Za-z]*|[A-Za-z]{{1,4}}\d[\dA-Za-z]*|[A-Z]{{1,4}}(?![a-z'])|[a-z](?![\w'])"
    rf"|(?i:[a-z]\s+as\s+in\s+[a-z]+)|(?i:(?:{_DIGW}|{_NATOW}|{_SYMW})(?![\w'])))"
)
ID_SEQ = re.compile(rf"(?<![\w'])(?:{_ID_TOK})(?:(?:\s*,\s*|\s+|\s*-\s*)(?:{_ID_TOK}))+")
_DIGIT_WORD = re.compile(rf"(?i)^(?:{_DIGW})$")
_LETTER_TOK = re.compile(rf"(?i)^(?:[a-z]|{_NATOW}|[a-z]\s+as\s+in\s+[a-z]+)$")
_ID_PIECES = re.compile(r"(?i)[a-z]\s+as\s+in\s+[a-z]+|[\w']+")
FIGURE_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")
CURRENCY = {"$", "\u20ac", "\u00a3", "\u00a5"}
ID_TRIGGER = re.compile(
    r"(?i)\b(?:matter|docket|parcel|mls|bol|pro|ncr|lot|kit|receipt|loyalty|permit|subject|po|so|"
    r"trk|mrn|sku|serial|site|unit)\b(?:\s+(?:number|no\.?|#|is|was))*[\s#:.-]*"
    r"(?P<num>[A-Za-z]{0,3}\d[\d\s-]{1,}\d|0\d{2})"
)


# The last digits of a card or account: "the card ending 8605", "last four are 3317".
LAST_DIGITS = re.compile(
    r"(?i)\b(?:ending(?:\s+in)?|last\s+(?:four|4|five|5)(?:\s+(?:digits|numbers))?"
    r"(?:\s+(?:are|is|of))?)\s*[:#]?\s*(?P<num>\d{3,5})\b"
)
CARD_WORD = re.compile(r"(?i)\b(?:card|visa|mastercard|amex|debit|credit)\b")
# A bare number of four or more digits is an identifier or code more often than not
# (verification codes, ticket, store and member numbers). Years, figures with separators,
# amounts with a currency sign and decimals are excluded.
LONG_NUMBER = re.compile(
    r"(?<![\w$\u20ac\u00a3.,/:-])(?!(?:19|20)\d\d(?!\d))\d{4,}(?![\w.,/:-]|\.\d)"
    r"(?!\s*(?:%|k\b|m\b|bn\b|thousand|million|billion|percent|dollars?|bucks|euros?|pounds))"
)


def _id_digit_units(seq: str) -> tuple[int, int, int]:
    """(digit units, letter units, tokens) in a dictated identifier."""
    digits = letters = toks = 0
    for piece in _ID_PIECES.findall(seq):
        toks += 1
        if any(c.isdigit() for c in piece):
            digits += sum(c.isdigit() for c in piece)
            letters += sum(c.isalpha() for c in piece)
        elif _DIGIT_WORD.match(piece):
            digits += 1
        elif _LETTER_TOK.match(piece):
            letters += 1
    return digits, letters, toks


# Dictated emails the normaliser cannot rebuild: "kwame dot okoro plus wedding at inbox dot
# example", "yusra.bell at fenmoor dash analytics dot example", "front office at hollinbrook
# dot k twelve dot example" (one extra word allowed per label for split or spoken parts).
_SEP = r"(?:dot|underscore|dash|hyphen|plus|period)"
SPOKEN_EMAIL = re.compile(
    rf"(?i)(?<![\w@])(?P<pre>[\w'-]+\s+)?(?P<local>[\w.'+-]+(?:\s+{_SEP}\s+[\w.'+-]+){{0,5}})\s+at\s+"
    rf"(?:[\w-]+(?:\s+[\w-]+)?\s+(?:dot|dash|hyphen)\s+){{1,5}}(?:{TLDS})\b"
)
_CANON_SEP = {"dot": ".", "period": ".", "underscore": "_", "dash": "-", "hyphen": "-", "plus": "+",
              "at": "@"}  # fmt: skip
_CANON_RE = re.compile(r"(?i)\s+(dot|period|underscore|dash|hyphen|plus|at)\s+")


def canon_spoken_email(s: str) -> str:
    """'kwame dot okoro plus x at inbox dot example' -> 'kwame.okoro+x@inbox.example'."""
    out = _CANON_RE.sub(lambda m: _CANON_SEP[m.group(1).lower()], s.strip())
    return re.sub(r"\s+", "", out).lower()


# Spelled confirmations: "B DOT TREMAYNE", "H dot V O S S B E R G".
SPELLED_EMAIL = re.compile(r"(?i)\b[a-z]\s+dot\s+(?:(?:[a-z]\s+){2,}[a-z]\b|[a-z][a-z'-]+)")


SECRET_TRIGGER = re.compile(
    r"(?i)\b(?:pin|pin\s+code|cvv|cvc|cv2|security\s+code|passcode|password|sort\s+code|"
    r"verification\s+code|one[- ]time\s+code|otp|access\s+code)\b"
    r"(?:\s*(?:is|was|number|code|:|-))*\s*(?P<num>[\w-]{3,20})"
)
SHORT_RUN = re.compile(r"(?<![\w.,])\d{4,6}(?![\w]|[.,]\d)")
SCALE_WORDS = re.compile(r"(?i)\b(?:hundred|thousand|million|billion|trillion|point)\b")


def iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "").upper()
    if not (15 <= len(s) <= 34) or not s[:2].isalpha():
        return False
    moved = s[4:] + s[:4]
    try:
        num = "".join(str(int(c, 36)) for c in moved)
    except ValueError:
        return False
    return int(num) % 97 == 1


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


class PatternDetector:
    name: ClassVar[str] = "patterns"
    categories: ClassVar[frozenset[str]] = frozenset(
        {"EMAIL", "PHONE", "SSN", "CARD", "IP", "URL", "ACCOUNT", "DATE"}
    )

    def __init__(self, config: Config) -> None:
        self.dates = config.detect_dates

    def detect(self, window: Window) -> Iterator[Span]:
        for f in window.fields:
            for view in ("orig", "norm"):
                text = f.views[view].text
                if not any(c.isdigit() or c in "@." for c in text) and not self.dates:
                    continue
                yield from self._scan(f, view, text)

    def _span(self, f: Field, view: str, s: int, e: int, cat: str, conf: float, ent: str,
              reason: str) -> Span:  # fmt: skip
        return Span(f.id, s, e, cat, f"patterns.{cat.lower()}", conf, ent, reason, view=view)

    def _scan(self, f: Field, view: str, text: str) -> Iterator[Span]:
        sp = self._span
        for m in EMAIL.finditer(text):
            yield sp(f, view, m.start(), m.end(), "EMAIL", 0.95, m.group().lower(), "email")
        if view == "orig":
            for m in SPOKEN_EMAIL.finditer(text):
                # The extra leading word is only for a one-word spoken local part ("front office
                # at ..."), never in front of "zeb dot quux at ...".
                start = m.start()
                if m.group("pre") and re.search(rf"(?i)\s{_SEP}\s", m.group("local")):
                    start = m.start("local")
                ent = canon_spoken_email(text[m.start("local") : m.end()])
                yield sp(f, view, start, m.end(), "EMAIL", 0.6, ent, "email:dictated")
            for m in SPELLED_EMAIL.finditer(text):
                yield sp(f, view, m.start(), m.end(), "EMAIL", 0.6, canon_spoken_email(m.group()),
                         "email:spelled")  # fmt: skip
        for rx in (URL, DOMAIN):
            for m in rx.finditer(text):
                s, e = m.start(), m.end()
                while e > s and text[e - 1] in ".,;:!?)'\"":
                    e -= 1
                if "@" in text[max(0, s - 1) : s]:
                    continue
                yield sp(f, view, s, e, "URL", 0.9, text[s:e].lower(), "url")
        for m in IPV4.finditer(text):
            yield sp(f, view, m.start(), m.end(), "IP", 1.0, m.group(), "ipv4")
        for m in IPV6.finditer(text):
            g = m.group()
            if "::" not in g and g.count(":") < 4:
                continue  # "10:30:00" is a time, not an address
            try:
                ipaddress.IPv6Address(g)
            except ValueError:
                continue
            yield sp(f, view, m.start(), m.end(), "IP", 1.0, g.lower(), "ipv6")
        for m in SSN.finditer(text):
            yield sp(f, view, m.start(), m.end(), "SSN", 0.9, _digits(m.group()), "ssn")
        for m in CARD.finditer(text):
            d = _digits(m.group())
            if 13 <= len(d) <= 19:
                ok = luhn_ok(d)
                yield sp(f, view, m.start(), m.end(), "CARD", 1.0 if ok else 0.5, d,
                         "card:luhn" if ok else "card:noluhn")  # fmt: skip
        for rx in (PHONE_SEP, PHONE7):
            for m in rx.finditer(text):
                d = _digits(m.group())
                if len(d) >= 7:
                    yield sp(f, view, m.start(), m.end(), "PHONE", 0.85, d, "phone")
        for m in DIGIT_RUN.finditer(text):
            yield from self._bare_digits(f, view, text, m.start(), m.end())
        yield from self._corrections(f, view, text)
        yield from self._ids(f, view, text)
        if view == "norm":
            for m in SHORT_RUN.finditer(text):
                a, b = f.project(view, m.start(), m.end())
                src = f.orig[a:b]
                if not any(c.isdigit() for c in src) and not SCALE_WORDS.search(src):
                    # Four or more digits read out one by one: an identifier, PIN or number.
                    yield sp(
                        f, view, m.start(), m.end(), "ACCOUNT", 0.6, m.group(), "digits:spoken"
                    )
        for m in PHONE_TRIGGER.finditer(text):
            s, e = m.span("num")
            yield sp(f, view, s, e, "PHONE", 0.5, _digits(m.group("num")), "phone:context")
        for m in LAST_FOUR.finditer(text):
            s, e = m.span("num")
            yield sp(f, view, s, e, "CARD", 0.6, m.group("num"), "card:last4")
        for m in NUMBER_TRIGGER.finditer(text):
            s, e = m.span("num")
            if len(_digits(m.group("num"))) >= 4:
                yield sp(f, view, s, e, "ACCOUNT", 0.6, _digits(m.group("num")), "account:context")
        for rx in (ACCOUNT_CODE, HASH_ID, ALNUM_ID):
            for m in rx.finditer(text):
                yield sp(
                    f, view, m.start(), m.end(), "ACCOUNT", 0.7, m.group().lower(), "account:id"
                )
        if self.dates:
            for s0, e0, conf, coarse in find_dates(text):
                reason = "date:coarse" if coarse else "date"
                yield sp(f, view, s0, e0, "DATE", conf, text[s0:e0].lower(), reason)

    def _corrections(self, f: Field, view: str, text: str) -> Iterator[Span]:
        """ "555... sorry, 556-0142" / "4471, sorry, 4472": digit groups right before and right
        after a correction cue belong to the number (both the abandoned and the real one).
        Linear: cues are found first, then a bounded window on each side is searched."""
        if not any(c.isdigit() for c in text):
            return
        for cue in CORRECTION_CUE.finditer(text):
            before = text[max(0, cue.start() - LOOK) : cue.start()]
            groups = list(DIGIT_GROUP.finditer(before))
            if groups and before[groups[-1].end() :].strip(" .,;:-") == "":
                g = groups[-1]
                off = max(0, cue.start() - LOOK)
                yield self._span(f, view, off + g.start(), off + g.end(), "PHONE", 0.5,
                                 _digits(g.group()), "number:abandoned")  # fmt: skip
                after = text[cue.end() : cue.end() + LOOK]
                g2 = DIGIT_GROUP.search(after)
                if g2 and after[: g2.start()].strip(" .,;:-") in ("", "it's", "its", "make that"):
                    yield self._span(f, view, cue.end() + g2.start(), cue.end() + g2.end(), "PHONE",
                                     0.5, _digits(g2.group()), "number:corrected")  # fmt: skip

    def _ids(self, f: Field, view: str, text: str) -> Iterator[Span]:
        sp = self._span
        if view == "orig":
            for m in ID_SEQ.finditer(text):
                if (
                    FIGURE_RE.match(m.group())
                    or text[max(0, m.start() - 1) : m.start()] in CURRENCY
                ):
                    continue  # "1,650" or "$42,000" is a figure, not a dictated ID
                d, letters, toks = _id_digit_units(m.group())
                if d >= 4 or (d >= 2 and letters >= 3 and toks >= 6):
                    yield sp(f, view, m.start(), m.end(), "ACCOUNT", 0.6, m.group().lower(),
                             "id:dictated")  # fmt: skip
        for m in ID_TRIGGER.finditer(text):
            yield sp(f, view, m.start(), m.end(), "ACCOUNT", 0.6, m.group("num").lower(),
                     "id:context")  # fmt: skip
        for m in LAST_DIGITS.finditer(text):
            cat = "CARD" if CARD_WORD.search(text, max(0, m.start() - 40), m.start()) else "ACCOUNT"
            yield sp(f, view, m.start("num"), m.end("num"), cat, 0.6, m.group("num"),
                     "id:last-digits")  # fmt: skip
        if view == "orig":  # the norm view turns "three point two million" into digits
            for m in LONG_NUMBER.finditer(text):
                yield sp(f, view, m.start(), m.end(), "ACCOUNT", 0.5, m.group(), "id:long-number")
        for m in ADDRESS.finditer(text):
            tail = ADDRESS_TAIL.match(text, m.end())  # all parts optional: always matches
            end = tail.end() if tail else m.end()
            yield sp(f, view, m.start(), end, "LOCATION", 0.7, m.group().lower(), "address")
        for m in UNIT.finditer(text):
            yield sp(
                f, view, m.start(), m.end(), "LOCATION", 0.6, m.group().lower(), "address:unit"
            )
        for rx in POSTCODES:
            for m in rx.finditer(text):
                yield sp(
                    f, view, m.start(), m.end(), "LOCATION", 0.6, m.group().lower(), "postcode"
                )
        for m in POSTCODE_TRIGGER.finditer(text):
            s, e = m.span("num")
            yield sp(f, view, s, e, "LOCATION", 0.6, m.group("num").lower(), "postcode:context")
        if not any(c.isdigit() for c in text):
            return
        for m in INTL_PHONE.finditer(text):
            if len(_digits(m.group())) >= 8:
                yield sp(
                    f, view, m.start(), m.end(), "PHONE", 0.85, _digits(m.group()), "phone:intl"
                )
        for m in IBAN.finditer(text):
            ok = iban_ok(m.group())
            key = m.group().replace(" ", "").upper()
            reason = "iban" if ok else "iban:nocheck"
            yield sp(f, view, m.start(), m.end(), "ACCOUNT", 1.0 if ok else 0.6, key, reason)
        for m in MIXED_ID.finditer(text):
            if len(_digits(m.group())) >= 4:
                yield sp(f, view, m.start(), m.end(), "ACCOUNT", 0.6, m.group().lower(), "id:mixed")
        for rx, reason in ((UK_NI, "uk-ni"), (SORT_CODE, "sort-code"), (MAC, "mac")):
            for m in rx.finditer(text):
                yield sp(f, view, m.start(), m.end(), "ACCOUNT", 0.7, m.group().lower(), reason)
        for rx, cat, reason in (
            (EXPIRY, "CARD", "card:expiry"),
            (SECRET_TRIGGER, "ACCOUNT", "secret:context"),
        ):
            for m in rx.finditer(text):
                s, e = m.span("num")
                if any(c.isdigit() for c in m.group("num")) or cat == "ACCOUNT":
                    yield sp(f, view, s, e, cat, 0.6, m.group("num").lower(), reason)

    def _bare_digits(self, f: Field, view: str, text: str, s: int, e: int) -> Iterator[Span]:
        d = text[s:e]
        if view != "orig":
            a, b = f.project(view, s, e)
            if SCALE_WORDS.search(f.orig[a:b]):
                return  # a spoken cardinal ("two point five million"), handled as an amount
        before = text[max(0, s - 60) : s]
        n = len(d)
        if 13 <= n <= 19 and luhn_ok(d):
            yield self._span(f, view, s, e, "CARD", 0.9, d, "card:bare")
        elif n == 9 and SSN_CONTEXT.search(before):
            yield self._span(f, view, s, e, "SSN", 0.8, d, "ssn:context")
        elif n in (7, 10, 11):
            yield self._span(f, view, s, e, "PHONE", 0.6, d, "phone:bare")
        else:
            yield self._span(f, view, s, e, "ACCOUNT", 0.5, d, "digits:bare")
