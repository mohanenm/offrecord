"""Configuration: deny-list entries with per-entry options, topic terms, detector switches.

The config is sensitive (it lists clients and codenames). Errors refer to entry indexes and
field names only, never values, and the config is never echoed.
"""

from __future__ import annotations

import csv
import difflib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from offrecord.errors import UsageError
from offrecord.model import categories
from offrecord.safeio import read_input_text

MATCH_MODES = ("fuzzy", "exact", "case_sensitive")
# Every key the config may use, per section. Anything else is an error: a misspelt key would
# otherwise be ignored and leave text readable that the user meant to hide.
TOP_KEYS = ("denylist", "protect", "topics", "detect", "plugins", "models")
TOPIC_KEYS = ("redact", "flag", "personal", "mode")
DETECT_KEYS = ("dates", "business", "people", "unknown_words")
MODEL_KEYS = ("tier", "ner", "ner_threshold", "threads", "llm")
ENTRY_KEYS = ("name", "category", "aliases", "match", "min_score", "context", "common_word")
_KEY_SHAPE = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
DEFAULT_MIN_SCORE = 85  # rapidfuzz ratio, 0-100; lower = more recall

# Topic terms: a sentence containing one becomes a TOPIC span (the whole sentence).
# Personal circumstances (special-category personal data: health, family and legal matters, an
# individual's employment or conduct). These sentences are redacted at every protection level.
DEFAULT_TOPICS_PERSONAL = (
    "termination", "terminate", "terminated", "firing", "fired", "severance",
    "performance improvement plan", "pip", "diagnosed", "diagnosis", "diabetes", "diabetic",
    "cancer", "chemo", "chemotherapy", "depression", "anxiety", "disorder", "breakdown",
    "medication", "medications", "therapist", "therapy", "surgery", "pregnant", "pregnancy",
    "miscarriage", "allergy", "allergies", "allergic", "coeliac", "celiac", "apnea", "dyslexia",
    "adhd", "autism", "autistic", "hospitalized", "hospitalised", "fractured", "broke his",
    "broke her", "broke my", "disease", "suicide", "suicidal", "overdose", "rehab",
    "medical leave", "sick leave", "mental health", "dementia", "seizure", "seizures", "hiv",
    "break out in hives", "sleep study", "adverse event", "kidney injury", "health insurance",
    "divorce", "divorced", "custody", "restraining order", "protective order", "temporary order",
    "passed away", "maiden name", "defamation", "work visa", "visa", "sponsor", "immigration",
    "green card", "deportation", "arrested", "criminal", "conviction", "probation", "eviction",
    "foreclosure", "notice of default", "behind on", "lost his job", "lost her job", "lost my job",
    "estate dispute", "moved out", "fraud", "scam", "scammed", "theft", "stole", "stolen",
    "stealing", "camera footage", "recordable", "osha", "not tied off", "fell from", "urgent care",
    "wage claim", "heart attack", "heart scare", "had a stroke", "a stroke", "infection",
    "outbreak", "sentinel event", "died", "death", "funeral", "hospice", "illness", "injury",
    "injured", "passed in", "loss prevention", "use of force", "use-of-force", "misconduct",
    "harassment", "harassed", "discrimination", "retaliation", "whistleblower", "grievance",
    "disciplinary", "written up",
)  # fmt: skip
# Business-sensitive terms (deals, workforce plans, litigation, secrecy, incidents). Redacted
# with protect: confidential; review flags with the default protect: pii.
DEFAULT_TOPICS_REDACT = (
    "acquisition", "acquisitions", "acquire", "acquired", "acquiring", "merger", "mergers",
    "buyout", "takeover", "layoff", "layoffs", "laid off", "lay off", "lay people off",
    "reduction in force", "rif", "restructuring", "restructure", "reorg", "reorganization",
    "hiring freeze", "furlough", "furloughs", "lawsuit", "litigation", "litigate", "settlement",
    "settle", "settling", "sue", "suing", "sued", "subpoena", "investigation", "regulator",
    "regulators", "bankruptcy", "insolvency", "insolvent", "divest", "divestiture", "divesting",
    "ipo", "fundraise", "fundraising", "term sheet", "valuation", "confidential", "under nda",
    "do not share", "don't share", "don't mention", "do not mention", "between us",
    "between you and me", "off the record", "keep this quiet", "price increase", "price increases",
    "raise prices", "raising prices", "raise our prices", "price hike", "double the price",
    "cut the team", "cutting the team", "cut jobs", "cutting jobs", "close the site",
    "close the plant", "close the office", "closing the site", "shut down the",
    "shutting down the", "wind down", "exit the market", "poach", "poaching", "breach",
    "data breach", "health inspection", "without a permit", "without a certificate",
    "asset protection", "tip anyone off", "investigate", "investigating", "auditors",
    "audit findings", "never reported", "unreported", "violation", "failed its", "going under",
    "haven't paid", "die failure", "objection", "complaint says", "not public", "isn't public",
    "hasn't been announced", "not been announced", "nobody below", "nobody outside",
    "nobody else knows", "haven't told", "hasn't told", "told me privately", "keep it off",
    "off the minutes", "keep it that way", "don't let him in", "don't let her in", "diligence",
    "acqui-hire", "acquihire", "reduction of", "transfers if", "wind-down", "lose the account",
    "shopping their", "shopping us", "evaluating a competitor", "may not renew", "won't renew",
    "not going to renew", "second supplier", "they're leaving", "threatening to", "stop the line",
    "lawyer", "lawyers", "attorney", "attorneys", "counsel", "legal team", "legal says",
    "legal signed off", "privileged", "inquiry", "enquiry", "probe", "can't comment", "no comment",
    "habitability", "default notice", "demand letter", "keep this close", "keep it close",
    "keep that close", "quietly", "rumour", "rumours", "rumor", "rumors", "don't know it yet",
    "doesn't know it yet", "don't know yet", "hasn't heard", "haven't heard", "made it public",
    "make it public", "goes in email", "go in email", "they find out", "find out about",
    "will find out", "they'll find out", "under wraps", "can't talk about", "hours cut",
    "cut hours", "cutting hours", "getting cut", "shrinking the workforce", "headcount reduction",
    "not every seat", "let go", "letting go", "redundancy", "redundancies", "made redundant",
    "hiring pause", "consolidate", "consolidating", "consolidation", "merged with", "being merged",
    "get merged", "getting merged", "merge the teams", "merge the two", "looking to sell",
    "for sale", "selling the", "not selling", "closing the", "closing our", "close the",
    "close our", "not renewing", "lose them", "losing them", "walk away", "going overseas", "mold",
    "mould", "product recall", "a recall", "the recall", "contamination", "contaminated",
)  # fmt: skip
# Routine business terms: review flags unless topics.mode is "redact".
DEFAULT_TOPICS_FLAG = (
    "pricing", "price", "prices", "discount", "discounts", "renewal", "renewals", "churn",
    "budget", "forecast", "quota", "margin", "margins", "competitor", "competitors",
    "roadmap", "at risk", "contract", "contracts", "deal", "negotiation", "negotiate",
    "partnership", "exclusive", "exclusivity", "strategy", "strategic",
)  # fmt: skip


