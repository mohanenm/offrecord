"""Tier 0 score floors on the tuning and validation sets.

A change that loses personal-data recall, or redacts much more than it should, fails here.
Failure messages carry category names and numbers only, never transcript text. The floors sit
a little below the measured scores; raise them when the scores rise for good.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from offrecord.evaluation import runner, sets
from offrecord.redact.pipeline import tier0_system

ROOT = Path(__file__).parent.parent

PII_RECALL_FLOOR = {"tuning": 0.99, "validation": 0.98}
CATEGORY_FLOOR = 0.90  # each PII category with at least CATEGORY_MIN_LABELS labels
CATEGORY_MIN_LABELS = 20
PRECISION_FLOOR = 0.85  # character precision on validation


@functools.cache
def report(name: str) -> runner.Report:
    items, cfg = runner.load_generated(sets.set_dir(name, ROOT))
    assert items, f"set {name} is empty"
    return runner.score(items, tier0_system(cfg), name)


@pytest.mark.parametrize("name", sorted(PII_RECALL_FLOOR))
def test_pii_recall_floor(name):
    recall = report(name).pii.recall
    assert recall is not None
    assert recall >= PII_RECALL_FLOOR[name], f"PII recall {recall:.4f} < {PII_RECALL_FLOOR[name]}"


def test_pii_category_recall_floor_on_validation():
    rep = report("validation")
    below = {
        cat: round(c.recall, 4)
        for cat, c in sorted(rep.per_category.items())
        if cat in runner.PII_LABELS
        and c.total >= CATEGORY_MIN_LABELS
        and c.recall is not None
        and c.recall < CATEGORY_FLOOR
    }
    assert not below, f"PII categories below {CATEGORY_FLOOR}: {below}"


def test_precision_floor_on_validation():
    precision = report("validation").precision
    assert precision is not None
    assert precision >= PRECISION_FLOOR, f"character precision {precision:.4f} < {PRECISION_FLOOR}"
