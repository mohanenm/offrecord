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
from offrecord.redact.wordlists import CALENDAR, FUNCTION_WORDS, is_common, proper_names

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
INTRO_CUE = re.compile(r"(?i)(?:my\s+name|name's|this\s+is|i'm|named|called)\b")
TITLE_WORDS = frozenset(
    "mr mrs ms miss mx dr doctor prof professor sgt sergeant lt lieutenant capt captain col "
    "colonel gen general officer detective det agent judge justice father fr sister brother rev "
    "reverend pastor rabbi imam coach mayor senator sen rep governor gov president dean nurse "
    "aunt uncle lord lady sir dame madam".split()
)
# A capitalised common word right after these is a thing, not a person ("The Pound", "our Chase").
DETERMINERS = frozenset("the a an this that these those our my your his her their its".split())
TITLE_CUE = re.compile(
    r"(?i)\b(?:my\s+name\s+is|my\s+name's|name's|this\s+is|i'm|named|called|"
    r"mr|mrs|ms|miss|mx|dr|doctor|prof|professor|sgt|sergeant|lt|lieutenant|capt|captain|"
    r"col|colonel|gen|general|officer|detective|det|agent|judge|justice|father|fr|sister|"
    r"brother|rev|reverend|pastor|rabbi|imam|coach|mayor|senator|sen|rep|governor|gov|"
    r"president|dean|nurse|aunt|uncle|lord|lady|sir|dame|madam)\.?\s+"
    r"(?P<name>[^\W\d_]+(?:[-'][^\W\d_]+)?(?:\s+[^\W\d_]+(?:[-'][^\W\d_]+)?)?)"
)
# "my name is Will", "Dr. May", "this is An": right after an introduction or a title, a
# function word that looks like a name is one. Capitalised, or any case after "my name is".
_NAME_IS = re.compile(r"(?i)^(?:my\s+name\s+is|my\s+name's|name's)\s+$")
FUNCTION_NAMES = frozenset("will may an my do".split()) & FUNCTION_WORDS


def _function_word_name(cue: str, word: str) -> bool:
    if word.lower() not in FUNCTION_NAMES:
        return False
    return word[:1].isupper() or _NAME_IS.match(cue) is not None


INITIALS = re.compile(r"(?i)(?<![\w.])(?:[a-z]\.\s?){1,3}(?:[a-z]\.|[a-z][\w'-]{1,})")
NOT_INITIALS = re.compile(r"(?i)^(?:e\.\s?g\.|i\.\s?e\.|a\.\s?m\.|p\.\s?m\.|u\.\s?s\.|u\.\s?k\.)")


