"""Evaluation runner: per-category recall (headline), precision, leaks per 1,000 words.

A gold label counts as *caught* only if every letter and digit in it is covered by a redaction
span in the same field (strict, recall-first). Partially covered labels are *leaks* and are
also counted separately as ``partial``.
"""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from offrecord import adapters
from offrecord.evaluation.labels import Label, LabelledTranscript, Negative, strip_markup
from offrecord.limits import Limits
from offrecord.model import Document, FieldId, Span, severity, turn_field_id
from offrecord.redact.pipeline import readability
from offrecord.safeio import read_input_text

System = Callable[[Document], Sequence[Span]]
# Label categories that are personal data in every set. TOPIC and DATE labels mix personal and
# business items, so they are reported separately rather than in the PII headline.
PII_LABELS = frozenset("PERSON SPEAKER EMAIL PHONE SSN CARD ACCOUNT IP URL LOCATION".split())


@dataclass
class EvalItem:
    id: str
    doc: Document
    labels: list[Label]
    negatives: list[Negative]
    meta: dict[str, Any] = field(default_factory=dict)


# --- loading --------------------------------------------------------------------------------


def load_generated(
    directory: Path, final_check: bool = False
) -> tuple[list[EvalItem], dict[str, Any]]:
    """Load ``*.labels.json`` + transcript pairs and the set's ``denylist.yaml``. The sealed set
    (recognised by its labels, so a copy is refused too) loads only for the final check."""
    from offrecord.errors import UsageError

    items = []
    for lp in sorted(directory.glob("*.labels.json")):
        meta = json.loads(lp.read_text(encoding="utf-8"))
        if meta.get("set") == "sealed":
            from offrecord.evaluation.sets import SEALED_MANIFEST_SHA256, verify_manifest

            if not final_check:
                raise UsageError("the sealed set is scored only with --final-check")
            if not verify_manifest(directory, SEALED_MANIFEST_SHA256):
                raise UsageError("the sealed set does not match its pinned manifest")
        tid = meta["id"]
        fmt = meta["format"]
        tp = directory / f"{tid}.{fmt}"
        doc = adapters.get(fmt).parse(read_input_text(tp), Limits())
        labels = [Label.from_json(d) for d in meta.pop("labels")]
        negs = [Negative(**d) for d in meta.pop("negatives")]
        items.append(EvalItem(tid, doc, labels, negs, meta))
    return items, _load_config(directory)


def _load_config(directory: Path) -> dict[str, Any]:
    p = directory / "denylist.yaml"
    if p.exists():
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {}


def load_markup_dir(directory: Path) -> tuple[list[EvalItem], dict[str, Any]]:
    """Load hand-written inline-markup transcripts (``*.txt``) from a folder."""
    items = []
    for tp in sorted(directory.glob("*.txt")):
        lt, doc = parse_markup_transcript(tp.stem, read_input_text(tp))
        items.append(EvalItem(lt.id, doc, lt.labels, lt.negatives, {"set": "markup"}))
    return items, _load_config(directory)


def parse_markup_transcript(tid: str, text: str) -> tuple[LabelledTranscript, Document]:
    """Parse a marked-up txt transcript: markup is stripped per line, then the txt adapter's
    speaker detection runs on the clean text; labels are aligned per turn."""
    from offrecord.errors import InputFormatError

    raw_lines = text.split("\n")
    clean_lines, line_labels = [], []
    for no, raw in enumerate(raw_lines):
        clean, labels, negs = strip_markup(raw, no, "line")
        clean_lines.append(clean)
        line_labels.append((labels, negs))
    doc = adapters.get("txt").parse("\n".join(clean_lines), Limits())
    lt = LabelledTranscript(tid, [], [])
    for t in doc.turns:
        lt.speakers.append(t.speaker)
        lt.texts.append(t.text)
        if t.source_lines is None:
            continue
        first, last = t.source_lines
        header = t.extra.get("layout") == "header"
        offset = 0  # running offset of each line's body within the turn text
        for ln in range(first, last + 1):
            line = clean_lines[ln - 1]
            labels, negs = line_labels[ln - 1]
            if ln == first and (t.speaker is not None or "ts" in t.extra):
                body = t.text.split("\n", 1)[0] if not header else ""
                body_start = len(line) - len(body) if body else len(line)
            else:
                body_start = 0
                body = line
            spk_start = line.find(t.speaker) if (t.speaker and ln == first) else -1
            for lb in labels:
                if lb.start >= body_start and body:
                    s, e = offset + lb.start - body_start, offset + lb.end - body_start
                    fld, idx = "text", t.index
                elif spk_start >= 0 and spk_start <= lb.start < spk_start + len(t.speaker or ""):
                    s, e = lb.start - spk_start, lb.end - spk_start
                    fld, idx = "speaker", t.index
                else:
                    raise InputFormatError(f"could not align a label on line {ln}")
                target = t.text if fld == "text" else (t.speaker or "")
                if target[s:e] != line[lb.start : lb.end]:
                    raise InputFormatError(f"could not align a label on line {ln}")
                lt.labels.append(Label(idx, fld, s, e, lb.category, lb.entity))
            for ng in negs:
                if ng.start >= body_start:
                    s = offset + ng.start - body_start
                    lt.negatives.append(Negative(t.index, "text", s, s + ng.end - ng.start))
            if body or ln != first:
                offset += len(body) + 1
    return lt, doc


