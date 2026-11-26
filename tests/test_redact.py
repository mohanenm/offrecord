"""Tier 0 detectors, merger and replacer. All data synthetic."""

from __future__ import annotations

import pytest

from offrecord import adapters
from offrecord.limits import Limits
from offrecord.model import Span, turn_field_id
from offrecord.redact.config import from_mapping
from offrecord.redact.detectors.patterns import luhn_ok
from offrecord.redact.merge import merge
from offrecord.redact.pipeline import detect_document, redact_document
from offrecord.redact.replace import Mapping, escape_placeholder_like

CFG = {
    "denylist": [
        {
            "name": "Quillfeather Analytics",
            "category": "CLIENT",
            "aliases": ["Quillfeather", "QFA"],
        },
        {"name": "Lantern", "category": "BRAND", "common_word": True, "context": ["app", "store"]},
        {"name": "Project Bluefin", "category": "CODENAME", "aliases": ["Bluefin"]},
    ],
    "protect": "confidential",  # these tests cover detection; the pii policy is tested below
}


def redact(text: str, cfg=CFG, fmt="txt"):
    doc = adapters.get(fmt).parse(text, Limits())
    red, _spans, mapping, _stats = redact_document(doc, from_mapping(cfg))
    return red, mapping


def body(red) -> str:
    return "\n".join(f"{t.speaker}: {t.text}" for t in red.turns)


@pytest.mark.parametrize(
    ("line", "secret"),
    [
        ("Orla: email me at orla.v@quill.example please", "orla.v@quill.example"),
        ("Orla: call 415-555-0142 tomorrow", "555-0142"),
        ("Orla: my number is five five five oh one four two", "five five five oh one four two"),
        ("Orla: SSN 912-34-5678 on file", "912-34-5678"),
        ("Orla: card 4111 1111 1111 1111 expired", "4111 1111 1111 1111"),
        ("Orla: the box is 192.0.2.44 now", "192.0.2.44"),
        ("Orla: see https://quill.example/pricing for it", "quill.example/pricing"),
        ("Orla: account number 88123456 is locked", "88123456"),
        ("Orla: we did $42,000 last year", "42,000"),
        ("Orla: up 12% on plan", "12%"),
        ("Orla: we run 340 stores now", "340"),
        ("Orla: john dot smith at example dot com", "john dot smith at example dot com"),
    ],
)
def test_patterns_and_business(line, secret):
    red, _ = redact(line + "\nOrla: ok\n")
    assert secret not in body(red)


@pytest.mark.parametrize("case", [str, str.lower, str.upper])
def test_denylist_any_casing(case):
    red, _ = redact(case("Orla: the Quillfeather Analytics renewal and QFA again\nOrla: ok\n"))
    out = body(red).lower()
    assert "quillfeather" not in out and "qfa" not in out


def test_denylist_asr_split_and_misspelling():
    red, _ = redact("Orla: so quill feather wants a call and quilfeather agreed\nOrla: ok\n")
    out = body(red).lower()
    assert "quill feather" not in out and "quilfeather" not in out


def test_common_word_brand_needs_context():
    red, _ = redact("Orla: the lantern on my desk\nOrla: the Lantern app crashed\n")
    assert "lantern on my desk" in red.turns[0].text
    assert "Lantern" not in red.turns[1].text


def test_same_entity_same_placeholder_across_aliases():
    red, m = redact("Orla: Quillfeather Analytics called\nOrla: then QFA called again\n")
    ph = [p for p, e in m.entries.items() if e.category == "CLIENT"]
    assert len(ph) == 1
    assert ph[0] in red.turns[0].text and ph[0] in red.turns[1].text


def test_speaker_names_redacted_in_labels_and_text():
    red, _m = redact("Orla Venkatesh: hi Bram\nBram Okafor: hi Orla, and Okafor here\n")
    out = body(red)
    for name in ("Orla", "Venkatesh", "Bram", "Okafor"):
        assert name not in out
    # "Bram" in text and "Bram Okafor" as a speaker share a placeholder.
    assert red.turns[1].speaker in red.turns[0].text


