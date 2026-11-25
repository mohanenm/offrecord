"""Pluggable, offline-safe token counting. All counts are estimates.

- ``approx`` (default when nothing else is available): a documented heuristic close to common
  BPE tokenizers on English text: words of up to 6 letters count 1, longer words one per 4
  letters, digits one per 3, every punctuation mark 1.
- ``tiktoken:<encoding>``: OpenAI's tokenizer, only if the optional ``tiktoken`` package is
  installed and the encoding file is already in the local cache (``offrecord setup tokenizer``
  puts it there, or copy it in for air-gapped machines). It is never downloaded at run time:
  if the file is missing we fall back to ``approx`` instead of letting tiktoken fetch it.

Different models tokenize differently (e.g. Claude vs OpenAI); treat every count as approximate.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from offrecord.paths import data_dir

ENCODING_URLS = {
    "cl100k_base": "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken",
    "o200k_base": "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken",
}
# SHA-256 of the encoding files, verified by `setup` (download or --from copy).
ENCODING_SHA256 = {
    "cl100k_base": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    "o200k_base": "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
}
_PIECES = re.compile(r"[A-Za-z]+|\d+|[^\sA-Za-z\d]")


@dataclass(frozen=True)
class Counter:
    name: str
    count: Callable[[str], int]
    approximate: bool = True


def approx_count(text: str) -> int:
    n = 0
    for p in _PIECES.findall(text):
        if p[0].isalpha():
            n += 1 if len(p) <= 6 else math.ceil(len(p) / 4)
        elif p[0].isdigit():
            n += math.ceil(len(p) / 3)
        else:
            n += 1
    return n


APPROX = Counter("approx", approx_count)


def tiktoken_cache_dir() -> Path:
    return data_dir() / "tiktoken"


def cache_path(encoding: str) -> Path:
    """Where tiktoken looks for an encoding file (sha1 of its URL, in TIKTOKEN_CACHE_DIR)."""
    url = ENCODING_URLS[encoding]
    return tiktoken_cache_dir() / hashlib.sha1(url.encode()).hexdigest()  # noqa: S324 - tiktoken's naming


_VERIFIED: dict[str, bool] = {}


def tiktoken_available(encoding: str) -> bool:
    """Installed and the cached encoding file passes its checksum. A corrupt cache file would
    make tiktoken delete it and re-download it, so it is never handed to tiktoken."""
    if encoding not in ENCODING_URLS or importlib.util.find_spec("tiktoken") is None:
        return False
    path = cache_path(encoding)
    if not path.is_file():
        return False
    if encoding not in _VERIFIED:
        _VERIFIED[encoding] = verify_encoding_file(encoding, path.read_bytes())
    return _VERIFIED[encoding]


def get_counter(spec: str = "auto") -> Counter:
    """``auto`` | ``approx`` | ``tiktoken:<encoding>``. Falls back to approx, never fetches."""
    if spec == "approx":
        return APPROX
    encoding = spec.split(":", 1)[1] if spec.startswith("tiktoken:") else "cl100k_base"
    if spec not in ("auto",) and not spec.startswith("tiktoken:"):
        return APPROX
    if not tiktoken_available(encoding):
        return APPROX
    os.environ["TIKTOKEN_CACHE_DIR"] = str(tiktoken_cache_dir())
    import tiktoken  # optional dependency

    enc = tiktoken.get_encoding(encoding)
    return Counter(f"tiktoken:{encoding}", lambda t: len(enc.encode(t, disallowed_special=())))


def verify_encoding_file(encoding: str, data: bytes) -> bool:
    return hashlib.sha256(data).hexdigest() == ENCODING_SHA256[encoding]
