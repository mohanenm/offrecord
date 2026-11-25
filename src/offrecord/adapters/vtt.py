"""WebVTT (hand-written parser; never drops text).

Speakers come from voice tags (``<v Name>``) or a ``Name:`` prefix on the cue payload.
Cue identifiers are free text and are scanned. NOTE/STYLE/REGION blocks and anything that does
not parse as a cue go into metadata so they are scanned too, never silently dropped.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import ClassVar

from offrecord.adapters._common import (
    TS_RE,
    accepted_labels,
    check_metadata,
    check_turns,
    format_ts,
    no_blank_lines,
    parse_ts,
    split_label,
    strip_markup,
)
from offrecord.limits import Limits
from offrecord.model import Document, Turn

_TIMING = re.compile(rf"^\s*(?P<a>{TS_RE})\s*-->\s*(?P<b>{TS_RE})(?P<settings>[^\n]*)$")
_VOICE = re.compile(r"<v(?:\.[^\s<>]{0,40})?[ \t]+(?P<name>[^<>\n]{0,200})>")


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class VttAdapter:
    name: ClassVar[str] = "vtt"
    extensions: ClassVar[tuple[str, ...]] = (".vtt", ".webvtt")
    # Cue ids and cue settings are re-emitted on render, so they are scanned like text.
    free_text_extras: ClassVar[frozenset[str]] = frozenset({"id", "settings"})

    def sniff(self, head: str) -> float:
        return 1.0 if head.lstrip("\ufeff").startswith("WEBVTT") else 0.0

    def parse(self, text: str, limits: Limits) -> Document:
        lines = text.split("\n")
        meta: dict[str, str] = {}
        i = 0
        if not lines or not lines[0].startswith("WEBVTT"):
            # Not strictly VTT; still parse cues so nothing is dropped.
            pass
        else:
            header_rest = lines[0][len("WEBVTT") :].strip()
            if header_rest:
                meta["header"] = header_rest
            i = 1
            n = 0
            while i < len(lines) and lines[i].strip():
                meta[f"header_{n}"] = lines[i]
                n += 1
                i += 1

        # Split remaining lines into blocks separated by blank lines, keeping line numbers.
        blocks: list[tuple[int, list[str]]] = []
        cur: list[str] = []
        start_no = i + 1
        for j in range(i, len(lines)):
            if lines[j].strip():
                if not cur:
                    start_no = j + 1
                cur.append(lines[j])
            elif cur:
                blocks.append((start_no, cur))
                cur = []
        if cur:
            blocks.append((start_no, cur))

        cues = []
        counters = {"note": 0, "style": 0, "region": 0, "unparsed": 0}
        for start_no, blk in blocks:
            first = blk[0]
            kind = first.split(None, 1)[0] if first.split() else ""
            if kind in ("NOTE", "STYLE", "REGION") and "-->" not in first:
                key = kind.lower()
                meta[f"{key}_{counters[key]}"] = "\n".join(blk)
                counters[key] += 1
                continue
            t_idx = next((k for k, ln in enumerate(blk[:2]) if "-->" in ln), None)
            m = _TIMING.match(blk[t_idx]) if t_idx is not None else None
            if t_idx is None or m is None:
                meta[f"unparsed_{counters['unparsed']}"] = "\n".join(blk)
                counters["unparsed"] += 1
                continue
            cue_id = blk[0] if t_idx == 1 else None
            payload = "\n".join(blk[t_idx + 1 :])
            cues.append((start_no, start_no + len(blk) - 1, cue_id, m, payload))

        # Speakers: voice tags first; otherwise "Name:" prefixes accepted by the shared heuristic.
        limits.check_turn_count(len(cues))
        for idx, c in enumerate(cues):
            limits.check_turn_chars(idx, len(c[4]))  # before any regex runs on the payload
        parsed: list[tuple[str, str | None, str]] = []
        candidates = []
        for c in cues:
            payload = c[4]
            voices = list(_VOICE.finditer(payload))
            if voices:
                first = strip_markup(voices[0].group("name")).strip()
                # Later voices in the same cue keep their names, inline, so nothing is dropped.
                body = payload[: voices[0].start()] + payload[voices[0].end() :]
                for v in voices[1:]:
                    body = body.replace(v.group(0), f"\n{v.group('name').strip()}: ", 1)
                parsed.append(("voice", first, strip_markup(body).strip("\n")))
                continue
            plain = strip_markup(payload)
            sl = split_label(plain)
            if sl:
                candidates.append(sl[0])
            parsed.append(("plain", None, plain))
        accepted = accepted_labels(candidates, len(cues))

        texts: list[str] = []
        turns_info = []
        for c, (style, speaker, plain) in zip(cues, parsed, strict=True):
            body = plain
            if style == "plain":
                sl = split_label(plain)
                if sl and sl[0] in accepted:
                    speaker, body, style = sl[0], sl[1], "label"
            texts.append(body)
            turns_info.append((c, style, speaker or None))
        check_turns(limits, texts)

        turns = []
        for idx, ((first_no, last_no, cue_id, m, _), style, speaker) in enumerate(turns_info):
            extra: dict[str, str] = {"style": style}
            settings = m.group("settings").strip()
            if settings:
                extra["settings"] = settings
            if cue_id is not None:
                extra["id"] = cue_id
            turns.append(
                Turn(
                    index=idx,
                    speaker=speaker,
                    text=texts[idx],
                    start=parse_ts(m.group("a")),
                    end=parse_ts(m.group("b")),
                    source_lines=(first_no, last_no),
                    extra=MappingProxyType(extra),
                )
            )
        check_metadata(limits, len(meta))
        return Document(
            format=self.name,
            turns=tuple(turns),
            metadata=MappingProxyType(meta),
            free_text_extras=self.free_text_extras,
        )

    def render(self, doc: Document) -> str:
        out = ["WEBVTT" + (f" {doc.metadata['header']}" if "header" in doc.metadata else "")]
        out.extend(v for k, v in doc.metadata.items() if k.startswith("header_"))
        out.append("")
        for k, v in doc.metadata.items():
            if k.split("_")[0] in ("note", "style", "region"):
                out.extend([no_blank_lines(v).replace("-->", "->"), ""])
            elif k.startswith("unparsed_"):
                out.extend(["NOTE unparsed block", no_blank_lines(v).replace("-->", "->"), ""])
        for t in doc.turns:
            if "id" in t.extra:
                out.append(t.extra["id"].replace("-->", "->"))
            settings = t.extra.get("settings", "")
            timing = f"{format_ts(t.start or 0.0)} --> {format_ts(t.end or t.start or 0.0)}"
            out.append(timing + (f" {settings}" if settings else ""))
            style = t.extra.get("style", "plain")
            body = _escape(no_blank_lines(t.text))
            if t.speaker is not None and style == "voice":
                out.append(f"<v {_escape(t.speaker)}>{body}")
            elif t.speaker is not None:
                out.append(f"{_escape(t.speaker)}: {body}")
            else:
                out.append(body)
            out.append("")
        return "\n".join(out)


ADAPTER = VttAdapter()
