"""Score offrecord on Earnings-21: 44 real earnings calls (speech-recognition style text) with
entity tags, by Rev, licensed CC BY-SA 4.0. An outside test: never tuned on; text is never
printed; only aggregate numbers come out. The data is not part of this repository.

Get the data (development machine only; offrecord itself never downloads it):

    git clone --depth 1 https://github.com/revdotcom/speech-datasets /tmp/speech-datasets
    # DIR = /tmp/speech-datasets/earnings21/output, which holds nlp/ and tags/

usage: python scripts/earnings21_eval.py DIR [tier0|tier1|tier2]

OntoNotes classes are mapped to offrecord's: PERSON; GPE, LOC and FAC as LOCATION; WEBSITE as
URL; dates, money, percentages and cardinals as business context; ORG as an organisation.
Writes DIR/result.TIER.json.
"""

import ast
import json
import sys
from pathlib import Path

from offrecord.evaluation.labels import Label
from offrecord.evaluation.runner import EvalItem, format_report, score
from offrecord.model import Document, Turn
from offrecord.redact.pipeline import tier0_system

MAP = {
    "PERSON": "PERSON",
    "GPE": "LOCATION",
    "LOC": "LOCATION",
    "FAC": "LOCATION",
    "WEBSITE": "URL",
    "DATE": "DATE",
    "YEAR": "DATE",
    "MONEY": "AMOUNT",
    "PERCENT": "PERCENT",
    "CARDINAL": "QUANTITY",
    "QUANTITY": "QUANTITY",
    "ORG": "ORG",
}
SKIP = {"ABBREVIATION", "CONTRACTION", "FALLBACK", "ALPHANUMERIC", "ORDINAL"}


def load(nlp: Path, tags: Path) -> EvalItem:
    classes = {k: v["entity_type"] for k, v in json.loads(tags.read_text()).items()}
    turns, labels = [], []
    cur_spk, parts, length = None, [], 0
    open_ent: dict[str, tuple[int, int, int]] = {}  # id -> (turn, start, end)

    def close(turn_idx):
        for eid, (ti, s, e) in list(open_ent.items()):
            cls = classes.get(eid, "")
            if cls and cls not in SKIP:
                labels.append(Label(ti, "text", s, e, MAP.get(cls, cls), f"{cls}#{eid}"))
        open_ent.clear()

    lines = nlp.read_text(encoding="utf-8").splitlines()[1:]
    for line in lines:
        f = line.split("|")
        if len(f) < 8:
            continue
        tok, spk, punct, case, wer = f[0], f[1], f[4], f[5], f[7]
        if spk != cur_spk:
            close(len(turns) - 1)
            if parts:
                turns.append("".join(parts))
            parts, length, cur_spk = [], 0, spk
        word = (
            tok.capitalize()
            if case == "UC"
            else tok.upper()
            if case == "CA"
            else tok.lower()
            if case == "LC"
            else tok
        )
        if parts:
            parts.append(" ")
            length += 1
        start = length
        parts.append(word + punct)
        length += len(word) + len(punct)
        ids = ast.literal_eval(wer) if wer.strip().startswith("[") else []
        ids = [str(i) for i in ids]
        for eid in list(open_ent):
            if eid not in ids:  # entity ended before this token
                ti, s, e = open_ent.pop(eid)
                cls = classes.get(eid, "")
                if cls and cls not in SKIP:
                    labels.append(Label(ti, "text", s, e, MAP.get(cls, cls), f"{cls}#{eid}"))
        for eid in ids:
            ti, s, _ = open_ent.get(eid, (len(turns), start, 0))
            open_ent[eid] = (ti, s, start + len(word))
    close(len(turns))
    if parts:
        turns.append("".join(parts))
    doc = Document("txt", tuple(Turn(i, None, t) for i, t in enumerate(turns)))
    return EvalItem(nlp.stem, doc, labels, [])


if __name__ == "__main__":
    d = Path(sys.argv[1])
    tier = sys.argv[2] if len(sys.argv) > 2 else "tier0"
    items = [
        load(p, d / "tags" / f"{p.stem}.wer_tag.json") for p in sorted((d / "nlp").glob("*.nlp"))
    ]
    models = {
        "tier0": {},
        "tier1": {"tier": 1, "ner": ["gliner-small-v2.1", "bert-base-ner"]},
        "tier2": {"tier": 2, "ner": ["gliner-small-v2.1", "bert-base-ner"], "llm": "phi-4-mini-q4"},
    }[tier]
    rep = score(items, tier0_system({"models": models} if models else {}), f"earnings21 [{tier}]")
    print(format_report([rep]))
    with open(d / f"result.{tier}.json", "w") as out:
        json.dump(rep.to_json(), out, indent=1)
