"""Adapter tests. All transcript content is synthetic (fake people and companies)."""

from __future__ import annotations

import json

import pytest

from offrecord import adapters
from offrecord.adapters._common import speaker_key
from offrecord.errors import InputFormatError, InputLimitError
from offrecord.limits import Limits
from offrecord.model import Document

CANARY = "ZQXCANARY7731"
L = Limits()


def parse(name: str, text: str) -> Document:
    return adapters.get(name).parse(text, L)


# --- txt ---------------------------------------------------------------------------------------

TXT = """Orla Venkatesh: Thanks for joining. Can everyone hear me?
Bram Okafor: Yes. So about the Quillfeather renewal,
it's due in March.

[00:01:05] Orla Venkatesh: Right, fifty thousand dollars ARR.
Bram Okafor (00:01:09): Agreed.
"""


def test_txt_speakers_and_continuation():
    doc = parse("txt", TXT)
    assert [t.speaker for t in doc.turns] == [
        "Orla Venkatesh", "Bram Okafor", "Orla Venkatesh", "Bram Okafor"
    ]  # fmt: skip
    assert doc.turns[1].text == "Yes. So about the Quillfeather renewal,\nit's due in March."
    assert doc.turns[2].start == 65.0
    assert doc.turns[3].start == 69.0
    assert doc.turns[1].source_lines == (2, 3)


def test_txt_header_layout():
    doc = parse("txt", "Orla Venkatesh  0:03\nHello there.\n\nBram Okafor  0:07\nHi.\n")
    assert [t.speaker for t in doc.turns] == ["Orla Venkatesh", "Bram Okafor"]
    assert doc.turns[0].text == "Hello there."
    assert doc.turns[1].start == 7.0


def test_txt_one_off_colon_is_not_a_speaker():
    doc = parse("txt", "Speaker 1: hi\nHere is the thing: we need it\nSpeaker 1: ok\n")
    assert doc.turns[0].speaker == "Speaker 1"
    assert "Here is the thing: we need it" in doc.turns[0].text


def test_txt_lowercase_and_caps():
    for text in (TXT.lower(), TXT.upper()):
        doc = parse("txt", text)
        assert len(doc.turns) == 4
        assert all(t.speaker for t in doc.turns)


def test_txt_round_trip_preserves_text():
    doc = parse("txt", TXT)
    again = parse("txt", adapters.get("txt").render(doc))
    assert [(t.speaker, t.text) for t in again.turns] == [(t.speaker, t.text) for t in doc.turns]


# --- vtt ---------------------------------------------------------------------------------------

VTT = """WEBVTT Kind: captions

NOTE recorded for Quillfeather

intro-1
00:00:01.000 --> 00:00:04.000 align:start
<v Orla Venkatesh>Hello &amp; welcome</v>

00:00:04.500 --> 00:00:06.000
<v.loud Bram Okafor><i>Thanks</i>, Orla.

00:00:06.500 --> 00:00:08.000
no speaker here
"""


def test_vtt_parse():
    doc = parse("vtt", VTT)
    assert [t.speaker for t in doc.turns] == ["Orla Venkatesh", "Bram Okafor", None]
    assert doc.turns[0].text == "Hello & welcome"
    assert doc.turns[1].text == "Thanks, Orla."
    assert doc.turns[0].start == 1.0 and doc.turns[0].end == 4.0
    assert doc.turns[0].extra["id"] == "intro-1"
    assert "id" in doc.free_text_extras
    assert doc.metadata["note_0"] == "NOTE recorded for Quillfeather"
    assert doc.metadata["header"] == "Kind: captions"


def test_vtt_label_style_speakers():
    text = (
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nOrla V: hi\n\n2\n"
        "00:00:02.000 --> 00:00:03.000\nBram O: hello\n\n3\n00:00:03.000 --> 00:00:04.000\n"
        "Orla V: bye\n\n4\n00:00:04.000 --> 00:00:05.000\nBram O: bye\n"
    )
    doc = parse("vtt", text)
    assert [t.speaker for t in doc.turns] == ["Orla V", "Bram O", "Orla V", "Bram O"]
    assert doc.turns[0].text == "hi"


