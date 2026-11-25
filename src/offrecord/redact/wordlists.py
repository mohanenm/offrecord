"""Bundled word lists (derived from SCOWL, see data/LICENSE-SCOWL.txt). Loaded lazily, once."""

from __future__ import annotations

import gzip
from functools import lru_cache
from importlib import resources

# Business shorthand and speech tokens that are not names even though they are not in the
# dictionary list.
EXTRA_COMMON = frozenset(
    "arr mrr qbr crm erp api apis saas paas kpi kpis roi okr okrs eta faq sla slas sso hr cfo ceo "
    "cto coo cmo cro vp vps svp evp pm pms eod eow fyi asap usd gbp eur ok okay um uh umm uhh mm "
    "mmm hmm hm yeah yep yup nope nah gonna wanna gotta kinda sorta dunno lemme gimme ya yah q1 q2 "
    "q3 q4 h1 h2 yoy qoq mom ytd fy capex opex ebitda cogs gtm sdr sdrs bdr ae aes csm pov poc mvp "
    "ux ui qa devops sre url urls email emails pdf csv ppt xls pos sku skus b2b b2c smb msp isv "
    "rfp rfi rfq nda ndas sow msa po ar ap gl ip ips vpn wifi ok'd cc bcc dm dms pto ooo wfh zoom "
    "slack teams webex onboarding offboarding upsell upsells cross-sell headcount backfill "
    "backfills roadmap roadmaps pipeline signup signups login logins admin admins dashboard "
    "dashboards workflow workflows".split()
)


# "I" and its contractions: the dictionary list only keeps lower-case entries.
PRONOUNS = frozenset("i i'm i'll i'd i've im ill ive id".split())


# Very frequent function words: never treated as a name part, even when a speaker or deny-list
# person happens to share one ("Will", "May" are handled as names only in name contexts).
FUNCTION_WORDS = frozenset(
    """
    a an the and or but if then so of to in on at by for with from as is are was were be been
    being am do does did have has had will would shall should can could may might must not no
    yes it its this that these those there here what which who whom whose when where why how
    all any both each few more most other some such only own same than too very just also i
    me my we us our you your he him his she her they them their one two up down out over under
    again once about into through during before after above below off further while because
    until nor own get got go going ok okay well oh hi hey right sure thanks
    """.split()
)


@lru_cache(maxsize=1)
def common_words() -> frozenset[str]:
    data = resources.files("offrecord.data").joinpath("common_words.txt.gz").read_bytes()
    return frozenset(gzip.decompress(data).decode("ascii").split()) | EXTRA_COMMON | PRONOUNS


@lru_cache(maxsize=1)
def proper_names() -> frozenset[str]:
    data = resources.files("offrecord.data").joinpath("proper_names.txt.gz").read_bytes()
    return frozenset(gzip.decompress(data).decode("ascii").split())


def is_common(token: str) -> bool:
    """Case-independent. Handles possessives and contractions ("we'll", "Orla's")."""
    t = token.lower()
    words = common_words()
    if t in words:
        return True
    if "'" in t:
        base = t.split("'", 1)[0]
        return base in words or t.removesuffix("'s") in words
    return False
