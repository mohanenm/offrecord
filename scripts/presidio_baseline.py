"""Off-the-shelf baseline: Microsoft Presidio (AnalyzerEngine defaults, spaCy en_core_web_lg).

Run only in a throwaway virtualenv; Presidio is not a dependency of offrecord:

    python3 -m venv /tmp/venv-presidio && /tmp/venv-presidio/bin/pip install presidio-analyzer
    /tmp/venv-presidio/bin/python -m spacy download en_core_web_lg
    /tmp/venv-presidio/bin/pip install -e .
    /tmp/venv-presidio/bin/python scripts/presidio_baseline.py eval/baselines/presidio.json

Note: run it from the repository root. Presidio may fetch a public-suffix list on first use
(tldextract); it is not covered by offrecord's network guard.
"""

import json
import sys
import time
from pathlib import Path

from presidio_analyzer import AnalyzerEngine

from offrecord.evaluation.runner import load_generated, score
from offrecord.model import Span, turn_field_id

ROOT = Path(__file__).resolve().parent.parent
engine = AnalyzerEngine()


def presidio_system(config):
    def system(doc):
        out = []
        for t in doc.turns:
            for part, text in (("text", t.text), ("speaker", t.speaker or "")):
                if not text:
                    continue
                for r in engine.analyze(text=text, language="en"):
                    out.append(
                        Span(
                            turn_field_id(t.index, part),
                            r.start,
                            r.end,
                            "NAME",
                            f"presidio.{r.entity_type}",
                            min(1.0, float(r.score)),
                        )
                    )
        return out

    return system


res = []
for name in ("tuning", "heldout"):
    items, cfg = load_generated(ROOT / "eval" / "generated" / name)
    t0 = time.perf_counter()
    r = score(items, presidio_system(cfg), f"{name} [presidio]")
    j = r.to_json()
    j["seconds"] = round(time.perf_counter() - t0, 1)
    res.append(j)
    c = j["per_category"]
    summary = f"recall {j['recall']:.4f} prec {j['precision_chars']:.4f}"
    print(name, summary, f"leaks {j['leaks_per_1k_words']:.2f} {j['seconds']}s")
    print("   ", " ".join(f"{k}={v['recall']:.2f}" for k, v in sorted(c.items())))
Path(sys.argv[1]).write_text(json.dumps(res, indent=1))
