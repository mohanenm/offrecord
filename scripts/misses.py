"""Dev tool: show missed labels for the TUNING sets only (never run on held-out or user sets).

.venv/bin/python scripts/misses.py [--hw] [CATEGORY ...]

--hw: the hand-written tuning set (eval/tuning_handwritten) instead of the generated one.
--neg: list NEG spans (must not be redacted) that a redaction touches, instead of misses.
"""

import sys
from collections import Counter
from pathlib import Path

from offrecord.evaluation.runner import _covered, _intervals, load_generated, load_markup_dir
from offrecord.model import turn_field_id
from offrecord.redact.pipeline import tier0_system

args = sys.argv[1:]
hw = "--hw" in args
if hw:
    items, cfg = load_markup_dir(Path("eval/tuning_handwritten"))
else:
    items, cfg = load_generated(Path("eval/generated/tuning"))
system = tier0_system(cfg)
want = {a for a in args if a not in ("--hw", "--neg")}
if "--neg" in args:
    for it in items:
        spans = _intervals(system(it.doc))
        for ng in it.negatives:
            t = it.doc.turns[ng.turn]
            src = t.text if ng.field == "text" else (t.speaker or "")
            ivs = spans.get(turn_field_id(ng.turn, ng.field), [])
            if any(a < ng.end and b > ng.start for a, b in ivs):
                ctx = src[max(0, ng.start - 30) : ng.start] + "[[" + src[ng.start : ng.end] + "]]"
                print(f"NEG-HIT {it.id} | {ctx + src[ng.end : ng.end + 20]!r}")
    sys.exit(0)
counts = Counter()
for it in items:
    spans = _intervals(system(it.doc))
    for lb in it.labels:
        if want and lb.category not in want:
            continue
        fid = turn_field_id(lb.turn, lb.field)
        t0 = it.doc.turns[lb.turn]
        src0 = t0.text if lb.field == "text" else (t0.speaker or "")
        full, part = _covered(spans.get(fid, []), lb.start, lb.end, src0)
        if full:
            continue
        t = it.doc.turns[lb.turn]
        src = t.text if lb.field == "text" else (t.speaker or "")
        counts[(lb.category, lb.form)] += 1
        ctx = (
            src[max(0, lb.start - 30) : lb.start]
            + "[["
            + src[lb.start : lb.end]
            + "]]"
            + src[lb.end : lb.end + 20]
        )
        print(f"{lb.category:9} {lb.form:14} {'PART' if part else 'MISS'} {it.id} | {ctx!r}")
print(counts.most_common())
