"""Helpers shared by adapters. No function here may put input text into an error message."""

from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Sequence

from offrecord.limits import Limits

# h:mm:ss(.mmm|,mmm) or mm:ss(.mmm). ASCII digits only: a timestamp is kept outside the
# scanned text, so it must never be able to carry anything but digits.
TS_RE = r"(?:[0-9]{1,3}:)?[0-9]{1,2}:[0-9]{2}(?:[.,][0-9]{1,3})?"
_TS_FULL = re.compile(rf"^{TS_RE}$")


def parse_ts(s: str) -> float | None:
    s = s.strip()
    if not _TS_FULL.match(s):
        return None
    total = 0.0
    for p in s.replace(",", ".").split(":"):  # the regex admits only ASCII digits: float() is safe
        total = total * 60 + float(p)
    return total


def format_ts(seconds: float, sep: str = ".") -> str:
    ms_total = round(seconds * 1000)
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


# Only known caption tags (and VTT inline timestamps) are stripped; anything else that looks
# like a tag stays in the text so it is scanned. Attribute values of stripped styling tags
# (e.g. font colours) are dropped and never re-emitted.
_TAG_RE = re.compile(
    r"</?(?:b|i|u|c|v|s|ruby|rt|lang|font|span|em|strong)(?:[.\s][^<>\n]{0,200})?>"
    rf"|<{TS_RE}>",
    re.IGNORECASE,
)
_SSA_RE = re.compile(r"\{\\[^{}\n]{0,100}\}")


def strip_markup(s: str) -> str:
    """Remove known caption tags and SSA override codes, then unescape entities."""
    s = _TAG_RE.sub("", s)
    s = _SSA_RE.sub("", s)
    return html.unescape(s)


# --- Speaker labels ---------------------------------------------------------------------------

GENERIC_SPEAKER_RE = re.compile(
    r"^(?:speaker|spk|spkr|participant|person|voice|caller|agent|customer|rep|interviewer|"
    r"interviewee|host|guest|moderator|unknown|user|assistant|attendee)"
    r"(?:[ _-]*(?:\d{1,3}|[a-z]))?$",
    re.IGNORECASE,
)

# "Label: text". The label: 1-48 chars, no colon, no sentence punctuation at the end.
_LABEL_RE = re.compile(r"(?P<label>[^:\s][^:\n]{0,47}?)[ \t]*:[ \t]+")
_MAX_LABEL_WORDS = 5
LABEL_PREFIX = 64  # regexes only ever look at this many characters of a line


def split_label(line: str) -> tuple[str, str] | None:
    """Return (label, rest) if the line looks like ``Label: text``.

    Linear time: leading whitespace is skipped once, and the pattern only sees a short prefix.
    """
    lead = len(line) - len(line.lstrip(" \t"))
    m = _LABEL_RE.match(line[lead : lead + LABEL_PREFIX])
    if not m:
        return None
    label = m.group("label").strip()
    if not label or len(label.split()) > _MAX_LABEL_WORDS:
        return None
    if label.lower().startswith(("http", "www")) or _TS_FULL.match(label):
        return None
    if label[-1] in ".?!,;" or label.isdigit():
        return None
    return label, line[lead + m.end() :]


def accepted_labels(candidates: Sequence[str], units: int = 0) -> set[str]:
    """Which candidate labels to treat as speakers.

    Accepted if the label is generic ("Speaker 1"), or repeats (compared case- and
    punctuation-insensitively), or if at least half of the ``units`` (lines or cues) carry a
    label, which means the document is labelled throughout.
    Rejected labels stay in the turn text, so they are still scanned: no recall loss.
    """
    keys = Counter(speaker_key(c) for c in candidates)
    mostly_labelled = len(candidates) >= 2 and len(candidates) * 2 >= units
    out = set()
    for c in candidates:
        k = speaker_key(c)
        if (
            GENERIC_SPEAKER_RE.match(c.strip())
            or keys[k] >= 2
            or len(keys) <= 1
            or (mostly_labelled and len(c.split()) <= 4 and not (set(k.split()) & _FUNCTION_WORDS))
        ):
            out.add(c)
    return out


# Words that almost never appear in a speaker name; a "label" containing one is a sentence.
_FUNCTION_WORDS = frozenset(
    "is are was were be the a an to of and or but so we i you it this that here there what when "
    "why how my our your his her their its not no yes if then than as at on in for with about "
    "thing note todo".split()
)


_KEY_STRIP = re.compile(r"[^\w]+", re.UNICODE)


def speaker_key(label: str) -> str:
    """Case-, punctuation- and spacing-insensitive key used to unify speaker labels.

    "JOHN SMITH", "John Smith:", "john  smith" -> "john smith"; "SPEAKER_01" -> "speaker 1".
    """
    s = label.casefold().replace("_", " ")
    s = _KEY_STRIP.sub(" ", s)
    s = re.sub(r"\b0+(\d)", r"\1", s)
    return " ".join(s.split())


def check_turns(limits: Limits, texts: Sequence[str]) -> None:
    limits.check_turn_count(len(texts))
    for i, t in enumerate(texts):
        limits.check_turn_chars(i, len(t))


def check_metadata(limits: Limits, n: int) -> None:
    if n > limits.max_metadata_fields:
        from offrecord.errors import InputLimitError

        raise InputLimitError(
            f"input has {n} metadata fields; limit is {limits.max_metadata_fields} "
            "(max_metadata_fields)"
        )


_BLANK_RUN = re.compile(r"\n[ \t]*\n+")


def no_blank_lines(s: str) -> str:
    """Cue payloads (VTT/SRT) cannot contain blank lines."""
    return _BLANK_RUN.sub("\n", s)
