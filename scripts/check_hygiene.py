"""Fail if a file that must stay local is tracked, or if a built distribution ships one.

Working notes, personal assistant settings and environment files belong on the developer's
machine only, and the sdist and wheel should contain the package, its tests and its metadata,
nothing else (no evaluation data, models, keys or mappings).

    python scripts/check_hygiene.py              # tracked files
    python scripts/check_hygiene.py --dist dist  # also every .tar.gz and .whl in dist/
"""

import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

NEVER_TRACK = re.compile(
    r"^notes/"
    r"|(^|/)(PROGRESS|DECISIONS)\.md$"
    r"|^docs/proposals/"
    r"|(^|/)CLAUDE\.local\.md$"
    r"|(^|/)\.claude/settings\.local\.json$"
    r"|(^|/)\.env(\..*)?$"
)
# What a distribution may contain, after the sdist's top-level "offrecord-x.y.z/" is stripped.
SDIST_OK = re.compile(
    r"^(src/offrecord(\.egg-info)?/|tests/[^/]+\.py$"
    r"|(pyproject\.toml|README\.md|LICENSE|PKG-INFO|setup\.cfg)$)"
)
WHEEL_OK = re.compile(r"^(offrecord/|offrecord-[^/]+\.dist-info/)")
NEVER_SHIP = re.compile(r"\.(onnx|gguf|key|pem|mapping|ormap|oraudit|env)$|(^|/)eval/")


def offending(paths: list[str]) -> list[str]:
    return [p for p in paths if NEVER_TRACK.search(p)]


def bad_members(archive: Path) -> list[str]:
    if archive.name.endswith(".whl"):
        with zipfile.ZipFile(archive) as z:
            names, ok = z.namelist(), WHEEL_OK
    else:
        with tarfile.open(archive) as t:
            names = [m.name.partition("/")[2] for m in t.getmembers() if m.isfile()]
        ok = SDIST_OK
    return [n for n in names if n and (not ok.search(n) or NEVER_SHIP.search(n))]


def main(argv: list[str]) -> int:
    tracked = subprocess.run(
        ["git", "ls-files"],  # noqa: S607 - developer script; git comes from PATH
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    problems = [f"tracked but must stay local: {p}" for p in offending(tracked)]
    if argv[:1] == ["--dist"]:
        archives = sorted(Path(argv[1]).glob("*.tar.gz")) + sorted(Path(argv[1]).glob("*.whl"))
        if not archives:
            problems.append(f"no distributions found in {argv[1]}")
        for a in archives:
            problems += [f"{a.name} should not contain: {n}" for n in bad_members(a)]
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
