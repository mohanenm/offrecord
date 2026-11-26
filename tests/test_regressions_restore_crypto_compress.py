"""Regression tests for restore, the encrypted mapping and key files, compression and the CLI
output paths. Synthetic data only."""

from __future__ import annotations

import json
import os
import stat
import time

import pytest

from offrecord import crypto
from offrecord.cli import main
from offrecord.compress import clean_text, compress
from offrecord.errors import ExitCode, MappingError, UsageError
from offrecord.model import Document, Turn
from offrecord.redact.replace import escape_placeholder_like
from offrecord.restore import bare_collisions, restore_text

BS = chr(92)
P = {
    "entries": {
        "<P1>": {"occurrences": ["Alice Zorblatt"]},
        "<P2>": {"occurrences": ["Zed Fakename"]},
        "<L1>": {"occurrences": ['Suite P2, 9 "Fake" Lane']},
    },
    "bare_collisions": ["P1"],
}


def doc(*turns):
    return Document("txt", tuple(Turn(i, s, t) for i, (s, t) in enumerate(turns)))


# Bare-token normalisation.
ARABIC_ONE = chr(0x661)


@pytest.mark.parametrize("text", ["room P01", f"room C{ARABIC_ONE}"])
def test_noncanonical_bare_tokens_never_restored(text):
    assert restore_text(text, P, "id")[0] == text


def test_bare_collisions_use_restore_grammar():
    assert bare_collisions(["room P1 and P01"], ["<P1>"]) == ["P1"]


# No double substitution.
def test_restored_values_are_not_restored_again():
    out, _ = restore_text("<L1> and <P2>", P, "id")
    assert out == 'Suite P2, 9 "Fake" Lane and Zed Fakename'


# Escaping covers every form restore accepts; compression cannot create placeholders.
@pytest.mark.parametrize("src", ["&lt;P1&gt; here", "<P1>", f"<{BS}P1>", f"{BS}u003cP1{BS}u003e"])
def test_source_placeholder_forms_round_trip_literally(src):
    esc, _ = escape_placeholder_like(src)
    assert restore_text(esc, P, "id")[0] == src


def test_compression_cannot_create_a_placeholder():
    assert clean_text("< um P1> stays") == "< um P1> stays"


# No catastrophic backtracking in light compression.
def test_light_compression_linear_on_hostile_line():
    t0 = time.perf_counter()
    compress(doc(("A", "a'" * 12_000 + " x- y")), "light")
    assert time.perf_counter() - t0 < 2.0


# Crafted KDF parameters are refused before any expensive work.
def test_crafted_scrypt_parameters_refused():
    pw = crypto.Secret("passphrase", b"correct horse battery staple")
    blob = crypto.encrypt("map", "or1-x", {"entries": {}}, pw, scrypt_n=2**14)
    hdr = crypto.read_header(blob, "map")
    hdr["kdf"].update(n=2**22, r=32, p=16)
    first, _, rest = blob.split(b"\n", 2)
    evil = first + b"\n" + json.dumps(hdr).encode() + b"\n" + rest
    t0 = time.perf_counter()
    with pytest.raises(MappingError):
        crypto.decrypt(evil, "map", pw)
    assert time.perf_counter() - t0 < 1.0


def _with_header(blob: bytes, first: bytes | None = None, header: bytes | None = None) -> bytes:
    old_first, old_header, rest = blob.split(b"\n", 2)
    return (first or old_first) + b"\n" + (header or old_header) + b"\n" + rest


def _scrypt_header(**params: object) -> bytes:
    kdf = {"name": "scrypt", "salt": "AAAAAAAAAAAAAAAAAAAAAA==", **params}
    return json.dumps({"id": "or1-x", "kdf": kdf}).encode()


@pytest.mark.parametrize(
    ("first", "header"),
    [
        (b"OFFRECORD-MAP x", None),  # version not a number
        (b"OFFRECORD-MAP " + chr(0x661).encode(), None),  # non-ASCII digit
        (b"OFFRECORD-MAP " + b"9" * 5000, None),  # too many digits for int()
        (b"OFFRECORD-MAP 1 2", None),
        (None, b'{"id": "or1-x", "kdf": "scrypt"}'),  # kdf not a mapping
        (None, b'{"id": "or1-x", "kdf": ["scrypt"]}'),
        (None, b'{"id": "or1-x", "kdf": null}'),
        (None, b'{"id": 7, "kdf": {}}'),
        (None, b'{"id": "or1-x"}'),
        (None, b"[1, 2]"),
        (None, b"[" * 100_000),
        (None, b"\xff\xfe"),
        (None, _scrypt_header()),  # parameters missing
        (None, _scrypt_header(n="lots", r=8, p=1)),
        (None, _scrypt_header(n=[1], r=8, p=1)),
        (None, _scrypt_header(n=float("inf"), r=8, p=1)),
    ],
)
def test_malformed_mapping_headers_are_mapping_errors(first, header):
    pw = crypto.Secret("passphrase", b"correct horse battery staple")
    blob = crypto.encrypt("map", "or1-x", {"entries": {}}, pw, scrypt_n=2**14)
    with pytest.raises(MappingError) as ei:
        crypto.decrypt(_with_header(blob, first, header), "map", pw)
    assert "or1-x" not in str(ei.value) and "scrypt" not in str(ei.value)