def test_generic_speakers_kept():
    red, _ = redact("Speaker 1: hello\nSpeaker 2: hi\n")
    assert red.turns[0].speaker == "Speaker 1"


def test_split_phone_across_interruption():
    text = "Orla: my direct line is five five five\nBram: mm-hmm\nOrla: oh one four two thanks\n"
    red, _ = redact(text)
    assert "five five five" not in red.turns[0].text
    assert "oh one four two" not in red.turns[2].text


@pytest.mark.parametrize(
    ("mode", "weak"), [(None, "flag"), ("mixed", "flag"), ("default", "flag"), ("redact", "redact")]
)
def test_topic_terms_strong_always_redact_weak_by_mode(mode, weak):
    doc = adapters.get("txt").parse(
        "Orla: We are planning layoffs in March.\nOrla: Pricing is next.\n", Limits()
    )
    cfg = {"protect": "confidential", **({"topics": {"mode": mode}} if mode else {})}
    spans = detect_document(doc, from_mapping(cfg))
    t0 = [s for s in spans if s.field == turn_field_id(0, "text") and s.category == "TOPIC"]
    t1 = [s for s in spans if s.field == turn_field_id(1, "text") and s.category == "TOPIC"]
    assert t0 and t0[0].action == "redact"
    assert t1 and t1[0].action == weak


def test_unknown_words_redacted_as_names():
    red, _ = redact("Orla: we met Zorbatek yesterday\nOrla: ok\n")
    assert "Zorbatek" not in red.turns[0].text


def test_placeholder_like_text_is_escaped():
    red, _ = redact("Orla: the <C1> model and <p 2> too\nOrla: ok\n")
    assert "<\\C1>" in red.turns[0].text
    assert escape_placeholder_like("x <AMT3> y")[0] == "x <\\AMT3> y"


def test_metadata_and_cue_ids_scanned():
    vtt = (
        "WEBVTT\n\nNOTE call with Quillfeather Analytics\n\nqfa-intro\n"
        "00:00:01.000 --> 00:00:02.000\n<v Orla>hello\n"
    )
    red, _ = redact(vtt, fmt="vtt")
    assert "Quillfeather" not in str(dict(red.metadata))
    assert "qfa" not in red.turns[0].extra["id"].lower()


def test_merge_unions_partial_overlaps():
    fid = turn_field_id(0, "text")
    out = merge([Span(fid, 0, 5, "PERSON", "a", 0.9), Span(fid, 3, 10, "EMAIL", "b", 0.5)])
    assert len(out) == 1 and (out[0].start, out[0].end) == (0, 10)
    assert out[0].category == "EMAIL" and out[0].confidence == 0.5


def test_merge_keeps_flags_outside_redactions():
    fid = turn_field_id(0, "text")
    out = merge([Span(fid, 0, 5, "X", "a", 0.9), Span(fid, 1, 3, "TOPIC", "b", 0.5, action="flag"),
                 Span(fid, 8, 12, "TOPIC", "b", 0.5, action="flag")])  # fmt: skip
    assert [(s.start, s.action) for s in out] == [(0, "redact"), (8, "flag")]


def test_luhn():
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")


def test_mapping_round_trip_payload():
    _, m = redact("Orla Venkatesh: Quillfeather at 415-555-0142\nOrla Venkatesh: ok\n")
    m2 = Mapping.from_payload(m.to_payload())
    assert m2.entries.keys() == m.entries.keys() and m2.id == m.id


def test_mapping_ids_are_random_and_content_free():
    a, b = Mapping(), Mapping()
    assert a.id != b.id and a.id.startswith("or1-") and len(a.id) == 30


def test_place_and_org_names_expand():
    red, _ = redact("Orla: we moved to Port Wexmoor and signed Zandrel Freight\nOrla: ok\n")
    t = red.turns[0].text
    assert "Port" not in t and "Freight" not in t and "Wexmoor" not in t


def test_adjacent_unknown_name_words_share_one_placeholder():
    red, _ = redact("Orla: ask Zorbin Quaxley about it\nOrla: ok\n")
    assert red.turns[0].text.count("<") == 1


