"""GLiNER (zero-shot span NER) inference with ONNX Runtime, without the ``gliner`` package
(which pulls in PyTorch). Follows GLiNER's span-marker scheme:

    [CLS] <<ENT>> label1 <<ENT>> label2 ... <<SEP>> word1 word2 ... [SEP]

``words_mask`` marks the first sub-token of each text word (1-based word index); every span of
up to ``max_width`` words is scored per label; spans above the threshold are decoded greedily
(highest score first, no overlaps).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

WORD_RE = re.compile(r"\w+(?:[-_]\w+)*|\S")


@dataclass(frozen=True)
class Entity:
    start: int  # character offsets in the input text
    end: int
    label: str
    score: float


class Gliner:
    def __init__(self, model_dir: Path, threads: int = 1) -> None:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np = np
        cfg = json.loads((model_dir / "gliner_config.json").read_text())
        self.max_width = int(cfg.get("max_width", 12))
        self.max_len = int(cfg.get("max_len", 384))
        self.ent = cfg.get("ent_token", "<<ENT>>")
        self.sep = cfg.get("sep_token", "<<SEP>>")
        opts = ort.SessionOptions()
        opts.log_severity_level = 3  # errors only
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_dir / "model.onnx"), opts,
                                            providers=["CPUExecutionProvider"])  # fmt: skip
        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tok.no_truncation()

    def predict(self, text: str, labels: list[str], threshold: float = 0.5) -> list[Entity]:
        words = [(m.start(), m.end()) for m in WORD_RE.finditer(text)]
        if not words or not labels:
            return []
        out: list[Entity] = []
        # Chunk the words so prompt + words fit the model's token budget.
        budget = max(32, self.max_len - 4 * len(labels) - 8)
        size = budget // 2
        overlap = min(16, size // 4)  # overlapping chunks: no name is cut at a boundary
        i = 0
        while i < len(words):
            chunk = words[i : i + size]
            out.extend(self._predict_chunk(text, chunk, labels, threshold))
            if i + size >= len(words):
                break
            i += size - overlap
        return out

    def _predict_chunk(self, text: str, words: list[tuple[int, int]], labels: list[str],
                       threshold: float) -> list[Entity]:  # fmt: skip
        np = self.np
        prompt: list[str] = []
        for lab in labels:
            prompt += [self.ent, lab]
        prompt.append(self.sep)
        tokens = prompt + [text[s:e] for s, e in words]
        enc = self.tok.encode(tokens, is_pretokenized=True)
        ids = enc.ids
        if len(ids) > self.max_len + 128:
            # Pathological sub-tokenisation: split the chunk and retry.
            half = len(words) // 2
            if half == 0:
                return []
            return (self._predict_chunk(text, words[:half], labels, threshold)
                    + self._predict_chunk(text, words[half:], labels, threshold))  # fmt: skip
        words_mask = []
        seen = set()
        for wid in enc.word_ids:
            if wid is not None and wid >= len(prompt) and wid not in seen:
                seen.add(wid)
                words_mask.append(wid - len(prompt) + 1)
            else:
                words_mask.append(0)
        n = len(words)
        w = self.max_width
        span_idx = np.array([[(s, s + k) for s in range(n) for k in range(w)]], dtype=np.int64)
        span_mask = np.array([[(s + k) < n for s in range(n) for k in range(w)]], dtype=bool)
        feeds = {
            "input_ids": np.array([ids], dtype=np.int64),
            "attention_mask": np.ones((1, len(ids)), dtype=np.int64),
            "words_mask": np.array([words_mask], dtype=np.int64),
            "text_lengths": np.array([[n]], dtype=np.int64),
            "span_idx": span_idx,
            "span_mask": span_mask,
        }
        logits = self.session.run(["logits"], feeds)[0][0]  # [n, w, classes]
        probs = 1.0 / (1.0 + np.exp(-logits))
        cands = []
        for s, k, c in zip(*np.where(probs > threshold), strict=True):
            if s + k < n:
                cands.append((float(probs[s, k, c]), int(s), int(s + k), int(c)))
        cands.sort(reverse=True)
        taken: list[tuple[int, int]] = []
        result = []
        for score, s, e, c in cands:
            if any(not (e < a or s > b) for a, b in taken):
                continue
            taken.append((s, e))
            result.append(Entity(words[s][0], words[e][1], labels[c], score))
        return result