def test_malformed_mapping_header_exit_code(home, capsys):
    tmp, key = home
    blob = crypto.encrypt("map", "or1-x", {"entries": {}}, crypto.load_key_file(key))
    m = tmp / "bad.ormap"
    m.write_bytes(_with_header(blob, header=b'{"id": "or1-x", "kdf": "hkdf-sha256"}'))
    ans = tmp / "ans.txt"
    ans.write_text("see <C1>")
    capsys.readouterr()
    args = ["restore", str(ans), "--mapping", str(m), "--key-file", str(key), "--assume-mapping"]
    assert main(args) == ExitCode.MAPPING
    err = capsys.readouterr().err
    assert "E_MAPPING" in err and "internal error" not in err


# Compression must not reverse meaning.
def test_negation_is_not_a_duplicate():
    d = doc(
        ("A", "The patient should take the medication twice a day with food."),
        ("B", "The patient should never take the medication twice a day with food."),
    )
    out, _ = compress(d, "medium")
    assert any("never" in t.text for t in out.turns)


@pytest.mark.parametrize("text", ["No.", "Do not.", "Wait.", "Go ahead.", "Yes."])
def test_decisions_survive_medium(text):
    d = doc(("A", "Shall we sign the contract today with the new terms?"), ("B", text),
            ("A", "Understood, I will tell the team about the decision now."))  # fmt: skip
    out, _ = compress(d, "medium")
    assert text.rstrip(".").lower() in " ".join(t.text.lower() for t in out.turns)


@pytest.mark.parametrize("text", ["The score was 2 2", "yes yes no"])
def test_numbers_and_yes_no_not_collapsed(text):
    assert clean_text(text) == text


# Separation covers subdirectories of the key's directory.
def test_mapping_below_key_dir_refused(tmp_path):
    kp = tmp_path / "keys" / "k.key"
    kp.parent.mkdir()
    kp.write_text(crypto.generate_key())
    os.chmod(kp, 0o600)
    key = crypto.load_key_file(kp)
    with pytest.raises(UsageError):
        crypto.check_separation(tmp_path / "keys" / "sub" / "m.ormap", key)
    crypto.check_separation(tmp_path / "elsewhere" / "m.ormap", key)


def test_symlinked_key_file_refused(tmp_path):
    kp = tmp_path / "k.key"
    kp.write_text(crypto.generate_key())
    os.chmod(kp, 0o600)
    link = tmp_path / "link.key"
    link.symlink_to(kp)
    with pytest.raises(UsageError):
        crypto.load_key_file(link)


# Near misses reported; values escaped for the format.
def test_long_placeholder_reported_unknown():
    _, rep = restore_text("x <C1234567> y", P, "id")
    assert rep.unknown


def test_vtt_escaped_source_unescapes_once():
    out, _ = restore_text(f"WEBVTT\n\n&lt;{BS}P1&gt; and &lt;P2&gt;", P, "id")
    assert "&lt;P1&gt;" in out and "Zed Fakename" in out


def test_json_values_escaped():
    out, _ = restore_text(json.dumps({"t": "<L1>"}), P, "id")
    assert json.loads(out)["t"] == 'Suite P2, 9 "Fake" Lane'


# CLI ----------------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFRECORD_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OFFRECORD_PASSPHRASE", raising=False)
    monkeypatch.delenv("OFFRECORD_KEY_FILE", raising=False)
    key = tmp_path / "keys" / "k.key"
    assert main(["keygen", str(key)]) == 0
    return tmp_path, key


def _mid(out_text: str) -> str:
    for line in out_text.splitlines():
        if "mapping=" in line:
            return line.split("mapping=")[1].strip()
        if "_offrecord_mapping" in line:
            return line.split('"')[3]
    raise AssertionError("no mapping id")


