"""Dev tool: rebuild the bundled word lists in src/offrecord/data from SCOWL.

    python scripts/build_wordlists.py SCOWL          # check the committed files (no writes)
    python scripts/build_wordlists.py SCOWL --write  # rewrite them

SCOWL is the release tarball or its unpacked directory. The script does not download it; fetch
it by hand from https://downloads.sourceforge.net/project/wordlist/SCOWL/2020.12.07/scowl-2020.12.07.tar.gz
(SHA-256 5587667caa20c4891390c2d42dbb4d5c4c3f41bee77af1457ece3ba23fb859cc, checked here).

Source: SCOWL 2020.12.07, the files in final/, read as Latin-1. Every list is sorted, one
entry per line, ASCII, and every entry is lower-cased.

common_words.txt.gz: every english-* and american-* file of size 35 or less (SCOWL's en_US
size-35 selection, without the special-* files), keeping entries that are lower-case a-z only.
Only the words and abbreviations categories survive that filter.

proper_names.txt.gz: english-proper-names and american-proper-names of size 70 or less, plus
english-upper and american-upper of size 50 or less. A trailing "'s" is removed, then entries
that are one capital letter followed by lower-case a-z are kept and lower-cased, minus the 57
capitalised everyday words in NOT_NAMES (months, weekdays, nationalities and languages,
religions, two holidays, "god", "bible" and "internet").
"""

import argparse
import hashlib
import re
import struct
import sys
import tarfile
import zlib
from pathlib import Path

SHA256 = "5587667caa20c4891390c2d42dbb4d5c4c3f41bee77af1457ece3ba23fb859cc"
DATA = Path(__file__).resolve().parent.parent / "src" / "offrecord" / "data"

NOT_NAMES = frozenset(
    "january february march april may june july august september october november december "
    "monday tuesday wednesday thursday friday saturday sunday "
    "african american arab arabic asian australian british canadian chinese danish dutch "
    "english european french german greek indian irish italian japanese mexican norwegian "
    "russian scottish spanish swedish turkish welsh "
    "catholic christian jewish muslim protestant christmas easter god bible internet".split()
)
LOWER = re.compile(r"[a-z]+")
CAPITALISED = re.compile(r"[A-Z][a-z]+")


def read_final(src: Path) -> dict[tuple[str, int], list[str]]:
    """(category, size) -> entries, for every file in SCOWL's final/ directory."""
    out: dict[tuple[str, int], list[str]] = {}

    def add(name: str, raw: bytes) -> None:
        category, size = name.rsplit(".", 1)
        out[(category, int(size))] = raw.decode("latin-1").splitlines()

    if src.is_dir():
        for p in sorted((src / "final").iterdir()):
            add(p.name, p.read_bytes())
        return out
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    if digest != SHA256:
        sys.exit(f"{src}: SHA-256 {digest} is not the SCOWL 2020.12.07 release ({SHA256})")
    with tarfile.open(src) as tar:
        for m in tar.getmembers():
            parts = m.name.split("/")
            f = tar.extractfile(m) if len(parts) == 3 and parts[1] == "final" else None
            if f is not None:
                add(parts[2], f.read())
    return out


def select(final: dict[tuple[str, int], list[str]], prefixes: tuple[str, ...], most: int):
    for (category, size), entries in final.items():
        if category.startswith(prefixes) and size <= most:
            yield from entries


def common_words(final: dict[tuple[str, int], list[str]]) -> list[str]:
    words = select(final, ("english-", "american-"), 35)
    return sorted({w for w in words if LOWER.fullmatch(w)})


def proper_names(final: dict[tuple[str, int], list[str]]) -> list[str]:
    entries = [
        *select(final, ("english-proper-names", "american-proper-names"), 70),
        *select(final, ("english-upper", "american-upper"), 50),
    ]
    names = {e.removesuffix("'s") for e in entries}
    return sorted({n.lower() for n in names if CAPITALISED.fullmatch(n)} - NOT_NAMES)


def gzip_bytes(words: list[str]) -> bytes:
    """Deterministic gzip: no name, mtime 0, level 9.

    The header is written by hand so the bytes do not depend on the platform; its OS byte is
    19, what zlib 1.2.12 writes on macOS, where the committed files were made.
    """
    data = "".join(w + "\n" for w in words).encode("ascii")
    deflate = zlib.compressobj(9, zlib.DEFLATED, -15)
    body = deflate.compress(data) + deflate.flush()
    header = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\x13"
    return header + body + struct.pack("<II", zlib.crc32(data), len(data) & 0xFFFFFFFF)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scowl", type=Path, help="scowl-2020.12.07.tar.gz or its unpacked directory")
    ap.add_argument("--write", action="store_true", help="rewrite the files in src/offrecord/data")
    args = ap.parse_args()
    final = read_final(args.scowl)
    status = 0
    for name, words in (
        ("common_words.txt.gz", common_words(final)),
        ("proper_names.txt.gz", proper_names(final)),
    ):
        path = DATA / name
        built = gzip_bytes(words)
        if args.write:
            path.write_bytes(built)
            print(f"{name}: wrote {len(words)} entries")
            continue
        committed = path.read_bytes()
        have = zlib.decompress(committed, 31).decode("ascii").splitlines()
        if built == committed:
            print(f"{name}: identical ({len(words)} entries)")
        elif have == words:
            # Same entries; a different zlib build compresses differently.
            print(f"{name}: same {len(words)} entries, different gzip bytes")
        else:
            missing, extra = len(set(have) - set(words)), len(set(words) - set(have))
            print(f"{name}: differs: {missing} committed entries not rebuilt, {extra} new")
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
