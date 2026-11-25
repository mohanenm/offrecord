"""Detector plug-ins (approved interface, proposal 0001 / D-020).

A detector factory takes the Config and returns a Detector instance for one document. The
detector receives turn Windows and yields Spans against any view of a field; the pipeline
projects them to original coordinates. Detectors may only add spans.

Third-party detectors come from the ``offrecord.detectors`` entry-point group and are loaded
only when named in the config's ``plugins`` list.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from importlib import metadata
from typing import ClassVar, Protocol, runtime_checkable

from offrecord.errors import UsageError
from offrecord.model import Span
from offrecord.normalize.fields import Window
from offrecord.redact.config import Config

ENTRY_POINT_GROUP = "offrecord.detectors"


@runtime_checkable
class Detector(Protocol):
    name: ClassVar[str]
    categories: ClassVar[frozenset[str]]

    def detect(self, window: Window) -> Iterable[Span]: ...


DetectorFactory = Callable[[Config], Detector]
_REGISTRY: dict[str, DetectorFactory] = {}


def register(name: str, factory: DetectorFactory) -> None:
    if name in _REGISTRY:
        raise ValueError("detector name already registered")
    _REGISTRY[name] = factory


def _ensure_builtins() -> None:
    if _REGISTRY:
        return
    from offrecord.redact.detectors import business, denylist, patterns, people

    register("patterns", patterns.PatternDetector)
    register("denylist", denylist.DenyListDetector)
    register("business", business.BusinessDetector)
    register("people", people.PeopleDetector)


def build(config: Config) -> list[Detector]:
    _ensure_builtins()
    factories = dict(_REGISTRY)
    if config.plugins:
        found = {ep.name: ep for ep in metadata.entry_points(group=ENTRY_POINT_GROUP)}
        missing = set(config.plugins) - set(found)
        if missing:
            raise UsageError(f"{len(missing)} configured detector plug-in(s) are not installed")
        for name in config.plugins:
            factories[f"plugin:{name}"] = found[name].load()
    if config.tier >= 1 and config.ner_models:
        from offrecord.redact.detectors.ner import NerDetector

        factories["ner"] = NerDetector
    if config.tier >= 2:
        from offrecord.redact.detectors.llm import LlmDetector

        factories["llm"] = LlmDetector
    out = []
    for factory in factories.values():
        det = factory(config)
        if not isinstance(det, Detector):
            raise UsageError("a detector does not implement the interface")
        out.append(det)
    return out
