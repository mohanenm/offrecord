"""Optional model tiers (Phase 8): manifest, local store, verification.

Models are obtained only by ``offrecord setup models`` (download from pinned Hugging Face
revisions) or by copying the files in (air-gapped: ``setup models --from DIR``). Every file is
SHA-256-verified at install time and again by ``doctor``. The tool never downloads anything
during a run.
"""

from __future__ import annotations

import hashlib
import platform
from dataclasses import dataclass, field
from pathlib import Path

from offrecord.paths import data_dir


@dataclass(frozen=True)
class ModelFile:
    local: str  # file name inside the model's directory
    remote: str  # path in the repository
    sha256: str
    size: int
    arch: str = ""  # "" = any; "arm64" / "x86_64" for architecture-specific builds


@dataclass(frozen=True)
class ModelSpec:
    name: str
    tier: int
    kind: str  # "gliner" | "token-ner" | "embed" | "gguf"
    repo: str
    revision: str
    license: str
    min_ram_gb: float
    files: tuple[ModelFile, ...] = field(default_factory=tuple)

    def files_for_this_machine(self) -> list[ModelFile]:
        arch = _arch()
        return [f for f in self.files if not f.arch or f.arch == arch]


def _arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x86_64"


# Pinned values (SHA-256 strings exceed the line length on purpose).
MANIFEST: dict[str, ModelSpec] = {
    "gliner-small-v2.1": ModelSpec(
        "gliner-small-v2.1", 1, "gliner", "onnx-community/gliner_small-v2.1",
        "8142fb00740ccea973e64b1272949ff48653df5e", "apache-2.0", 4.0,
        (
            ModelFile("model.onnx", "onnx/model_int8.onnx",
                      "c76c90920547fd937aaf505e7f2de5ec73168bf1c25abbb55a298104cb061400", 183403734),
            ModelFile("tokenizer.json", "tokenizer.json",
                      "677203884d026e721115cf0daccf70ec4239545a13d6619e3e66d7151e0c9ce3", 8657198),
            ModelFile("gliner_config.json", "gliner_config.json",
                      "8e8b59de124a256a3f3de67879d0da686fe3f73ecc05093506ba525e451b920d", 731),
        ),
    ),
    "bert-base-ner": ModelSpec(
        "bert-base-ner", 1, "token-ner", "Xenova/bert-base-NER",
        "8e892123e8b7c2c0c2bd1dcb598b7d244c4e53aa", "mit", 4.0,
        (
            ModelFile("model.onnx", "onnx/model_int8.onnx",
                      "7de0a4606c65b60da275a72f37b76a102c41e2b79c6463096a9d0cb800bf3f2c", 108486236),
            ModelFile("tokenizer.json", "tokenizer.json",
                      "343989712a36cd8b253efeaf8baf6a08b9d2583f78e395e83832e8ee9f8d8ee1", 668923),
            ModelFile("config.json", "config.json",
                      "a73a2eccc921bbdea95a94b49a157d3694b5c2abbae7a6f3000e14404a9c31a8", 999),
        ),
    ),
    "minilm-l6-v2": ModelSpec(
        "minilm-l6-v2", 1, "embed", "sentence-transformers/all-MiniLM-L6-v2",
        "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", "apache-2.0", 4.0,
        (
            ModelFile("model.onnx", "onnx/model_qint8_arm64.onnx",
                      "4278337fd0ff3c68bfb6291042cad8ab363e1d9fbc43dcb499fe91c871902474", 23026053,
                      "arm64"),
            ModelFile("model.onnx", "onnx/model_quint8_avx2.onnx",
                      "b941bf19f1f1283680f449fa6a7336bb5600bdcd5f84d10ddc5cd72218a0fd21", 23046789,
                      "x86_64"),
            ModelFile("tokenizer.json", "tokenizer.json",
                      "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037", 466247),
        ),
    ),
    "phi-4-mini-q4": ModelSpec(
        "phi-4-mini-q4", 2, "gguf", "bartowski/microsoft_Phi-4-mini-instruct-GGUF",
        "7ff82c2aaa4dde30121698a973765f39be5288c0", "mit", 8.0,
        (
            ModelFile("model.gguf", "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf",
                      "01999f17c39cc3074afae5e9c539bc82d45f2dd7faa3917c66cbef76fce8c0c2", 2491874688),
        ),
    ),
    "qwen3-1.7b-q4": ModelSpec(
        "qwen3-1.7b-q4", 2, "gguf", "unsloth/Qwen3-1.7B-GGUF",
        "d7f544eead698dbd1f15126ef60b45a1e1933222", "apache-2.0", 4.0,
        (
            ModelFile("model.gguf", "Qwen3-1.7B-Q4_K_M.gguf",
                      "b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897", 1107409472),
        ),
    ),
}  # fmt: skip

# Default Tier 1 ensemble: GLiNER + BERT-NER (chosen on the tuning set, D-044) + embeddings.
TIER1_NER = ("gliner-small-v2.1", "bert-base-ner")
TIER1_DEFAULT = (*TIER1_NER, "minilm-l6-v2")
TIER2_DEFAULT = "phi-4-mini-q4"
TIER2_FALLBACK = "qwen3-1.7b-q4"


def models_dir() -> Path:
    return data_dir() / "models"


def model_path(name: str) -> Path:
    return models_dir() / name


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


STAMP = ".verified.json"


def _stat_key(p: Path) -> list[int]:
    st = p.stat()
    return [st.st_size, st.st_mtime_ns, st.st_ino]


def write_stamp(name: str) -> None:
    """Record (size, mtime, inode) of files whose hash was just verified."""
    import json

    d = model_path(name)
    stamp = {f.local: _stat_key(d / f.local) for f in MANIFEST[name].files_for_this_machine()}
    p = d / STAMP
    p.write_text(json.dumps(stamp))
    p.chmod(0o600)


def verify_for_load(name: str) -> bool:
    """Called before a model is loaded. Small files and ONNX models are re-hashed every time (well
    under a second); large GGUF files are re-hashed only if their size/mtime/inode changed since
    the last full verification (setup or doctor)."""
    import json

    spec = MANIFEST[name]
    d = model_path(name)
    try:
        stamp = json.loads((d / STAMP).read_text())
    except (OSError, ValueError):
        stamp = {}
    for f in spec.files_for_this_machine():
        p = d / f.local
        if not p.is_file() or p.stat().st_size != f.size:
            return False
        if f.size > 512 * 1024 * 1024 and stamp.get(f.local) == _stat_key(p):
            continue
        if sha256_file(p) != f.sha256:
            return False
    write_stamp(name)
    return True


def installed(name: str, verify: bool = False) -> bool:
    """All files present (and, with ``verify``, matching their pinned SHA-256)."""
    spec = MANIFEST[name]
    d = model_path(name)
    for f in spec.files_for_this_machine():
        p = d / f.local
        if not p.is_file() or p.stat().st_size != f.size:
            return False
        if verify and sha256_file(p) != f.sha256:
            return False
    return True
