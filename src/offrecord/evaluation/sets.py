"""Locating evaluation sets and systems under test."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from offrecord.errors import UsageError
from offrecord.evaluation import baseline

# Frozen generation parameters. The held-out set must never be regenerated with different
# parameters after tuning starts (see DECISIONS D-030).
SET_PARAMS = {
    "tuning": {"seed": 1234, "n": 72},
    "heldout": {"seed": 987_654, "n": 56},
}
MANIFEST = "MANIFEST.sha256"
# SHA-256 of the committed held-out MANIFEST.sha256. Pinned here so that rewriting the manifest
# alongside changed files is still detected. Changing this constant must be logged in
# eval/CORRECTIONS.md (DECISIONS D-030).
HELDOUT_MANIFEST_SHA256 = "4b9a83b03e3d379c22e380ee93178ed4a8378cdef2b5ff9c8d3fca4e99c40d1b"


def repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in (p, *p.parents):
        if (cand / "eval" / "templates").is_dir():
            return cand
    raise UsageError("could not find the eval/ folder; run from the repository or pass --dir")


# Built-in sets, in report order. Tuning happens only on TUNING_SETS. ``validation`` is the
# generated set first used as the held-out set; its folder and generator name keep the old
# name ``heldout`` (the generator seeds from it), which is also accepted as an alias.
SET_NAMES = ("tuning", "tuning-hw", "validation", "user")
TUNING_SETS = ("tuning", "tuning-hw")
ALIASES = {"heldout": "validation"}


def set_dir(name: str, root: Path) -> Path:
    name = ALIASES.get(name, name)
    if name == "user":
        return root / "eval" / "user_set"
    if name == "tuning-hw":
        return root / "eval" / "tuning_handwritten"
    if name == "validation":
        return root / "eval" / "generated" / "heldout"
    return root / "eval" / "generated" / name


def systems() -> dict[str, Any]:
    out: dict[str, Any] = {"null": baseline.null_system, "baseline": baseline.regex_baseline}
    try:
        from offrecord.redact.pipeline import tier0_system
    except ModuleNotFoundError as e:  # Phase 3 not built yet
        if e.name not in ("offrecord.redact", "offrecord.redact.pipeline"):
            raise
        return out
    out["tier0"] = tier0_system

    def with_models(**extra: Any) -> Any:
        def factory(config: dict[str, Any]) -> Any:
            models = {**(config.get("models") or {}), **extra}
            return tier0_system({**config, "models": models})

        return factory

    from offrecord.models import installed

    if installed("gliner-small-v2.1"):
        out["tier1"] = with_models(tier=1, ner=["gliner-small-v2.1"])
        if installed("bert-base-ner"):
            out["tier1-ensemble"] = with_models(tier=1, ner=["gliner-small-v2.1", "bert-base-ner"])
            out["tier1-bert"] = with_models(tier=1, ner=["bert-base-ner"])
    if installed("phi-4-mini-q4") or installed("qwen3-1.7b-q4"):
        llm = "phi-4-mini-q4" if installed("phi-4-mini-q4") else "qwen3-1.7b-q4"
        ner = ["gliner-small-v2.1"] if installed("gliner-small-v2.1") else []
        out["tier2"] = with_models(tier=2, ner=ner, llm=llm)
    return out


def manifest_lines(directory: Path) -> list[str]:
    lines = []
    for p in sorted(directory.iterdir()):
        if p.is_file() and p.name != MANIFEST:
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}")
    return lines


def verify_manifest(directory: Path, pinned: str | None = None) -> bool:
    """Files match the folder's manifest, and (if given) the manifest matches a pinned hash."""
    m = directory / MANIFEST
    if not m.exists():
        return False
    if pinned is not None and hashlib.sha256(m.read_bytes()).hexdigest() != pinned:
        return False
    return m.read_text(encoding="utf-8").splitlines() == manifest_lines(directory)


def is_heldout(directory: Path, root: Path | None = None) -> bool:
    try:
        r = root or repo_root()
    except UsageError:
        return False
    return directory.resolve() == set_dir("heldout", r).resolve()
