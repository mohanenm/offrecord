"""Seeded, deterministic synthetic transcript generator (docs/eval-format.md).

Fills YAML templates with fake entities from pools, adds speech noise, applies casing
variants, and renders every supported format with gold labels. All values are synthetic:
phone numbers use the fictional 555-01xx range, cards are public test numbers, SSNs use the
never-issued 9xx area, IPs are documentation ranges, domains are reserved TLDs.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from offrecord import adapters
from offrecord.evaluation.labels import Label, LabelledTranscript, Negative
from offrecord.limits import Limits
from offrecord.model import Document, Turn

FORMATS = ("txt", "vtt", "srt", "json")
SLOT_RE = re.compile(r"\{\{|\}\}|\{(?P<type>[a-z_]+)(?:#(?P<k>\w+))?(?:\.(?P<form>\w+))?\}")
# Inline labels in template text: [[TOPIC: ...]] (slots inside are filled), [[NEG: ...]], and
# [[CATEGORY: literal]] / [[CATEGORY#id: literal]] for phrasings the slot forms do not cover.
INLINE_RE = re.compile(r"\[\[(?P<cat>[A-Z]+)(?:#(?P<id>\w+))?:\s*(?P<text>.*?)\]\]", re.DOTALL)

CATEGORY = {
    "person": "PERSON", "client": "CLIENT", "brand": "BRAND", "brand_common": "BRAND",
    "product": "PRODUCT", "codename": "CODENAME", "location": "LOCATION", "email": "EMAIL",
    "phone": "PHONE", "ssn": "SSN", "card": "CARD", "ip": "IP", "url": "URL",
    "account": "ACCOUNT", "order": "ACCOUNT", "date": "DATE", "amount": "AMOUNT",
    "percent": "PERCENT", "quantity": "QUANTITY",
}  # fmt: skip
INLINE_CATEGORIES = frozenset(CATEGORY.values()) | {"ORG"}
NAMED = {"client": "clients", "brand": "brands", "product": "products", "codename": "codenames"}

# Public test card numbers (never real accounts).
TEST_CARDS = [
    "4111111111111111", "4242424242424242", "4012888888881881", "5555555555554444",
    "5105105105105100", "378282246310005", "6011111111111117", "3530111333300000",
]  # fmt: skip
DIGIT_WORD = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
ONES = DIGIT_WORD
TEENS_W = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
           "seventeen", "eighteen", "nineteen"]  # fmt: skip
TENS_W = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]  # fmt: skip
FILLERS = ["um", "uh", "you know", "I mean", "like", "sort of", "basically"]
GENERIC_SPEAKERS = ["Speaker 1", "Speaker 2", "Speaker 3", "Speaker 4", "Speaker 5"]


def number_words(n: int) -> str:
    """English words for 0 <= n < 10**12 (no "and")."""
    if n < 10:
        return ONES[n]
    if n < 20:
        return TEENS_W[n - 10]
    if n < 100:
        t, o = divmod(n, 10)
        return TENS_W[t] + (f" {ONES[o]}" if o else "")
    for scale, word in (
        (10**9, "billion"),
        (10**6, "million"),
        (10**3, "thousand"),
        (100, "hundred"),
    ):
        if n >= scale:
            q, r = divmod(n, scale)
            return f"{number_words(q)} {word}" + (f" {number_words(r)}" if r else "")
    raise ValueError(n)


def spoken_digits(digits: str, rng: random.Random, oh: float = 0.5) -> str:
    words = []
    for d in digits:
        if not d.isdigit():
            continue
        words.append("oh" if d == "0" and rng.random() < oh else DIGIT_WORD[int(d)])
    return " ".join(words)


def asr_variant(name: str, rng: random.Random) -> str:
    """A speech-recognition-style misspelling: split compounds, phonetic swaps, dropped letters."""
    s = name.lower()
    ops = []
    word = s.split()[0]
    if len(word) >= 6:
        ops.append("split")
    for a, b in (
        ("ph", "f"),
        ("ck", "k"),
        ("qu", "kw"),
        ("c", "k"),
        ("y", "i"),
        ("ee", "ea"),
        ("x", "ks"),
        ("th", "t"),
        ("ai", "ay"),
        ("ll", "l"),
        ("tt", "t"),
        ("ss", "s"),
    ):
        if a in s:
            ops.append((a, b))
    if s.endswith("e"):
        ops.append("drop_e")
    if not ops:
        ops.append("split")
    op = rng.choice(ops)
    if op == "split":
        cut = max(2, min(len(word) - 2, len(word) // 2 + rng.choice([-1, 0, 1])))
        return s.replace(word, word[:cut] + " " + word[cut:], 1)
    if op == "drop_e":
        return s[:-1]
    a, b = op
    return s.replace(a, b, 1)


@dataclass
class Entity:
    type: str
    key: str
    data: dict[str, Any]
    known: bool | None = None


@dataclass
class _TurnBuild:
    speaker_key: str
    parts: list[str] = field(default_factory=list)
    labels: list[tuple[int, int, str, str, str, bool | None]] = field(default_factory=list)
    negs: list[tuple[int, int]] = field(default_factory=list)
    length: int = 0

    def add(self, text: str) -> None:
        self.parts.append(text)
        self.length += len(text)

    def add_label(self, text: str, cat: str, ent: str, form: str, known: bool | None) -> None:
        self.labels.append((self.length, self.length + len(text), cat, ent, form, known))
        self.add(text)

    def add_neg(self, text: str) -> None:
        self.negs.append((self.length, self.length + len(text)))
        self.add(text)

    @property
    def text(self) -> str:
        return "".join(self.parts)


KNOWN_PEOPLE = 60


class Pools:
    """Entity pools. ``known_people`` is a fixed, deterministic list of people the user would
    have in their deny-list; everyone else drawn from first x last name pairs is unknown."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.d = data
        rng = random.Random("known-people")
        firsts, lasts = list(data["first_names"]), list(data["last_names"])
        known: list[str] = []
        while len(known) < min(KNOWN_PEOPLE, len(firsts) * len(lasts) // 2):
            full = f"{rng.choice(firsts)} {rng.choice(lasts)}"
            if full not in known:
                known.append(full)
        self.known_people = known
        self._known = set(known)

    def known_person(self, full: str) -> bool:
        return full in self._known

    @staticmethod
    def known_org(name: str) -> bool:
        """About three quarters of clients/brands/products/codenames are in the deny-list; the
        rest are unknown, so org recall is not only a deny-list lookup test."""
        return int(hashlib.sha256(name.encode()).hexdigest(), 16) % 4 != 0


class Generator:
    def __init__(self, templates: Sequence[dict[str, Any]], pools: dict[str, Any], set_name: str,
                 seed: int) -> None:  # fmt: skip
        self.templates = list(templates)
        self.pools = Pools(pools)
        self.set_name = set_name
        self.seed = seed

    # --- entities --------------------------------------------------------------------------

    def _new_entity(self, typ: str, key: str, rng: random.Random, used: set[str]) -> Entity:
        p = self.pools.d
        if typ == "person":
            for _ in range(50):
                if rng.random() < 0.5:
                    full = rng.choice(self.pools.known_people)
                else:
                    full = f"{rng.choice(p['first_names'])} {rng.choice(p['last_names'])}"
                if full not in used:
                    break
            used.add(full)
            first, last = full.split(" ", 1)
            return Entity(typ, key, {"full": full, "first": first, "last": last},
                          self.pools.known_person(full))  # fmt: skip
        if typ in NAMED or typ == "brand_common":
            pool = p[NAMED.get(typ, "brand_common")]
            fresh = [e for e in pool if f"{typ}:{e['name']}" not in used] or pool
            entry = rng.choice(fresh)
            used.add(f"{typ}:{entry['name']}")
            return Entity(typ, key, dict(entry), self.pools.known_org(entry["name"]))
        if typ == "location":
            return Entity(typ, key, {"full": rng.choice(p["locations"])})
        if typ == "email":
            first = rng.choice(p["first_names"]).lower()
            last = rng.choice(p["last_names"]).lower()
            local = rng.choice(
                [f"{first}.{last}", f"{first[0]}{last}", f"{first}", f"{first}_{last}"]
            )
            return Entity(typ, key, {"local": local, "domain": rng.choice(p["email_domains"])})
        if typ == "phone":
            area = f"{rng.randint(2, 9)}{rng.randint(0, 9)}{rng.randint(0, 9)}"
            line = f"01{rng.randint(0, 9)}{rng.randint(0, 9)}"
            return Entity(typ, key, {"area": area, "line": line, "with_area": rng.random() < 0.6})
        if typ == "ssn":
            # Area 9xx is never issued as an SSN; middle group 01-49 is not an ITIN range.
            area = f"9{rng.randint(0, 99):02d}"
            return Entity(
                typ, key, {"digits": f"{area}{rng.randint(1, 49):02d}{rng.randint(1, 9999):04d}"}
            )
        if typ == "card":
            return Entity(typ, key, {"digits": rng.choice(TEST_CARDS)})
        if typ == "ip":
            if rng.random() < 0.2:
                return Entity(typ, key, {"v": f"2001:db8::{rng.randint(1, 0xFFFF):x}"})
            net = rng.choice(["192.0.2", "198.51.100", "203.0.113", "10.20"])
            tail = f"{rng.randint(1, 254)}" if net.count(".") == 2 else \
                f"{rng.randint(0, 254)}.{rng.randint(1, 254)}"  # fmt: skip
            return Entity(typ, key, {"v": f"{net}.{tail}"})
        if typ == "url":
            path = rng.choice(
                ["pricing", "account/settings", "docs/onboarding", "q3-plan", "login"]
            )
            return Entity(typ, key, {"host": rng.choice(p["url_hosts"]), "path": path})
        if typ in ("account", "order"):
            n = rng.randint(6, 10)
            digits = "".join(str(rng.randint(0, 9)) for _ in range(n))
            prefix = (
                rng.choice(["", "", "ACCT-", "AC"])
                if typ == "account"
                else rng.choice(["", "#", "ORD-", "PO-"])
            )
            return Entity(typ, key, {"digits": digits, "prefix": prefix})
        if typ == "date":
            return Entity(typ, key, {"y": rng.randint(2024, 2028), "m": rng.randint(1, 12),
                                     "d": rng.randint(1, 28)})  # fmt: skip
        if typ == "amount":
            v = rng.choice([rng.randint(5, 950) * 1000, rng.randint(1, 90) * 100_000,
                            rng.randint(2, 40) * 50, rng.randint(11, 99) * 100_000_0])  # fmt: skip
            return Entity(typ, key, {"v": v})
        if typ == "percent":
            return Entity(
                typ, key, {"v": rng.choice([rng.randint(1, 60), rng.randint(1, 20) + 0.5])}
            )
        if typ == "quantity":
            return Entity(typ, key, {"v": rng.choice([rng.randint(3, 99), rng.randint(100, 2500)])})
        raise ValueError(f"unknown slot type {typ}")

    # --- surface forms ---------------------------------------------------------------------

    def _surface(self, e: Entity, form: str, rng: random.Random) -> tuple[str, str]:
        """Return (text, form actually used)."""
        d, t = e.data, e.type
        form = form or "default"
        if t == "person":
            if form == "asr":
                return asr_variant(d["full"], rng), form
            return d.get(form, d["full"]), form if form in d else "full"
        if t in NAMED:
            if form == "alias" and d.get("aliases"):
                return rng.choice(d["aliases"]), form
            if form == "asr":
                return (rng.choice(d["asr"]) if d.get("asr") else asr_variant(d["name"], rng)), form
            if form == "lower":
                return d["name"].lower(), form
            return d["name"], "full"
        if t == "brand_common":
            return (d["word"], "word") if form == "word" else (d["name"], "full")
        if t == "location":
            return d["full"], "full"
        if t == "email":
            if form == "spoken":
                local = d["local"].replace(".", " dot ").replace("_", " underscore ")
                dom = d["domain"].replace(".", " dot ")
                return f"{local} at {dom}", form
            return f"{d['local']}@{d['domain']}", "written"
        if t == "phone":
            area, line = d["area"], d["line"]
            if form == "spoken":
                s = spoken_digits("555" + line, rng)
                if d["with_area"]:
                    s = spoken_digits(area, rng) + ", " + s
                return s, form
            if form == "corrected":
                # Say a wrong digit, correct it, and end on the entity's real value.
                full = (area if d["with_area"] else "") + "555" + line
                k = rng.randrange(len(full))
                wrong = full[:k] + str((int(full[k]) + rng.randint(1, 8)) % 10)
                fix = rng.choice(["sorry", "no wait", "I mean", "scratch that", "let me redo that"])
                return (f"{spoken_digits(wrong, rng)}... {fix}, "
                        f"{spoken_digits(full, rng)}"), form  # fmt: skip
            style = rng.choice(
                ["{a}-555-{l}", "({a}) 555-{l}", "{a}.555.{l}", "+1 {a} 555 {l}", "555-{l}"]
            )
            if not d["with_area"]:
                style = "555-{l}"
            return style.format(a=area, l=line), "written"
        if t == "ssn":
            g = d["digits"]
            if form == "spoken":
                return spoken_digits(g, rng, oh=0.3), form
            return f"{g[:3]}-{g[3:5]}-{g[5:]}", "written"
        if t == "card":
            g = d["digits"]
            if form == "spoken":
                return spoken_digits(g, rng, oh=0.2), form
            style = rng.choice(["plain", "space", "dash"])
            if style == "plain" or len(g) != 16:
                return g, "written"
            sep = " " if style == "space" else "-"
            return sep.join(g[i : i + 4] for i in range(0, 16, 4)), "written"
        if t == "ip":
            return d["v"], "written"
        if t == "url":
            if form == "spoken":
                host = d["host"].replace(".", " dot ")
                return f"{host} slash {d['path'].split('/')[0]}", form
            return f"https://{d['host']}/{d['path']}", "written"
        if t in ("account", "order"):
            if form == "spoken":
                return spoken_digits(d["digits"], rng), form
            return f"{d['prefix']}{d['digits']}", "written"
        if t == "date":
            y, m, dd = d["y"], d["m"], d["d"]
            style = rng.choice(
                [
                    "{M} {d}",
                    "{M} {d}, {y}",
                    "{m}/{d}/{y}",
                    "{y}-{m:02d}-{d:02d}",
                    "{d}{o} of {M}",
                    "{M} {d}{o}",
                ]
            )
            o = "th" if 10 <= dd % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(dd % 10, "th")
            return style.format(M=MONTHS[m - 1], d=dd, y=y, m=m, o=o), "written"
        if t == "amount":
            v = d["v"]
            if form == "spoken":
                return f"{number_words(v)} dollars", form
            if v >= 1_000_000 and v % 100_000 == 0:
                return rng.choice(
                    [
                        f"${v / 1_000_000:g}M",
                        f"${v / 1_000_000:g} million",
                        f"{v / 1_000_000:g} million dollars",
                    ]
                ), "written"
            if v >= 1000 and v % 1000 == 0:
                return rng.choice(
                    [f"${v:,}", f"{v // 1000}k", f"${v // 1000}K", f"{v // 1000} grand"]
                ), "written"
            return f"${v:,}", "written"
        if t == "percent":
            v = d["v"]
            if form == "spoken":
                if isinstance(v, float):
                    whole = int(v)
                    return f"{number_words(whole)} point five percent", form
                return f"{number_words(v)} percent", form
            return rng.choice([f"{v:g}%", f"{v:g} percent"]), "written"
        if t == "quantity":
            v = d["v"]
            if form == "spoken":
                return number_words(v), form
            return f"{v:,}", "written"
        raise ValueError(t)

    # --- transcript ------------------------------------------------------------------------

    def _noise(self, text: str, rng: random.Random, rate: float) -> str:
        """Insert fillers at word boundaries in literal text."""
        if rate <= 0 or not text.strip():
            return text
        words = text.split(" ")
        out = []
        for i, w in enumerate(words):
            # Only before a word that follows a space (never glued to a preceding slot) and
            # never before punctuation.
            if i > 0 and w[:1].isalpha() and rng.random() < rate:
                out.append(rng.choice(FILLERS) + ",")
            out.append(w)
        return " ".join(out)

    def _fill(self, tb: _TurnBuild, text: str, entities: dict[str, Entity], rng: random.Random,
              used: set[str], noise: float) -> None:  # fmt: skip
        pos = 0
        for m in INLINE_RE.finditer(text):
            self._fill_slots(tb, text[pos : m.start()], entities, rng, used, noise)
            cat, body = m.group("cat"), m.group("text")
            pos = m.end()
            if cat == "TOPIC":  # labelled as a whole; slots inside are filled and labelled too
                start = tb.length
                self._fill_slots(tb, body, entities, rng, used, 0.0)
                self._topic_n += 1
                tb.labels.append((start, tb.length, "TOPIC", f"topic#{self._topic_n}", "", None))
            elif cat == "NEG":
                tb.add_neg(body)
            elif cat in INLINE_CATEGORIES:
                key = f"{cat.lower()}#{m.group('id') or body.casefold()}"
                tb.add_label(body, cat, key, "literal", None)
            else:
                raise ValueError(f"unknown inline label category: {cat}")
        self._fill_slots(tb, text[pos:], entities, rng, used, noise)

    def _entity(self, typ: str, k: str | None, entities: dict[str, Entity], rng: random.Random,
                used: set[str]) -> Entity:  # fmt: skip
        key = f"{typ}#{k}" if k else f"{typ}#auto{len(entities)}"
        if key not in entities:
            entities[key] = self._new_entity(typ, key, rng, used)
        return entities[key]

    def _fill_slots(self, tb: _TurnBuild, text: str, entities: dict[str, Entity],
                    rng: random.Random, used: set[str], noise: float) -> None:  # fmt: skip
        pos = 0
        for m in SLOT_RE.finditer(text):
            tb.add(self._noise(text[pos : m.start()], rng, noise))
            pos = m.end()
            if m.group() in ("{{", "}}"):
                tb.add(m.group()[0])
                continue
            typ, k, form = m.group("type"), m.group("k"), m.group("form") or ""
            if typ not in CATEGORY:
                raise ValueError(f"unknown slot type: {typ}")
            e = self._entity(typ, k, entities, rng, used)
            surface, used_form = self._surface(e, form, rng)
            if typ == "brand_common" and used_form == "word":
                tb.add_neg(surface)
            else:
                tb.add_label(surface, CATEGORY[typ], e.key, used_form, e.known)
        tb.add(self._noise(text[pos:], rng, noise))

    def generate_one(self, index: int) -> tuple[LabelledTranscript, list[Entity]]:
        rng = random.Random(f"{self.set_name}:{self.seed}:{index}")
        self._topic_n = 0
        tpl = self.templates[index % len(self.templates)]
        entities: dict[str, Entity] = {}
        used: set[str] = set()
        noise = rng.choice([0.0, 0.03, 0.08])
        generic = rng.random() < 0.2
        speaker_names: dict[str, tuple[str, Entity | None]] = {}
        for n, key in enumerate(tpl["speakers"]):
            if generic:
                speaker_names[key] = (GENERIC_SPEAKERS[n % len(GENERIC_SPEAKERS)], None)
            else:
                e = self._entity("person", f"spk{key}", entities, rng, used)
                speaker_names[key] = (e.data["full"], e)

        builds: list[_TurnBuild] = []
        for turn in tpl["turns"]:
            if "split" in turn:
                sp = turn["split"]
                typ, _, rest = sp["slot"].partition("#")
                k, _, form = rest.partition(".")
                e = self._entity(typ, k or None, entities, rng, used)
                surface, used_form = self._surface(
                    e, form or ("spoken" if typ in ("phone", "card", "account", "ssn") else ""), rng
                )
                words = surface.split(" ")
                cut = max(1, len(words) // 2)
                p1, p2 = " ".join(words[:cut]), " ".join(words[cut:])
                tb = _TurnBuild(sp["speaker"])
                self._fill_slots(tb, sp.get("before", "").rstrip() + " ", entities, rng, used, 0)
                tb.add_label(p1, CATEGORY[typ], e.key, used_form + "+split", e.known)
                builds.append(tb)
                ib = _TurnBuild(sp["interrupt"]["speaker"])
                self._fill_slots(ib, sp["interrupt"]["text"], entities, rng, used, 0)
                builds.append(ib)
                tb2 = _TurnBuild(sp["speaker"])
                if p2:
                    tb2.add_label(p2, CATEGORY[typ], e.key, used_form + "+split", e.known)
                self._fill_slots(tb2, " " + sp.get("after", "").lstrip(), entities, rng, used, 0)
                builds.append(tb2)
                continue
            ((key, text),) = turn.items()
            tb = _TurnBuild(str(key))
            self._fill(tb, str(text), entities, rng, used, noise)
            builds.append(tb)

        casing = rng.choices(["mixed", "lower", "upper"], weights=[70, 18, 12])[0]

        def case(s: str) -> str:
            return s.lower() if casing == "lower" else s.upper() if casing == "upper" else s

        lt = LabelledTranscript(
            id=f"{self.set_name}-{index:04d}",
            speakers=[],
            texts=[],
            meta={
                "template": tpl["id"],
                "domain": tpl.get("domain", "other"),
                "casing": casing,
                "seed": self.seed,
                "set": self.set_name,
                "generic_speakers": generic,
            },
        )
        for i, tb in enumerate(builds):
            name, spk_entity = speaker_names[tb.speaker_key]
            lt.speakers.append(case(name))
            lt.texts.append(case(tb.text))
            if spk_entity is not None:
                lt.labels.append(Label(i, "speaker", 0, len(name), "PERSON", spk_entity.key,
                                       "full", spk_entity.known))  # fmt: skip
            for s, e_, cat, ent, form, known in tb.labels:
                lt.labels.append(Label(i, "text", s, e_, cat, ent, form, known))
            for s, e_ in tb.negs:
                lt.negatives.append(Negative(i, "text", s, e_))
        return lt, list(entities.values())

    def generate(self, n: int) -> Iterator[tuple[LabelledTranscript, list[Entity]]]:
        for i in range(n):
            yield self.generate_one(i)


# --- rendering to formats -------------------------------------------------------------------


def to_document(lt: LabelledTranscript, fmt: str) -> Document:
    turns = []
    t = 0.0
    for i, (sp, text) in enumerate(zip(lt.speakers, lt.texts, strict=True)):
        dur = 1.0 + len(text) / 15.0
        extra = {"style": "voice"} if fmt == "vtt" else {"style": "label"} if fmt == "srt" else {}
        turns.append(Turn(index=i, speaker=sp, text=text, start=round(t, 3), end=round(t + dur, 3),
                          extra=extra))  # fmt: skip
        t += dur + 0.2
    return Document(format=fmt, turns=tuple(turns))


def render(lt: LabelledTranscript, fmt: str) -> str:
    """Render to ``fmt`` and verify the adapter parses it back to the same turns."""
    out = adapters.get(fmt).render(to_document(lt, fmt))
    back = adapters.get(fmt).parse(out, Limits())
    got = [(t.speaker, t.text) for t in back.turns]
    want = list(zip(lt.speakers, lt.texts, strict=True))
    if got != want:
        raise AssertionError(f"round trip mismatch for {lt.id} in {fmt}")
    return out


# --- deny-list for a set ---------------------------------------------------------------------


def denylist_from_pools(pools: dict[str, Any]) -> list[dict[str, Any]]:
    """What a user would plausibly maintain: names and aliases only (never ASR variants)."""
    out: list[dict[str, Any]] = []
    for typ, cat in (("clients", "CLIENT"), ("brands", "BRAND"), ("products", "PRODUCT"),
                     ("codenames", "CODENAME")):  # fmt: skip
        for e in pools.get(typ, []):
            if Pools.known_org(e["name"]):
                out.append(
                    {"name": e["name"], "category": cat, "aliases": list(e.get("aliases", []))}
                )
    for e in pools.get("brand_common", []):
        if Pools.known_org(e["name"]):
            out.append({"name": e["name"], "category": "BRAND", "aliases": [], "common_word": True,
                        "context": list(e.get("context", []))})  # fmt: skip
    for full in Pools(pools).known_people:
        out.append({"name": full, "category": "PERSON", "aliases": []})
    return out


# --- loading & writing ------------------------------------------------------------------------


def load_set(root: Path, set_name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tdir = root / "eval" / "templates" / set_name
    templates = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(tdir.glob("*.yaml"))]
    pools = yaml.safe_load(
        (root / "eval" / "pools" / set_name / "pools.yaml").read_text(encoding="utf-8")
    )
    if not templates:
        raise FileNotFoundError(f"no templates for set {set_name}")
    return templates, pools


def write_set(root: Path, set_name: str, seed: int, n: int, out_dir: Path,
              formats: Sequence[str] = FORMATS) -> list[Path]:  # fmt: skip
    templates, pools = load_set(root, set_name)
    gen = Generator(templates, pools, set_name, seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for i, (lt, _) in enumerate(gen.generate(n)):
        fmt = formats[i % len(formats)]
        ext = {"txt": "txt", "vtt": "vtt", "srt": "srt", "json": "json"}[fmt]
        path = out_dir / f"{lt.id}.{ext}"
        path.write_text(render(lt, fmt), encoding="utf-8")
        labels = {
            "id": lt.id,
            **lt.meta,
            "format": fmt,
            "labels": [lb.to_json() for lb in lt.labels],
            "negatives": [ng.to_json() for ng in lt.negatives],
        }
        (out_dir / f"{lt.id}.labels.json").write_text(
            json.dumps(labels, indent=1), encoding="utf-8"
        )
        written.append(path)
    (out_dir / "denylist.yaml").write_text(
        yaml.safe_dump({"denylist": denylist_from_pools(pools)}, sort_keys=False), encoding="utf-8"
    )
    return written
