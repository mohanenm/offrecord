"""Dictated identifiers and emails (synthetic phrases only)."""

import pytest

from offrecord import adapters
from offrecord.limits import Limits
from offrecord.model import turn_field_id
from offrecord.redact.config import from_mapping
from offrecord.redact.pipeline import detect_document


def covered(text: str) -> set[int]:
    doc = adapters.get("txt").parse(f"A: {text}\nA: ok\n", Limits())
    out: set[int] = set()
    for sp in detect_document(doc, from_mapping({})):
        if sp.detector.startswith("patterns.") and sp.field == turn_field_id(0, "text"):
            out.update(range(sp.start, sp.end))
    return out


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("BOL is B O L dash seven seven four one nine.", "B O L dash seven seven four one nine"),
        ("ORDER NUMBER IS SO 4471 9930, THANKS.", "SO 4471 9930"),
        ("Exactly. Two six whiskey lima, zero zero four, eight eight, one seven.",
         "Two six whiskey lima, zero zero four, eight eight, one seven"),
        ("The review on matter 2026-0417, the cold storage one.", "2026-0417"),
        ("The PO will be PO 4500 7731 22.", "PO 4500 7731 22"),
        ("An inspection notice for site oh one four.", "oh one four"),
        ("Her email is yusra.bell at fenmoor dash analytics dot example.",
         "yusra.bell at fenmoor dash analytics dot example"),
        ("Copy in kwame dot okoro plus wedding at inbox dot example.",
         "kwame dot okoro plus wedding at inbox dot example"),
        ("B DOT TREMAYNE, T R E M A Y N E?", "B DOT TREMAYNE"),
    ],
)  # fmt: skip
def test_dictated_found(text, secret):
    i = text.index(secret)
    assert set(range(i, i + len(secret))) <= covered(text)


def test_plain_sentence_not_an_id():
    assert not covered("I think we should meet at the office at ten.")


@pytest.mark.parametrize(
    ("line", "secret"),
    [
        ("Two six W L, was it, like W as in whiskey L as in lima?", "W as in whiskey L as in lima"),
        ("The code is r, u, m, p, seven.", "r, u, m, p, seven"),
        ("Use the one ending 8605.", "8605"),
        ("The checking account ending 3317 had a transfer.", "3317"),
        ("482917, perfect, that matches.", "482917"),
        ("Call 617-555-0142 extension 31 tomorrow.", "617-555-0142 extension 31"),
        ("And the area code is 412.", "412"),
    ],
)
def test_spelled_ids_last_digits_codes_and_extensions(line, secret):
    from offrecord.redact.pipeline import redact_document

    doc = adapters.get("txt").parse(f"A: {line}\nB: ok\n", Limits())
    red = redact_document(doc, from_mapping({}))[0]
    text = red.turns[0].text
    # No piece of the value may stay readable (whole words; "as in" is filler).
    words = set(text.replace(",", " ").replace(".", " ").replace("?", " ").split())
    assert not words & {p for p in secret.replace(",", " ").split() if p not in ("as", "in")}


@pytest.mark.parametrize(
    ("line", "secret"),
    [
        ("In a browser, go to one ninety-two dot zero dot two dot one.", "ninety-two"),
        ("Your temporary password is hunter green seven seven all lower case.", "hunter green"),
        ("The password for her visitors is blue lantern forty. Anyone else?", "lantern"),
        ("The barrier code is star, two, five, eight, hash.", "star, two, five"),
        ("Seven one four.", "Seven one four"),
        ("Email it to w underscore tarrant forty-one at quarrymail dot example.", "tarrant"),
        (
            "Ship to nine oh five maple hollow road apartment twelve c lindenfield ohio four three "
            "oh two six thanks",
            "lindenfield ohio",
        ),
    ],
)
def test_spoken_identifiers_secrets_emails_and_addresses(line, secret):
    from offrecord.redact.pipeline import redact_document

    doc = adapters.get("txt").parse(f"Orla: {line}\nBram: ok\n", Limits())
    assert secret not in redact_document(doc, from_mapping({}))[0].turns[0].text


def test_plain_words_before_a_spoken_address_are_not_an_email():
    from offrecord.redact.pipeline import redact_document

    doc = adapters.get("txt").parse(
        "A: check the outage status any time at home dot test\n", Limits()
    )
    # Only "any time at home dot test" is taken for a spoken email, never the whole clause.
    assert "check the outage status" in redact_document(doc, from_mapping({}))[0].turns[0].text
