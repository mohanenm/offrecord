"""Input adapters.

Adapters turn decoded text into a :class:`Document` and render a (redacted) Document back to
their format. They never open files and never include input text in errors.

Third-party adapters are discovered through the ``offrecord.adapters`` entry-point group but
are only loaded when explicitly named (``enabled_plugins``), so an installed package cannot
silently inject code into the pipeline.
"""

from __future__ import annotations

from collections.abc import Iterable
from importlib import metadata
from pathlib import PurePath
from typing import ClassVar, Protocol, runtime_checkable

from offrecord.errors import InputFormatError, UsageError
from offrecord.limits import Limits
from offrecord.model import Document

ENTRY_POINT_GROUP = "offrecord.adapters"


@runtime_checkable
class Adapter(Protocol):
    name: ClassVar[str]
    extensions: ClassVar[tuple[str, ...]]

    def sniff(self, head: str) -> float: ...

    def parse(self, text: str, limits: Limits) -> Document: ...

    def render(self, doc: Document) -> str: ...


_REGISTRY: dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    if adapter.name in _REGISTRY:
        raise ValueError("adapter name already registered")
    _REGISTRY[adapter.name] = adapter
    return adapter


def get(name: str) -> Adapter:
    _ensure_builtins()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UsageError(f"unknown input format; known: {', '.join(sorted(_REGISTRY))}") from None


def names() -> list[str]:
    _ensure_builtins()
    return sorted(_REGISTRY)


def load_plugins(enabled: Iterable[str]) -> None:
    """Load the named third-party adapters from entry points. Unknown names are an error."""
    wanted = set(enabled)
    if not wanted:
        return
    found = {ep.name: ep for ep in metadata.entry_points(group=ENTRY_POINT_GROUP)}
    missing = wanted - set(found)
    if missing:
        raise UsageError(f"{len(missing)} configured adapter plug-in(s) are not installed")
    for name in sorted(wanted):
        obj = found[name].load()
        adapter = obj() if isinstance(obj, type) else obj
        if not isinstance(adapter, Adapter):
            raise UsageError("a configured adapter plug-in does not implement the interface")
        if adapter.name not in _REGISTRY:
            register(adapter)


SNIFF_BYTES = 4096


def detect(text: str, filename: str | None = None) -> Adapter:
    """Pick an adapter: highest sniff score; the file extension breaks ties."""
    _ensure_builtins()
    head = text[:SNIFF_BYTES]
    ext = PurePath(filename).suffix.lower() if filename else ""
    best: tuple[float, int, str] | None = None
    for adapter in _REGISTRY.values():
        score = adapter.sniff(head)
        ext_bonus = 1 if ext and ext in adapter.extensions else 0
        key = (score, ext_bonus, adapter.name)
        if best is None or key[:2] > best[:2]:
            best = key
    if best is None or best[0] <= 0:
        raise InputFormatError("could not detect the input format; pass --format")
    return _REGISTRY[best[2]]


_builtins_loaded = False


def _ensure_builtins() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    from offrecord.adapters import jsonfmt, srt, txt, vtt

    for mod in (txt, vtt, srt, jsonfmt):
        register(mod.ADAPTER)
