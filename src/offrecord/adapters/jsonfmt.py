"""Generic JSON transcripts: Whisper/WhisperX-style ``segments``, and simple arrays of
``{speaker, text, start, end}`` objects (also nested under keys like ``utterances``).

Privacy rules:
- Every string and number in the file is either turn text/speaker or a metadata field, so it
  is scanned. Object keys outside a small known schema are scanned too.
- Metadata field names are opaque counters (``json:7``), never JSON paths, because paths
  contain keys, and keys can be content.
- Word-level and token arrays (``words``, ``tokens``, ``word_segments``...) and long integer
  arrays are dropped: they duplicate the turn text in a form detectors cannot see (a token-id
  list decodes back to the original text). Whisper's top-level ``text`` is regenerated from the
  turns on render.
"""

from __future__ import annotations

import json
from collections import deque
from types import MappingProxyType
from typing import Any, ClassVar

from offrecord.adapters._common import check_metadata, check_turns
from offrecord.errors import InputFormatError, InputLimitError
from offrecord.limits import Limits
from offrecord.model import Document, Turn

TEXT_KEYS = ("text", "transcript", "content", "utterance", "sentence", "caption", "body")
SPEAKER_KEYS = ("speaker", "speaker_label", "speaker_name", "name", "spk", "participant", "role")
START_KEYS = ("start", "start_time", "startTime", "begin", "start_ts")
END_KEYS = ("end", "end_time", "endTime", "stop", "end_ts")
DROP_KEYS = frozenset(
    {
        "words",
        "tokens",
        "token_ids",
        "tokenids",
        "word_segments",
        "chars",
        "characters",
        "word_timestamps",
        "alignment",
        "logprobs",
        "token_logprobs",
    }
)  # compared case-insensitively, see _dropped()
KNOWN_KEYS = DROP_KEYS.union(
    TEXT_KEYS,
    SPEAKER_KEYS,
    START_KEYS,
    END_KEYS,
    "id seek segments utterances turns results entries messages monologues language temperature "
    "avg_logprob compression_ratio no_speech_prob confidence duration title metadata speakers "
    "channel index".split(),
)
LONG_INT_ARRAY = 8


class _Num(str):
    """Raw JSON number text (kept as text: avoids float rounding and huge-int DoS)."""