# --- scoring --------------------------------------------------------------------------------


@dataclass
class Counts:
    total: int = 0
    caught: int = 0
    partial: int = 0

    @property
    def recall(self) -> float | None:
        return self.caught / self.total if self.total else None


@dataclass
class CharCounts:
    redacted: int = 0  # characters redacted (by spans of this category)
    on_gold: int = 0  # ... of which inside some gold label

    @property
    def precision(self) -> float | None:
        return self.on_gold / self.redacted if self.redacted else None


@dataclass
class Report:
    """Scores for one set and one system.

    - Recall is strict: a label counts only if every letter and digit is redacted.
    - Speaker labels count once per distinct speaker per transcript (category ``SPEAKER``):
      caught only if every occurrence is redacted. Otherwise a speaker label repeated on every
      turn would dominate the headline.
    - Precision is character-based: redacted characters inside any gold label / all redacted
      characters. Per-category precision uses the predicted span's category.
    - ``negatives_hit``: NEG items (ordinary uses of common-word brands) touched by a redaction.
    - A transcript is *clean* if none of its labels leaked; per category, among the transcripts
      that contain that category. ``clean_high`` counts only high-severity categories.
    """

    name: str
    per_category: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    per_form: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    per_casing: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    known_split: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    chars: CharCounts = field(default_factory=CharCounts)
    # Text fields only: a speaker label redacted on every turn is easy and would dominate.
    chars_text: CharCounts = field(default_factory=CharCounts)
    chars_by_category: dict[str, CharCounts] = field(
        default_factory=lambda: defaultdict(CharCounts)
    )
    negatives_hit: int = 0
    negatives_total: int = 0
    words: int = 0
    transcripts: int = 0
    leaks_per_transcript: list[int] = field(default_factory=list)
    # Personal data only (PII_LABELS), the headline for the default protect: pii. The other
    # labels (business context) are counted when redacted or flagged for review; a flag is a
    # prompt to look, not protection.
    pii: Counts = field(default_factory=Counts)
    pii_by_transcript: list[tuple[int, int]] = field(default_factory=list)  # (labels, caught)
    clean_pii: int = 0
    other_total: int = 0
    other_redacted_or_flagged: int = 0
    other_redacted: int = 0  # ... of which redacted (the rest are flags left readable)
    text_chars: int = 0  # readability: how much of the text the redactions cover
    text_redacted: int = 0
    sentences: int = 0
    sentences_mostly_redacted: int = 0
    clean_high: int = 0
    with_category: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    clean_by_category: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def clean_rate(self) -> float | None:
        n = len(self.leaks_per_transcript)
        return sum(x == 0 for x in self.leaks_per_transcript) / n if n else None

    @property
    def clean_pii_rate(self) -> float | None:
        return self.clean_pii / self.transcripts if self.transcripts else None

    @property
    def other_redacted_or_flagged_rate(self) -> float | None:
        return self.other_redacted_or_flagged / self.other_total if self.other_total else None

    @property
    def other_redacted_rate(self) -> float | None:
        return self.other_redacted / self.other_total if self.other_total else None

    @property
    def precision_text(self) -> float | None:
        return self.chars_text.precision

    @property
    def negatives_hit_rate(self) -> float | None:
        return self.negatives_hit / self.negatives_total if self.negatives_total else None

    def pii_intervals(self, rounds: int = 2000) -> dict[str, tuple[float, float] | None]:
        """95% bootstrap intervals for PII recall and the PII-clean rate, resampling whole
        transcripts (labels in one transcript are not independent). Fixed seed: the same
        report always gives the same interval."""
        rows = self.pii_by_transcript
        if len(rows) < 2:
            return {"pii_recall": None, "clean_pii": None}
        rng = random.Random(0)  # noqa: S311 (resampling, not security)
        recall, clean = [], []
        for _ in range(rounds):
            sample = rng.choices(rows, k=len(rows))
            total = sum(t for t, _ in sample)
            recall.append(sum(c for _, c in sample) / total if total else 1.0)
            clean.append(sum(c == t for t, c in sample) / len(sample))

        def ci(xs: list[float]) -> tuple[float, float]:
            xs.sort()
            return xs[int(0.025 * len(xs))], xs[int(0.975 * len(xs)) - 1]

        return {"pii_recall": ci(recall), "clean_pii": ci(clean)}

    @property
    def clean_high_rate(self) -> float | None:
        return self.clean_high / self.transcripts if self.transcripts else None

    def clean_rate_for(self, cat: str) -> float | None:
        n = self.with_category.get(cat, 0)
        return self.clean_by_category.get(cat, 0) / n if n else None

    @property
    def leaks_median(self) -> float | None:
        return statistics.median(self.leaks_per_transcript) if self.leaks_per_transcript else None

    @property
    def overall(self) -> Counts:
        c = Counts()
        for v in self.per_category.values():
            c.total += v.total
            c.caught += v.caught
            c.partial += v.partial
        return c

    @property
    def precision(self) -> float | None:
        return self.chars.precision

    @property
    def leaks_per_1k_words(self) -> float | None:
        o = self.overall
        return (o.total - o.caught) * 1000 / self.words if self.words else None

    def to_json(self) -> dict[str, Any]:
        def cj(d: dict[str, Counts]) -> dict[str, Any]:
            return {k: {"total": v.total, "caught": v.caught, "partial": v.partial,
                        "recall": v.recall} for k, v in sorted(d.items())}  # fmt: skip

        o = self.overall
        return {
            "name": self.name, "transcripts": self.transcripts, "words": self.words,
            "recall": o.recall, "labels": o.total, "caught": o.caught, "partial": o.partial,
            "precision_chars": self.precision, "redacted_chars": self.chars.redacted,
            "precision_text": self.precision_text, "negatives_hit_rate": self.negatives_hit_rate,
            "other_redacted": self.other_redacted_rate,
            "negatives_hit": self.negatives_hit, "negatives": self.negatives_total,
            "leaks_per_1k_words": self.leaks_per_1k_words,
            "text_redacted_share": (
                self.text_redacted / self.text_chars if self.text_chars else None
            ),
            "sentences_mostly_redacted_share": (
                self.sentences_mostly_redacted / self.sentences if self.sentences else None
            ),
            "pii_recall": self.pii.recall, "pii_labels": self.pii.total,
            "clean_pii": self.clean_pii_rate,
            **{f"{k}_ci95": v for k, v in self.pii_intervals().items()},
            "other_redacted_or_flagged": self.other_redacted_or_flagged_rate,
            "clean_transcripts": self.clean_rate, "clean_high_severity": self.clean_high_rate,
            "leaks_per_transcript_median": self.leaks_median,
            "leaks_per_transcript_max": max(self.leaks_per_transcript, default=None),
            "clean_by_category": {
                k: self.clean_rate_for(k) for k in sorted(self.with_category)
            },
            "per_category": cj(self.per_category),
            "precision_by_category": {
                k: v.precision for k, v in sorted(self.chars_by_category.items())
            },
            "per_form": cj(self.per_form), "per_casing": cj(self.per_casing),
            "known_vs_unknown": cj(self.known_split),
        }  # fmt: skip


