# AGENTS.md

Guidance for coding agents working in this repository. Humans: see CONTRIBUTING.md, which this
file points to rather than repeats.

offrecord is an offline Python CLI that redacts meeting and call transcripts before they reach
an LLM and restores the originals afterwards. `docs/architecture.md` explains how it works.

## Commands

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' -c requirements/dev.lock
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest -q
offrecord eval --set tuning --set validation      # recall, precision, clean-transcript rate
```

All four checks must pass before a commit. Python 3.10 is the floor.

## Rules that are never relaxed

These are explained in CONTRIBUTING.md under "Rules".

- No real sensitive data in code, tests, fixtures, docs or commit messages. Synthetic only.
- No network access at run time; only `offrecord setup` downloads, pinned and verified.
- Never print, log or echo transcript text, deny-list entries or mapping contents. Errors name
  positions and categories only. Lint rule T20 blocks `print` outside the CLI modules.
- Recall first: prefer over-redaction when trading recall against precision.
- Tier 0 works without optional extras or models. Never add PyTorch. New runtime dependencies
  need maintainer approval.

## Evaluation discipline

- Tune detectors on `tuning` and `tuning-hw` only; check generalisation on `validation`.
- Never tune on, edit or print items from `user` or the sealed set (`eval/generated/sealed`).
  CI fails if the sealed set changes.
- Log label changes in `eval/CORRECTIONS.md`.

## Conventions

- Small commits, one logical change, short imperative messages. Stage files by path.
- Keep code small: reuse what exists, prefer the standard library, no speculative options or
  abstractions. Logic with branches, parsing or security gets a test.
- Write in plain prose; follow the voice checklist in CONTRIBUTING.md.
- Do not commit personal assistant files (`CLAUDE.local.md`, `.claude/settings.local.json`) or
  working notes (`notes/`).
