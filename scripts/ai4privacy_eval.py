"""Score offrecord on ai4privacy pii-masking-200k (English rows), as an outside test.

Never tuned on; rows and labels are never printed; only aggregate numbers come out. The data's
licence allows assessment but forbids redistributing it or anything derived from it, so it is
not part of this repository and results are published as aggregates only.

Get the data (development machine only; offrecord itself never downloads it): download the
dataset from https://huggingface.co/datasets/ai4privacy/pii-masking-200k and keep the rows whose
"language" is "en" as JSON lines (fields: id, source_text, privacy_mask).

usage: python scripts/ai4privacy_eval.py FILE.jsonl [tier0|tier1] [N]
Writes FILE.jsonl.TIER.result.json.
"""

import json
import sys

from offrecord.evaluation.labels import Label
from offrecord.evaluation.runner import EvalItem, format_report, score
from offrecord.model import Document, Turn
from offrecord.redact.pipeline import tier0_system

MAP = {
    "FIRSTNAME": "PERSON", "MIDDLENAME": "PERSON", "LASTNAME": "PERSON",
    "EMAIL": "EMAIL", "PHONENUMBER": "PHONE", "SSN": "SSN", "CREDITCARDNUMBER": "CARD",
    "ACCOUNTNUMBER": "ACCOUNT", "IBAN": "ACCOUNT", "PIN": "ACCOUNT", "PASSWORD": "ACCOUNT",
    "USERNAME": "ACCOUNT", "CREDITCARDCVV": "ACCOUNT", "MASKEDNUMBER": "ACCOUNT",
    "PHONEIMEI": "ACCOUNT", "VEHICLEVIN": "ACCOUNT", "VEHICLEVRM": "ACCOUNT",
    "BITCOINADDRESS": "ACCOUNT", "ETHEREUMADDRESS": "ACCOUNT", "LITECOINADDRESS": "ACCOUNT",
    "MAC": "ACCOUNT", "BIC": "ACCOUNT",
    "IP": "IP", "IPV4": "IP", "IPV6": "IP", "URL": "URL",
    "STREET": "LOCATION", "BUILDINGNUMBER": "LOCATION", "SECONDARYADDRESS": "LOCATION",
    "CITY": "LOCATION", "COUNTY": "LOCATION", "STATE": "LOCATION", "ZIPCODE": "LOCATION",
    "NEARBYGPSCOORDINATE": "LOCATION",
    "DOB": "DATE", "DATE": "DATE",
}  # fmt: skip

path = sys.argv[1]
tier = sys.argv[2] if len(sys.argv) > 2 else "tier0"
limit = int(sys.argv[3]) if len(sys.argv) > 3 else None
items = []
with open(path, encoding="utf-8") as f:
    for n, line in enumerate(f):
        if limit and n >= limit:
            break
        row = json.loads(line)
        text = row["source_text"]
        labels = []
        for k, sp in enumerate(row["privacy_mask"]):
            cat = MAP.get(sp["label"], sp["label"])
            if 0 <= sp["start"] < sp["end"] <= len(text):
                labels.append(Label(0, "text", sp["start"], sp["end"], cat, f"{cat}#{k}"))
        items.append(EvalItem(str(row["id"]), Document("txt", (Turn(0, None, text),)), labels, []))
cfg = {}
if tier == "tier1":
    cfg = {"models": {"tier": 1, "ner": ["gliner-small-v2.1", "bert-base-ner"]}}
rep = score(items, tier0_system(cfg), f"ai4privacy-en [{tier}]")
print(format_report([rep]))
with open(path + f".{tier}.result.json", "w") as out:
    json.dump(rep.to_json(), out, indent=1)
