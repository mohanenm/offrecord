"""Regression tests for input handling: adapters (scan coverage, size limits) and the
normaliser (Unicode digits, invisible characters, confusables). All data is synthetic.
"""

from __future__ import annotations

import contextlib
import json
import time

import pytest

from offrecord import adapters
from offrecord.limits import Limits
from offrecord.normalize import build_fields
from offrecord.normalize.spoken import normalize

CANARY = "ZQXCANARY"
L = Limits()


def scanned_text(doc) -> str:
    return "\n".join(f.orig for f in build_fields(doc))


def assert_rendered_text_is_scanned(name: str, text: str) -> None:
    """Every occurrence of CANARY in the re-rendered output must also be in a scanned field."""
    a = adapters.get(name)
    doc = a.parse(text, L)
    out = a.render(doc)
    if CANARY in out:
        assert CANARY in scanned_text(doc), f"{name}: canary re-emitted without being scanned"


# Cue settings and timing-line tails are re-emitted, so they are scanned.
def test_vtt_cue_settings_are_scanned():
    t = f"WEBVTT\n\n00:00:01.000 --> 00:00:02.000 align:start {CANARY} 555-12-3456\nhi\n"
    assert_rendered_text_is_scanned("vtt", t)
    assert CANARY in scanned_text(adapters.get("vtt").parse(t, L))


def test_srt_timing_tail_is_scanned():
    t = f"1\n00:00:01,000 --> 00:00:02,000 X1:{CANARY} call 555\nhi\n"
    assert_rendered_text_is_scanned("srt", t)
    assert CANARY in scanned_text(adapters.get("srt").parse(t, L))


# No text is dropped before scanning.
def test_unknown_angle_brackets_are_kept():
    t = f"1\n00:00:01,000 --> 00:00:02,000\nif a < {CANARY} and b > c\n"
    assert CANARY in scanned_text(adapters.get("srt").parse(t, L))


def test_second_voice_name_is_kept():
    t = f"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Orla>hi</v> <v {CANARY}>hello</v>\n"
    doc = adapters.get("vtt").parse(t, L)
    assert CANARY in scanned_text(doc)


def test_txt_continuation_line_keeps_leading_time():
    doc = adapters.get("txt").parse("Orla: see you\n10:30 tomorrow then\nOrla: ok\n", L)
    assert "10:30 tomorrow then" in doc.turns[0].text


def test_json_duplicate_keys_both_scanned():
    t = '{"note": "' + CANARY + '-1", "note": "second", "segments": [{"text": "hi"}]}'
    doc = adapters.get("json").parse(t, L)
    assert CANARY + "-1" in scanned_text(doc)


# Nothing reaches the output unscanned.
def test_txt_timestamp_is_ascii_digits_only():
    # Unicode digits must not be accepted as a (non-scanned) timestamp.
    doc = adapters.get("txt").parse("\u0661\u0662:\u0664\u0665 Orla: hi\nOrla: x\n", L)
    for t in doc.turns:
        assert t.extra.get("ts", "").isascii()


def test_json_nested_token_arrays_dropped():
    t = json.dumps({"segments": [{"text": "hi", "ids": [[50256, 1234], [42, 7], [1, 2, 3, 4, 5]]}]})
    out = adapters.get("json").render(adapters.get("json").parse(t, L))
    assert "50256" not in out


def test_json_drop_keys_case_insensitive():
    t = json.dumps({"segments": [{"text": "hi", "Words": [{"word": CANARY}], "token_ids": [1]}]})
    out = adapters.get("json").render(adapters.get("json").parse(t, L))
    assert CANARY not in out and "token_ids" not in out


# Linear time within the size caps.
@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("txt", " " * 100_000 + "x\n"),
        ("txt", "a" * 100_000 + "\n"),
        ("srt", "1\n00:00:01,000 --> 00:00:02,000\n" + " " * 100_000 + "x\n"),
        ("vtt", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n" + "<v a" * 25_000 + "\n"),
        ("json", json.dumps({f"k{i}": i for i in range(50_000)} | {"segments": [{"text": "x"}]})),
    ],
)
def test_adapters_are_not_quadratic(name, text):
    t0 = time.perf_counter()
    with contextlib.suppress(Exception):  # only timing matters here
        adapters.get(name).parse(text, Limits(max_line_chars=1_000_000))
    assert time.perf_counter() - t0 < 3.0


@pytest.mark.parametrize("text", ["1," * 20_000, "1 " * 20_000, "five " * 20_000, "a " * 20_000])
def test_normaliser_is_not_quadratic(text):
    t0 = time.perf_counter()
    normalize(text)
    assert time.perf_counter() - t0 < 3.0


# Unusual Unicode digits.
@pytest.mark.parametrize(
    "text", ["\\u2776 thousand dollars", "\\u1369 hundred people", "\\u0663 point \\u2776 million"]
)
def test_unicode_digits_do_not_crash(text):
    normalize(text.encode().decode("unicode_escape"))


def test_non_ascii_decimal_digits_fold_to_ascii():
    assert (
        normalize(b"\\u0665\\u0665\\u0665-\\u0661\\u0662\\u0661\\u0662".decode("unicode_escape"))[0]
        == "555-1212"
    )


# Normalisation: accents, invisible characters, confusables.
def test_combining_accents_compose():
    decomposed = b"Jose\\u0301".decode("unicode_escape")
    assert normalize(decomposed)[0] == b"Jos\\u00e9".decode("unicode_escape")


@pytest.mark.parametrize("ch", ["2062", "034f", "fe0f", "180e", "2066", "202e", "200b", "00ad"])
def test_invisible_characters_removed(ch):
    hidden = f"Jo\\u{ch}hn".encode().decode("unicode_escape")
    assert normalize(hidden)[0] == "John"


def test_tag_characters_removed():
    assert normalize("Jo" + chr(0xE0041) + "hn")[0] == "John"


def test_mixed_script_confusables_folded():
    assert normalize(b"\\u0410cme".decode("unicode_escape"))[0] == "Acme"


def test_genuine_cyrillic_left_alone():
    word = b"\\u043f\\u0440\\u0438\\u0432\\u0435\\u0442".decode("unicode_escape")
    assert normalize(word)[0] == word


# Bracketed-timestamp txt is detected as txt.
def test_bracket_timestamp_txt_detected_as_txt():
    assert adapters.detect("[00:00:01] Orla: hi\n[00:00:02] Bram: yo\n").name == "txt"


# Lone surrogates.
def test_json_lone_surrogate_rejected():
    from offrecord.errors import InputFormatError

    with pytest.raises(InputFormatError):
        adapters.get("json").parse('{"segments": [{"text": "a\\ud800b"}]}', L)
