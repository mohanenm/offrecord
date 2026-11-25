"""Fail if a file that must stay local is tracked, or if a built distribution ships one.

Working notes, personal assistant settings and environment files belong on the developer's
machine only, and the sdist and wheel should contain the package, its tests and its metadata,
nothing else (no evaluation data, models, keys or mappings).

    python scripts/check_hygiene.py              # tracked files, sealed set history
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
    r"|(^|/)(PROGRESS|DECISIONS|OPEN_QUESTIONS)\.md$"
    r"|^docs/proposals/"
    r"|(^|/)CLAUDE\.local\.md$"
    r"|(^|/)\.claude/settings\.local\.json$"
    r"|(^|/)\.env(\..*)?$"
    r"|\.(ormap|oraudit|key|pem|passphrase|onnx|gguf)$"  # mappings, audit logs, keys, models
)
# What a distribution may contain, after the sdist's top-level "offrecord-x.y.z/" is stripped.
SDIST_OK = re.compile(
    r"^(src/offrecord(\.egg-info)?/|tests/[^/]+\.py$"
    r"|(pyproject\.toml|README\.md|LICENSE|PKG-INFO|setup\.cfg)$)"
)
# The sealed evaluation set is committed once and never touched again.
SEALED_PATHS = ["eval/templates/sealed", "eval/pools/sealed", "eval/generated/sealed"]
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


def _git(*args: str) -> str:
    # Developer script: git comes from PATH, arguments are fixed.
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout  # noqa: S603, S607


def main(argv: list[str]) -> int:
    tracked = _git("ls-files").splitlines()
    problems = [f"tracked but must stay local: {p}" for p in offending(tracked)]
    touched = _git("log", "--format=%H", "--", *SEALED_PATHS).split()
    if len(touched) > 1:
        problems.append(f"the sealed set changed after its first commit ({len(touched)} commits)")
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
