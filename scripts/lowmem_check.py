"""Tier 0 low-memory check: redact (and compress) a long synthetic transcript, report peak RSS.

    python scripts/lowmem_check.py [TURNS] [--cap-mb N]

--cap-mb sets RLIMIT_AS before work starts (enforced on Linux; macOS ignores RLIMIT_AS, so
there we only measure). The Tier 0 floor is a 2 GB machine (Pi 4 class).
"""

from __future__ import annotations

import resource
import sys
import time


def main() -> int:
    turns = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 5000
    if "--cap-mb" in sys.argv:
        cap = int(sys.argv[sys.argv.index("--cap-mb") + 1]) * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        except (ValueError, OSError):
            print("note: RLIMIT_AS not supported here; measuring only", file=sys.stderr)

    from pathlib import Path

    from offrecord import adapters
    from offrecord.compress import compress
    from offrecord.evaluation.generator import Generator, load_set, render
    from offrecord.limits import Limits
    from offrecord.redact.config import from_mapping
    from offrecord.redact.pipeline import redact_document

    root = Path(__file__).resolve().parent.parent
    templates, pools = load_set(root, "tuning")
    gen = Generator(templates, pools, "lowmem", 7)
    parts = []
    n = 0
    i = 0
    while n < turns:
        lt, _ = gen.generate_one(i)
        # A long meeting has a handful of speakers, not hundreds: reuse four fixed names.
        cast = ["Orla Venkatesh", "Bram Okafor", "Tamsin Lindqvist", "Kofi Abernathy"]
        lt.speakers = [cast[k % 4] for k in range(len(lt.speakers))]
        lt.labels = [lb for lb in lt.labels if lb.field != "speaker"]
        parts.append(render(lt, "txt"))
        n += len(lt.texts)
        i += 1
    text = "\n".join(parts)
    doc = adapters.get("txt").parse(text, Limits())
    t0 = time.perf_counter()
    red, spans, _mapping, _stats = redact_document(doc, from_mapping({}))
    compress(red, "medium")
    dt = time.perf_counter() - t0
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
    print(
        f"turns={len(doc.turns)} chars={len(text)} spans={len(spans)} seconds={dt:.1f} "
        f"peak_rss_mb={peak_mb:.0f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
