"""Sentence embeddings (MiniLM, ONNX int8): mean pooling + L2 normalisation."""

from __future__ import annotations

from pathlib import Path


class Embedder:
    def __init__(self, model_dir: Path, threads: int = 1) -> None:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np = np
        opts = ort.SessionOptions()
        opts.log_severity_level = 3  # errors only
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_dir / "model.onnx"), opts,
                                            providers=["CPUExecutionProvider"])  # fmt: skip
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tok.enable_truncation(256)
        self.tok.enable_padding()

    def embed(self, texts: list[str], batch: int = 32):
        np = self.np
        out = []
        for i in range(0, len(texts), batch):
            encs = self.tok.encode_batch(texts[i : i + batch])
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feeds)[0]
            m = mask[..., None].astype(hidden.dtype)
            pooled = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
            out.append(pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None))
        return np.vstack(out) if out else np.zeros((0, 384))
