"""Per-user locations. ``OFFRECORD_HOME`` overrides everything (used by tests)."""

from __future__ import annotations

import os
from pathlib import Path


def _home_override() -> Path | None:
    h = os.environ.get("OFFRECORD_HOME")
    return Path(h) if h else None


def data_dir() -> Path:
    h = _home_override()
    if h:
        return h / "data"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "offrecord"


def config_dir() -> Path:
    h = _home_override()
    if h:
        return h / "config"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "offrecord"


def mapping_store() -> Path:
    return data_dir() / "mappings"


def default_key_path() -> Path:
    return config_dir() / "keys" / "default.key"
