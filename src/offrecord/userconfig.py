"""Per-user setup: default config file, deny-list editing, last-mapping pointer, LLM prompt.

The config (deny-list) names clients and codenames: it is sensitive. It is written owner-only
and never echoed; commands report counts and categories only.
"""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from typing import Any

import yaml

from offrecord.errors import UsageError
from offrecord.paths import config_dir, data_dir
from offrecord.safeio import ensure_private_dir, read_input_text, write_atomic

DENYLIST_CATEGORIES = ("CLIENT", "BRAND", "PRODUCT", "CODENAME", "PERSON", "ORG", "LOCATION")
HEADER = (
    "# offrecord config. SENSITIVE: it names your clients, products and codenames.\n"
    "# Keep it private; do not commit or share it. Format: see README (deny-list).\n"
)


def default_config_path() -> Path:
    env = os.environ.get("OFFRECORD_CONFIG")
    return Path(env) if env else config_dir() / "config.yaml"


def resolve_config(explicit: str | None) -> Path | None:
    """The config to use: an explicit path, else the default file if it exists."""
    if explicit:
        return Path(explicit)
    p = default_config_path()
    return p if p.is_file() else None


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(read_input_text(path)) or {}
    except yaml.YAMLError:
        raise UsageError("config is not valid YAML") from None
    if not isinstance(data, dict):
        raise UsageError("config must be a mapping at the top level")
    if not isinstance(data.get("denylist") or [], list):
        raise UsageError("'denylist' must be a list")
    return data


def _save_raw(path: Path, data: dict[str, Any]) -> None:
    from offrecord.redact.config import from_mapping

    from_mapping(data)  # validate before writing anything
    ensure_private_dir(path.parent, chmod_existing=False)
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    write_atomic(path, (HEADER + body).encode("utf-8"), force=True)


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def add_entries(path: Path, entries: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Merge entries into the config's deny-list. Same name (case-insensitive) and category:
    aliases are merged instead of adding a duplicate. Returns (added, merged, total)."""
    data = _load_raw(path)
    deny: list[dict[str, Any]] = list(data.get("denylist") or [])
    index = {(_norm(str(e.get("name", ""))), str(e.get("category", "CLIENT")).upper()): e
             for e in deny if isinstance(e, dict)}  # fmt: skip
    added = merged = 0
    for raw in entries:
        name = " ".join(str(raw.get("name", "")).split())
        if not name:
            continue
        cat = str(raw.get("category", "CLIENT")).upper()
        if cat not in DENYLIST_CATEGORIES:
            raise UsageError(f"unknown category; use one of {', '.join(DENYLIST_CATEGORIES)}")
        aliases = [" ".join(a.split()) for a in raw.get("aliases") or [] if a and a.strip()]
        key = (_norm(name), cat)
        if key in index:
            old = index[key]
            have = {_norm(a) for a in old.get("aliases") or []}
            new = [a for a in aliases if _norm(a) not in have]
            if new:
                old["aliases"] = list(old.get("aliases") or []) + new
            merged += 1
            continue
        entry: dict[str, Any] = {"name": name, "category": cat}
        if aliases:
            entry["aliases"] = aliases
        for k in ("common_word", "context", "match", "min_score"):
            if k in raw and raw[k] not in (None, "", []):
                entry[k] = raw[k]
        deny.append(entry)
        index[key] = entry
        added += 1
    data["denylist"] = deny
    _save_raw(path, data)
    return added, merged, len(deny)


def read_csv_entries(path: str) -> list[dict[str, Any]]:
    """CSV with a header: name,category[,aliases][,common_word][,context]. Aliases and context
    are separated by ';' or '|'."""
    rows = list(csv.DictReader(io.StringIO(read_input_text(path))))
    if rows and "name" not in rows[0]:
        raise UsageError("the CSV needs a header row with at least a 'name' column")
    out = []
    for r in rows:
        e: dict[str, Any] = {"name": r.get("name") or "", "category": r.get("category") or "CLIENT"}
        for k in ("aliases", "context"):
            v = r.get(k) or ""
            parts = [x.strip() for x in v.replace("|", ";").split(";") if x.strip()]
            if parts:
                e[k] = parts
        if (r.get("common_word") or "").strip().lower() in ("1", "true", "yes"):
            e["common_word"] = True
        out.append(e)
    return out


def split_list(answer: str) -> list[str]:
    return [x.strip() for x in answer.replace(";", ",").split(",") if x.strip()]


# --- last mapping (for `unpaste`) --------------------------------------------------------------


def _last_mapping_file() -> Path:
    return data_dir() / "last-mapping"


def remember_mapping(path: Path) -> None:
    d = ensure_private_dir(data_dir(), chmod_existing=False)
    write_atomic(d / "last-mapping", str(path.resolve()).encode("utf-8"), force=True)


def last_mapping() -> Path | None:
    f = _last_mapping_file()
    if not f.is_file():
        return None
    p = Path(f.read_text(encoding="utf-8").strip())
    return p if p.is_file() else None


# --- LLM prompt wrapper ------------------------------------------------------------------------

PROMPT_RULES = (
    "The transcript below has been redacted. Names, companies, contact details, numbers and "
    "other sensitive details were replaced with placeholders in angle brackets (a short code of "
    "capital letters and a number). When you refer to them, copy the placeholders exactly as "
    "written, including the angle brackets. Do not rename, merge, split or expand them, and do "
    "not guess what they stand for."
)


def wrap_prompt(instruction: str, transcript: str) -> str:
    return (
        f"{instruction.strip()}\n\n{PROMPT_RULES}\n\n"
        f"--- transcript ---\n{transcript.rstrip()}\n--- end of transcript ---\n"
    )
