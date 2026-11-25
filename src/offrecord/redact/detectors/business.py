"""Rule-based business context: amounts, percentages, quantities, topic sentences, codenames.

Recall first:
- Currency amounts are always redacted (with or without trigger words).
- Percentages are always redacted (idioms like "a hundred percent" excepted).
- Quantities are redacted when followed by a business unit noun ("340 stores") or when they sit
  near a trigger term ("ARR", "revenue", "headcount", ...).
- Sentences containing a strong topic term are redacted whole (TOPIC); weak terms only flag the
  sentence for review (both configurable; see Config.topics_*).
- "Project X" / "Operation X" / "Initiative X" are codenames when X is not a dictionary word.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterator
from typing import ClassVar

from offrecord.model import Field, Span
from offrecord.normalize.fields import Window
from offrecord.redact.config import Config
from offrecord.redact.wordlists import is_common

NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
SCALE = r"(?:k|K|m|M|mm|MM|b|B|bn|BN|thousand|million|billion|trillion|grand)"
CURRENCY_WORDS = r"(?:dollars?|bucks|usd|eur|euros?|gbp|pounds?|quid|yen|cad|aud)"
# Tails that belong to an amount: "$540 an hour", "twelve bucks a seat", "41k MRR",
# "$600,000 over three years", "812 dollars and forty cents".
RATE_UNITS = (
    r"hour|hr|day|week|wk|month|mo|year|yr|annum|head|person|night|seat|user|yard|load|piece|"
    r"unit|letter|square\s+foot|sq\s?ft|foot|ft|mile|lb|kilo|kg|ton|tonne|share|license|licence|"
    r"room|plate|cover|ticket|visit|session|shift|employee|member|item|box|pallet|gallon|litre|"
    r"liter|page|word|minute|min"
)
RATE_TAIL = rf"(?:\s*(?:/|\ba\b|\ban\b|\bper\b|\beach\b)\s*(?:{RATE_UNITS})\b)"
METRIC_TAIL = r"(?:\s+(?:arr|mrr|acv|tcv|base|salary|ote|all[- ]in|upfront|flat|in\s+earnest)\b)"
CENTS_TAIL = r"(?:\s+and\s+[\w-]+\s+cents?\b)"
OVER_TAIL = r"(?:\s+over\s+(?:\d+|[a-z]+)\s+(?:years?|months?|weeks?|quarters?)\b)"
TAILS = rf"(?:{CENTS_TAIL}|{RATE_TAIL}|{METRIC_TAIL}|{OVER_TAIL})*"
CUR = r"[$\u20ac\u00a3\u00a5]"
CORE = (
    rf"(?:{CUR}\s?(?:{NUM})(?:\s?{SCALE}\b)?"
    rf"|(?<![\w.])(?:{NUM})(?:\s+and\s+a\s+half)?\s?{SCALE}\b(?:\s+{CURRENCY_WORDS}\b)?"
    rf"|(?<![\w.])(?:{NUM})\s?{CURRENCY_WORDS}\b"
    rf"|\b(?:usd|eur|gbp|cad|aud)\s?(?:{NUM})(?:\s?{SCALE}\b)?)"
)
NUM_WORD = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety)(?:-[a-z]+)?"
)
# A range start joined to an amount: "$148,000 to $176,000", "46 to 52k", "sixty to 90 bucks".
RANGE_HEAD = (
    rf"(?:(?:(?<![\w.]){CUR}?\s?(?:{NUM})(?:\s?{SCALE}\b)?\s*(?:-|to|and|or)"
    rf"|\b{NUM_WORD}\s+to)\s*)?"
)
AMOUNT_RES = [
    re.compile(rf"(?i){RANGE_HEAD}{CORE}{TAILS}"),
    # A bare number with a rate or pay tail: "1460 an hour", "34 a head", "220 base".
    re.compile(rf"(?i)(?<![\w.$])(?:{NUM})(?:{RATE_TAIL}|{METRIC_TAIL}){TAILS}"),
    # "a dollar twenty", "a buck fifty"
    re.compile(rf"(?i)\ba\s+(?:dollar|buck|euro|pound|quid)(?:\s+(?:and\s+)?(?:{NUM}))?\b{TAILS}"),
]
# Spoken amounts with a scale word ("three point two million", "ten thousand") are figures on
# their own, without a trigger (the norm view turns them into digits).
SPOKEN_SCALE = re.compile(r"(?i)\b(?:thousand|million|billion|trillion|grand)\b")
DIG_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
# A spoken or written figure: "41", "twelve", "seventy-something", "point seven five",
# "two and a half", "one point five".
FIG = (
    rf"(?:(?:{NUM})|{NUM_WORD}(?:[\s-]+(?:something|ish|{NUM_WORD}))?|"
    rf"(?:a\s+)?point(?:\s+{DIG_WORD})+)"
    rf"(?:\s+and\s+a\s+(?:half|quarter)|\s+point(?:\s+{DIG_WORD})+)?"
)
PCT_UNIT = r"(?:%|percent\b|per\s?cent\b|pct\b|bps\b|basis\s+points\b)"
PCT_TAIL = r"(?:\s+(?:year[- ]over[- ]year|yoy|qoq|discount|equity|off)\b)?"
PERCENT_RE = re.compile(
    rf"(?i)(?<![\w.])(?:{FIG}\s*(?:-|to|and|or)\s*)?{FIG}\s?{PCT_UNIT}{PCT_TAIL}"
)
PERCENT_MORE = [
    # "down one and a half points", "up two points"
    re.compile(
        r"(?i)\b(?:up|down|by|gained|lost|dropped|rose|fell|grew|shrank)\s+"
        rf"(?:about\s+|roughly\s+|around\s+)?(?P<d>{FIG}\s+(?:percentage\s+)?points?)\b"
    ),
    # "the high eighties", "mid-twenties"
    re.compile(
        r"(?i)(?P<d>\b(?:high|mid|low)[- ](?:teens|twenties|thirties|forties|fifties|sixties|"
        r"seventies|eighties|nineties))\b"
    ),
    # splits: "50/50", "60/40", "fifty-fifty"
    re.compile(
        r"(?i)(?P<d>(?<![\d/])(?:50/50|60/40|40/60|70/30|30/70|80/20|20/80|90/10|10/90)"
        r"(?![\d/])|\bfifty[- ]fifty\b)"
    ),
]
PERCENT_IDIOM = re.compile(r"(?i)^(?:100|a hundred|one hundred|110|a thousand)\s?(?:%|percent)")
UNIT_NOUNS = (
    "stores|store|units|unit|seats|seat|licenses|licences|license|users|customers|clients|"
    "employees|people|staff|heads|fte|ftes|locations|sites|offices|branches|accounts|deals|"
    "opportunities|opps|registers|terminals|devices|trucks|vehicles|warehouses|depots|sku|skus|"
    "orders|subscribers|members|patients|beds|clinics|hospitals|schools|restaurants|outlets|"
    "franchises|reps|agents|engineers|hires|positions|roles|contracts|tickets|cases|calls|"
    "boxes|pallets|shipments|tons|tonnes|plants|factories|lines|projects|partners|vendors|"
    "cartons|crates|kits|attendees|guests|rooms|spaces|ovens|machines|nurses|doctors|kids|"
    "students|children|transactions|pieces|parts|items|packages|parcels|containers|loads|"
    "yards|acres|square\\s+feet|sq\\s?ft|patients|participants|volunteers|families|homes|"
    "households|tables|covers|cars|drivers|routes|riders|downloads|installs|signups|sign-ups"
)
TIME_UNITS = (
    r"hours?|hrs?|days?|weeks?|months?|years?|yrs?|minutes?|mins?|seconds?|secs?|nights?|"
    r"floors?|steps?|levels?|spots?|places?|notches?|inches|inch|miles?|feet|foot|pounds?|lbs?|"
    r"dozen|o'?clock|am|pm|degrees?"
)
NOT_A_RATE = (
    rf"(?!\s*(?:%|percent\b|per\s?cent\b|pct\b|points?\b|bps\b|basis\b)"
    rf"|\s+(?:\w+\s+)?(?:{UNIT_NOUNS}|{TIME_UNITS}|{CURRENCY_WORDS}|{SCALE})\b)"
)
# Rates without a percent word, found in tuning transcripts: a figure after a movement verb
# ("ticket was up about four", "usage is down like forty"), a share ("half of my time", "a
# fifth of your book", "three and a half"), a decimal after "at"/"to"/"is" ("we're at 12.8").
PERCENT_BARE = [
    re.compile(
        r"(?i)\b(?:up|down|rose|fell|grew|dropped|slipped|declined|increased|decreased|climbed|"
        r"jumped|gained|lost|raised|bumped)\s+(?:about\s+|like\s+|roughly\s+|around\s+|"
        rf"nearly\s+|almost\s+|another\s+|over\s+)?(?P<d>{FIG})\b{NOT_A_RATE}"
    ),
    re.compile(
        r"(?i)(?<!first\s)(?<!second\s)(?<!back\s)(?<!other\s)(?<!better\s)"
        rf"(?P<d>\b(?:(?<!and\s)a[\s-]+half|(?:a|one|two|three|four)[\s-]+(?:halves|thirds?|"
        rf"quarters?|fifths?|tenths?)|one[\s-]+half|half|{NUM_WORD}\s+and\s+a\s+(?:half|quarter)))\b"
        r"(?![\s-]+(?:past|to\s+\w+|running|in\s+a\s+row|"
        r"an?\s+(?:hour|day|week|month|year|mile|inch|pound|dozen)|"
        r"of\s+an?\s+(?:hour|inch|mile)|of\s+(?:the\s+)?(?:year|fiscal)|ago|time|way|"
        rf"century|{TIME_UNITS})\b)"
    ),
    re.compile(
        r"(?i)\b(?:at|to|is|was|of|negative|minus|hit|around|about)\s+"
        rf"(?P<d>(?<![\d.])\d{{1,2}}\.\d{{1,2}})(?![\d.]){NOT_A_RATE}"
    ),
]
LONE_ONE = re.compile(r"(?i)one(?:-(?:off|on|time|way|to))?$")
# Spoken numbers ("nine", "seventeen", "sixty two", "six and change", "one in five", "double")
# count as figures near a trigger, like digits do. A lone "one" is too common to count.
SPOKEN_NUM = re.compile(
    rf"(?i)\b(?:{NUM_WORD}(?:[\s-]+(?:and\s+)?(?:{NUM_WORD}|hundred)){{0,6}}"
    rf"(?:\s+and\s+a\s+(?:half|quarter)|\s+point(?:\s+{DIG_WORD})+|\s+and\s+change|[\s-]ish)?"
    rf"|(?:a\s+)?point(?:\s+{DIG_WORD})+|double|doubled|triple|tripled"
    rf"|{NUM_WORD}\s+in\s+{NUM_WORD})\b"
    rf"(?![\s-]+(?:{TIME_UNITS}|past|times|pack)\b)"  # durations and clock times are not figures
)
# Spoken counts: "eleven", "three hundred and twelve", "a hundred and forty", "four or five".
QWORD = rf"(?:{NUM_WORD}|hundred|thousand|million|billion)"
QFIG = (
    rf"(?:(?:{NUM})|(?:an?\s+(?=hundred|thousand|million))?{QWORD}"
    rf"(?:[\s-]+(?:and\s+)?{QWORD}){{0,6}})"  # bounded: linear time on long number chains
)
QTY_UNIT_RE = re.compile(
    rf"(?i)(?<![\w.$])(?:{QFIG}\s+(?:or|to)\s+)?{QFIG}"
    rf"(?=\s+(?:\w+\s+)?(?:{UNIT_NOUNS})\b|\s+per\s+(?:{UNIT_NOUNS}|store|site|location)\b)"
)
TRIGGERS = re.compile(
    r"(?i)\b(?:arr|mrr|revenue|revenues|margin|margins|bookings|booked|pipeline|churn|budget|"
    r"budgets|headcount|quota|forecast|ebitda|profit|profits|loss|losses|sales|spend|cost|costs|"
    r"price|pricing|valuation|run[- ]rate|gross|net|growth|retention|nrr|grr|acv|tcv|ltv|cac|"
    r"burn|runway|cash|deal|contract|discount|fee|fees|salary|bonus|comp|compensation|"
    r"units|volume|capex|opex|q[1-4]|fy\d{2,4}|yoy|qoq|ytd|"
    # money and negotiation context (spoken amounts often have no currency word)
    r"paid|pay|pays|charged|charge|charges|total|totals|offer|offered|counter|credit|earnest|"
    r"deductible|grant|grants|fund|funding|asking|approved|rate|rates|wage|wages|base|"
    r"assessment|tax|taxes|balance|savings|owe|owed|refund|deposit|invoice|invoiced|raise|"
    r"round|allocation|pro-rata|preference|closing|premium|surcharge|fine|fines|settlement|"
    r"demand|dues|rent|mortgage|loan|quote|quoted|bid|afford|stretch|grand|bucks|dollars?|"
    r"euros?|pounds|cents|matched|capped|commission|stipend|invest|investment|amount|spent|"
    r"transfer|transferred|withdrawal|purchase|bill|billed|range|cheaper|expensive|"
    r"interest|apr|equity|stake|target|traffic|ticket|indirect|markup|dilution|"
    # rates that are usually said without "percent"
    r"attrition|vacancy|occupancy|royalty|conversion|turnover|utili[sz]ation|yield|usage|"
    r"comps?|basket|overhead|increase|increases|premiums|fill\s+rate|on[- ]time|win\s+rate|"
    r"discounting|labou?r|capacity|share|absenteeism|readmissions?|participation|"
    r"enrol(?:l)?ment|reimbursement|copay|coinsurance|cpi|inflation|apy|irr|roi|delinquency|"
    r"chargebacks?|shrink|spoilage|scrap|defects?|uptime|downtime|load\s+factor|"
    + UNIT_NOUNS
    + r")\b"
)
# Bare numbers (and "N to N" ranges) near a trigger; times of day are not figures.
BARE_NUM_RE = re.compile(
    rf"(?<![\w.$:/-])(?:{NUM})(?:\s*(?:-|to)\s*(?:{NUM}))?(?![\w:/]|\.\d)"
    r"(?!\s*(?:am|pm|a\.m\.|p\.m\.|o'?clock)\b)"
)
TRIGGER_RADIUS = 60  # characters on either side
# A line break also ends a sentence: speech-to-text lines often have no final punctuation.
SENTENCE_RE = re.compile(r"(?:[^.!?\n]|\.(?=\d))+(?:[.!?]+|$)", re.MULTILINE)
CODENAME_RE = re.compile(
    r"(?i)\b(?:project|operation|initiative|program|programme|codename|code\s+name)\s+([A-Za-z][\w-]{2,})"
)


def _term_regex(terms: tuple[str, ...]) -> re.Pattern[str] | None:
    if not terms:
        return None
    alts = sorted({re.escape(t.strip().lower()).replace(r"\ ", r"\s+") for t in terms if t.strip()},
                  key=len, reverse=True)  # fmt: skip
    return re.compile(r"(?i)(?<![\w'])(?:" + "|".join(alts) + r")(?![\w'])")


class BusinessDetector:
    name: ClassVar[str] = "business"
    categories: ClassVar[frozenset[str]] = frozenset(
        {"AMOUNT", "PERCENT", "QUANTITY", "TOPIC", "CODENAME"}
    )

    def __init__(self, config: Config) -> None:
        self.enabled = config.detect_business
        mode = config.topic_mode
        strong, weak = config.topics_redact, config.topics_flag
        if mode == "redact":
            strong, weak = strong + weak, ()
        elif mode == "flag":
            strong, weak = (), strong + weak
        elif mode == "off":
            strong, weak = (), ()
        self.strong = _term_regex(strong)
        self.weak = _term_regex(weak)

    def detect(self, window: Window) -> Iterator[Span]:
        if not self.enabled:
            return
        for f in window.fields:
            if f.kind == "speaker":
                continue
            for view in ("orig", "norm"):
                yield from self._figures(f, view, f.views[view].text)
            yield from self._topics(f)
            yield from self._codenames(f)

    def _figures(self, f: Field, view: str, text: str) -> Iterator[Span]:
        # No digit-only shortcut: spoken figures ("two and a half percent") have no digits.
        for rx in AMOUNT_RES:
            for m in rx.finditer(text):
                yield Span(f.id, m.start(), m.end(), "AMOUNT", "business.amount", 0.9,
                           None, "amount", view=view)  # fmt: skip
        for m in PERCENT_RE.finditer(text):
            if PERCENT_IDIOM.match(m.group()):
                continue
            yield Span(f.id, m.start(), m.end(), "PERCENT", "business.percent", 0.9, None,
                       "percent", view=view)  # fmt: skip
        for rx in (*PERCENT_MORE, *PERCENT_BARE):
            for m in rx.finditer(text):
                if LONE_ONE.match(m.group("d")):  # "follow up one more time"
                    continue
                yield Span(f.id, m.start("d"), m.end("d"), "PERCENT", "business.percent", 0.6,
                           None, "percent:spoken", view=view)  # fmt: skip
        for m in QTY_UNIT_RE.finditer(text):
            yield Span(f.id, m.start(), m.end(), "QUANTITY", "business.quantity", 0.8, None,
                       "quantity:unit", view=view)  # fmt: skip
        if view == "norm":
            for m in BARE_NUM_RE.finditer(text):
                a, b = f.project(view, m.start(), m.end())
                if SPOKEN_SCALE.search(f.orig[a:b]):
                    yield Span(f.id, m.start(), m.end(), "AMOUNT", "business.amount", 0.7,
                               None, "amount:spoken-scale", view=view)  # fmt: skip
        trig = [t.start() for t in TRIGGERS.finditer(text)]
        if trig:
            spoken = SPOKEN_NUM.finditer(text) if view == "orig" else iter(())
            for m in (*BARE_NUM_RE.finditer(text), *spoken):
                s, e = m.span()
                if LONE_ONE.match(m.group()):
                    continue
                # Nearest trigger starts via bisect: linear-time overall.
                k = bisect.bisect_left(trig, s)
                near = [trig[j] for j in (k - 1, k) if 0 <= j < len(trig)]
                if any(abs(s - t) <= TRIGGER_RADIUS + 20 for t in near):
                    yield Span(f.id, s, e, "QUANTITY", "business.quantity", 0.6, None,
                               "quantity:trigger", view=view)  # fmt: skip

    def _topics(self, f: Field) -> Iterator[Span]:
        if self.strong is None and self.weak is None:
            return
        text = f.orig
        for m in SENTENCE_RE.finditer(text):
            sent = m.group()
            s = m.start() + (len(sent) - len(sent.lstrip()))
            e = m.end()
            if e <= s:
                continue
            if self.strong is not None and self.strong.search(sent):
                yield Span(f.id, s, e, "TOPIC", "business.topic", 0.7, None, "topic:strong")
            elif self.weak is not None and self.weak.search(sent):
                yield Span(f.id, s, e, "TOPIC", "business.topic", 0.5, None, "topic:weak",
                           action="flag")  # fmt: skip

    def _codenames(self, f: Field) -> Iterator[Span]:
        for m in CODENAME_RE.finditer(f.orig):
            if not is_common(m.group(1)):
                yield Span(f.id, m.start(), m.end(), "CODENAME", "business.codename", 0.7,
                           m.group(1).lower(), "codename:pattern")  # fmt: skip