def _dropped(key: str) -> bool:
    return key.lower().replace("-", "_") in DROP_KEYS


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Keep duplicate keys (renamed ``key~2``...) instead of silently losing earlier values."""
    out: dict[str, Any] = {}
    for k, v in pairs:
        key, n = k, 1
        while key in out:
            n += 1
            key = f"{k}~{n}"
        out[key] = v
    return out


def _parse(text: str, limits: Limits) -> Any:
    try:
        return json.loads(
            text,
            parse_int=_Num,
            parse_float=_Num,
            parse_constant=_Num,
            object_pairs_hook=_pairs,
        )
    except RecursionError:
        raise InputLimitError(
            f"JSON nesting too deep (max_json_depth {limits.max_json_depth})"
        ) from None
    except json.JSONDecodeError as e:
        raise InputFormatError(f"invalid JSON at line {e.lineno}, column {e.colno}") from None


def _has_surrogate(s: str) -> bool:
    return any("\ud800" <= c <= "\udfff" for c in s)


def _check_tree(root: Any, limit: int) -> None:
    """Depth limit, and reject lone surrogates (they cannot be written back out as UTF-8)."""
    stack = [(root, 1)]
    while stack:
        node, d = stack.pop()
        if d > limit:
            raise InputLimitError(f"JSON nesting deeper than {limit} (max_json_depth)")
        if isinstance(node, dict):
            for k, v in node.items():
                if _has_surrogate(k):
                    raise InputFormatError("JSON contains an invalid (lone surrogate) escape")
                stack.append((v, d + 1))
        elif isinstance(node, list):
            stack.extend((v, d + 1) for v in node)
        elif isinstance(node, str) and _has_surrogate(node):
            raise InputFormatError("JSON contains an invalid (lone surrogate) escape")


def _numeric_count(node: Any) -> int | None:
    """Number of leaves if ``node`` is a (nested) list of numbers only, else None."""
    if isinstance(node, _Num):
        return 1
    if isinstance(node, list):
        total = 0
        for x in node:
            c = _numeric_count(x)
            if c is None:
                return None
            total += c
        return total
    return None


def _first_key(d: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        if k in d:
            return k
    return None


def _find_turn_list(root: Any) -> tuple[list, tuple] | None:
    """Breadth-first search (depth <= 3) for the first list of dicts with text keys."""
    queue: deque[tuple[Any, tuple]] = deque([(root, ())])
    while queue:
        node, path = queue.popleft()
        if isinstance(node, list) and node and all(isinstance(x, dict) for x in node):
            with_text = sum(1 for x in node if _first_key(x, TEXT_KEYS) is not None)
            if with_text * 2 >= len(node):
                return node, path
        if len(path) >= 3:
            continue
        if isinstance(node, dict):
            for k, v in node.items():
                if not _dropped(k):
                    queue.append((v, (*path, k)))
    return None


def _to_float(v: Any) -> float | None:
    if isinstance(v, str) and len(v) <= 32:
        try:
            return float(v)
        except ValueError:
            return None
    return None


class _Builder:
    def __init__(self) -> None:
        self.meta: dict[str, str] = {}

    def ref(self, value: str) -> str:
        key = f"json:{len(self.meta)}"
        self.meta[key] = value
        return key

    def key_node(self, k: str) -> tuple:
        return ("lit", k) if k in KNOWN_KEYS else ("meta", self.ref(k), False)

    def node(self, v: Any) -> tuple:
        if isinstance(v, dict):
            return (
                "obj",
                [(self.key_node(k), self.node(x)) for k, x in v.items() if not _dropped(k)],
            )
        if isinstance(v, list):
            # Number-only arrays (also nested) longer than a few values are token ids,
            # embeddings or similar: they can decode back to text, so they are dropped.
            n = _numeric_count(v)
            if n is not None and n > LONG_INT_ARRAY:
                return ("arr", [])
            return ("arr", [self.node(x) for x in v])
        if isinstance(v, _Num):
            return ("meta", self.ref(str(v)), True)
        if isinstance(v, str):
            return ("meta", self.ref(v), False)
        return ("lit", v)  # bool / None


class JsonAdapter:
    name: ClassVar[str] = "json"
    extensions: ClassVar[tuple[str, ...]] = (".json",)

    def sniff(self, head: str) -> float:
        s = head.lstrip("\ufeff \t\r\n")
        if s[:1] == "{":
            return 0.8
        if s[:1] == "[" and s[1:].lstrip()[:1] in ("{", "]"):
            return 0.8  # an array of objects, not "[00:01] Name: text"
        return 0.0

    def parse(self, text: str, limits: Limits) -> Document:
        root = _parse(text, limits)
        _check_tree(root, limits.max_json_depth)
        found = _find_turn_list(root)
        if found is None:
            raise InputFormatError("no list of transcript segments found in JSON")
        turn_list, turn_path = found

        b = _Builder()
        turns: list[Turn] = []
        turn_skels: list[tuple] = []
        texts = []
        for i, seg in enumerate(turn_list):
            tk = _first_key(seg, TEXT_KEYS)
            sk = _first_key(seg, SPEAKER_KEYS)
            st = _first_key(seg, START_KEYS)
            en = _first_key(seg, END_KEYS)
            raw_text = seg.get(tk) if tk else ""
            text_val = raw_text if isinstance(raw_text, str) else ""
            sp = seg.get(sk) if sk else None
            speaker = str(sp) if isinstance(sp, (str, _Num)) and str(sp).strip() else None
            items = []
            for k, v in seg.items():
                if _dropped(k):
                    continue
                if k == tk and isinstance(raw_text, str):
                    items.append((("lit", k), ("turn", i, "text")))
                elif k == sk and speaker is not None:
                    items.append((("lit", k), ("turn", i, "speaker")))
                else:
                    items.append((b.key_node(k), b.node(v)))
            turn_skels.append(("obj", items))
            texts.append(text_val)
            turns.append(
                Turn(
                    index=i,
                    speaker=speaker,
                    text=text_val,
                    start=_to_float(seg.get(st)) if st else None,
                    end=_to_float(seg.get(en)) if en else None,
                )
            )
        check_turns(limits, texts)

        def skel(node: Any, path: tuple) -> tuple:
            if path == turn_path:
                return ("turns",)
            if isinstance(node, dict):
                items = []
                for k, v in node.items():
                    if _dropped(k):
                        continue
                    if (
                        k == "text"
                        and path == ()
                        and isinstance(v, str)
                        and turn_path == ("segments",)
                    ):
                        items.append((("lit", k), ("fulltext",)))
                        continue
                    items.append((b.key_node(k), skel(v, (*path, k))))
                return ("obj", items)
            return b.node(node)

        skeleton = skel(root, ())
        check_metadata(limits, len(b.meta))
        return Document(
            format=self.name,
            turns=tuple(turns),
            metadata=MappingProxyType(b.meta),
            render_state={"skeleton": skeleton, "turns": turn_skels},
        )

    def render(self, doc: Document) -> str:
        state = doc.render_state
        if not isinstance(state, dict) or "skeleton" not in state:
            # Document not produced by this adapter: emit a simple array.
            data = [
                {
                    k: v
                    for k, v in (
                        ("speaker", t.speaker),
                        ("start", t.start),
                        ("end", t.end),
                        ("text", t.text),
                    )
                    if v is not None
                }
                for t in doc.turns
            ]
            return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

        turns = doc.turns

        def value(node: tuple) -> Any:
            kind = node[0]
            if kind == "lit":
                return node[1]
            if kind == "meta":
                s = doc.metadata.get(node[1], "")
                if node[2]:
                    try:
                        return int(s) if s.lstrip("-").isdigit() and len(s) < 30 else float(s)
                    except ValueError:
                        return s  # number was redacted into text
                return s
            if kind == "obj":
                out: dict[str, Any] = {}
                for k, v in node[1]:
                    key, n = str(value(k)), 1
                    base = key
                    while key in out:  # two keys can redact to the same placeholder
                        n += 1
                        key = f"{base}~{n}"
                    out[key] = value(v)
                return out
            if kind == "arr":
                return [value(x) for x in node[1]]
            if kind == "turn":
                t = turns[node[1]] if node[1] < len(turns) else None
                if t is None:
                    return ""
                return t.text if node[2] == "text" else t.speaker
            if kind == "turns":
                return [value(s) for s in state["turns"][: len(turns)]]
            if kind == "fulltext":
                return " ".join(t.text.strip() for t in turns)
            raise ValueError("unknown skeleton node")

        return json.dumps(value(state["skeleton"]), ensure_ascii=False, indent=2) + "\n"


ADAPTER = JsonAdapter()
