# Contributing

Thanks for helping. offrecord handles sensitive transcripts, so a few rules are stricter than
usual. Please read this page before opening a pull request.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]' -c requirements/dev.lock    # Python 3.10: requirements/dev-py310.lock
```

The optional model tiers need `pip install -e '.[dev,ner,llm]' -c requirements/dev.lock -c
requirements/ner.lock -c requirements/llm.lock` and `offrecord setup models` (and `--tier 2` for
the LLM). Tests that need real models skip when the models are absent.

## Before every commit

```sh
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest -q
```

CI runs the same checks on every supported Python version, plus a Tier 0 job with no extras
installed and a check that no local-only file is tracked (`scripts/check_hygiene.py`). A Tier 1
job installs the `[ner]` extra and the pinned models on Python 3.12 and runs the whole suite, so
the model tests run there instead of skipping. Tier 2 is not run in CI.

`tests/test_recall_floor.py` fails if Tier 0 recall or precision on `tuning` and `validation`
falls below fixed floors (`docs/eval-format.md` lists them). Raise a floor when the scores rise
for good; lowering one needs a reason in the pull request.

Keep commits small, one logical change each, with a short imperative message ("Handle spoken
percent ranges", not "Updated business.py"). Stage files by path, not with `git add -A`.

## Rules

1. No real sensitive data anywhere: not in code, tests, fixtures, samples, docs, issues or
   commit messages. Use invented names and companies, `.example`/`.test`/`.invalid` domains,
   555-01xx phone numbers, public test card numbers and SSNs in the 9xx range.
2. No network access at run time. Only `offrecord setup` may download, and only pinned,
   checksum-verified files. A test fails if any other command opens a connection.
3. Personal data first, and recall first for it. offrecord's job is to keep personal data out
   of what users paste; business context is flagged by default and redacted only with
   `protect: confidential`. When a change trades PII recall for precision, recall wins unless
   there is a measured reason. Report PII recall and the PII-clean rate in the pull request.
4. Treat every transcript as sensitive. Never print, log or echo its content. Errors name
   positions, sizes and categories only. The deny-list, mapping file and audit log are
   sensitive too.
5. Tier 0 must keep working with no optional extras and no models, in about 2 GB of RAM.
   Nothing may pull in PyTorch.
6. Python 3.10 is the floor: no `tomllib`, `ExceptionGroup`, `Self` or `except*`.
7. New runtime dependencies need discussion in an issue first. Pin them.

## Evaluation

The evaluation sets are synthetic and were generated, not collected (see
`docs/eval-format.md`). Detectors are tuned on `tuning` and `tuning-hw` only. Use `validation`
to check that a change generalises; a change that helps tuning but not validation is probably
overfitting. Never tune on `user` or the sealed set, and never edit the sealed set: CI fails if
it changes.

```sh
offrecord eval --set tuning --set validation
.venv/bin/python scripts/misses.py PERCENT     # tuning-set misses for one category (--hw: tuning-hw)
```

Label changes go in `eval/CORRECTIONS.md` with the reason and the effect on scores.

## AI-assisted contributions

Contributions written with coding assistants are welcome, as long as you have read and tested
everything you submit and can explain it. `AGENTS.md` gives assistants the project's
conventions. Keep personal assistant settings (`CLAUDE.local.md`, `.claude/settings.local.json`)
out of the repository; `.gitignore` already covers them.

## Voice

Docs, comments, error messages and commit messages should read as one careful developer
wrote them. Before you submit, check:

- Plain words. No "seamless", "robust", "leverage", "comprehensive", "cutting-edge" or
  "powerful". Say what the thing does.
- No formulaic openers ("In today's world", "Let's dive in") or closers ("In summary").
- Lists only for genuinely parallel items, not to break up every paragraph. Bold sparingly.
- Not everything comes in threes. Give the number of examples the point needs.
- No emoji.
- Comments explain why, not what the next line does. Don't narrate history ("now we",
  "added after the last release"); that belongs in the commit message.
- Error messages say what went wrong and what to do, without transcript content.
- State limits and measured numbers plainly, including the bad ones.

## Reporting security issues

Please don't open a public issue for a vulnerability. See `SECURITY.md`.