def test_self_correction_bridged():
    red, _ = redact(
        "Orla: it's five five five... sorry, five five six, oh one four two\nOrla: ok\n"
    )
    assert "sorry" not in red.turns[0].text


def test_spoken_number_starting_with_oh():
    red, _ = redact("Orla: the code is oh nine nine four\nOrla: ok\n")
    assert "nine" not in red.turns[0].text


def test_spoken_url_path():
    red, _ = redact("Orla: go to quill dot example slash pricing now\nOrla: ok\n")
    assert "pricing" not in red.turns[0].text


def test_topic_sentence_not_split_at_decimal():
    doc = adapters.get("txt").parse(
        "Orla: we are raising prices by 13.5 percent in May.\n", Limits()
    )
    cfg = from_mapping({"protect": "confidential"})
    spans = [s for s in detect_document(doc, cfg) if s.category == "TOPIC"]
    assert spans and spans[0].start == 0


def test_common_word_brand_with_generic_cue():
    red, _ = redact("Orla: thank you for calling Lantern support\nOrla: ok\n")
    assert "Lantern" not in red.turns[0].text


def test_shared_first_name_is_not_attributed_to_either_speaker():
    red, _ = redact("Wendell Abernathy: hey Wendell\nWendell Sandoval: hi there\n")
    a, b = red.turns[0].speaker, red.turns[1].speaker
    assert "Wendell" not in red.turns[0].text
    assert a not in red.turns[0].text and b not in red.turns[0].text


def test_cross_window_consistency():
    lines = [f"Orla Venkatesh: turn {i} about Quillfeather" for i in range(30)]
    doc = adapters.get("txt").parse("\n".join(lines) + "\n", Limits())
    cfg = from_mapping(CFG)
    cfg.window_turns, cfg.lookback_turns = 4, 1
    red, _spans, _mapping, _stats = redact_document(doc, cfg)
    phs = {t.text.split()[-1] for t in red.turns}
    assert len(phs) == 1 and len({t.speaker for t in red.turns}) == 1


def test_project_scope_shares_placeholders():
    m = Mapping(scope="project")
    out = []
    for text in ("Orla: Quillfeather called\nOrla: ok\n", "Bram: Quillfeather again\nBram: ok\n"):
        doc = adapters.get("txt").parse(text, Limits())
        red, _s, m, _st = redact_document(doc, from_mapping(CFG), m)
        out.append(red.turns[0].text.split()[0])
    assert out[0] == out[1]


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ("A: Good morning, Larchmere Family Health, how can I help?", "Larchmere Family Health"),
        ("A: She's represented by Pratt Ivey and Sola now.", "Pratt Ivey and Sola"),
        ("A: Zandrow and Sons can start Monday.", "Zandrow and Sons"),
        (
            "A: Thanks for calling Copperline Federal Credit Union.",
            "Copperline Federal Credit Union",
        ),
    ],
)
def test_org_names_expand_over_generic_words(line, name):
    red, _ = redact(line + "\nA: ok\n", cfg={})
    for word in name.split():
        if word != "and":
            assert word not in body(red)


@pytest.mark.parametrize(
    ("sentence", "personal"),
    [
        ("I was diagnosed with something serious last spring.", True),
        ("We're going through a divorce and there's a custody hearing.", True),
        ("Asset protection thinks it's internal theft.", True),
        ("It isn't public yet, so keep it off the minutes.", False),
        ("Their COO said they may not renew next year.", False),
    ],
)
def test_personal_topics_always_redacted_business_topics_by_protection(sentence, personal):
    text = f"A: Okay. {sentence} Anyway.\nA: ok\n"
    assert sentence not in body(redact(text, cfg={"protect": "confidential"})[0])
    pii = body(redact(text, cfg={})[0])
    assert (sentence not in pii) == personal  # business topics are only flagged by default
    assert "Anyway." in pii


@pytest.mark.parametrize(
    ("line", "place"),
    [
        ("We met on High Street by the bank.", "High Street"),
        ("I'm stuck in Fort Worth, traffic is bad.", "Fort Worth"),
        ("Traffic on I-30 is backed up past Route 9.", "I-30"),
        ("Wicker Park is our best store.", "Wicker Park"),
        ("THE YARD ON QUARRY ROAD IS OURS.", "QUARRY ROAD"),
    ],
)
def test_places_without_a_place_list(line, place):
    assert place not in body(redact(line)[0])


