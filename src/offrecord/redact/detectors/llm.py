"""Tier 2: a small instruct model (GGUF via llama.cpp) returns JSON labels only.

- It never rewrites the transcript: it returns ``{"spans": [{"line", "text", "category"}]}``
  under a JSON-schema grammar, and code locates each ``text`` in the given line. Spans that
  cannot be located exactly (case-insensitive) are discarded. The model can only add
  redactions.
- Only gated sentences are sent (same cheap checks as Tier 1), batched per window into numbered
  lines to keep the number of calls low.
- What it adds over rules and NER: indirect identifiers ("the only pharmacy chain we serve in
  Alaska") and business-sensitive context phrased without trigger words.
- llama.cpp's native logging is silenced so no prompt text can reach stderr.
"""

from __future__ import annotations

import ctypes
import json
import re
from collections.abc import Iterator
from typing import Any, ClassVar

from offrecord.model import Field, Span
from offrecord.normalize.fields import Window
from offrecord.redact.config import Config
from offrecord.redact.detectors.ner import SENTENCE_RE, gate

CATEGORIES = {
    "person": "PERSON",
    "organization": "ORG",
    "brand": "BRAND",
    "product": "PRODUCT",
    "codename": "CODENAME",
    "location": "LOCATION",
    "indirect_identifier": "INDIRECT",
    "confidential_business": "TOPIC",
}
SCHEMA = {
    "type": "object",
    "properties": {
        "spans": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "integer"},
                    "text": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                },
                "required": ["line", "text", "category"],
            },
        }
    },
    "required": ["spans"],
}
SYSTEM = (
    "You label sensitive information in meeting transcript lines. Reply with JSON only. "
    "For each sensitive phrase, give the line number, the phrase copied exactly from that line, "
    "and a category: person, organization, brand, product, codename, location, "
    "indirect_identifier (a description that could identify a specific person, client or place, "
    "e.g. 'the only pharmacy chain we serve in Alaska', 'our biggest client in Ohio'), or "
    "confidential_business (deal terms, financial details, strategy, layoffs, acquisitions, "
    "legal matters). Tokens like <C1> or <AMT2> are already redacted: ignore them. "
    "Do not rewrite or explain anything. If nothing is sensitive, return an empty list."
)
MAX_BATCH_CHARS = 1500
_LLM_CACHE: dict[tuple[str, int], Any] = {}
_LOG_CB = None


def _silence_llama_logs() -> None:
    global _LOG_CB
    if _LOG_CB is not None:
        return
    import llama_cpp

    @ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)
    def _noop(level, text, user_data):
        return None

    _LOG_CB = _noop  # keep a reference so it is not garbage-collected
    llama_cpp.llama_log_set(_LOG_CB, ctypes.c_void_p(0))


def load_llm(name: str, threads: int) -> Any:
    from offrecord.errors import EnvironmentCheckError
    from offrecord.models import MANIFEST, installed, model_path, verify_for_load

    key = (name, threads)
    if key in _LLM_CACHE:
        return _LLM_CACHE[key]
    if name not in MANIFEST or MANIFEST[name].kind != "gguf" or not installed(name):
        raise EnvironmentCheckError(
            f"model {name} is not installed; run: offrecord setup models --tier 2"
        )
    if not verify_for_load(name):
        raise EnvironmentCheckError(
            f"model {name} failed its checksum; reinstall it with setup models"
        )
    _silence_llama_logs()
    from llama_cpp import Llama

    llm = Llama(model_path=str(model_path(name) / "model.gguf"), n_ctx=4096, n_threads=threads,
                n_gpu_layers=0, verbose=False, seed=0)  # fmt: skip
    _LLM_CACHE[key] = llm
    return llm


class LlmDetector:
    name: ClassVar[str] = "llm"
    categories: ClassVar[frozenset[str]] = frozenset(CATEGORIES.values())

    def __init__(self, config: Config) -> None:
        self.model_name = config.llm_model
        self.llm = load_llm(config.llm_model, config.model_threads)
        self.calls = 0
        self.dropped = 0  # batches whose output could not be used (counted, never fatal)

    def detect(self, window: Window) -> Iterator[Span]:
        lines: list[tuple[Field, int, str]] = []  # (field, offset, sentence)
        for f in window.fields:
            if f.kind == "speaker":
                continue
            for m in SENTENCE_RE.finditer(f.orig):
                sent = m.group()
                if sent.strip() and gate(sent):
                    lines.append((f, m.start(), sent))
        batch: list[tuple[Field, int, str]] = []
        size = 0
        for item in lines:
            if batch and size + len(item[2]) > MAX_BATCH_CHARS:
                yield from self._label(batch)
                batch, size = [], 0
            batch.append(item)
            size += len(item[2])
        if batch:
            yield from self._label(batch)

    def _label(self, batch: list[tuple[Field, int, str]]) -> Iterator[Span]:
        numbered = "\n".join(f"{i + 1}: {s.strip()}" for i, (_f, _o, s) in enumerate(batch))
        user = numbered + ("\n/no_think" if "qwen" in self.model_name else "")
        self.calls += 1
        try:
            resp = self.llm.create_chat_completion(
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                response_format={"type": "json_object", "schema": SCHEMA},
                temperature=0.0,
                max_tokens=600,
            )
            choice = resp["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError("truncated")
            data = json.loads(choice["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError):
            # Too long or malformed: halve the batch and retry; a single bad line adds nothing
            # (rules and NER results stand).
            if len(batch) > 1:
                half = len(batch) // 2
                yield from self._label(batch[:half])
                yield from self._label(batch[half:])
            else:
                self.dropped += 1
            return
        spans = data.get("spans") if isinstance(data, dict) else None
        if not isinstance(spans, list):
            self.dropped += 1
            return
        for item in spans:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item["line"]) - 1
                text = str(item["text"]).strip()
                cat = CATEGORIES[item["category"]]
            except (KeyError, ValueError, TypeError):
                continue
            if not (0 <= idx < len(batch)) or len(text) < 2:
                continue
            f, off, sent = batch[idx]
            # Every occurrence, located on the original string (case-insensitive regex keeps the
            # original offsets; lower() could change lengths). Not found = paraphrase: discarded.
            for m in re.finditer(re.escape(text), sent, re.IGNORECASE):
                s = off + m.start()
                yield Span(f.id, s, off + m.end(), cat, f"llm.{self.model_name}", 0.5, None,
                           f"llm:{item['category']}")  # fmt: skip
