"""Fail if a file that must stay local is tracked by git.

Working notes, personal assistant settings and environment files belong on the developer's
machine only. Run in CI and before releases: python scripts/check_hygiene.py
"""

import re
import subprocess
import sys

NEVER_TRACK = re.compile(
    r"^notes/"
    r"|(^|/)(PROGRESS|DECISIONS)\.md$"
    r"|^docs/proposals/"
    r"|(^|/)CLAUDE\.local\.md$"
    r"|(^|/)\.claude/settings\.local\.json$"
    r"|(^|/)\.env(\..*)?$"
)


def offending(paths: list[str]) -> list[str]:
    return [p for p in paths if NEVER_TRACK.search(p)]


def main() -> int:
    tracked = subprocess.run(
        ["git", "ls-files"],  # noqa: S607 - developer script; git comes from PATH
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    bad = offending(tracked)
    for p in bad:
        print(f"tracked but must stay local: {p}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
