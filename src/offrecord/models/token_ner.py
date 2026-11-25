"""Token-classification NER (BERT-style, BIO tags) with ONNX Runtime and HF tokenizers.

Used as a second, architecturally different ensemble member next to GLiNER: different models
make different mistakes, and in this design a model can only add redactions.
"""

from __future__ import annotations

import json
from pathlib import Path

from offrecord.models.gliner import Entity


class TokenNer:
    def __init__(self, model_dir: Path, threads: int = 1) -> None:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np = np
        cfg = json.loads((model_dir / "config.json").read_text())
        self.id2label = {int(k): v for k, v in cfg["id2label"].items()}
        opts = ort.SessionOptions()
        opts.log_severity_level = 3  # errors only
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_dir / "model.onnx"), opts,
                                            providers=["CPUExecutionProvider"])  # fmt: skip
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tok.enable_truncation(512)

    def predict(
        self, text: str, labels: list[str] | None = None, threshold: float = 0.5
    ) -> list[Entity]:
        enc = self.tok.encode(text)
        if len(enc.ids) >= 512:
            # Would be truncated: split near the middle and recurse (no silent loss of the tail).
            mid = text.rfind(" ", 0, len(text) // 2 + 1)
            if mid > 0:
                left = self.predict(text[:mid], labels, threshold)
                right = self.predict(text[mid:], labels, threshold)
                return left + [Entity(e.start + mid, e.end + mid, e.label, e.score) for e in right]
        return self._predict_one(text, enc, threshold)

    def _predict_one(self, text: str, enc, threshold: float) -> list[Entity]:
        np = self.np
        feeds = {"input_ids": np.array([enc.ids], dtype=np.int64),
                 "attention_mask": np.array([enc.attention_mask], dtype=np.int64)}  # fmt: skip
        if "token_type_ids" in self.inputs:
            feeds["token_type_ids"] = np.zeros((1, len(enc.ids)), dtype=np.int64)
        logits = self.session.run(None, feeds)[0][0]
        ex = np.exp(logits - logits.max(-1, keepdims=True))
        probs = ex / ex.sum(-1, keepdims=True)
        out: list[Entity] = []
        cur: list | None = None
        for i, (s, e) in enumerate(enc.offsets):
            if s == e:  # special tokens
                continue
            k = int(probs[i].argmax())
            lab = self.id2label.get(k, "O")
            score = float(probs[i, k])
            if lab == "O" or score < threshold:
                if cur:
                    out.append(Entity(cur[0], cur[1], cur[2], cur[3]))
                    cur = None
                continue
            kind = lab.split("-", 1)[-1]
            continuing = (
                cur is not None and cur[2] == kind and (lab.startswith("I-") or s == cur[1])
            )
            if continuing:
                cur[1] = e
                cur[3] = min(cur[3], score)
            else:
                if cur:
                    out.append(Entity(cur[0], cur[1], cur[2], cur[3]))
                cur = [s, e, kind, score]
        if cur:
            out.append(Entity(cur[0], cur[1], cur[2], cur[3]))
        return [_widen(text, e) for e in out]


def _widen(text: str, e: Entity) -> Entity:
    """Sub-word fragments ("z" of "zandrelcorp") are widened to whole words (recall first)."""
    s, t = e.start, e.end
    while s > 0 and text[s - 1].isalnum():
        s -= 1
    while t < len(text) and text[t].isalnum():
        t += 1
    return Entity(s, t, e.label, e.score)