def test_vtt_render_escapes_placeholders():
    doc = parse("vtt", VTT)
    t0 = doc.turns[0]
    from dataclasses import replace

    doc2 = replace(doc, turns=(replace(t0, text="hi <C1>"), *doc.turns[1:]))
    out = adapters.get("vtt").render(doc2)
    assert "&lt;C1&gt;" in out
    again = parse("vtt", out)
    assert again.turns[0].text == "hi <C1>"


def test_vtt_unparsed_block_kept_as_metadata():
    doc = parse("vtt", "WEBVTT\n\nthis is not a cue\nbut has text\n")
    assert list(doc.metadata.values()) == ["this is not a cue\nbut has text"]


def test_vtt_round_trip():
    doc = parse("vtt", VTT)
    again = parse("vtt", adapters.get("vtt").render(doc))
    assert [(t.speaker, t.text, t.start) for t in again.turns] == [
        (t.speaker, t.text, t.start) for t in doc.turns
    ]
    assert set(again.metadata.values()) == set(doc.metadata.values())


# --- srt ---------------------------------------------------------------------------------------

SRT = """1
00:00:01,000 --> 00:00:03,000
ORLA: Morning all.

2
00:00:03,500 --> 00:00:05,000
BRAM: Morning.

3
00:00:05,000 --> 00:00:07,000
ORLA: Let's start.

4
00:00:07,000 --> 00:00:08,000
[Narrator] <i>Later</i>
"""


def test_srt_parse_and_round_trip():
    doc = parse("srt", SRT)
    assert [t.speaker for t in doc.turns] == ["ORLA", "BRAM", "ORLA", "Narrator"]
    assert doc.turns[3].text == "Later"
    assert doc.turns[1].start == 3.5
    again = parse("srt", adapters.get("srt").render(doc))
    assert [(t.speaker, t.text) for t in again.turns] == [(t.speaker, t.text) for t in doc.turns]


def test_srt_non_numeric_first_line_is_kept():
    doc = parse("srt", "Quillfeather call\n00:00:01,000 --> 00:00:02,000\nhello\n")
    assert "Quillfeather call" in doc.turns[0].text


# --- json --------------------------------------------------------------------------------------

WHISPER = {
    "text": " Hello Orla. Call me at five five five.",
    "language": "en",
    "segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 2.0,
            "text": " Hello Orla.",
            "tokens": [50364, 2425, 1, 2, 3, 4, 5, 6, 7],
            "words": [{"word": "Hello"}, {"word": "Orla."}],
            "avg_logprob": -0.2,
        },
        {
            "id": 1,
            "start": 2.0,
            "end": 4.0,
            "text": " Call me at five five five.",
            "speaker": "SPEAKER_01",
            "client_ref": "Quillfeather",
        },
    ],
}


def test_json_whisper():
    doc = parse("json", json.dumps(WHISPER))
    assert [t.text for t in doc.turns] == [" Hello Orla.", " Call me at five five five."]
    assert doc.turns[1].speaker == "SPEAKER_01"
    assert doc.turns[0].start == 0.0 and doc.turns[1].end == 4.0
    assert "Quillfeather" in doc.metadata.values()
    assert "en" in doc.metadata.values()
    # Metadata names are opaque counters, never JSON paths.
    assert all(k.startswith("json:") for k in doc.metadata)


def test_json_render_drops_word_and_token_arrays():
    doc = parse("json", json.dumps(WHISPER))
    out = json.loads(adapters.get("json").render(doc))
    seg0 = out["segments"][0]
    assert "words" not in seg0 and "tokens" not in seg0
    assert seg0["avg_logprob"] == -0.2
    assert out["text"] == "Hello Orla. Call me at five five five."
    assert out["segments"][1]["client_ref"] == "Quillfeather"


