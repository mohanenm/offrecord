"""Configuration: deny-list entries with per-entry options, topic terms, detector switches.

The config is sensitive (it lists clients and codenames). Errors refer to entry indexes and
field names only, never values, and the config is never echoed.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from offrecord.errors import UsageError
from offrecord.model import categories
from offrecord.safeio import read_input_text

MATCH_MODES = ("fuzzy", "exact", "case_sensitive")
DEFAULT_MIN_SCORE = 85  # rapidfuzz ratio, 0-100; lower = more recall

# Strong topic terms: the whole sentence is redacted (TOPIC). Weak terms: review flag only.
DEFAULT_TOPICS_REDACT = (
    "acquisition", "acquisitions", "acquire", "acquired", "acquiring", "merger", "mergers",
    "buyout", "takeover", "layoff", "layoffs", "laid off", "lay off", "lay people off",
    "reduction in force", "rif", "restructuring", "restructure", "reorg", "reorganization",
    "hiring freeze", "furlough", "furloughs", "termination", "terminate", "terminated",
    "firing", "fired", "severance", "performance improvement plan", "pip", "lawsuit",
    "litigation", "litigate", "settlement", "settle", "settling", "sue", "suing", "sued",
    "subpoena", "investigation", "regulator", "regulators", "bankruptcy", "insolvency",
    "insolvent", "divest", "divestiture", "divesting", "ipo", "fundraise", "fundraising",
    "term sheet", "valuation", "confidential", "under nda", "do not share", "don't share",
    "don't mention", "do not mention", "between us", "between you and me", "off the record",
    "keep this quiet", "price increase", "price increases", "raise prices", "raising prices",
    "raise our prices", "price hike", "double the price", "cut the team", "cutting the team",
    "cut jobs", "cutting jobs", "close the site", "close the plant", "close the office",
    "closing the site", "shut down the", "shutting down the", "wind down", "exit the market",
    "poach", "poaching", "breach", "data breach",
    # Health (special-category personal data)
    "diagnosed", "diagnosis", "diabetes", "diabetic", "cancer", "chemo", "chemotherapy",
    "depression", "anxiety", "disorder", "breakdown", "medication", "medications",
    "therapist", "therapy", "surgery", "pregnant", "pregnancy", "miscarriage", "allergy",
    "allergies", "allergic", "coeliac", "celiac", "apnea", "dyslexia", "adhd", "autism",
    "autistic", "hospitalized", "hospitalised", "fractured", "broke his", "broke her",
    "broke my", "disease", "suicide", "suicidal", "overdose", "rehab", "medical leave",
    "sick leave", "mental health", "dementia", "seizure", "seizures", "hiv",
    "break out in hives", "sleep study", "adverse event", "kidney injury", "health inspection",
    "health insurance",
    # Family, legal and personal circumstances
    "divorce", "divorced", "custody", "restraining order", "protective order", "temporary order",
    "passed away", "maiden name", "defamation", "work visa", "visa", "sponsor", "immigration",
    "green card", "deportation", "arrested", "criminal", "conviction", "probation",
    "eviction", "foreclosure", "notice of default", "behind on", "lost his job", "lost her job",
    "lost my job", "estate dispute", "moved out", "without a permit", "without a certificate",
    # Wrongdoing, incidents, investigations
    "fraud", "scam", "scammed", "theft", "stole", "stolen", "stealing", "asset protection",
    "camera footage", "tip anyone off", "investigate", "investigating", "auditors",
    "audit findings", "never reported", "unreported", "recordable", "osha", "violation",
    "failed its", "going under", "haven't paid", "not tied off", "fell from", "urgent care",
    "die failure", "objection", "complaint says",
    # Unannounced plans and secrets
    "not public", "isn't public", "hasn't been announced", "not been announced",
    "nobody below", "nobody outside", "nobody else knows", "haven't told", "hasn't told",
    "told me privately", "keep it off", "off the minutes", "keep it that way",
    "don't let him in", "don't let her in", "diligence", "acqui-hire", "acquihire",
    "reduction of", "transfers if", "wind-down",
    # Customers at risk (the account, not routine pricing talk)
    "lose the account", "shopping their", "shopping us", "evaluating a competitor",
    "may not renew", "won't renew", "not going to renew", "second supplier",
    "they're leaving", "threatening to", "stop the line",
)  # fmt: skip
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
    topics_redact: tuple[str, ...] = DEFAULT_TOPICS_REDACT
    topics_flag: tuple[str, ...] = DEFAULT_TOPICS_FLAG
    topic_mode: str = "default"  # default | redact | flag | off
    detect_dates: bool = True
    detect_business: bool = True
    detect_people: bool = True
    unknown_words: str = "redact"  # redact | flag | off
    review_threshold: float = 0.6
    window_turns: int = 50
    lookback_turns: int = 3
    plugins: tuple[str, ...] = ()
    # Optional tiers. Tier 0 is the default; models only ever add redactions.
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


def _entry(i: int, raw: Any) -> DenyEntry:
    if not isinstance(raw, dict):
        raise UsageError(f"deny-list entry {i}: must be a mapping")
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
    raw_list = data.get("denylist") or []
    if not isinstance(raw_list, list):
        raise UsageError("'denylist' must be a list")
    cfg.denylist = [_entry(i, e) for i, e in enumerate(raw_list)]
    topics = data.get("topics") or {}
    if not isinstance(topics, dict):
        raise UsageError("'topics' must be a mapping")
    if "redact" in topics:
        cfg.topics_redact = tuple(t.lower() for t in _str_list(topics["redact"], "topics.redact"))
    if "flag" in topics:
        cfg.topics_flag = tuple(t.lower() for t in _str_list(topics["flag"], "topics.flag"))
    cfg.topic_mode = str(topics.get("mode", cfg.topic_mode))
    if cfg.topic_mode not in ("default", "redact", "flag", "off"):
        raise UsageError("topics.mode must be default, redact, flag or off")
    det = data.get("detect") or {}
    if not isinstance(det, dict):
        raise UsageError("'detect' must be a mapping")
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
