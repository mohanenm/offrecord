"""Output rendering: compact plain text (default) or the original format.

Compact text: a header with the mapping id, a legend of short speaker labels (placeholders or
generic labels only, never names), then one line per turn. Compression builds on this.
"""

from __future__ import annotations

import string

from offrecord import adapters
from offrecord.adapters._common import speaker_key
from offrecord.model import Document

HEADER_PREFIX = "# offrecord"


def header(mapping_id: str | None) -> str:
    return (
        f"{HEADER_PREFIX} mapping={mapping_id}" if mapping_id else f"{HEADER_PREFIX} mapping=none"
    )


def _letters() -> list[str]:
    out = list(string.ascii_uppercase)
    out += [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    return out


def speaker_legend(doc: Document) -> dict[str, str]:
    """Redacted speaker label -> short label (A, B, ...), in order of first appearance."""
    legend: dict[str, str] = {}
    letters = _letters()
    for t in doc.turns:
        if t.speaker is None:
            continue
        k = speaker_key(t.speaker)
        if k not in legend:
            legend[k] = letters[len(legend)] if len(legend) < len(letters) else f"S{len(legend)}"
    return legend


def render_compact(doc: Document, mapping_id: str | None) -> str:
    """``doc`` must already be redacted."""
    legend = speaker_legend(doc)
    names: dict[str, str] = {}
    for t in doc.turns:
        if t.speaker is not None:
            names.setdefault(speaker_key(t.speaker), t.speaker)
    lines = [header(mapping_id)]
    if legend:
        lines.append("# speakers: " + ", ".join(f"{v}={names[k]}" for k, v in legend.items()))
    for t in doc.turns:
        text = " ".join(t.text.split())
        if not text:
            continue
        lab = legend.get(speaker_key(t.speaker)) if t.speaker else None
        lines.append(f"{lab}: {text}" if lab else text)
    return "\n".join(lines) + "\n"


def render_original(doc: Document, mapping_id: str | None) -> str:
    """Original format; the mapping id goes in a format-appropriate comment/header field."""
    out = adapters.get(doc.format).render(doc)
    hdr = header(mapping_id)
    if doc.format == "vtt":
        # After the WEBVTT header block (header lines end at the first blank line).
        head, sep, rest = out.partition("\n\n")
        return f"{head}\n\nNOTE {hdr[2:]}\n\n{rest}" if sep else f"{out}\nNOTE {hdr[2:]}\n"
    if doc.format == "json":
        # JSON has no comments: the mapping id goes in a top-level key (arrays are wrapped).
        import json

        data = json.loads(out)
        if isinstance(data, dict):
            data = {"_offrecord_mapping": mapping_id or "none", **data}
        else:
            data = {"_offrecord_mapping": mapping_id or "none", "segments": data}
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if doc.format == "srt":
        # SRT has no comments: a zero-length first cue carries the mapping id.
        return f"0\n00:00:00,000 --> 00:00:00,000\n{hdr[2:]}\n\n{out}"
    return f"{hdr}\n{out}"