def _orig_only(spans: Iterable[Span]) -> list[Span]:
    out = []
    for s in spans:
        if s.view != "orig":
            raise ValueError("system returned a span that was not projected to orig coordinates")
        out.append(s)
    return out


def _intervals(spans: Iterable[Span]) -> dict[FieldId, list[tuple[int, int]]]:
    by: dict[FieldId, list[tuple[int, int]]] = defaultdict(list)
    for s in spans:
        if s.action == "redact" and s.end > s.start:
            by[s.field].append((s.start, s.end))
    return {fid: _union(ivs) for fid, ivs in by.items()}


def _union(ivs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(ivs):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _covered(ivs: list[tuple[int, int]], a: int, b: int, src: str) -> tuple[bool, bool]:
    """(fully covered, partially covered). Only letters and digits inside the label of ``src``
    must be covered: a leaked space or comma between two redacted words reveals nothing."""
    inside = [(max(a, s), min(b, e)) for s, e in ivs if s < b and e > a]
    if not inside:
        return False, False
    covered = bytearray(b - a)
    for s, e in inside:
        covered[s - a : e - a] = b"\x01" * (e - s)
    full = all(covered[i - a] or not src[i].isalnum() for i in range(a, b))
    return full, True


def _overlap_len(ivs: Sequence[tuple[int, int]], a: int, b: int) -> int:
    return sum(max(0, min(b, e) - max(a, s)) for s, e in ivs)


def score(items: Sequence[EvalItem], system: System, name: str) -> Report:
    rep = Report(name)
    for it in items:
        rep.transcripts += 1
        pii_before = (rep.pii.total, rep.pii.caught)
        rep.words += sum(len(t.text.split()) + len((t.speaker or "").split()) for t in it.doc.turns)
        raw = _orig_only(system(it.doc))
        spans = _intervals(raw)
        flagged = _intervals(replace(s, action="redact") for s in raw if s.action == "flag")
        for total, n in zip(
            ("text_chars", "text_redacted", "sentences", "sentences_mostly_redacted"),
            readability(it.doc, raw),
            strict=True,
        ):
            setattr(rep, total, getattr(rep, total) + n)
        casing = str(it.meta.get("casing", "n/a"))
        gold_by_field: dict[FieldId, list[tuple[int, int]]] = defaultdict(list)
        speakers: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
        present: set[str] = set()
        leaked: list[str] = []
        for lb in it.labels:
            fid = turn_field_id(lb.turn, lb.field)
            gold_by_field[fid].append((lb.start, lb.end))
            t = it.doc.turns[lb.turn]
            src = t.text if lb.field == "text" else (t.speaker or "")
            full, part = _covered(spans.get(fid, []), lb.start, lb.end, src)
            if lb.field == "speaker":
                speakers[lb.entity].append((full, part))
                continue
            present.add(lb.category)
            if not full:
                leaked.append(lb.category)
            if lb.category in PII_LABELS:
                rep.pii.total += 1
                rep.pii.caught += int(full)
            else:
                rep.other_total += 1
                seen = full or _covered(flagged.get(fid, []), lb.start, lb.end, src)[0]
                rep.other_redacted_or_flagged += int(seen)
                rep.other_redacted += int(full)
            keys = [
                (rep.per_category, lb.category),
                (rep.per_form, f"{lb.category}:{lb.form or 'n/a'}"),
                (rep.per_casing, casing),
            ]
            if lb.known is not None:
                keys.append(
                    (rep.known_split, f"{lb.category}:{'known' if lb.known else 'unknown'}")
                )
            for d, k in keys:
                c = d[k]
                c.total += 1
                c.caught += int(full)
                c.partial += int(part and not full)
        for occ in speakers.values():
            full = all(f for f, _ in occ)
            present.add("SPEAKER")
            rep.pii.total += 1
            rep.pii.caught += int(full)
            if not full:
                leaked.append("SPEAKER")
            part = any(p for _, p in occ) or (any(f for f, _ in occ) and not full)
            for d, k in ((rep.per_category, "SPEAKER"), (rep.per_casing, casing)):
                c = d[k]
                c.total += 1
                c.caught += int(full)
                c.partial += int(part and not full)
        rep.pii_by_transcript.append(
            (rep.pii.total - pii_before[0], rep.pii.caught - pii_before[1])
        )
        rep.leaks_per_transcript.append(len(leaked))
        rep.clean_high += not any(severity(cat) == "high" for cat in leaked)
        rep.clean_pii += not any(cat in PII_LABELS for cat in leaked)
        for cat in present:
            rep.with_category[cat] += 1
            rep.clean_by_category[cat] += cat not in leaked
        gold_union = {fid: _union(ivs) for fid, ivs in gold_by_field.items()}
        for fid, ivs in spans.items():
            for a, b in ivs:
                on_gold = _overlap_len(gold_union.get(fid, []), a, b)
                rep.chars.redacted += b - a
                rep.chars.on_gold += on_gold
                if fid[0] == "turn" and fid[2] == "text":
                    rep.chars_text.redacted += b - a
                    rep.chars_text.on_gold += on_gold
        by_cat: dict[str, dict[FieldId, list[tuple[int, int]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for sp in raw:
            if sp.action == "redact" and sp.end > sp.start:
                by_cat[sp.category][sp.field].append((sp.start, sp.end))
        for cat, fields in by_cat.items():
            cc = rep.chars_by_category[cat]
            for fid, ivs in fields.items():
                for a, b in _union(ivs):
                    cc.redacted += b - a
                    cc.on_gold += _overlap_len(gold_union.get(fid, []), a, b)
        rep.negatives_total += len(it.negatives)
        for ng in it.negatives:
            if _overlap_len(spans.get(turn_field_id(ng.turn, ng.field), []), ng.start, ng.end):
                rep.negatives_hit += 1
    return rep


def _ci(iv: tuple[float, float] | None) -> str:
    return "n/a" if iv is None else f"{iv[0] * 100:.1f}-{iv[1] * 100:.1f}%"


def format_report(reports: Sequence[Report]) -> str:
    """Side-by-side per-category recall table for several reports (e.g. tuning vs held-out)."""

    def pct(x: float | None) -> str:
        return "  n/a" if x is None else f"{x * 100:5.1f}"

    cats = sorted({c for r in reports for c in r.per_category})
    head = f"{'category':<10}" + "".join(f" | {r.name[:22]:>22}" for r in reports)
    lines = [head, "-" * len(head)]
    for c in cats:
        row = f"{c:<10}"
        for r in reports:
            v = r.per_category.get(c)
            cell = f"{pct(v.recall if v else None)}% of {v.total if v else 0:>5}"
            row += f" | {cell:>22}"
        lines.append(row)
    lines.append("-" * len(head))
    for label, fn in (
        ("PII RECALL", lambda r: f"{pct(r.pii.recall)}% of {r.pii.total:>5}"),
        ("  95% CI", lambda r: _ci(r.pii_intervals()["pii_recall"])),
        ("PII clean", lambda r: f"{pct(r.clean_pii_rate)}%"),
        ("  95% CI", lambda r: _ci(r.pii_intervals()["clean_pii"])),
        (
            "other r/f",
            lambda r: f"{pct(r.other_redacted_or_flagged_rate)}% of {r.other_total:>5}",
        ),
        ("  redacted", lambda r: f"{pct(r.other_redacted_rate)}%"),
        ("RECALL", lambda r: f"{pct(r.overall.recall)}% of {r.overall.total:>5}"),
        ("prec(char)", lambda r: f"{pct(r.precision)}%"),
        ("prec(text)", lambda r: f"{pct(r.precision_text)}%"),
        (
            "leaks/1kw",
            lambda r: "  n/a" if r.leaks_per_1k_words is None else f"{r.leaks_per_1k_words:6.2f}",
        ),
        (
            "NEG hit",
            lambda r: f"{r.negatives_hit} of {r.negatives_total} ({pct(r.negatives_hit_rate)}%)",
        ),
        (
            "redacted",
            lambda r: f"{pct(r.text_redacted / r.text_chars if r.text_chars else None)}% of text",
        ),
        ("CLEAN", lambda r: f"{pct(r.clean_rate)}% of {r.transcripts:>5}"),
        ("clean high", lambda r: f"{pct(r.clean_high_rate)}%"),
        (
            "leaks/tr",
            lambda r: (
                "n/a"
                if r.leaks_median is None
                else f"median {r.leaks_median:g}, max {max(r.leaks_per_transcript)}"
            ),
        ),
    ):
        lines.append(f"{label:<10}" + "".join(f" | {fn(r):>22}" for r in reports))
    lines += [
        "95% CI: bootstrap over transcripts. other r/f: labels that are not personal data "
        "(figures, organisations, business topics) redacted or flagged; a flag only asks you "
        "to look; '  redacted' is the part actually redacted. prec(text): character precision "
        "on turn text only (prec(char) also counts speaker labels). NEG hit: must-not-redact "
        "items that were redacted.",
    ]
    lines += ["", "Clean transcripts by category (share of transcripts with that category)"]
    for c in sorted({c for r in reports for c in r.with_category}):
        cells = []
        for r in reports:
            n = r.with_category.get(c, 0)
            cells.append(f"{pct(r.clean_rate_for(c))}% of {n:>5}" if n else "")
        lines.append(f"{c:<10}" + "".join(f" | {x:>22}" for x in cells))
    return "\n".join(lines)