@dataclass(frozen=True)
class DenyEntry:
    index: int
    name: str
    category: str
    aliases: tuple[str, ...] = ()
    match: str = "fuzzy"
    min_score: int = DEFAULT_MIN_SCORE
    context: tuple[str, ...] = ()
    common_word: bool = False

    def variants(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass
class Config:
    denylist: list[DenyEntry] = field(default_factory=list)
    # What to protect. "pii" (default): personal data is redacted; business context (company
    # names found by rules or models, figures, business topics, quarters) only gets review
    # flags. "confidential": business context is redacted too. Deny-list entries always redact.
    protect: str = "pii"
    topics_personal: tuple[str, ...] = DEFAULT_TOPICS_PERSONAL
    topics_redact: tuple[str, ...] = DEFAULT_TOPICS_REDACT
    topics_flag: tuple[str, ...] = DEFAULT_TOPICS_FLAG
    topic_mode: str = "mixed"  # redact | mixed | flag | off
    detect_dates: bool = True
    detect_business: bool = True
    detect_people: bool = True
    unknown_words: str = "redact"  # redact | flag | off
    review_threshold: float = 0.6
    window_turns: int = 50
    lookback_turns: int = 3
    plugins: tuple[str, ...] = ()
    # Optional tiers. Tier 0 is the default; models only add redactions.
    tier: int = 0
    ner_models: tuple[str, ...] = ("gliner-small-v2.1", "bert-base-ner")  # ensemble
    ner_threshold: float = 0.25  # chosen on the tuning set
    model_threads: int = 2
    llm_model: str = "phi-4-mini-q4"
    # Derived per document by the pipeline (not user config): names from speaker labels.
    speaker_names: tuple[str, ...] = ()


def _bool(value: Any, where: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, str)):
        v = str(value).strip().lower()
        if v in ("true", "yes", "1", "on"):
            return True
        if v in ("false", "no", "0", "off", ""):
            return False
    raise UsageError(f"{where}: must be true or false")


