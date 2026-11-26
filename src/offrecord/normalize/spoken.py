"""Spoken-form normalisation for the ``norm`` view.

Passes, in order (each yields edits; maps are chained back to the original):
1. character folding: zero-width characters removed, NFKC, smart quotes/dashes, odd spaces
2. spelled letters: "S as in Sam", "s-m-i-t-h", "S M I T H", NATO words, "double L"
3. cardinal numbers with scale/money context: "fifty thousand dollars" -> "50000 dollars"
4. digit runs: "five five five one two one two" -> "5551212", "oh" as zero inside runs
5. spoken email/URL: "john dot smith at acme dot com" -> "john.smith@acme.com"

Everything is case-independent (case is preserved in output, never relied on). The passes are
conservative where a wrong rewrite could hide text; detectors also scan the ``orig`` view.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from offrecord.offsets import OffsetChain, OffsetMap, apply_edits

Edit = tuple[int, int, str]

# --- 1. character folding --------------------------------------------------------------------

_FOLD = {
    **dict.fromkeys("\u2018\u2019\u201a\u201b\u2032", "'"),
    **dict.fromkeys("\u201c\u201d\u201e\u201f\u2033", '"'),
    **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
    **dict.fromkeys("\u00a0\u2007\u202f\u2009\u200a\u3000\u1680\u205f", " "),
    "\u2026": "...",
}


def _invisible(ch: str) -> bool:
    """Format characters (zero-width, bidi controls, tags, soft hyphen...), variation selectors
    and the combining grapheme joiner: all can hide inside a word to defeat matching."""
    return (
        unicodedata.category(ch) == "Cf"
        or "\ufe00" <= ch <= "\ufe0f"
        or "\U000e0100" <= ch <= "\U000e01ef"
        or ch == "\u034f"
    )


def _fold_cluster(cluster: str) -> str:
    if len(cluster) == 1 and cluster in _FOLD:
        return _FOLD[cluster]
    out = unicodedata.normalize("NFKC", cluster)
    # Any remaining non-ASCII decimal digit (e.g. Arabic-Indic, Devanagari) -> ASCII digit.
    return "".join(
        str(unicodedata.decimal(c)) if not c.isascii() and unicodedata.decimal(c, None) is not None
        else c
        for c in out
    )  # fmt: skip


def fold_chars(text: str) -> list[Edit]:
    """Delete invisible characters; NFKC per grapheme cluster (so combining accents compose);
    fold quotes, dashes, odd spaces and non-ASCII digits."""
    if text.isascii():
        return []
    edits: list[Edit] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if not ch.isascii() and _invisible(ch):
            edits.append((i, i + 1, ""))
            i += 1
            continue
        j = i + 1
        while j < n and not text[j].isascii() and unicodedata.combining(text[j]):
            j += 1
        cluster = text[i:j]
        if not cluster.isascii():
            rep = _fold_cluster(cluster)
            if rep != cluster:
                edits.append((i, j, rep))
        i = j
    return edits


# Cyrillic and Greek letters that look like Latin ones. Folded only inside words that mix
# scripts or consist only of look-alikes, so genuine Cyrillic/Greek text is left alone.
CONFUSABLES = {
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0443": "y",
    "\u0445": "x", "\u0456": "i", "\u0458": "j", "\u0455": "s", "\u0501": "d", "\u04bb": "h",
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M", "\u041d": "H",
    "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T", "\u0423": "Y", "\u0425": "X",
    "\u0406": "I", "\u0408": "J", "\u0405": "S",
    "\u03b1": "a", "\u03bf": "o", "\u03c1": "p", "\u03b9": "i", "\u03ba": "k", "\u03bd": "v",
    "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H", "\u0399": "I",
    "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O", "\u03a1": "P", "\u03a4": "T",
    "\u03a5": "Y", "\u03a7": "X",
}  # fmt: skip
_WORD_RE = re.compile(r"[^\W\d_]+")


def fold_confusables(text: str) -> list[Edit]:
    if text.isascii():
        return []
    edits: list[Edit] = []
    for m in _WORD_RE.finditer(text):
        w = m.group()
        if w.isascii() or not any(c in CONFUSABLES for c in w):
            continue
        non_ascii = [c for c in w if not c.isascii()]
        if all(c in CONFUSABLES for c in non_ascii):
            for k, c in enumerate(w):
                if c in CONFUSABLES:
                    edits.append((m.start() + k, m.start() + k + 1, CONFUSABLES[c]))
    return edits


# --- tokens ----------------------------------------------------------------------------------


def _isnum(s: str) -> bool:
    """ASCII digits only (``str.isdigit`` accepts characters that ``int`` rejects)."""
    return s.isascii() and s.isdigit()


_TOKEN_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)?|[^\w\s]|_")


@dataclass(frozen=True, slots=True)
class Tok:
    start: int
    end: int
    text: str
    low: str


def tokenize(text: str) -> list[Tok]:
    return [Tok(m.start(), m.end(), m.group(), m.group().lower()) for m in _TOKEN_RE.finditer(text)]


_SEPARATORS = {",", "-", ".", "/"}
_SEP_WORDS = {"dash", "hyphen"}


def _next_unit_index(toks: Sequence[Tok], j: int, max_seps: int = 3) -> int:
    """Skip separators (",", "-", ".", "/", "dash", "hyphen") between run units."""
    k = j
    while (
        k < len(toks)
        and (toks[k].text in _SEPARATORS or toks[k].low in _SEP_WORDS)
        and (k - j < max_seps)
    ):
        k += 1
    return k


# --- 2. spelled letters ----------------------------------------------------------------------

NATO = {
    "alpha": "a", "alfa": "a", "bravo": "b", "charlie": "c", "delta": "d", "echo": "e",
    "foxtrot": "f", "golf": "g", "hotel": "h", "india": "i", "juliet": "j", "juliett": "j",
    "kilo": "k", "lima": "l", "mike": "m", "november": "n", "oscar": "o", "papa": "p",
    "quebec": "q", "romeo": "r", "sierra": "s", "tango": "t", "uniform": "u", "victor": "v",
    "whiskey": "w", "whisky": "w", "xray": "x", "yankee": "y", "zulu": "z",
}  # fmt: skip


def _letter_unit(toks: Sequence[Tok], i: int) -> tuple[int, str, bool] | None:
    """Return (tokens consumed, letters, used_as_in) for a spelled-letter unit at i."""
    t = toks[i]
    if len(t.low) == 1 and t.low.isalpha():
        # "S as in Sam" / "S for Sam" / "S like Sam"
        for pat in (("as", "in"), ("for",), ("like",)):
            j = i + 1
            if j + len(pat) < len(toks) and all(toks[j + k].low == pat[k] for k in range(len(pat))):
                word = toks[j + len(pat)]
                if word.low[:1] == t.low and word.low.isalpha():
                    return 1 + len(pat) + 1, t.text, True
        return 1, t.text, False
    if t.low == "double" and i + 1 < len(toks):
        n = toks[i + 1]
        if len(n.low) == 1 and n.low.isalpha():
            return 2, n.text * 2, False
    return None


def _nato_unit(toks: Sequence[Tok], i: int) -> tuple[int, str, bool] | None:
    t = toks[i]
    if t.low in NATO:
        return 1, NATO[t.low], False
    return None


def spelled_letters(text: str) -> list[Edit]:
    toks = tokenize(text)
    edits: list[Edit] = []
    i = 0
    while i < len(toks):
        for unit_fn, min_units in ((_letter_unit, 3), (_nato_unit, 3)):
            units: list[tuple[int, int, str]] = []  # (first tok, last tok, letters)
            as_in = False
            j = i
            while j < len(toks):
                k = _next_unit_index(toks, j) if units else j
                if k >= len(toks):
                    break
                u = unit_fn(toks, k)
                if u is None:
                    break
                n, letters, used = u
                units.append((k, k + n - 1, letters))
                as_in = as_in or used
                j = k + n
            if len(units) >= min_units or (as_in and len(units) >= 1 and unit_fn is _letter_unit):
                # A lone "S as in Sam" still normalises to "S" (useful for the next unit).
                start = toks[units[0][0]].start
                end = toks[units[-1][1]].end
                edits.append((start, end, "".join(u[2] for u in units)))
                i = j
                break
        else:
            i += 1
    return edits


# --- 3/4. numbers ----------------------------------------------------------------------------

_DIGIT_WORDS_ALL: dict[str, str | None] = {
    "zero": "0", "oh": "0", "o": "0", "nought": "0", "naught": "0", "one": "1", "two": "2",
    "three": "3", "four": "4", "for": None, "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "niner": "9",
}  # fmt: skip
DIGIT_WORDS: dict[str, str] = {k: v for k, v in _DIGIT_WORDS_ALL.items() if v is not None}
TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
SCALES = {"thousand": 10**3, "million": 10**6, "billion": 10**9, "trillion": 10**12}
NUMBER_TRIGGERS = {
    "number", "phone", "cell", "mobile", "card", "account", "acct", "zip", "postcode",
    "code", "pin", "ssn", "social", "extension", "ext", "order", "ticket", "reference", "ref",
    "id", "routing", "digits", "four", "fax", "passport", "license", "licence", "policy",
    "member", "invoice", "confirmation", "tracking", "case", "claim", "is", "it's", "at",
}  # fmt: skip
MONEY_WORDS = {
    "dollar", "dollars", "buck", "bucks", "usd", "euro", "euros", "pound", "pounds", "quid",
    "grand", "k", "percent", "per", "units", "stores", "users", "seats", "customers",
    "locations", "employees", "people", "headcount", "licenses", "licences", "accounts",
    "sites", "offices", "branches", "deals", "orders", "devices", "trucks", "members",
    "subscribers", "patients", "clinics", "outlets", "reps", "agents", "hires", "roles",
    "contracts", "registers", "terminals", "warehouses", "depots", "vehicles",
}  # fmt: skip


def _small_number(toks: Sequence[Tok], i: int) -> tuple[int, int] | None:
    """Parse 1..99 at i: returns (tokens consumed, value)."""
    t = toks[i]
    if t.low in DIGIT_WORDS and t.low not in ("oh", "o"):
        return 1, int(DIGIT_WORDS[t.low])
    if t.low in TEENS:
        return 1, TEENS[t.low]
    if t.low in TENS:
        v = TENS[t.low]
        j = i + 1
        if j < len(toks) and toks[j].text == "-" and j + 1 < len(toks):
            j += 1
        if j < len(toks) and toks[j].low in DIGIT_WORDS and toks[j].low not in ("zero", "oh", "o"):
            return j - i + 1, v + int(DIGIT_WORDS[toks[j].low])
        return 1, v
    return None


def _cardinal(toks: Sequence[Tok], i: int) -> tuple[int, str, bool] | None:
    """Parse a cardinal ("two hundred and fifty thousand", "two point five million").

    Returns (tokens consumed, numeral, has_scale_or_point)."""
    total = 0
    current = 0
    j = i
    seen_any = False
    strong = False
    decimal = ""
    need_mult = False  # after a small number only a multiplier/"point" may follow
    if (
        j < len(toks)
        and toks[j].low == "a"
        and j + 1 < len(toks)
        and (toks[j + 1].low == "hundred" or toks[j + 1].low in SCALES)
    ):
        current = 1
        seen_any = True
        j += 1
    while j < len(toks):
        t = toks[j]
        sn = _small_number(toks, j) if not _isnum(t.low) else None
        if _isnum(t.low) and len(t.low) <= 15 and not seen_any:
            current = int(t.low)
            seen_any = True
            need_mult = True
            j += 1
            continue
        if sn is not None and not need_mult:
            n, v = sn
            current += v
            seen_any = True
            need_mult = True
            j += n
            continue
        if t.low == "hundred" and seen_any and current % 100 == current:
            current = max(current, 1) * 100
            need_mult = False
            j += 1
            continue
        if t.low in SCALES and seen_any:
            total += max(current, 1) * SCALES[t.low]
            current = 0
            strong = True
            need_mult = False
            j += 1
            continue
        if (
            t.low == "and"
            and seen_any
            and j + 1 < len(toks)
            and (_small_number(toks, j + 1) is not None)
        ):
            j += 1
            continue
        if t.low == "point" and seen_any and j + 1 < len(toks):
            k = j + 1
            digits = ""
            while k < len(toks) and (toks[k].low in DIGIT_WORDS or _isnum(toks[k].low)):
                digits += DIGIT_WORDS.get(toks[k].low, toks[k].low)
                k += 1
            if digits:
                decimal = digits
                strong = True
                j = k
                # A scale may follow the decimal: "two point five million".
                if j < len(toks) and toks[j].low in SCALES:
                    scaled = (current + float("0." + decimal)) * SCALES[toks[j].low]
                    total += round(scaled)
                    current = 0
                    decimal = ""
                    j += 1
                continue
        break
    if not seen_any:
        return None
    value = total + current
    numeral = f"{value}.{decimal}" if decimal else str(value)
    return j - i, numeral, strong


def cardinals(text: str) -> list[Edit]:
    toks = tokenize(text)
    edits: list[Edit] = []
    i = 0
    while i < len(toks):
        if _isnum(toks[i].low) and not (
            i + 1 < len(toks) and toks[i + 1].low in ("hundred", *SCALES, "point")
        ):
            i += 1
            continue
        c = _cardinal(toks, i)
        if c is None or c[0] < 1:
            i += 1
            continue
        n, numeral, strong = c
        nxt = toks[i + n].low if i + n < len(toks) else ""
        word_tokens = sum(1 for t in toks[i : i + n] if not _isnum(t.low))
        # Multi-word numbers ("forty seven", "three hundred") always become digits in the norm
        # view; single number words only with scale/money/unit context ("five stores").
        in_digit_run = nxt in DIGIT_WORDS or _isnum(nxt)  # "eight hundred five five five..."
        multi = word_tokens >= 2 and not in_digit_run
        if word_tokens and (strong or multi or nxt in MONEY_WORDS):
            edits.append((toks[i].start, toks[i + n - 1].end, numeral))
            i += n
        else:
            i += 1
    return edits


def _digit_unit(toks: Sequence[Tok], i: int, in_run: bool) -> tuple[int, str, bool] | None:
    """Return (tokens consumed, digits, is_word) for one unit of a digit run."""
    t = toks[i]
    if _isnum(t.low):
        return 1, t.low, False
    if t.low in ("double", "triple") and i + 1 < len(toks):
        d = toks[i + 1].low
        if d in DIGIT_WORDS:
            return 2, DIGIT_WORDS[d] * (2 if t.low == "double" else 3), True
    if t.low in ("oh", "o"):
        if in_run:
            return 1, "0", True
        # "oh nine nine ...": a leading "oh" counts when a digit word follows directly.
        nxt = toks[i + 1].low if i + 1 < len(toks) else ""
        if (nxt in DIGIT_WORDS and nxt not in ("oh", "o")) or _isnum(nxt):
            return 1, "0", True
        return None
    if t.low in DIGIT_WORDS:
        return 1, DIGIT_WORDS[t.low], True
    if t.low in TEENS:
        return 1, str(TEENS[t.low]), True
    if t.low in TENS:
        sn = _small_number(toks, i)
        if sn is not None:
            return sn[0], f"{sn[1]:02d}", True
    if t.low == "hundred" and in_run:
        return 1, "00", True
    return None


def digit_runs(text: str) -> list[Edit]:
    toks = tokenize(text)
    edits: list[Edit] = []
    i = 0
    while i < len(toks):
        units: list[tuple[int, int, str, bool]] = []
        j = i
        while j < len(toks):
            k = _next_unit_index(toks, j) if units else j
            if k >= len(toks):
                break
            u = _digit_unit(toks, k, in_run=bool(units))
            if u is None:
                break
            n, digits, is_word = u
            units.append((k, k + n - 1, digits, is_word))
            j = k + n
        # "oh" cannot end a run on its own ("five oh" is fine; "...two, oh well" is not).
        while units and toks[units[-1][0]].low in ("oh", "o") and len(units) < 3:
            units.pop()
        words = sum(1 for u in units if u[3])
        n_digits = sum(len(u[2]) for u in units)
        trigger = any(t.low in NUMBER_TRIGGERS for t in toks[max(0, i - 5) : i])
        if words >= 1 and (n_digits >= 3 or (len(units) >= 2 and trigger)):
            start = toks[units[0][0]].start
            end = toks[units[-1][1]].end
            edits.append((start, end, "".join(u[2] for u in units)))
            i = units[-1][1] + 1
        else:
            # No sub-run of a failed run can succeed (it has fewer units and no trigger of its
            # own), so skip past it: keeps the pass linear on long number lists.
            i = max(i + 1, units[-1][1] + 1) if units else i + 1
    return edits


# --- 5. spoken email / URL -------------------------------------------------------------------

TLDS = {
    "com", "org", "net", "io", "co", "edu", "gov", "uk", "us", "de", "fr", "ca", "au", "in",
    "biz", "info", "ai", "app", "dev", "me", "ly", "nl", "es", "it", "jp", "ch", "se", "no",
    "eu", "tv", "xyz", "cloud", "tech", "example", "test", "invalid", "local", "internal",
    "ie", "nz", "sg", "za", "br", "mx", "pl", "be", "at", "dk", "fi", "pt", "gr", "kr", "cn",
}  # fmt: skip
# Newer endings that are also ordinary words: only after a literal "dot" ("acme dot health"),
# never "point" or "period" ("at this point today").
WORD_TLDS = {
    "health", "care", "clinic", "dental", "law", "legal", "insurance", "bank", "finance",
    "shop", "store", "online", "site", "agency", "studio", "solutions", "services", "network",
    "digital", "media", "systems", "group", "academy", "school", "events", "travel", "energy",
    "realty", "properties",
}  # fmt: skip
_DOT = {"dot", "period", ".", "point"}
_AT = {"at", "@"}
_LOCAL_JOIN = {"dot": ".", "period": ".", ".": ".", "underscore": "_", "_": "_",
               "dash": "-", "hyphen": "-", "-": "-"}  # fmt: skip
_STOP_LOCAL = {"me", "us", "him", "her", "them", "is", "it's", "email", "mail", "address"}
# Words that never join a domain label spoken as two words ("acme health dot org").
_NOT_LABEL = {
    "site", "website", "page", "link", "url", "visit", "go", "at", "to", "is", "on", "our",
    "the", "their", "your", "my", "new", "web", "online", "check", "email",
}  # fmt: skip
_PATH_JOIN = {"dash": "-", "hyphen": "-", "underscore": "_", "-": "-", "_": "_"}


_DOMAIN_TOKEN_RE = re.compile(r"[^\W_]+(?:-[^\W_]+)*(?:'s)?|[^\w\s]|_")


def _is_label(t: Tok) -> bool:
    return t.low.replace("-", "").isalnum() and t.low not in _DOT and t.low not in _AT


def _strip_possessive(word: str) -> str:
    return word[:-2] if word.endswith("'s") else word


def spoken_email_url(text: str) -> list[Edit]:
    toks = [
        Tok(m.start(), m.end(), m.group(), m.group().lower())
        for m in _DOMAIN_TOKEN_RE.finditer(text)
    ]
    edits: list[Edit] = []
    used_until = -1
    for k, t in enumerate(toks):
        tld = _strip_possessive(t.low)
        if k <= used_until or k < 2 or toks[k - 1].low not in _DOT:
            continue
        if tld not in TLDS and not (tld in WORD_TLDS and toks[k - 1].low == "dot"):
            continue
        # Only rewrite if at least one separator is spoken (otherwise it is already literal).
        # Walk back over "label dot label dot tld".
        j = k
        domain = [_strip_possessive(t.text)]
        spoken = toks[k - 1].low != "."
        while j - 2 >= 0 and toks[j - 1].low in _DOT and _is_label(toks[j - 2]) and len(domain) < 6:
            label = toks[j - 2].text
            spoken = spoken or toks[j - 1].low != "."
            j -= 2
            # Speech-to-text often splits a name: "acme health dot org" -> acmehealth.org.
            if (
                spoken
                and j - 1 >= 0
                and _is_label(toks[j - 1])
                and toks[j - 1].low not in _NOT_LABEL
                and toks[j - 1].low not in _STOP_LOCAL
                and toks[j - 1].end + 1 == toks[j].start
            ):
                label = toks[j - 1].text + label
                j -= 1
            domain.insert(0, label)
        if len(domain) < 2:
            continue
        start_tok = j
        result = ".".join(d.lower() for d in domain)
        # Email: "local at domain". A path after the domain makes it a URL ("find it online at
        # acme dot com slash help").
        path_next = k + 1 < len(toks) and toks[k + 1].low in ("slash", "/")
        if j - 2 >= 0 and toks[j - 1].low in _AT and not path_next:
            spoken = spoken or toks[j - 1].low == "at"
            local: list[str] = []
            m = j - 2
            if _is_label(toks[m]) and toks[m].low not in _STOP_LOCAL:
                local.insert(0, toks[m].text)
                while (
                    m - 2 >= 0
                    and toks[m - 1].low in _LOCAL_JOIN
                    and _is_label(toks[m - 2])
                    and len(local) < 6
                ):
                    local.insert(0, _LOCAL_JOIN[toks[m - 1].low])
                    local.insert(0, toks[m - 2].text)
                    spoken = spoken or toks[m - 1].low.isalpha()
                    m -= 2
                start_tok = m
                result = "".join(local) + "@" + result
        end_tok = k
        if "@" not in result:
            # Spoken URL path: "acme dot com slash pricing slash plans".
            while (
                end_tok + 2 < len(toks)
                and toks[end_tok + 1].low in ("slash", "/")
                and (toks[end_tok + 2].low.replace("-", "").isalnum())
            ):
                result += "/" + toks[end_tok + 2].low
                spoken = True
                end_tok += 2
                # "slash pricing dash plans" -> /pricing-plans
                while (
                    end_tok + 2 < len(toks)
                    and toks[end_tok + 1].low in _PATH_JOIN
                    and toks[end_tok + 2].low.isalnum()
                ):
                    result += _PATH_JOIN[toks[end_tok + 1].low] + toks[end_tok + 2].low
                    end_tok += 2
        if not spoken:
            continue
        edits.append((toks[start_tok].start, toks[end_tok].end, result))
        used_until = end_tok
    return edits


# --- pipeline --------------------------------------------------------------------------------

PASSES = (fold_chars, fold_confusables, spelled_letters, cardinals, digit_runs, spoken_email_url)


def _non_overlapping(edits: Iterable[Edit]) -> list[Edit]:
    out: list[Edit] = []
    last_end = -1
    for e in sorted(edits, key=lambda e: (e[0], -(e[1] - e[0]))):
        if e[0] >= last_end and e[1] > e[0]:
            out.append(e)
            last_end = e[1]
    return out


def normalize(text: str) -> tuple[str, OffsetChain]:
    """Return the ``norm`` view text and its chained offset map back to ``text``."""
    maps: list[OffsetMap] = []
    cur = text
    for p in PASSES:
        edits = _non_overlapping(p(cur))
        if not edits:
            continue
        cur, m = apply_edits(cur, edits)
        maps.append(m)
    if not maps:
        maps.append(OffsetMap.identity(len(text)))
    return cur, OffsetChain(maps)