def test_json_simple_array_and_nested():
    arr = [{"speaker": "Orla", "text": "hi"}, {"speaker": "Bram", "text": "yo", "start": "1.5"}]
    doc = parse("json", json.dumps(arr))
    assert [t.speaker for t in doc.turns] == ["Orla", "Bram"]
    nested = {"results": {"utterances": arr}}
    doc = parse("json", json.dumps(nested))
    assert [t.text for t in doc.turns] == ["hi", "yo"]


def test_json_unknown_keys_are_scanned():
    data = {"Quillfeather Account": {"x": 1}, "segments": [{"text": "hi"}]}
    doc = parse("json", json.dumps(data))
    assert "Quillfeather Account" in doc.metadata.values()


def test_json_numbers_are_scanned_as_text():
    data = {"account_number": 12345678901234567890, "segments": [{"text": "hi"}]}
    doc = parse("json", json.dumps(data))
    assert "12345678901234567890" in doc.metadata.values()


def test_json_render_redacted_number_becomes_string():
    data = {"acct": 1234, "segments": [{"text": "hi"}]}
    doc = parse("json", json.dumps(data))
    from dataclasses import replace

    key = next(k for k, v in doc.metadata.items() if v == "1234")
    doc2 = replace(doc, metadata={**doc.metadata, key: "<ACCT1>"})
    out = json.loads(adapters.get("json").render(doc2))
    assert out["acct"] == "<ACCT1>"


def test_json_depth_limit():
    deep = "[" * 100 + "]" * 100
    with pytest.raises(InputLimitError):
        adapters.get("json").parse(deep, Limits(max_json_depth=64))


def test_json_huge_nesting_does_not_crash():
    deep = "[" * 100_000 + "]" * 100_000
    with pytest.raises((InputLimitError, InputFormatError)):
        parse("json", deep)


def test_json_no_segments():
    with pytest.raises(InputFormatError):
        parse("json", json.dumps({"a": 1}))


# --- detection, limits, canaries -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "name"),
    [(VTT, "vtt"), (SRT, "srt"), (json.dumps(WHISPER), "json"), (TXT, "txt")],
)
def test_detect(text, name):
    assert adapters.detect(text).name == name


def test_detect_extension_breaks_ties():
    assert adapters.detect("hello", "x.txt").name == "txt"


@pytest.mark.parametrize("name", ["txt", "vtt", "srt"])
def test_turn_count_limit(name):
    texts = {
        "txt": "A: x\nB: y\nA: z\nB: w\n",
        "vtt": "WEBVTT\n\n"
        + "".join(f"00:00:0{i}.000 --> 00:00:0{i}.500\nx\n\n" for i in range(4)),
        "srt": "".join(f"{i}\n00:00:0{i},000 --> 00:00:0{i},500\nx\n\n" for i in range(1, 5)),
    }
    with pytest.raises(InputLimitError):
        adapters.get(name).parse(texts[name], Limits(max_turns=3))


def test_turn_length_limit():
    with pytest.raises(InputLimitError) as ei:
        adapters.get("txt").parse("A: " + CANARY * 10 + "\n", Limits(max_turn_chars=20))
    assert CANARY not in str(ei.value)


@pytest.mark.parametrize(
    "bad",
    [
        '{"segments": [{"text": "' + CANARY + '"}',  # truncated
        '{"' + CANARY + '": ',
        "[" + CANARY + "]",
    ],
)
def test_json_errors_have_no_content(bad):
    with pytest.raises(InputFormatError) as ei:
        parse("json", bad)
    assert CANARY not in str(ei.value)


def test_speaker_key_unification():
    assert speaker_key("JOHN SMITH") == speaker_key("John Smith:") == speaker_key("john  smith")
    assert speaker_key("SPEAKER_01") == "speaker 1"


def test_plugins_are_opt_in(monkeypatch):
    calls = []

    class FakeEP:
        name = "evil"

        def load(self):
            calls.append(1)
            raise AssertionError("must not load")

    monkeypatch.setattr(adapters.metadata, "entry_points", lambda group: [FakeEP()])
    adapters.load_plugins([])  # nothing enabled: nothing loaded
    assert calls == []
