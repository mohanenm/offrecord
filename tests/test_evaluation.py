"""Evaluation: generator determinism, label alignment, markup parsing, scoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from offrecord.evaluation.baseline import null_system, regex_baseline
from offrecord.evaluation.generator import (
    FORMATS,
    Generator,
    asr_variant,
    denylist_from_pools,
    load_set,
    number_words,
    render,
    write_set,
)
from offrecord.evaluation.runner import (
    format_report,
    load_generated,
    load_markup_dir,
    parse_markup_transcript,
    score,
)
from offrecord.evaluation.sets import manifest_lines, verify_manifest
from offrecord.model import Span, turn_field_id

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def tuning():
    return load_set(ROOT, "tuning")


def test_generator_is_deterministic(tuning):
    t, p = tuning
    a = Generator(t, p, "tuning", 7).generate_one(3)[0]
    b = Generator(t, p, "tuning", 7).generate_one(3)[0]
    c = Generator(t, p, "tuning", 8).generate_one(3)[0]
    assert (a.texts, a.labels) == (b.texts, b.labels)
    assert a.texts != c.texts


def test_labels_point_at_the_right_text(tuning):
    t, p = tuning
    for lt, _ in Generator(t, p, "tuning", 11).generate(24):
        for lb in lt.labels:
            src = lt.texts[lb.turn] if lb.field == "text" else lt.speakers[lb.turn]
            assert 0 <= lb.start < lb.end <= len(src)
            assert src[lb.start : lb.end].strip() == src[lb.start : lb.end]


def test_every_format_round_trips(tuning):
    t, p = tuning
    for lt, _ in Generator(t, p, "tuning", 5).generate(12):
        for fmt in FORMATS:
            render(lt, fmt)  # raises on mismatch


def test_generated_values_are_synthetic(tuning):
    t, p = tuning
    for _lt, ents in Generator(t, p, "tuning", 3).generate(24):
        for e in ents:
            if e.type == "phone":
                assert e.data["line"].startswith("01")  # fictional 555-01xx range
            if e.type == "ssn":
                assert e.data["digits"].startswith("9")  # never-issued area
            if e.type == "email":
                assert e.data["domain"].rsplit(".", 1)[1] in {"example", "test", "invalid"}


def test_inline_labels_in_templates(tuning):
    _, pools = tuning
    tpl = {"id": "x", "speakers": {"A": "a"}, "turns": [
        {"A": "Margin is [[PERCENT: up a third]] near [[LOCATION#c: the Tri-Cities]], "
              "but the [[NEG: lantern]] works. [[TOPIC: we may close the {location} site]]."},
    ]}  # fmt: skip
    lt = Generator([tpl], pools, "t", 1).generate_one(0)[0]
    got = {(lb.category, lt.texts[lb.turn][lb.start : lb.end].lower()) for lb in lt.labels}
    assert ("PERCENT", "up a third") in got and ("LOCATION", "the tri-cities") in got
    assert sum(lb.category == "LOCATION" for lb in lt.labels) == 2  # literal + slot in topic
    assert lt.texts[0][lt.negatives[0].start : lt.negatives[0].end].lower() == "lantern"
    with pytest.raises(ValueError):
        Generator([{**tpl, "turns": [{"A": "[[BOGUS: x]]"}]}], pools, "t", 1).generate_one(0)


def test_casing_variants_present(tuning):
    t, p = tuning
    casings = {lt.meta["casing"] for lt, _ in Generator(t, p, "tuning", 1).generate(40)}
    assert casings == {"mixed", "lower", "upper"}


def test_denylist_never_contains_asr_variants(tuning):
    _, p = tuning
    dl = denylist_from_pools(p)
    names = {n.lower() for e in dl for n in [e["name"], *e["aliases"]]}
    for group in ("clients", "brands", "products", "codenames"):
        for entry in p[group]:
            for v in entry.get("asr", []):
                assert v.lower() not in names


def test_number_words():
    assert number_words(42000) == "forty two thousand"
    assert number_words(1_500_000) == "one million five hundred thousand"


def test_asr_variant_differs():
    import random

    assert asr_variant("Quillfeather", random.Random(1)) != "quillfeather"


def test_write_and_score(tmp_path):
    write_set(ROOT, "tuning", 99, 8, tmp_path)
    items, cfg = load_generated(tmp_path)
    assert len(items) == 8 and "denylist" in cfg
    null = score(items, null_system(cfg), "null")
    assert null.overall.recall == 0.0
    base = score(items, regex_baseline(cfg), "base")
    assert base.overall.caught > 0
    assert "RECALL" in format_report([null, base])


def test_scoring_requires_full_coverage():
    from offrecord.evaluation.labels import Label
    from offrecord.evaluation.runner import EvalItem
    from offrecord.model import Document, Turn

    doc = Document("txt", (Turn(0, None, "call 555-0142 now"),))
    lb = Label(0, "text", 5, 13, "PHONE", "phone#1")
    item = EvalItem("x", doc, [lb], [])
    fid = turn_field_id(0, "text")
    partial = score([item], lambda d: [Span(fid, 5, 9, "PHONE", "t", 1.0)], "p")
    assert partial.overall.caught == 0 and partial.overall.partial == 1
    joined = score(
        [item],
        lambda d: [Span(fid, 5, 9, "PHONE", "t", 1.0), Span(fid, 9, 13, "PHONE", "t", 1.0)],
        "j",
    )
    assert joined.overall.caught == 1
    flagged = score([item], lambda d: [Span(fid, 5, 13, "PHONE", "t", 1.0, action="flag")], "f")
    assert flagged.overall.caught == 0  # a review flag is not a redaction


def test_clean_transcript_rate_and_severity():
    from offrecord.evaluation.labels import Label
    from offrecord.evaluation.runner import EvalItem
    from offrecord.model import Document, Turn

    fid = turn_field_id(0, "text")
    doc = Document("txt", (Turn(0, None, "call 555-0142 on 3 May"),))
    labels = [Label(0, "text", 5, 13, "PHONE", "p"), Label(0, "text", 17, 22, "DATE", "d")]
    items = [EvalItem(str(i), doc, labels, []) for i in range(3)]
    spans = {"0": [(5, 13), (17, 22)], "1": [(5, 13)], "2": [(17, 22)]}  # date / phone leak

    def system(d):
        return [Span(fid, a, b, "X", "t", 1.0) for a, b in spans[next(it)]]

    it = iter("012")
    r = score(items, system, "s")
    assert r.clean_rate == 1 / 3  # only the first transcript leaks nothing
    assert r.clean_high_rate == 2 / 3  # a leaked date is not high severity, a phone is
    assert r.clean_rate_for("PHONE") == 2 / 3 and r.leaks_per_transcript == [0, 1, 1]
    assert r.to_json()["leaks_per_transcript_max"] == 1


MARKUP = """[[PERSON:Orla Venkatesh]]: Morning. Is [[PERSON#bram:Bram]] joining?
[[PERSON#bram:Bram Okafor]]: Yes. The [[CLIENT:Quillfeather]] deal closed at [[AMOUNT:$42,000]].
and the [[NEG:lantern]] works, call [[PHONE:555-0142]].
[[PERSON:Orla Venkatesh]]: Great.
"""


def test_markup_alignment():
    lt, _doc = parse_markup_transcript("m", MARKUP)
    assert lt.speakers == ["Orla Venkatesh", "Bram Okafor", "Orla Venkatesh"]
    got = []
    for lb in lt.labels:
        src = lt.texts[lb.turn] if lb.field == "text" else lt.speakers[lb.turn]
        got.append((lb.category, lb.field, src[lb.start : lb.end]))
    assert ("CLIENT", "text", "Quillfeather") in got
    assert ("PHONE", "text", "555-0142") in got
    assert ("PERSON", "speaker", "Bram Okafor") in got
    assert ("PERSON", "text", "Bram") in got
    neg = lt.negatives[0]
    assert lt.texts[neg.turn][neg.start : neg.end] == "lantern"


def test_markup_dir(tmp_path):
    (tmp_path / "a.txt").write_text(MARKUP)
    items, cfg = load_markup_dir(tmp_path)
    assert len(items) == 1 and cfg == {}


def test_manifest(tmp_path):
    (tmp_path / "a").write_text("x")
    (tmp_path / "MANIFEST.sha256").write_text("\n".join(manifest_lines(tmp_path)))
    assert verify_manifest(tmp_path)
    (tmp_path / "a").write_text("y")
    assert not verify_manifest(tmp_path)


def test_committed_frozen_sets_are_unchanged():
    from offrecord.evaluation.sets import FROZEN

    for name, pin in FROZEN.items():
        assert verify_manifest(ROOT / "eval" / "generated" / name, pin), f"{name} set changed"


def test_sealed_set_needs_final_check(tmp_path):
    import shutil

    from offrecord.errors import UsageError

    sealed = ROOT / "eval" / "generated" / "sealed"
    with pytest.raises(UsageError):
        load_generated(sealed)
    copy = tmp_path / "copy"  # a copy elsewhere is still recognised
    shutil.copytree(sealed, copy)
    with pytest.raises(UsageError):
        load_generated(copy)
    # Structure only, with no detector run and no text in any message: every label points
    # at a non-empty, stripped stretch of its field.
    items, _ = load_generated(sealed, final_check=True)
    bad = 0
    for it in items:
        for lb in it.labels:
            t = it.doc.turns[lb.turn]
            src = (t.text if lb.field == "text" else t.speaker) or ""
            piece = src[lb.start : lb.end]
            bad += not piece or piece != piece.strip()
    assert len(items) == 80 and bad == 0


def test_rewritten_manifest_is_still_detected(tmp_path):
    import shutil

    from offrecord.evaluation.sets import HELDOUT_MANIFEST_SHA256, MANIFEST

    d = tmp_path / "heldout"
    shutil.copytree(ROOT / "eval" / "generated" / "heldout", d)
    victim = next(d.glob("*.labels.json"))
    victim.write_text(victim.read_text()[:100])
    (d / MANIFEST).write_text("\n".join(manifest_lines(d)) + "\n")
    assert verify_manifest(d)  # self-consistent again...
    assert not verify_manifest(d, HELDOUT_MANIFEST_SHA256)  # ...but not the pinned one


def test_generator_reproduces_committed_sets(tmp_path):
    from offrecord.evaluation.sets import MANIFEST, SET_PARAMS

    for name in ("tuning", "heldout"):
        out = tmp_path / name
        write_set(ROOT, name, SET_PARAMS[name]["seed"], SET_PARAMS[name]["n"], out)
        committed = ROOT / "eval" / "generated" / name
        a = [ln for ln in (committed / MANIFEST).read_text().splitlines() if ln]
        assert manifest_lines(out) == a, f"{name}: generator output differs from committed set"


def test_speaker_labels_count_once_per_speaker():
    from offrecord.evaluation.labels import Label
    from offrecord.evaluation.runner import EvalItem
    from offrecord.model import Document, Turn

    turns = tuple(Turn(i, "Orla V", "hello there") for i in range(10))
    labels = [Label(i, "speaker", 0, 6, "PERSON", "person#spkA") for i in range(10)]
    item = EvalItem("x", Document("txt", turns), labels, [])
    only_some = score(
        [item],
        lambda d: [Span(turn_field_id(i, "speaker"), 0, 6, "PERSON", "t", 1.0) for i in range(9)],
        "s",
    )
    assert only_some.per_category["SPEAKER"].total == 1
    assert only_some.per_category["SPEAKER"].caught == 0  # one occurrence leaked


def test_precision_is_character_based():
    from offrecord.evaluation.labels import Label
    from offrecord.evaluation.runner import EvalItem
    from offrecord.model import Document, Turn

    doc = Document("txt", (Turn(0, None, "aaaaa 555-0142 bbbbbbbbbbbbb"),))
    item = EvalItem("x", doc, [Label(0, "text", 6, 14, "PHONE", "p")], [])
    fid = turn_field_id(0, "text")
    spans = [
        Span(fid, 0, 6, "X", "t", 1.0),
        Span(fid, 6, 14, "PHONE", "t", 1.0),
        Span(fid, 14, 28, "X", "t", 1.0),
    ]
    rep = score([item], lambda d: spans, "p")
    assert rep.overall.caught == 1
    assert rep.precision == pytest.approx(8 / 28)


def test_text_precision_leaves_out_speaker_labels():
    from offrecord.evaluation.labels import Label
    from offrecord.evaluation.runner import EvalItem
    from offrecord.model import Document, Turn

    doc = Document("txt", (Turn(0, "Orla V", "aaaa 555-0142"),))
    labels = [
        Label(0, "speaker", 0, 6, "PERSON", "person#spk"),
        Label(0, "text", 5, 13, "PHONE", "p"),
    ]
    spans = [Span(turn_field_id(0, "speaker"), 0, 6, "PERSON", "t", 1.0),
             Span(turn_field_id(0, "text"), 0, 13, "PHONE", "t", 1.0)]  # fmt: skip
    rep = score([EvalItem("x", doc, labels, [])], lambda d: spans, "p")
    assert rep.precision == pytest.approx(14 / 19)
    assert rep.precision_text == pytest.approx(8 / 13)


def test_unprojected_spans_rejected():
    from offrecord.evaluation.runner import EvalItem
    from offrecord.model import Document, Turn

    item = EvalItem("x", Document("txt", (Turn(0, None, "abc"),)), [], [])
    with pytest.raises(ValueError):
        score(
            [item],
            lambda d: [Span(turn_field_id(0, "text"), 0, 1, "X", "t", 1.0, view="norm")],
            "v",
        )


def test_committed_sets_have_valid_labels():
    for name in ("tuning", "heldout"):
        d = ROOT / "eval" / "generated" / name
        if not d.exists():
            continue
        items, _ = load_generated(d)
        for it in items:
            for lb in it.labels:
                t = it.doc.turns[lb.turn]
                src = t.text if lb.field == "text" else t.speaker
                assert src is not None and lb.end <= len(src)
        meta = json.loads(next(d.glob("*.labels.json")).read_text())
        assert meta["set"] == name


def test_eval_set_pools_are_disjoint():
    import yaml

    def load(name):
        return yaml.safe_load((ROOT / "eval" / "pools" / name / "pools.yaml").read_text())

    def strings(pool):
        out = set()
        for k in ("clients", "brands", "products", "codenames", "brand_common"):
            for e in pool[k]:
                out |= {e["name"].lower(), *(a.lower() for a in e.get("aliases", []))}
                out |= {a.lower() for a in e.get("asr", [])}
                if "word" in e:
                    out.add(e["word"].lower())
        return out

    for a, b in (("tuning", "heldout"), ("tuning", "sealed"), ("heldout", "sealed")):
        t, h = load(a), load(b)
        # Counts only: a failure must not print sealed pool entries into a CI log.
        assert len(strings(t) & strings(h)) == 0, (a, b)
        assert len(set(t["first_names"]) & set(h["first_names"])) == 0, (a, b)
        assert len(set(t["last_names"]) & set(h["last_names"])) == 0, (a, b)


def test_coverage_ignores_spaces_between_redacted_words():
    from offrecord.evaluation.runner import _covered

    assert _covered([(0, 4), (5, 9)], 0, 9, "Orla Venk") == (True, True)
    assert _covered([(0, 4)], 0, 9, "Orla Venk") == (False, True)


def test_extras_on_a_small_slice():
    from offrecord.evaluation.extras import run_extras

    items, cfg = load_generated(ROOT / "eval" / "generated" / "tuning")
    ex = run_extras(items[:6], cfg, "t").to_json()
    assert ex["placeholders_lost_by_compression"] == 0
    assert 0 < ex["placeholder_consistency"] <= 1 and 0 < ex["restore_accuracy"] <= 1
    assert ex["token_reduction_by_level"]["medium"] >= ex["token_reduction_by_level"]["light"]


def test_cli_set_choices_match_sets_module():
    from pathlib import Path

    from offrecord import cli
    from offrecord.evaluation import sets

    assert tuple(cli.SET_CHOICES) == (*sets.SET_NAMES, sets.SEALED, *sets.ALIASES)
    assert set(sets.TUNING_SETS) <= set(sets.SET_NAMES)
    assert "validation" not in sets.TUNING_SETS and "user" not in sets.TUNING_SETS
    assert sets.set_dir("heldout", Path("/r")) == sets.set_dir("validation", Path("/r"))
    assert sets.set_dir("tuning-hw", Path("/r")) == Path("/r/eval/tuning_handwritten")


def test_pii_intervals_resample_transcripts_and_are_repeatable():
    from offrecord.evaluation.runner import Report

    rep = Report("x", pii_by_transcript=[(10, 10)] * 8 + [(10, 5)] * 2)
    iv = rep.pii_intervals()
    assert iv == rep.pii_intervals()  # fixed seed
    lo, hi = iv["pii_recall"]
    assert lo <= 0.9 <= hi < 1.0001
    lo, hi = iv["clean_pii"]
    assert lo <= 0.8 <= hi
    assert Report("y", pii_by_transcript=[(3, 3)]).pii_intervals()["pii_recall"] is None