def _str_list(value: Any, where: str, split: bool = False) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str) and split:
        return [v for v in value.split(";") if v.strip()]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise UsageError(f"{where}: must be a list of strings")
    return value


def _check_keys(data: dict[Any, Any], known: tuple[str, ...], prefix: str, where: str) -> None:
    """Reject unknown keys. A key is named only when it is a near miss of a known key: a
    mis-indented deny-list name would otherwise end up in the error message."""
    for n, key in enumerate(data, 1):
        if key in known:
            continue
        close = difflib.get_close_matches(key, known, n=1) if isinstance(key, str) else []
        if close and _KEY_SHAPE.match(key):
            raise UsageError(f"unknown config key: {prefix}{key} (did you mean {close[0]}?)")
        raise UsageError(f"unknown config key (key {n} {where}, not shown in case it is a "
                         f"name); known keys: {', '.join(known)}")  # fmt: skip


def _entry(i: int, raw: Any) -> DenyEntry:
    if not isinstance(raw, dict):
        raise UsageError(f"deny-list entry {i}: must be a mapping")
    _check_keys(raw, ENTRY_KEYS, f"denylist[{i}].", f"in deny-list entry {i}")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise UsageError(f"deny-list entry {i}: 'name' is required")
    cat = str(raw.get("category", "CLIENT")).upper()
    if cat not in categories():
        raise UsageError(f"deny-list entry {i}: unknown category")
    aliases = _str_list(raw.get("aliases"), f"deny-list entry {i}: 'aliases'", split=True)
    common = _bool(raw.get("common_word", False), f"deny-list entry {i}: 'common_word'")
    match = str(raw.get("match", "exact" if common else "fuzzy"))
    if match not in MATCH_MODES:
        raise UsageError(f"deny-list entry {i}: 'match' must be one of {', '.join(MATCH_MODES)}")
    try:
        score = int(raw.get("min_score", DEFAULT_MIN_SCORE))
    except (TypeError, ValueError):
        raise UsageError(f"deny-list entry {i}: 'min_score' must be an integer") from None
    if not 50 <= score <= 100:
        raise UsageError(f"deny-list entry {i}: 'min_score' must be between 50 and 100")
    context = _str_list(raw.get("context"), f"deny-list entry {i}: 'context'", split=True)
    return DenyEntry(
        index=i,
        name=name.strip(),
        category=cat,
        aliases=tuple(a.strip() for a in aliases if a.strip()),
        match=match,
        min_score=score,
        context=tuple(str(c).strip().lower() for c in context if str(c).strip()),
        common_word=common,
    )


