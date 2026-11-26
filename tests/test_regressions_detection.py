"""Regression tests for detection, placeholders and config parsing. Synthetic data."""

from __future__ import annotations

import json
import time

import pytest

from offrecord import adapters
from offrecord.errors import UsageError
from offrecord.limits import Limits
from offrecord.redact.config import from_mapping
from offrecord.redact.pipeline import redact_document


def run(text: str, cfg: dict | None = None, fmt: str = "txt"):
    doc = adapters.get(fmt).parse(text, Limits(max_line_chars=1_000_000))
    red, _spans, mapping, _ = redact_document(doc, from_mapping(cfg or {}))
    return red, mapping


def body(red) -> str:
    return (
        "\n".join(f"{t.speaker or ''}: {t.text}" for t in red.turns)
        + "\n"
        + json.dumps(dict(red.metadata))
    )


# Common-word names after strong cues / titles, bigrams, initials, speaker parts.
@pytest.mark.parametrize(
    ("line", "secret"),
    [
        ("A: My name is Frank White.", "Frank"),
        ("A: please ask Ms. Lee about it", "Lee"),
        ("A: Dr Smith signed it", "Smith"),
        ("A: Sgt. Brown called", "Brown"),
        ("A: Judge Hill ruled", "Hill"),
        ("A: Father Grant is here", "Grant"),
        ("A: I spoke with Mark Brown yesterday", "Mark Brown"),
        ("A: J. R. Brown wrote back", "Brown"),
    ],
)
def test_common_word_names(line, secret):
    red, _ = run(line + "\nA: ok\n")
    assert secret not in body(red)


def test_common_word_speaker_first_name_in_text():
    red, _ = run("Mark Brown: hello\nAda Quist: Sure, Mark.\n")
    assert "Mark" not in red.turns[1].text


def test_deny_person_first_name_alone():
    cfg = {"denylist": [{"name": "Marigold Thistlewaite", "category": "PERSON"}]}
    red, _ = run("A: ask Marigold today\nA: ok\n", cfg)
    assert "Marigold" not in red.turns[0].text


# Generic speaker labels must not swallow names.
@pytest.mark.parametrize("label", ["Agent Bob", "Host Kim", "Guest Lin"])
def test_titled_speaker_labels_are_names(label):
    red, _ = run(f"{label}: hi\n{label}: ok\n")
    name = label.split()[1]
    assert name not in body(red)


# Trigger in an earlier turn; longer interruption mid-number.
def test_trigger_in_earlier_turn():
    red, _ = run("A: my account number is\nB: mm-hmm\nA: 4471\n")
    assert "4471" not in red.turns[2].text


def test_split_card_with_longer_interruption():
    red, _ = run(
        "A: card is 4111 1111\nB: sorry could you repeat that a bit slower please\nA: 1111 1111\n"
    )
    assert "1111 1111" not in red.turns[2].text


# Corrected digits after a correction cue.
@pytest.mark.parametrize(
    "line",
    ["A: my number is 555 0100 sorry 0199", "A: account number is 4471, sorry, 4472"],
)
def test_corrected_value_redacted(line):
    red, _ = run(line + "\nA: ok\n")
    t = red.turns[0].text
    assert "0199" not in t and "4472" not in t


# Addresses, postcodes and ID formats.
@pytest.mark.parametrize(
    ("line", "secret"),
    [
        ("A: send it to 221 Baker Street please", "221 Baker"),
        ("A: we are at 14 Oak Road, Flat 3", "Oak Road"),
        ("A: postcode LS1 4AB", "LS1 4AB"),
        ("A: it's Springfield, IL 62704", "62704"),
        ("A: zip is 90210", "90210"),
        ("A: call +33 1 23 45 67 89", "45 67 89"),
        ("A: call +91 98765 43210", "98765"),
        ("A: licence MORGA753116SM9IJ", "753116"),
        ("A: VIN 1HGCM82633A004352", "82633"),
        ("A: NI number QQ 12 34 56 C", "12 34 56"),
        ("A: sort code 12-34-56", "12-34-56"),
        ("A: IBAN GB82 WEST 1234 5698 7654 32", "WEST"),
        ("A: my PIN is 4821", "4821"),
        ("A: the CVV is 123", "123"),
        ("A: expires 12/29", "12/29"),
        ("A: MAC 00:1A:2B:3C:4D:5E", "4D:5E"),
        ("A: user zquux_1987 logged in", "1987"),
    ],
)
def test_ids_and_addresses(line, secret):
    red, _ = run(line + "\nA: ok\n")
    assert secret not in red.turns[0].text


def test_address_split_across_turns():
    red, _ = run("A: it's one two three\nB: mm-hmm\nA: Wobble Lane\n")
    assert "Wobble" not in red.turns[2].text