def test_months_after_place_cues_are_not_places():
    doc = adapters.get("txt").parse("Can we meet in March about it?", Limits())
    assert not any(s.category == "LOCATION" for s in detect_document(doc, from_mapping(CFG)))


def test_readability_counts_redacted_text_and_sentences():
    from offrecord.redact.pipeline import readability

    doc = adapters.get("txt").parse("A: Call Orla. It is fine.\n", Limits())
    fid = turn_field_id(0, "text")
    spans = [Span(fid, 0, 9, "PERSON", "t", 1.0), Span(fid, 10, 12, "X", "t", 1.0, action="flag")]
    assert readability(doc, spans) == (len("Call Orla. It is fine."), 9, 2, 1)


@pytest.mark.parametrize(
    ("line", "place", "is_place"),
    [
        ("traffic on i-30 is bad", "i-30", True),
        ("We will hear from Bo Kestrel Tuesday.", "Tuesday", False),
        ("Good point, let's move on.", "Good point", False),
        ("Our CEO joined late.", "CEO", False),
        ("Sales at our Leeds store doubled.", "Leeds", True),
        ("The deck is in English now.", "English", False),
        ("i live on tolliver street now", "tolliver street", True),
    ],
)
def test_place_rules_edges(line, place, is_place):
    doc = adapters.get("txt").parse(line, Limits())
    t = doc.turns[0].text
    i = t.index(place)
    spans = [s for s in detect_document(doc, from_mapping(CFG)) if s.action == "redact"]
    if is_place:  # redacted (a merged span may carry another name category)
        assert any(s.start <= i and i + len(place) <= s.end for s in spans)
    else:
        assert not any(s.category == "LOCATION" and s.start < i + len(place) and i < s.end
                       for s in spans)  # fmt: skip


def test_readability_ignores_spans_past_the_turn():
    from offrecord.redact.pipeline import readability

    doc = adapters.get("txt").parse("A: short\n", Limits())
    fid = turn_field_id(0, "text")
    chars, red, _, _ = readability(doc, [Span(fid, 0, 50, "X", "t", 1.0)])
    assert red == chars == len("short")


def test_topic_rules_work_on_lines_without_final_punctuation():
    doc = adapters.get("txt").parse(
        "Orla: we are planning the acquisition of the shop\nanyway moving on.\n", Limits()
    )
    spans = detect_document(doc, from_mapping(CFG))
    assert any(s.category == "TOPIC" and s.action == "redact" for s in spans)


def test_pii_protection_flags_business_context_but_keeps_names_inside_it():
    text = (
        "A: Dana Whitfield says the renewal is $42,000, margin up 12%, layoffs in Q3.\n"
        "A: Call 415-555-0142 on March 3rd.\n"
    )
    doc = adapters.get("txt").parse(text, Limits())
    spans = detect_document(doc, from_mapping({}))
    t0, t1 = doc.turns[0].text, doc.turns[1].text
    red0 = [t0[s.start : s.end] for s in spans if s.field[1] == "0" and s.action == "redact"]
    flagged = {s.category for s in spans if s.action == "flag"}
    assert any("Dana Whitfield" in r for r in red0)  # a name inside a flagged sentence
    assert not any(x in r for r in red0 for x in ("42,000", "12%", "Q3", "layoffs"))
    assert {"AMOUNT", "PERCENT", "DATE", "TOPIC"} <= flagged
    red1 = {t1[s.start : s.end] for s in spans if s.field[1] == "1" and s.action == "redact"}
    assert {"415-555-0142", "March 3rd"} <= red1  # contact details and day-level dates


def test_deny_list_entries_always_redact():
    red, _ = redact("A: The Quillfeather renewal moved.\nA: ok\n", cfg={**CFG, "protect": "pii"})
    assert "Quillfeather" not in body(red)


