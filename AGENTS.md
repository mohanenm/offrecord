# AGENTS.md

Guidance for coding agents in this repository. The project's standards are in CONTRIBUTING.md;
read it first. This file adds what matters specifically when an agent does the work.

offrecord is an offline Python CLI that redacts meeting and call transcripts before they reach
an LLM and restores the originals afterwards. `docs/architecture.md` explains how it works,
`docs/security.md` what it defends against, and `docs/eval-format.md` how it is measured.

## Commands

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' -c requirements/dev.lock
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest -q
offrecord eval --set tuning --set validation
```

All four checks pass before every commit. Python 3.10 is the floor.

## Rules

CONTRIBUTING.md lists the project rules (synthetic data only, no run-time network access,
personal data first and recall first for it, never echo transcript content, Tier 0 without
extras). In addition:

- Don't print transcript text, deny-list entries or mapping contents in tool output or logs
  while you work, even from synthetic files you did not write. Counts and categories are fine.
- Don't open, print or tune on items from the `user` or `sealed` sets. Use `tuning` and
  `tuning-hw` to find misses (`scripts/misses.py`) and `validation` to check a change.
- Never edit `eval/generated/sealed`, `eval/templates/sealed` or `eval/pools/sealed`; CI fails
  if they change.
- Don't add runtime dependencies or anything that opens a network connection outside
  `offrecord setup` without the maintainer's agreement.
- Keep personal assistant files (`CLAUDE.local.md`, `.claude/settings.local.json`) and working
  notes (`notes/`) out of commits. `scripts/check_hygiene.py` checks this.

## Style

Small commits with short imperative messages; stage files by path. Keep code small and reuse
what exists. Prose follows the voice checklist in CONTRIBUTING.md.