# Placeholder resolution.
def test_partial_token_match_does_not_adopt_other_entity():
    red, _m = run("Ada Quist: hi\nBo Zank: I met Ada Brownfield and Zelda Quist\n")
    assert red.turns[1].text.count(red.turns[0].speaker) == 0


def test_fuzzy_match_does_not_register_surface_words():
    cfg = {"denylist": [{"name": "Quillfeather Dynamics", "category": "CLIENT"}]}
    red, _ = run(
        "A: The Quillfeather Dynamics Group called\nA: Zorblat Group and Wexmoor Group\n", cfg
    )
    t = red.turns[1].text
    phs = [w for w in t.split() if w.startswith("<")]
    assert len(set(phs)) >= 2 or not phs


def test_written_and_spoken_email_share_placeholder():
    _red, m = run("A: zeb.quux@example.com or zeb dot quux at example dot com\nA: ok\n")
    emails = [p for p, e in m.entries.items() if e.category == "EMAIL"]
    assert len(emails) == 1


# Strict config parsing.
def test_quoted_false_is_false():
    cfg = from_mapping(
        {"denylist": [{"name": "Harbor", "category": "BRAND", "common_word": "false"}]}
    )
    assert cfg.denylist[0].common_word is False


@pytest.mark.parametrize(
    "bad",
    [
        {"topics": {"redact": "merger"}},
        {"denylist": [{"name": "X", "context": {"a": 1}}]},
        {"denylist": [{"name": "X", "common_word": "maybe"}]},
    ],
)
def test_bad_config_types_rejected(bad):
    with pytest.raises(UsageError):
        from_mapping(bad)


@pytest.mark.parametrize(
    ("bad", "path", "hint"),
    [
        ({"protekt": "confidential"}, "protekt", "protect"),
        ({"topics": {"mod": "redact"}}, "topics.mod", "mode"),
        ({"detect": {"date": False}}, "detect.date", "dates"),
        ({"models": {"thread": 2}}, "models.thread", "threads"),
        ({"denylist": [{"name": "Zandrelcorp", "aliass": "ZC"}]}, "denylist[0].aliass", "aliases"),
    ],
)
def test_misspelt_config_keys_named_with_suggestion(bad, path, hint):
    with pytest.raises(UsageError) as ei:
        from_mapping(bad)
    assert f"unknown config key: {path} (did you mean {hint}?)" in str(ei.value)
    assert "Zandrelcorp" not in str(ei.value) and "ZC" not in str(ei.value)


@pytest.mark.parametrize(
    "bad",
    [
        {"Zandrelcorp Holdings": "CLIENT"},  # a deny-list name typed at the top level
        {"denylist": [{"Zandrelcorp Holdings": "CLIENT"}]},  # entry without "name:"
        {"models": {"zandrelcorp": 1}},
        {"detect": {7: True}},
    ],
)
def test_unknown_config_keys_rejected_without_echo(bad):
    with pytest.raises(UsageError) as ei:
        from_mapping(bad)
    msg = str(ei.value)
    assert "unknown config key (key 1 " in msg
    assert "zandrelcorp" not in msg.lower()


# Performance cliffs.
@pytest.mark.parametrize(
    "text",
    ["A: " + "1 " * 20_000 + "\nA: ok\n", "A: " + "sales 7, " * 8_000 + "\nA: ok\n"],
)
def test_no_quadratic_cliffs(text):
    t0 = time.perf_counter()
    run(text)
    assert time.perf_counter() - t0 < 10.0


# Plurals of common-word entries.
def test_plural_of_common_word_brand():
    cfg = {"denylist": [{"name": "Lantern", "category": "BRAND", "common_word": True,
                         "context": ["lamps"]}]}  # fmt: skip
    red, _ = run("A: Lanterns lamps are on sale\nA: ok\n", cfg)
    assert "Lanterns" not in red.turns[0].text


# Escaping is one-to-one.
def test_escaping_backslash_form():
    from offrecord.redact.replace import escape_placeholder_like
    from offrecord.restore import restore_text

    bs = chr(92)
    src = f"a <{bs}C3> b"
    esc, n = escape_placeholder_like(src)
    assert esc == f"a <{bs}{bs}C3> b" and n == 1
    payload = {"entries": {"<C3>": {"occurrences": ["Zorblat"]}}}
    assert restore_text(esc, payload, "id")[0] == src


# JSON key collisions.
def test_json_keys_colliding_after_redaction_are_kept():
    data = {"Zelda Quuxworth": "a", "zelda quuxworth": "b", "segments": [{"text": "hi"}]}
    red, _ = run(json.dumps(data), fmt="json")
    out = json.loads(adapters.get("json").render(red))
    assert len([k for k in out if k != "segments"]) == 2


# Common-word names stored alone in JSON metadata.
def test_short_json_value_with_given_name():
    data = {"attendee": "Mark", "segments": [{"text": "hi"}]}
    red, _ = run(json.dumps(data), fmt="json")
    assert "Mark" not in json.dumps(dict(red.metadata))
