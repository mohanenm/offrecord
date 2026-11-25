"""Tier 0 heuristics for names that are not in the deny-list (case-independent).

1. Cue phrases: "my name is X", "this is X", "hi X", "ask X", "Mr X", ... -> X is a PERSON when
   it is not a common word (or is a known given name).
2. Proper-name list: a token in the bundled proper-name list that is not also a common word,
   plus a following non-common token (surname) -> PERSON.
3. Unknown words: alphabetic tokens (3+ letters) that are in neither word list. In speech
   transcripts these are mostly names, brands and places. Redacted (or flagged) as NAME with low
   confidence, so they show up as review flags. Configurable: ``detect.unknown_words``.

Speaker labels themselves are always scanned (as fields); their names also feed the deny-list
detector so later mentions are caught.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator
from typing import ClassVar, Literal

from offrecord.adapters._common import GENERIC_SPEAKER_RE
from offrecord.model import Field, Span
from offrecord.normalize.fields import Window
from offrecord.redact.config import Config
from offrecord.redact.wordlists import FUNCTION_WORDS, is_common, proper_names

TOKEN_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")
CUES = re.compile(
    r"(?i)\b(?:my\s+name\s+is|name's|names\s+is|this\s+is|i'm|i\s+am|it's|speaking\s+with|"
    r"talking\s+to|talk\s+to|talked\s+to|spoke\s+(?:to|with)|ask|asked|loop\s+in|looping\s+in|"
    r"cc|copy|copying|hi|hey|hello|thanks|thank\s+you|bye|dear|mr|mrs|ms|miss|mx|dr|prof|"
    r"sir|madam|tell|told|meet|met|with|from|for|by|and|or|to|named|called|via|per|ping|pinged|"
    r"email|emailed|call|called|text|texted|introduce|introducing|welcome|congrats|"
    r"congratulations|manager|boss|colleague|contact|rep|assistant|lead|head)\.?\s+"
    r"(?P<name>[^\W\d_]+(?:\s+[^\W\d_]+)?)"
)
STRONG_CUES = re.compile(
    r"(?i)^(?:my\s+name\s+is|name's|speaking\s+with|mr|mrs|ms|miss|mx|dr|prof|dear|named|called)"
)
# Titles and strong cues: the next one or two words are a name, whatever they are.
TITLE_CUE = re.compile(
    r"(?i)\b(?:my\s+name\s+is|my\s+name's|name's|this\s+is|i'm|named|called|"
    r"mr|mrs|ms|miss|mx|dr|doctor|prof|professor|sgt|sergeant|lt|lieutenant|capt|captain|"
    r"col|colonel|gen|general|officer|detective|det|agent|judge|justice|father|fr|sister|"
    r"brother|rev|reverend|pastor|rabbi|imam|coach|mayor|senator|sen|rep|governor|gov|"
    r"president|dean|nurse|aunt|uncle|lord|lady|sir|dame|madam)\.?\s+"
    r"(?P<name>[^\W\d_]+(?:[-'][^\W\d_]+)?(?:\s+[^\W\d_]+(?:[-'][^\W\d_]+)?)?)"
)
INITIALS = re.compile(r"(?i)(?<![\w.])(?:[a-z]\.\s?){1,3}(?:[a-z]\.|[a-z][\w'-]{1,})")
NOT_INITIALS = re.compile(r"(?i)^(?:e\.\s?g\.|i\.\s?e\.|a\.\s?m\.|p\.\s?m\.|u\.\s?s\.|u\.\s?k\.)")


# Words that extend a detected name into a place or organisation name ("Port Ellery",
# "Corbin Heights", "Thornquist Freight"). General English naming patterns.
PLACE_BEFORE = frozenset(
    [
        "port",
        "east",
        "west",
        "north",
        "south",
        "new",
        "lake",
        "mount",
        "mt",
        "fort",
        "saint",
        "st",
        "upper",
        "lower",
        "greater",
        "old",
        "san",
        "santa",
        "los",
        "las",
        "el",
        "la",
        "le",
        "de",
        "del",
        "van",
        "von",
    ]
)
PLACE_AFTER = frozenset(
    [
        "heights",
        "falls",
        "bay",
        "county",
        "city",
        "park",
        "valley",
        "springs",
        "spring",
        "island",
        "isles",
        "beach",
        "hills",
        "hill",
        "harbor",
        "harbour",
        "creek",
        "ridge",
        "point",
        "village",
        "town",
        "township",
        "district",
        "station",
        "square",
        "street",
        "avenue",
        "ave",
        "road",
        "rd",
        "lane",
        "drive",
        "way",
        "bridge",
        "cove",
        "crossing",
        "junction",
        "grove",
        "landing",
        "meadows",
        "mills",
        "shores",
        "sound",
        "summit",
        "terrace",
        "vale",
        "view",
        "woods",
        "field",
        "fields",
        "commons",
        "wharf",
        "yards",
        "centre",
        "center",
        "library",
        "hall",
        "court",
        "mall",
        "plaza",
        "manor",
        "quay",
        "docks",
        "estate",
    ]
)
ORG_AFTER = frozenset(
    [
        "inc",
        "incorporated",
        "ltd",
        "limited",
        "llc",
        "llp",
        "plc",
        "corp",
        "corporation",
        "co",
        "company",
        "group",
        "holdings",
        "partners",
        "analytics",
        "health",
        "healthcare",
        "foods",
        "food",
        "logistics",
        "bank",
        "banking",
        "media",
        "insurance",
        "assurance",
        "mutual",
        "freight",
        "retail",
        "air",
        "airlines",
        "airways",
        "labs",
        "lab",
        "systems",
        "technologies",
        "technology",
        "tech",
        "solutions",
        "capital",
        "ventures",
        "outfitters",
        "industries",
        "international",
        "global",
        "consulting",
        "consultants",
        "services",
        "software",
        "pharma",
        "pharmaceuticals",
        "energy",
        "motors",
        "api",
        "pro",
        "suite",
        "console",
        "cloud",
        "platform",
        "studio",
        "studios",
        "foundation",
        "trust",
        "fund",
        "university",
        "college",
        "hospital",
        "clinic",
        "networks",
        "network",
        "robotics",
        "dynamics",
        "enterprises",
        "associates",
        "brands",
        "financial",
        "finance",
        "securities",
        "realty",
        "properties",
        "manufacturing",
        "supply",
        "supplies",
        "brothers",
        "bros",
        "sons",
        "daughters",
        "builders",
        "construction",
        "contractors",
        "roofing",
        "plumbing",
        "electric",
        "restoration",
        "title",
        "lending",
        "mortgage",
        "provisions",
        "lifting",
        "interiors",
        "mix",
        "specialty",
        "union",
        "federal",
        "medical",
        "elementary",
        "middle",
        "academy",
        "school",
        "plan",
        "water",
        "sorting",
        "clinical",
        "hotel",
        "dental",
        "pediatrics",
        "practice",
        "law",
        "legal",
        "agency",
        "transport",
        "trucking",
        "distribution",
        "wholesale",
        "bakery",
        "kitchen",
        "cafe",
        "market",
        "markets",
    ]
)


# Places without a list of place names: roads by number, capitalised words before a place word
# ("High Street", "Wicker Park", even when the words are ordinary), and capitalised words after
# a location cue ("in Fort Worth", "out of Cape Town", "our Leeds store").
ROUTE_RE = re.compile(
    r"(?i)\b(?:(?:route|highway|hwy|interstate|state\s+route)\s+\d{1,3}|i-\d{1,3})[a-z]?\b"
)
PLACE_CUE = re.compile(
    r"(?i)\b(?:in|at|from|out\s+of|near|across|into|outside|based\s+in|(?:moved|moving|"
    r"relocating|relocated|headed|heading|flying|flew|drove|driving)\s+to|back\s+home\s+in)"
    r"\s+(?:the\s+)?$"
)
# "our Leeds store", "our Tulsa office": "our" counts as a cue only before a site noun.
OUR_SITE = re.compile(
    r"(?i)\s+(?:store|office|site|branch|plant|warehouse|location|depot|clinic|campus|"
    r"factory|team|region|market)\b"
)
STREET_SUFFIX = frozenset("street road avenue lane drive boulevard parkway highway square".split())
NOT_PLACE = frozenset(
    "january february march april may june july august september october november december "
    "monday tuesday wednesday thursday friday saturday sunday i english spanish french german "
    "italian portuguese chinese mandarin japanese korean arabic hindi russian excel".split()
)
# Place words that are also everyday nouns: after a single common word they make a phrase
# ("Good point", "Either way", "Next drive"), not a place.
AMBIGUOUS_PLACE_WORDS = frozenset("point way drive spring springs falls crossing".split())


def _is_cap(w: str) -> bool:
    return w[:1].isupper()


def _expand(text: str, toks: list[re.Match[str]], i: int, j: int) -> tuple[int, int, str | None]:
    """Extend tokens i..j over adjacent place/org words. Returns (first, last, category).

    Also bridges up to two capitalised words that lead to a place/org word ("Larchmere Family
    Health", "Graniteford Ready Mix") and joins names with "and" ("Pratt Ivey and Sola",
    "Overbeck and Sons")."""
    cat = None
    while (
        i > 0
        and toks[i - 1].group().lower() in PLACE_BEFORE
        and _adjacent(text, toks[i - 1], toks[i])
    ):
        i -= 1
        cat = "LOCATION"
    n = len(toks)
    while j + 1 < n and _adjacent(text, toks[j], toks[j + 1]):
        w = toks[j + 1].group().lower()
        if w in PLACE_AFTER:
            cat = "LOCATION"
            j += 1
            continue
        if w in ORG_AFTER:
            cat = "ORG"
            j += 1
            continue
        # "X Group and Y Group" are two organisations: only join before any suffix.
        if w == "and" and cat is None and j + 2 < n and _adjacent(text, toks[j + 1], toks[j + 2]):
            nxt = toks[j + 2].group()
            if nxt.lower() in ORG_AFTER or (
                _is_cap(nxt) and nxt.lower() not in FUNCTION_WORDS and not is_common(nxt)
            ):
                cat = cat or "ORG"
                j += 2
                continue
            break
        # Bridge: one or two capitalised words, then a place/org word.
        done = False
        for k in (j + 1, j + 2):
            if k + 1 >= n or not all(
                _is_cap(toks[x].group())
                and toks[x].group().lower() not in FUNCTION_WORDS
                and _adjacent(text, toks[x - 1], toks[x])
                for x in range(j + 1, k + 1)
            ):
                break
            last = toks[k + 1].group().lower()
            if _adjacent(text, toks[k], toks[k + 1]) and (last in ORG_AFTER or last in PLACE_AFTER):
                cat = "LOCATION" if last in PLACE_AFTER else "ORG"
                j = k + 1
                done = True
                break
        if not done:
            break
    return i, j, cat


def _adjacent(text: str, a: re.Match[str], b: re.Match[str]) -> bool:
    gap = text[a.end() : b.start()]
    return len(gap) <= 2 and gap.strip(" -") == ""


class PeopleDetector:
    name: ClassVar[str] = "people"
    categories: ClassVar[frozenset[str]] = frozenset({"PERSON", "NAME"})

    def __init__(self, config: Config) -> None:
        self.enabled = config.detect_people
        self.unknown = config.unknown_words
        self.proper = proper_names()

    def detect(self, window: Window) -> Iterator[Span]:
        if not self.enabled:
            return
        for f in window.fields:
            if f.kind == "speaker":
                yield from self._speaker(f)
                continue
            text = f.views["norm"].text
            yield from self._cues(f, text)
            yield from self._titles(f, text)
            yield from self._tokens(f, text)
            yield from self._places(f, text)
            if f.kind in ("meta", "extra"):
                yield from self._short_field(f, text)

    def _speaker(self, f: Field) -> Iterator[Span]:
        label = f.orig.strip()
        if not label or GENERIC_SPEAKER_RE.match(label):
            return
        s = f.orig.index(label)
        yield Span(f.id, s, s + len(label), "PERSON", "people.speaker", 0.9, None,
                   "speaker-label")  # fmt: skip

    def _titles(self, f: Field, text: str) -> Iterator[Span]:
        for m in TITLE_CUE.finditer(text):
            words = m.group("name").split()
            take = []
            for w in words:
                if w.lower() in FUNCTION_WORDS:
                    break
                take.append(w)
            if not take:
                continue
            s = m.start("name")
            e = s + len(" ".join(take))
            yield Span(f.id, s, e, "PERSON", "people.title", 0.6, None, "person:title", view="norm")
        for m in INITIALS.finditer(text):
            if NOT_INITIALS.match(m.group()):
                continue
            yield Span(f.id, m.start(), m.end(), "PERSON", "people.initials", 0.5, None,
                       "person:initials", view="norm")  # fmt: skip

    def _short_field(self, f: Field, text: str) -> Iterator[Span]:
        """A metadata value of 1-3 words containing a known given name is a name ("Mark")."""
        words = TOKEN_RE.findall(text)
        if 1 <= len(words) <= 3 and any(w.lower() in self.proper for w in words):
            s = text.find(words[0])
            e = text.rfind(words[-1]) + len(words[-1])
            yield Span(
                f.id, s, e, "PERSON", "people.meta", 0.5, None, "person:meta-name", view="norm"
            )

    def _cues(self, f: Field, text: str) -> Iterator[Span]:
        for m in CUES.finditer(text):
            words = m.group("name").split()
            strong = bool(STRONG_CUES.match(m.group()))
            take = []
            for w in words:
                lw = w.lower()
                if lw in FUNCTION_WORDS:
                    break
                if (not is_common(lw) or lw in self.proper or strong) and len(w) >= 2:
                    take.append(w)
                else:
                    break
            if not take:
                continue
            s = m.start("name")
            e = s + len(" ".join(take)) if len(take) == len(words) else s + len(take[0])
            if len(take) == 2:
                e = m.end("name")
            yield Span(f.id, s, e, "PERSON", "people.cue", 0.6, None, "person:cue", view="norm")

    def _places(self, f: Field, text: str) -> Iterator[Span]:
        for m in ROUTE_RE.finditer(text):
            yield Span(f.id, m.start(), m.end(), "LOCATION", "people.place", 0.6, None,
                       "place:route", view="norm")  # fmt: skip
        toks = list(TOKEN_RE.finditer(text))
        n = len(toks)
        # "<word> Road" pairs need no capitals (ASR text is often all lower or upper case).
        for a, b in itertools.pairwise(toks):
            w, lw = a.group(), a.group().lower()
            if (
                b.group().lower() in STREET_SUFFIX
                and _adjacent(text, a, b)
                and lw not in FUNCTION_WORDS
                and (w[:1].isupper() or not is_common(lw))
            ):
                yield Span(f.id, a.start(), b.end(), "LOCATION", "people.place", 0.5, None,
                           "place:street", view="norm")  # fmt: skip
        if not any(c.islower() for c in text):
            return  # all capitals: a capitalised run means nothing
        i = 0
        while i < n:
            j = i
            while (
                j < n
                and j - i < 4
                and _is_cap(toks[j].group())
                and toks[j].group().lower() not in FUNCTION_WORDS
                and toks[j].group().lower() not in NOT_PLACE
                and (j == i or _adjacent(text, toks[j - 1], toks[j]))
            ):
                j += 1
            if j == i:
                i += 1
                continue
            k = j  # place words after the capitalised run
            while (
                k < n
                and toks[k].group().lower() in PLACE_AFTER
                and _adjacent(text, toks[k - 1], toks[k])
            ):
                k += 1
            first, last = toks[i].group().lower(), toks[j - 1].group().lower()
            suffix = k > j or (j - i >= 2 and (last in PLACE_AFTER or last in STREET_SUFFIX))
            if suffix and j - i == 1 and k - j == 1 and is_common(first):
                suffix = toks[j].group().lower() not in AMBIGUOUS_PLACE_WORDS  # "Good point"
            before = text[max(0, toks[i].start() - 30) : toks[i].start()]
            cued = PLACE_CUE.search(before) is not None or (
                re.search(r"(?i)\bour\s+$", before) is not None
                and OUR_SITE.match(text, toks[k - 1].end()) is not None
            )
            if suffix or cued:
                yield Span(f.id, toks[i].start(), toks[k - 1].end(), "LOCATION", "people.place",
                           0.5, None, "place:suffix" if suffix else "place:cue",
                           view="norm")  # fmt: skip
            i = j

    def _tokens(self, f: Field, text: str) -> Iterator[Span]:
        toks = list(TOKEN_RE.finditer(text))
        for i, m in enumerate(toks):
            w = m.group()
            lw = w.lower().removesuffix("'s")
            if len(lw) < 3:
                continue
            if is_common(lw):
                # Given name + surname even when both are dictionary words ("Mark Brown",
                # "Grace Hill"): a known given name followed by another proper name.
                if lw in self.proper and lw not in FUNCTION_WORDS and i + 1 < len(toks):
                    nxt = toks[i + 1]
                    nl = nxt.group().lower().removesuffix("'s")
                    if (
                        _adjacent(text, m, nxt)
                        and nl not in FUNCTION_WORDS
                        and (nl in self.proper or not is_common(nl))
                    ):
                        yield Span(f.id, m.start(), nxt.end(), "PERSON", "people.bigram", 0.5,
                                   None, "person:name-bigram", view="norm")  # fmt: skip
                continue
            j = i
            if lw in self.proper:
                if i + 1 < len(toks):
                    nxt = toks[i + 1]
                    if (
                        _adjacent(text, m, nxt)
                        and not is_common(nxt.group())
                        and len(nxt.group()) >= 2
                    ):
                        j = i + 1
                a, b, cat = _expand(text, toks, i, j)
                yield Span(f.id, toks[a].start(), toks[b].end(), cat or "PERSON", "people.proper",
                           0.6, None, "person:proper-name", view="norm")  # fmt: skip
            elif self.unknown != "off":
                a, b, cat = _expand(text, toks, i, j)
                action: Literal["redact", "flag"] = (
                    "redact" if self.unknown == "redact" or cat else "flag"
                )
                yield Span(f.id, toks[a].start(), toks[b].end(), cat or "NAME", "people.unknown",
                           0.4, lw if cat is None else None, "unknown-word", action=action,
                           view="norm")  # fmt: skip