@pytest.mark.parametrize(("name", "content"), [
    ("a.srt", "1\n00:00:01,000 --> 00:00:02,000\nORLA: Zandrelcorp signed\n\n"
              "2\n00:00:02,000 --> 00:00:03,000\nORLA: ok\n"),
    ("a.json", json.dumps([{"speaker": "Orla", "text": "Zandrelcorp signed"}])),
])  # fmt: skip
def test_srt_and_json_outputs_carry_mapping_id(home, capsys, name, content):
    tmp, key = home
    a = tmp / name
    a.write_text(content)
    b = tmp / ("b" + name[1:])
    b.write_text(content.replace("Zandrelcorp", "Quaxmoor"))
    for src in (a, b):
        out = tmp / (src.stem + ".out" + src.suffix)
        assert main(["redact", str(src), "-o", str(out), "--key-file", str(key),
                     "--output-format", "original"]) == 0  # fmt: skip
    b_map = (
        tmp
        / "home"
        / "data"
        / "mappings"
        / f"{_mid((tmp / ('b.out' + a.suffix)).read_text())}.ormap"
    )
    rc = main(
        [
            "restore",
            str(tmp / ("a.out" + a.suffix)),
            "--mapping",
            str(b_map),
            "--key-file",
            str(key),
        ]
    )
    assert rc == ExitCode.MAPPING


def test_vtt_note_bare_token_collision_not_restored(home, capsys):
    tmp, key = home
    a = tmp / "a.vtt"
    a.write_text("WEBVTT\n\nNOTE Recorded in meeting room P1\n\n00:00:01.000 --> 00:00:02.000\n"
                 "<v Orla Venkatesh>hello there\n")  # fmt: skip
    out = tmp / "a.out.vtt"
    assert (
        main(
            [
                "redact",
                str(a),
                "-o",
                str(out),
                "--key-file",
                str(key),
                "--output-format",
                "original",
            ]
        )
        == 0
    )
    m = tmp / "home" / "data" / "mappings" / f"{_mid(out.read_text())}.ormap"
    capsys.readouterr()
    assert main(["restore", str(out), "--mapping", str(m), "--key-file", str(key)]) == 0
    assert "room P1" in capsys.readouterr().out


def test_out_dir_suffix_from_format(home, capsys):
    tmp, _key = home
    src = tmp / "notes.AcmeMerger"
    src.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Orla>hi\n")
    od = tmp / "od"
    od.mkdir()
    assert (
        main(
            [
                "redact",
                str(src),
                "--no-mapping",
                "--out-dir",
                str(od),
                "--output-format",
                "original",
            ]
        )
        == 0
    )
    assert [p.suffix for p in od.iterdir()] == [".vtt"]


def test_keygen_leaves_existing_directory_mode(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("OFFRECORD_HOME", str(tmp_path / "home"))
    d = tmp_path / "shared"
    d.mkdir()
    os.chmod(d, 0o755)  # noqa: S103 - deliberately shared directory for the test
    assert main(["keygen", str(d / "my.key")]) == 0
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o755
    assert stat.S_IMODE(os.stat(d / "my.key").st_mode) == 0o600


def test_setup_from_fifo_does_not_hang(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("OFFRECORD_HOME", str(tmp_path / "home"))
    fifo = tmp_path / "f"
    os.mkfifo(fifo)
    assert main(["setup", "tokenizer", "--from", str(fifo)]) == ExitCode.INPUT


def test_no_orphaned_output_when_mapping_exists(home, capsys):
    tmp, key = home
    a = tmp / "a.txt"
    a.write_text("Orla: Zandrelcorp\nOrla: ok\n")
    m = tmp / "maps" / "m.ormap"
    m.parent.mkdir()
    m.write_text("existing")
    out = tmp / "o1.txt"
    rc = main(["redact", str(a), "-o", str(out), "--key-file", str(key), "--mapping-out", str(m)])
    assert rc == ExitCode.OUTPUT and not out.exists()


def test_both_secret_sources_is_an_error(home, capsys, monkeypatch):
    tmp, key = home
    monkeypatch.setenv("OFFRECORD_PASSPHRASE", "a synthetic test passphrase")
    a = tmp / "a.txt"
    a.write_text("Orla: hi\nOrla: ok\n")
    assert main(["redact", str(a), "--key-file", str(key)]) == ExitCode.USAGE


def test_env_passphrase_notice(home, capsys, monkeypatch):
    tmp, _key = home
    monkeypatch.setenv("OFFRECORD_PASSPHRASE", "a synthetic test passphrase")
    a = tmp / "a.txt"
    a.write_text("Orla: hi\nOrla: ok\n")
    assert main(["redact", str(a), "-o", str(tmp / "x.txt")]) == 0
    assert "OFFRECORD_PASSPHRASE" in capsys.readouterr().err
