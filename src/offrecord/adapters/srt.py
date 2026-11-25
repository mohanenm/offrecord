"""SubRip (.srt), hand-written parser. Never drops text: blocks that do not parse as cues go
into metadata (scanned) and are re-emitted at the end on render."""

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

_TIMING = re.compile(rf"^\s*(?P<a>{TS_RE})\s*-->\s*(?P<b>{TS_RE})(?P<rest>[^\n]*)$")
_BRACKET_SPEAKER = re.compile(r"^\s*\[(?P<name>[^\]\n]{1,48})\]\s*(?P<rest>.*)$", re.DOTALL)
_SNIFF = re.compile(rf"^\s*\d+\s*\n\s*{TS_RE}\s*-->\s*{TS_RE}", re.MULTILINE)


def _blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    blocks: list[tuple[int, list[str]]] = []
    cur: list[str] = []
    start = 1
    for j, ln in enumerate(lines):
        if ln.strip():
            if not cur:
                start = j + 1
            cur.append(ln)
        elif cur:
            blocks.append((start, cur))
            cur = []
    if cur:
        blocks.append((start, cur))
    return blocks


class SrtAdapter:
    name: ClassVar[str] = "srt"
    extensions: ClassVar[tuple[str, ...]] = (".srt",)
    # Anything after the timing (position fields, or junk) is re-emitted, so it is scanned.
    free_text_extras: ClassVar[frozenset[str]] = frozenset({"position"})

    def sniff(self, head: str) -> float:
        return 0.9 if _SNIFF.search(head.lstrip("\ufeff")[:1000]) else 0.0

    def parse(self, text: str, limits: Limits) -> Document:
        meta: dict[str, str] = {}
        cues: list[tuple[int, int, re.Match[str], str]] = []
        for start_no, blk in _blocks(text.split("\n")):
            t_idx = next((k for k, ln in enumerate(blk[:2]) if "-->" in ln), None)
            m = _TIMING.match(blk[t_idx]) if t_idx is not None else None
            if t_idx is None or m is None:
                meta[f"unparsed_{len(meta)}"] = "\n".join(blk)
                continue
            if t_idx == 1 and not blk[0].strip().isdigit():
                # A non-numeric first line is not an index: keep it as text, never drop it.
                payload = "\n".join([blk[0], *blk[t_idx + 1 :]])
            else:
                payload = "\n".join(blk[t_idx + 1 :])
            limits.check_turn_chars(len(cues), len(payload))  # before regexes on the payload
            cues.append((start_no, start_no + len(blk) - 1, m, strip_markup(payload)))

        candidates = []
        for c in cues:
            sl = split_label(c[3])
            if sl:
                candidates.append(sl[0])
        accepted = accepted_labels(candidates, len(cues))

        texts, info = [], []
        for c in cues:
            speaker, body, style = None, c[3], "plain"
            sl = split_label(body)
            bm = _BRACKET_SPEAKER.match(body)
            if sl and sl[0] in accepted:
                speaker, body, style = sl[0], sl[1], "label"
            elif bm:
                speaker, body, style = bm.group("name").strip(), bm.group("rest"), "bracket"
            texts.append(body)
            info.append((c, speaker, style))
        check_turns(limits, texts)
        check_metadata(limits, len(meta))

        turns = []
        for idx, ((first_no, last_no, m, _), speaker, style) in enumerate(info):
            extra = {"style": style}
            if m.group("rest").strip():
                extra["position"] = m.group("rest").strip()
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
        return Document(
            format=self.name,
            turns=tuple(turns),
            metadata=MappingProxyType(meta),
            free_text_extras=self.free_text_extras,
        )

    def render(self, doc: Document) -> str:
        out = []
        n = 0
        for t in doc.turns:
            n += 1
            timing = (
                f"{format_ts(t.start or 0.0, ',')} --> {format_ts(t.end or t.start or 0.0, ',')}"
            )
            pos = t.extra.get("position")
            body = no_blank_lines(t.text)
            style = t.extra.get("style")
            if t.speaker is not None and style == "bracket":
                body = f"[{t.speaker}] {body}"
            elif t.speaker is not None:
                body = f"{t.speaker}: {body}"
            out.extend([str(n), timing + (f" {pos}" if pos else ""), body, ""])
        for v in doc.metadata.values():
            n += 1
            zero = format_ts(0.0, ",")
            out.extend([str(n), f"{zero} --> {zero}", no_blank_lines(v).replace("-->", "->"), ""])
        return "\n".join(out)


ADAPTER = SrtAdapter()
