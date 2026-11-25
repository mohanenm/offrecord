"""The never-track check used in CI."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "check_hygiene", Path(__file__).parent.parent / "scripts" / "check_hygiene.py"
)
check_hygiene = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_hygiene)


def test_never_track_patterns():
    bad = [
        "notes/x.md",
        "docs/PROGRESS.md",
        "DECISIONS.md",
        "docs/proposals/0001.md",
        "CLAUDE.local.md",
        ".claude/settings.local.json",
        ".env",
        "sub/.env.production",
    ]
    ok = ["CLAUDE.md", "AGENTS.md", "docs/architecture.md", "src/offrecord/notes.py", ".envrc.md"]
    assert check_hygiene.offending(bad + ok) == bad


def test_repository_is_clean():
    assert check_hygiene.main() == 0
