"""Plain-text transcripts.

Recognised line shapes (any casing):
    Name: text                      [00:01:02] Name: text      00:01 Name: text
    Name (00:01:02): text           Name [00:01]: text
    Name  00:03          <- header line, text on the following lines (Otter-style)
Lines without a recognised speaker continue the current turn; blank lines end it.
Labels that do not look like speakers stay in the text (so they are still scanned).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar

from offrecord.adapters._common import (
    LABEL_PREFIX,
    TS_RE,
    accepted_labels,
    check_turns,
    parse_ts,
    split_label,
)
from offrecord.limits import Limits
from offrecord.model import Document, Turn

# All patterns are applied to short, left-stripped prefixes of a line so matching stays linear.
_LEAD_TS = re.compile(rf"[\[(]?(?P<ts>{TS_RE})[\])]?[ \t]+")
_LABEL_TS = re.compile(rf"^(?P<label>.*?)\s*[\[(](?P<ts>{TS_RE})[\])]\s*$")
_LABEL_TS_COLON = re.compile(
    rf"(?P<label>[^:\s\[(][^:\n\[(]{{0,47}}?)[ \t]*[\[(](?P<ts>{TS_RE})[\])][ \t]*:[ \t]+"
)
_HEADER = re.compile(rf"(?P<label>[^\s:][^:\n]{{0,47}}?)[ \t]{{2,}}(?P<ts>{TS_RE})[ \t]*$")
_HEADER_MAX = 80


@dataclass
class _Line:
    no: int  # 1-based
    raw: str
    ts: str | None = None
    label: str | None = None
    rest: str | None = None
    header: bool = False


def _analyse(no: int, raw: str) -> _Line:
    line = _Line(no, raw)
    stripped = raw.lstrip(" \t")
    if len(stripped) <= _HEADER_MAX:
        h = _HEADER.match(stripped)
        if h and len(h.group("label").split()) <= 5:
            line.label, line.ts = h.group("label").strip(), h.group("ts")
            line.header, line.rest = True, ""
            return line
    body = raw
    m = _LEAD_TS.match(stripped[:LABEL_PREFIX])
    if m:
        line.ts, body = m.group("ts"), stripped[m.end() :]
    lead = len(body) - len(body.lstrip(" \t"))
    lc = _LABEL_TS_COLON.match(body[lead : lead + LABEL_PREFIX + 16])
    if lc and len(lc.group("label").split()) <= 5:
        line.label, line.ts = lc.group("label").strip(), lc.group("ts")
        line.rest = body[lead + lc.end() :]
        return line
    sl = split_label(body)
    if sl:
        label, rest = sl
        lt = _LABEL_TS.match(label)
        if lt and lt.group("label").strip():
            label = lt.group("label").strip()
            line.ts = line.ts or lt.group("ts")
        line.label, line.rest = label, rest
    elif m:
        line.rest = body
    return line


class TxtAdapter:
    name: ClassVar[str] = "txt"
    extensions: ClassVar[tuple[str, ...]] = (".txt", ".text", ".md", ".log")

    def sniff(self, head: str) -> float:
        return 0.1  # fallback for anything that is text

    def parse(self, text: str, limits: Limits) -> Document:
        lines = [_analyse(i + 1, raw) for i, raw in enumerate(text.split("\n"))]
        units = sum(1 for ln in lines if ln.raw.strip())
        accepted = accepted_labels([ln.label for ln in lines if ln.label is not None], units)

        turns: list[dict] = []
        cur: dict | None = None
        blank_seen = False
        for ln in lines:
            if not ln.raw.strip():
                if cur is not None:
                    blank_seen = True
                cur = None
                continue
            if ln.label is not None and ln.label in accepted:
                cur = {
                    "speaker": ln.label,
                    "parts": [ln.rest] if ln.rest else [],
                    "ts": ln.ts,
                    "lines": [ln.no, ln.no],
                    "header": ln.header,
                }
                turns.append(cur)
                continue
            if ln.label is None and ln.ts is not None and ln.rest is not None and cur is None:
                body = ln.rest  # a new unlabelled turn starting with a timestamp
            else:
                body, ln.ts = ln.raw, None  # continuation lines keep their text verbatim
            if cur is None:
                cur = {"speaker": None, "parts": [], "ts": ln.ts, "lines": [ln.no, ln.no]}
                turns.append(cur)
            cur["parts"].append(body)
            cur["lines"][1] = ln.no

        texts = ["\n".join(t["parts"]) for t in turns]
        check_turns(limits, texts)
        out = []
        for i, (t, body) in enumerate(zip(turns, texts, strict=True)):
            extra = {}
            if t["ts"]:
                extra["ts"] = t["ts"]
            if t.get("header"):
                extra["layout"] = "header"
            out.append(
                Turn(
                    index=i,
                    speaker=t["speaker"],
                    text=body,
                    start=parse_ts(t["ts"]) if t["ts"] else None,
                    source_lines=(t["lines"][0], t["lines"][1]),
                    extra=MappingProxyType(extra),
                )
            )
        return Document(
            format=self.name,
            turns=tuple(out),
            render_state={"blank_sep": blank_seen},
        )

    def render(self, doc: Document) -> str:
        blocks = []
        for t in doc.turns:
            ts = t.extra.get("ts")
            if t.speaker is not None and t.extra.get("layout") == "header":
                head = f"{t.speaker}  {ts}" if ts else t.speaker
                blocks.append(f"{head}\n{t.text}" if t.text else head)
                continue
            prefix = f"[{ts}] " if ts else ""
            if t.speaker is not None:
                blocks.append(f"{prefix}{t.speaker}: {t.text}")
            else:
                blocks.append(f"{prefix}{t.text}")
        state = doc.render_state if isinstance(doc.render_state, dict) else {}
        sep = "\n\n" if state.get("blank_sep") else "\n"
        return sep.join(blocks) + "\n"


ADAPTER = TxtAdapter()