def from_mapping(data: dict[str, Any] | None) -> Config:
    data = data or {}
    if not isinstance(data, dict):
        raise UsageError("config must be a mapping at the top level")
    cfg = Config()
    _check_keys(data, TOP_KEYS, "", "at the top level")
    raw_list = data.get("denylist") or []
    if not isinstance(raw_list, list):
        raise UsageError("'denylist' must be a list")
    cfg.denylist = [_entry(i, e) for i, e in enumerate(raw_list)]
    if "protect" in data:
        cfg.protect = str(data["protect"])
        if cfg.protect not in ("pii", "confidential"):
            raise UsageError("protect must be pii or confidential")
    topics = data.get("topics") or {}
    if not isinstance(topics, dict):
        raise UsageError("'topics' must be a mapping")
    _check_keys(topics, TOPIC_KEYS, "topics.", "in topics")
    if "redact" in topics:
        cfg.topics_redact = tuple(t.lower() for t in _str_list(topics["redact"], "topics.redact"))
    if "flag" in topics:
        cfg.topics_flag = tuple(t.lower() for t in _str_list(topics["flag"], "topics.flag"))
    if "personal" in topics:
        cfg.topics_personal = tuple(
            t.lower() for t in _str_list(topics["personal"], "topics.personal")
        )
    cfg.topic_mode = str(topics.get("mode", cfg.topic_mode))
    if cfg.topic_mode == "default":  # the old name of "mixed", when that was the default
        cfg.topic_mode = "mixed"
    if cfg.topic_mode not in ("redact", "mixed", "flag", "off"):
        raise UsageError("topics.mode must be redact, mixed, flag or off")
    det = data.get("detect") or {}
    if not isinstance(det, dict):
        raise UsageError("'detect' must be a mapping")
    _check_keys(det, DETECT_KEYS, "detect.", "in detect")
    cfg.detect_dates = _bool(det.get("dates", cfg.detect_dates), "detect.dates")
    cfg.detect_business = _bool(det.get("business", cfg.detect_business), "detect.business")
    cfg.detect_people = _bool(det.get("people", cfg.detect_people), "detect.people")
    cfg.unknown_words = str(det.get("unknown_words", cfg.unknown_words))
    if cfg.unknown_words not in ("redact", "flag", "off"):
        raise UsageError("detect.unknown_words must be redact, flag or off")
    cfg.plugins = tuple(_str_list(data.get("plugins"), "plugins"))
    models = data.get("models") or {}
    if not isinstance(models, dict):
        raise UsageError("'models' must be a mapping")
    _check_keys(models, MODEL_KEYS, "models.", "in models")
    if "tier" in models:
        if models["tier"] not in (0, 1, 2):
            raise UsageError("models.tier must be 0, 1 or 2")
        cfg.tier = int(models["tier"])
    from offrecord.models import MANIFEST

    if "ner" in models:
        cfg.ner_models = tuple(_str_list(models["ner"], "models.ner"))
        for n in cfg.ner_models:
            if n not in MANIFEST or MANIFEST[n].kind not in ("gliner", "token-ner"):
                known = ", ".join(
                    k for k, v in MANIFEST.items() if v.kind in ("gliner", "token-ner")
                )
                raise UsageError(f"models.ner: unknown NER model; known: {known}")
    if "ner_threshold" in models:
        try:
            cfg.ner_threshold = float(models["ner_threshold"])
        except (TypeError, ValueError):
            raise UsageError("models.ner_threshold must be a number") from None
        if not 0.05 <= cfg.ner_threshold <= 0.95:
            raise UsageError("models.ner_threshold must be between 0.05 and 0.95")
    if "threads" in models:
        cfg.model_threads = max(1, int(models["threads"]))
    if "llm" in models:
        cfg.llm_model = str(models["llm"])
        if cfg.llm_model not in MANIFEST or MANIFEST[cfg.llm_model].kind != "gguf":
            known = ", ".join(k for k, v in MANIFEST.items() if v.kind == "gguf")
            raise UsageError(f"models.llm: unknown model; known: {known}")
    return cfg


def load(path: str | Path) -> Config:
    """Load YAML (or a CSV deny-list: name,category,aliases,match,min_score,context,common_word)."""
    text = read_input_text(path)
    if str(path).lower().endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(text)))
        entries = []
        for r in rows:
            r = {k: v for k, v in r.items() if v not in (None, "")}
            if "common_word" in r:
                r["common_word"] = str(r["common_word"]).strip().lower() in ("1", "true", "yes")
            entries.append(r)
        return from_mapping({"denylist": entries})
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        raise UsageError(f"config is not valid YAML{where}") from None
    return from_mapping(data)
