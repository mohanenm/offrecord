"""Dev tool: (re)generate evaluation sets and sample transcripts.

python -m offrecord.evaluation.generate tuning
python -m offrecord.evaluation.generate heldout|sealed --i-know-heldout-is-frozen
python -m offrecord.evaluation.generate samples
"""

from __future__ import annotations

import argparse
import shutil
import sys

from offrecord.evaluation.generator import Generator, load_set, render, write_set
from offrecord.evaluation.sets import MANIFEST, SET_PARAMS, manifest_lines, repo_root, set_dir

SAMPLES = [  # (template id, format, file name)
    ("t01-sales-renewal", "txt", "sales-call.txt"),
    ("t02-support-billing", "vtt", "support-call.vtt"),
    ("t04-standup", "srt", "standup.srt"),
    ("t03-internal-finance", "json", "finance-review.json"),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="offrecord.evaluation.generate")
    ap.add_argument("what", choices=["tuning", "heldout", "sealed", "samples"])
    ap.add_argument("--i-know-heldout-is-frozen", action="store_true")
    args = ap.parse_args(argv)
    root = repo_root()
    if args.what == "samples":
        templates, pools = load_set(root, "tuning")
        out = root / "samples"
        out.mkdir(exist_ok=True)
        for tid, fmt, name in SAMPLES:
            tpl = [t for t in templates if t["id"] == tid]
            lt, _ = Generator(tpl, pools, "sample", 2026).generate_one(0)
            (out / name).write_text(render(lt, fmt), encoding="utf-8")
        return 0
    d = set_dir(args.what, root)
    frozen = args.what in ("heldout", "sealed") and (d / MANIFEST).exists()
    if frozen and not args.i_know_heldout_is_frozen:
        sys.stderr.write("this set is frozen; refusing to regenerate\n")
        return 2
    if d.exists():
        shutil.rmtree(d)
    params = SET_PARAMS[args.what]
    write_set(root, args.what, params["seed"], params["n"], d)
    (d / MANIFEST).write_text("\n".join(manifest_lines(d)) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