@pytest.mark.parametrize(
    ("text", "name", "redacted"),
    [
        ("A: Yesterday Grace took the Friday shift.\nB: ok\n", "Grace", True),  # mid-sentence
        ("A: Grace is a nice word.\nB: ok\n", "Grace", False),  # sentence start: unknown
        ("A: I hope so, will do.\nB: ok\n", "hope", False),  # ordinary words stay
        (
            "Mark: I can drive.\nOrla: thanks, mark can take the van.\n",
            "mark",
            True,
        ),  # lower-case repeat of a speaker
    ],
)
def test_names_that_are_ordinary_words(text, name, redacted):
    red, _ = redact(text, cfg={})
    turn_texts = " ".join(t.text for t in red.turns)
    assert (name not in turn_texts) == redacted


def test_address_includes_unit_city_and_postcode():
    red, _ = redact("A: Ship it to 1460 West Fulton Street, Suite 300, Chicago, IL 60607 please.\n")
    out = body(red)
    assert not any(x in out for x in ("1460", "Suite 300", "Chicago", "60607"))
    assert "please." in out


def test_titles_are_redacted_with_the_name():
    red, _ = redact("A: Yeah, Dr. Haldane signed it.\nA: My name is Orla.\n", cfg={})
    out = body(red)
    assert "Dr." not in out and "Haldane" not in out
    assert "My name is" in out


@pytest.mark.parametrize(
    ("text", "name", "redacted"),
    [
        ("A: ok so tell mark the van is ready\nB: ok\n", "mark", True),
        ("A: Hope said the Friday shift is fine.\nB: ok\n", "Hope", True),
        ("A: thanks grace, see you then\nB: ok\n", "grace", True),
        ("A: I hope the van is ready.\nB: ok\n", "hope", False),
    ],
)
def test_ordinary_word_names_next_to_person_words(text, name, redacted):
    red, _ = redact(text, cfg={})
    words = " ".join(t.text for t in red.turns).replace(",", " ").split()
    assert (name not in words) == redacted


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("the general manager approved the budget\nin general the manager is happy", "in general"),
        ("i was talking to miss okafor\nwe cannot miss the deadline", "cannot miss the deadline"),
        ("Dr. Haldane approved it.\nApproved items go out today.", "Approved items"),
        ("A: The code is ready.\n", "The code is ready."),
        ("A: The area code is a mess.\n", "The area code is a mess."),
        ("A: The dress code is business casual.\n", "business casual"),
        ("A: The password is required.\n", "The password is required."),
        ("A: Our office is 12 Harbor Road and we cover it.\n", "and we cover it"),
        ("A: The quarter ending 2026 was good.\n", "ending 2026"),
        ("A: Customers arrive at the front desk dash in and out all day.\n", "front desk dash in"),
        ("A: The version is two dot four dot one dot seven.\n", "two dot four"),
        ("A: The Pound took a hit.\n", "The Pound"),
        ("A: Crystal Reports is slow.\n", "Crystal Reports"),
        ("A: Revenue hit 4500, up from 3900.\n", "4500"),
        ("A: Targets for Q4 FY25 are set.\n", "Q4 FY25"),
    ],
)
def test_everyday_speech_is_not_taken_for_pii(text, kept):
    red, _ = redact(text, cfg={})
    assert kept in " ".join(t.text for t in red.turns)


@pytest.mark.parametrize(
    ("text", "phrase", "action"),
    [
        ("A: The patient in bed twelve asked again.\n", "patient in bed twelve", "redact"),
        (
            "A: Ask our only Tagalog-speaking assistant.\n",
            "only Tagalog-speaking assistant",
            "redact",
        ),
        ("A: My husband will drive.\n", "My husband", "redact"),
        ("A: The vendor that went bust owes us.\n", "vendor that went bust", "flag"),
        ("A: The only way to fix it is a reboot.\n", None, None),
        ("A: The first thing we need is coffee.\n", None, None),
    ],
)
def test_indirect_identifiers(text, phrase, action):
    doc = adapters.get("txt").parse(text, Limits())
    spans = [s for s in detect_document(doc, from_mapping({})) if s.category == "INDIRECT"]
    if phrase is None:
        assert not spans
        return
    t = doc.turns[0].text
    hit = [s for s in spans if phrase in t[s.start : s.end]]
    assert hit and hit[0].action == action