# Words that extend a detected name into a place or organisation name ("Port Ellery",
# "Corbin Heights", "Thornquist Freight"). General English naming patterns.
PLACE_BEFORE = frozenset(
    "port east west north south new lake mount mt fort saint st upper lower greater old san santa "
    "los las el la le de del van von".split()
)
PLACE_AFTER = frozenset(
    "heights falls bay county city park valley springs spring island isles beach hills hill harbor "
    "harbour creek ridge point village town township district station square street avenue ave "
    "road rd lane drive way bridge cove crossing junction grove landing meadows mills shores sound "
    "summit terrace vale view woods field fields commons wharf yards centre center library hall "
    "court mall plaza manor quay docks estate vineyard coast panhandle plains bluffs parish river "
    "lake mountain mountains peninsula canyon".split()
)
ORG_AFTER = frozenset(
    "inc incorporated ltd limited llc llp plc corp corporation co company group holdings partners "
    "analytics health healthcare foods food logistics bank banking media insurance assurance "
    "mutual freight retail air airlines airways labs lab systems technologies technology tech "
    "solutions capital ventures outfitters industries international global consulting consultants "
    "services software pharma pharmaceuticals energy motors api pro suite console cloud platform "
    "studio studios foundation trust fund university college hospital clinic networks network "
    "robotics dynamics enterprises associates brands financial finance securities realty "
    "properties manufacturing supply supplies brothers bros sons daughters builders construction "
    "contractors roofing plumbing electric restoration title lending mortgage provisions lifting "
    "interiors mix specialty union federal medical elementary middle academy school plan water "
    "sorting clinical hotel dental pediatrics practice law legal agency transport trucking "
    "distribution wholesale bakery kitchen cafe market markets".split()
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
# Words said to or about a person, next to a given name that is also a common word.
PERSON_BEFORE = re.compile(
    r"(?i)\b(?:hi|hey|hello|thanks|thank\s+you|tell|ask|asked|told|call|called|ping|email|"
    r"text|cc|copy|with|and|via|from|for|to|like)\s+$"
)
PERSON_AFTER = re.compile(
    r"(?i)\s+(?:said|says|asked|told|thinks|thought|wants|wanted|needs|took|takes|called|"
    r"texted|emailed|is\s+out|was\s+out|is\s+off|will\s+cover|can\s+cover|covered|"
    r"agreed|signed|left|joined|and\s+i\b|and\s+me\b)"
)
# Indirect identifiers: a description that singles out one person or organisation without
# naming it ("the patient in bed twelve", "our only Tagalog-speaking assistant", "the vendor that
# went bust", "my husband"). Keyed on the head noun, so "the only way" or "the first thing" never
# match. People are personal data; organisations are business context (flagged).
PERSON_NOUN = (
    r"patient|man|men|woman|women|gentleman|lady|guy|girl|boy|kid|lad|lass|child|baby|daughter|"
    r"son|wife|husband|brother|sister|mother|father|mum|mom|dad|stepdad|stepmum|stepmom|parent|"
    r"partner|neighbou?r|tenant|guest|resident|driver|manager|assistant|nurse|doctor|registrar|"
    r"surgeon|officer|teacher|intern|engineer|contractor|cashier|guard|keyholder|lead|supervisor|"
    r"analyst|teller|chef|cook|attendant|owner|applicant|super|cio|cfo|ceo|coo|cto|counsel|pa|"
    r"nephew|niece|couple|receptionist|clerk|technician|agent|rep|lodger|boss|colleague|worker|"
    r"employee|volunteer|student|pupil|caller|landlord|landlady|person|fellow|chap|bloke|aide|"
    r"carer|caregiver|coordinator|director|admin|operator|mechanic|electrician|plumber|builder|"
    r"courier|porter|cleaner|janitor|secretary|grandson|granddaughter|ex-wife|ex-husband|"
    r"boyfriend|girlfriend|fiance|fiancee|twins?|one"
)
ORG_NOUN = (
    r"store|shop|account|customer|client|vendor|supplier|hospital|system|credit\s+union|bank|"
    r"firm|company|insurer|reseller|office|branch|site|plant|warehouse|academy|school|clinic|"
    r"practice|restaurant|truck|station|hotel|agency|chain|distributor|party|contractor"
)
_QUALIFIER = (
    r"who|whose|that|which|with\s+the|from\s+the|from|in|on|at|by|across|below|above|"
    r"next\s+to|near|of\s+the|directly|two\s+doors|we|they|you"
)
_TAIL = r"(?:\s+(?!and\b|but\b|so\b)[\w'-]+){1,10}"
UNIQUE = (
    r"only|sole|one|very\s+first|first|last|longest[- ]serving|longest[- ]staying|newest|oldest|"
    r"youngest|biggest|largest|main|new"
)
INDIRECT_RES = [
    # "the patient in bed twelve", "the vendor that went bust", "the wife of the man in bay C"
    re.compile(
        rf"(?i)\b(?:the|our|their|his|her|my|your)\s+(?:[\w'-]+\s+){{0,3}}?"
        rf"(?P<head>{PERSON_NOUN}|{ORG_NOUN})s?\s+(?:{_QUALIFIER})\b{_TAIL}"
    ),
    # "our only male teaching assistant", "our longest-serving driver", "our very first customer"
    re.compile(
        rf"(?i)\b(?:the|our|their|his|her|my)\s+(?:{UNIQUE})\s+(?:[\w'-]+\s+){{0,3}}?"
        rf"(?P<head>{PERSON_NOUN}|{ORG_NOUN})s?\b(?:\s+(?:{_QUALIFIER})\b{_TAIL})?"
    ),
    # "my husband", "his brother-in-law", "the applicant's brother", "the owner's son"
    re.compile(
        r"(?i)\b(?:my|his|her|their|our|your|(?:the\s+)?[\w-]+'s)\s+(?:new\s+|old\s+|ex-)?"
        r"(?P<head>wife|husband|daughter|son|brother|sister|mother|father|mum|mom|dad|stepdad|"
        r"stepmum|stepmom|partner|boyfriend|girlfriend|fiance|fiancee|manager|boss|lodger|"
        r"nephew|niece|grandson|granddaughter|brother-in-law|sister-in-law|union\s+rep|pa)\b"
    ),
    # "bed twelve", "bay C": a position standing for the person in it
    re.compile(
        r"(?i)\b(?P<head>bed|bay)\s+(?:[a-z]\b|\d{1,3}\b|(?:one|two|three|four|five|six|"
        r"seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|twenty)\b)"
    ),
]
_ORG_HEADS = re.compile(rf"(?i)^(?:{ORG_NOUN})$")
LIST_SEP = re.compile(r"\s*(?:,|,?\s*(?:and|or|&))\s*")
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
# Words that only end place names, for text without capitals ("orange county", "palm springs").
UNCASED_PLACE_END = frozenset(
    "county heights springs falls beach valley bay island harbor harbour vineyard coast "
    "panhandle parish city hills".split()
)
NOT_PLACE_ADJ = frozenset(
    "big small inner whole entire same other home next local nearby rural busy quiet little "
    "major nearest neighbouring neighboring every each any another different new old main "
    "capital host sister smart theme private public tourist coastal sunny rocky sandy windy "
    "this that these those which what our your their his her its my".split()
)
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
            yield from self._indirect(f, text)
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
            intro = INTRO_CUE.match(m.group()) is not None
            take: list[str] = []
            cue = m.group()[: m.start("name") - m.start()]
            for w in words:
                if w.lower() in FUNCTION_WORDS and not (not take and _function_word_name(cue, w)):
                    break
                # After a title word ("general", "agent", "miss" are ordinary words too), only a
                # capitalised or non-dictionary word is a name: "Dr. Haldane approved" stops at
                # "approved", "the general manager" takes nothing. "my name is grace" is enough.
                if not intro and not w[:1].isupper() and is_common(w):
                    break
                take.append(w)
            if not take:
                continue
            # An honorific is part of the name ("Dr. Haldane"); a phrase like "my name is" is not.
            s = m.start("name") if intro else m.start()
            e = m.start("name") + len(" ".join(take))
            yield Span(f.id, s, e, "PERSON", "people.title", 0.6, None,
                       "person:intro" if intro else "person:title", view="norm")  # fmt: skip
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
            take: list[str] = []
            for w in words:
                lw = w.lower()
                if lw in FUNCTION_WORDS:
                    break
                if take and not w[:1].isupper() and is_common(lw):
                    break  # a second name word must look like one: "Dr. Haldane approved"
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

    def _indirect(self, f: Field, text: str) -> Iterator[Span]:
        for rx in INDIRECT_RES:
            for m in rx.finditer(text):
                org = bool(_ORG_HEADS.match(m.group("head")))
                yield Span(f.id, m.start(), m.end(), "INDIRECT", "people.indirect", 0.4, None,
                           "indirect:org" if org else "indirect:person",
                           action="flag" if org else "redact",
                           view="norm")  # fmt: skip

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
        if not (any(c.islower() for c in text) and any(c.isupper() for c in text)):
            yield from self._uncased_places(f, text, toks)
        if not any(c.islower() for c in text):
            return  # all capitals: a capitalised run means nothing
        i = 0
        last_place = -1  # end of the previous place: "in London, Paris and Milan"
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
            if text[toks[j - 1].end() : toks[j - 1].end() + 1].isdigit():
                i = j  # "Q3", "H2", "B12": a code, not a place
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
            listed = last_place >= 0 and LIST_SEP.fullmatch(text, last_place, toks[i].start())
            if suffix or cued or listed:
                yield Span(f.id, toks[i].start(), toks[k - 1].end(), "LOCATION", "people.place",
                           0.5, None, "place:suffix" if suffix else "place:cue",
                           view="norm")  # fmt: skip
                last_place = toks[k - 1].end()
            i = j

    def _uncased_places(self, f: Field, text: str, toks: list[re.Match[str]]) -> Iterator[Span]:
        """Speech-to-text output without capitals: "orange county", "salt lake city", "palm
        springs". One or two words before a word that only ends place names, never a function
        word, number or generic adjective ("the county", "a big city", "the whole county")."""
        for k, t in enumerate(toks):
            if t.group().lower() not in UNCASED_PLACE_END or k == 0:
                continue
            a = k
            while a > 0 and k - a < 2 and _adjacent(text, toks[a - 1], toks[a]):
                w = toks[a - 1].group().lower()
                if w in FUNCTION_WORDS or w in NOT_PLACE_ADJ or w in DETERMINERS or w.isdigit():
                    break
                a -= 1
            if a < k:
                yield Span(f.id, toks[a].start(), t.end(), "LOCATION", "people.place", 0.5, None,
                           "place:uncased", view="norm")  # fmt: skip

    def _ordinary_word_name(self, text: str, m: re.Match[str], lw: str, mixed: bool) -> str:
        """For a known given name that is also a common word ("Grace", "Hope", "mark"): the
        reason it reads as a name here, or "" if it does not. Capitalised mid-sentence in
        mixed-case text, or next to a word said of or to a person ("tell mark", "hope said"),
        but never right after a determiner ("the Pound") or before another capitalised word
        ("Crystal Reports")."""
        if lw not in self.proper or lw in FUNCTION_WORDS or lw in CALENDAR:
            return ""
        before = text[max(0, m.start() - 24) : m.start()]
        prev = before.split()[-1].lower() if before.split() else ""
        if prev in DETERMINERS:
            return ""
        nxt = text[m.end() : m.end() + 24].split()
        w = m.group()
        if (
            mixed
            and w[:1].isupper()
            and not w.isupper()
            and before.strip()
            and before.rstrip()[-1] not in ".!?:\"'\n"
            and not (nxt and nxt[0][:1].isupper())
        ):
            return "person:capitalised-name"
        after = text[m.end() : m.end() + 24]
        if PERSON_BEFORE.search(before) or PERSON_AFTER.match(after):
            return "person:context"
        return ""

    def _tokens(self, f: Field, text: str) -> Iterator[Span]:
        toks = list(TOKEN_RE.finditer(text))
        mixed = any(c.islower() for c in text) and any(c.isupper() for c in text)
        for i, m in enumerate(toks):
            w = m.group()
            lw = w.lower().removesuffix("'s")
            if len(lw) < 3:
                continue
            reason = self._ordinary_word_name(text, m, lw, mixed) if is_common(lw) else ""
            if reason:
                yield Span(f.id, m.start(), m.end(), "PERSON", "people.proper", 0.5, None,
                           reason, view="norm")  # fmt: skip
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
