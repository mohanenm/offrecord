"""Encrypted mapping, restore with tolerant matching, wrong-mapping detection. Synthetic data."""

from __future__ import annotations

import os
import stat

import pytest

from offrecord import crypto
from offrecord.cli import main
from offrecord.errors import ExitCode, MappingError, UsageError
from offrecord.restore import bare_collisions, restore_text

FAST = 2**14  # fast scrypt for unit tests only; the CLI always uses the default (2^17)
PW = crypto.Secret("passphrase", b"correct horse battery staple")


def payload():
    return {
        "id": "or1-test",
        "version": 1,
        "scope": "document",
        "entries": {
            "<C1>": {"category": "CLIENT", "canonical": "quillfeather", "key": "deny#0",
                     "occurrences": ["Quillfeather"]},
            "<P2>": {"category": "PERSON", "canonical": "orla", "key": "spk:orla",
                     "occurrences": ["Orla"]},
        },
        "bare_collisions": [],
    }  # fmt: skip


# --- crypto -----------------------------------------------------------------------------------


def test_round_trip_passphrase():
    blob = crypto.encrypt("map", "or1-test", payload(), PW, scrypt_n=FAST)
    fid, data = crypto.decrypt(blob, "map", PW)
    assert fid == "or1-test" and data == payload()
    assert b"Quillfeather" not in blob and b"orla" not in blob.lower()


def test_round_trip_key_file(tmp_path):
    kp = tmp_path / "k.key"
    kp.write_text(crypto.generate_key())
    os.chmod(kp, 0o600)
    key = crypto.load_key_file(kp)
    blob = crypto.encrypt("map", "or1-x", payload(), key)
    assert crypto.decrypt(blob, "map", key)[1] == payload()
    with pytest.raises(MappingError):
        crypto.decrypt(blob, "map", PW)  # wrong kind of secret


def test_wrong_passphrase_fails_without_detail():
    blob = crypto.encrypt("map", "or1-test", payload(), PW, scrypt_n=FAST)
    with pytest.raises(MappingError) as ei:
        crypto.decrypt(blob, "map", crypto.Secret("passphrase", b"wrong passphrase!!"))
    assert "staple" not in str(ei.value)


def test_tampered_header_detected():
    blob = crypto.encrypt("map", "or1-test", payload(), PW, scrypt_n=FAST)
    swapped = blob.replace(b'"id":"or1-test"', b'"id":"or1-evil"', 1)
    with pytest.raises(MappingError):
        crypto.decrypt(swapped, "map", PW)


def test_tampered_token_detected():
    blob = bytearray(crypto.encrypt("map", "or1-test", payload(), PW, scrypt_n=FAST))
    blob[-10] = ord("A") if blob[-10] != ord("A") else ord("B")
    with pytest.raises(MappingError):
        crypto.decrypt(bytes(blob), "map", PW)


def test_default_scrypt_parameters_in_header():
    blob = crypto.encrypt("map", "or1-test", payload(), PW)
    hdr = crypto.read_header(blob, "map")
    assert hdr["kdf"]["n"] == 2**17 and hdr["kdf"]["r"] == 8 and hdr["kdf"]["p"] == 1


def test_key_file_permissions_enforced(tmp_path):
    kp = tmp_path / "k.key"
    kp.write_text(crypto.generate_key())
    os.chmod(kp, 0o644)
    with pytest.raises(UsageError):
        crypto.load_key_file(kp)


def test_secret_repr_hides_value():
    assert "staple" not in repr(PW)


def test_mapping_not_next_to_key(tmp_path):
    kp = tmp_path / "k.key"
    kp.write_text(crypto.generate_key())
    os.chmod(kp, 0o600)
    with pytest.raises(UsageError):
        crypto.check_separation(tmp_path / "m.ormap", crypto.load_key_file(kp))


# --- restore ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mangled",
    ["<C1>", "<c1>", "<C 1>", "< C1 >", "&lt;C1&gt;", "C1", "<C01>"],
)
def test_tolerant_restore(mangled):
    out, rep = restore_text(f"call {mangled} today", payload(), "or1-test")
    assert out == "call Quillfeather today" and rep.restored == 1


def test_possessive_and_punctuation():
    out, _ = restore_text("<C1>'s team and C1, and (<P2>).", payload(), "or1-test")
    assert out == "Quillfeather's team and Quillfeather, and (Orla)."


def test_lowercase_bare_not_restored():
    out, rep = restore_text("the c1 option", payload(), "or1-test")
    assert out == "the c1 option" and rep.restored == 0


def test_bare_collision_not_restored():
    p = payload()
    p["bare_collisions"] = ["C1"]
    out, rep = restore_text("model C1 for <C1>", p, "or1-test")
    assert out == "model C1 for Quillfeather" and rep.ambiguous


def test_unknown_placeholder_reported():
    out, rep = restore_text("ask <P9>", payload(), "or1-test")
    assert out == "ask <P9>" and rep.unknown["<P9>"] == 1


def test_escaped_source_placeholder_unescaped_not_restored():
    out, rep = restore_text("literal <\\C1> stays", payload(), "or1-test")
    assert out == "literal <C1> stays" and rep.restored == 0


def test_header_mismatch_fails():
    with pytest.raises(MappingError):
        restore_text("# offrecord mapping=or1-other\nA: <C1>", payload(), "or1-test")


def test_no_mapping_header_fails():
    with pytest.raises(MappingError):
        restore_text("# offrecord mapping=none\nA: <C1>", payload(), "or1-test")


def test_bare_collisions_found():
    assert bare_collisions(["the C1 model", "<C1> deal"], ["<C1>", "<P2>"]) == ["C1"]


# --- CLI round trip ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFRECORD_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OFFRECORD_PASSPHRASE", raising=False)
    monkeypatch.delenv("OFFRECORD_KEY_FILE", raising=False)
    return tmp_path


def test_cli_round_trip_with_key_file(home, capsys):
    key = home / "keys" / "k.key"
    assert main(["keygen", str(key)]) == ExitCode.OK
    assert stat.S_IMODE(os.stat(key).st_mode) == 0o600
    src = home / "in.txt"
    src.write_text("Orla Venkatesh: the Zandrelcorp renewal, call 415-555-0142\nBram Okafor: ok\n")
    out = home / "out.txt"
    assert main(["redact", str(src), "-o", str(out), "--key-file", str(key)]) == ExitCode.OK
    err = capsys.readouterr().err
    red = out.read_text()
    assert "Zandrelcorp" not in red and "555-0142" not in red
    mid = red.splitlines()[0].split("mapping=")[1]
    store = home / "home" / "data" / "mappings"
    mfile = store / f"{mid}.ormap"
    assert mfile.exists() and stat.S_IMODE(os.stat(mfile).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store).st_mode) == 0o700
    assert "Zandrelcorp" not in mfile.read_text(errors="replace")
    assert mid in err
    # Restore the redacted transcript.
    rest = home / "restored.txt"
    assert (
        main(
            ["restore", str(out), "--mapping", str(mfile), "--key-file", str(key), "-o", str(rest)]
        )
        == 0
    )
    restored = rest.read_text()
    assert "Zandrelcorp" in restored and "555-0142" in restored
    assert stat.S_IMODE(os.stat(rest).st_mode) == 0o600
    # Restore an LLM-style answer (no header, mangled placeholders).
    phs = [w.strip(",.") for w in red.split() if w.startswith("<")]
    answer = home / "answer.txt"
    answer.write_text(f"Summary: {phs[0].lower()} raised {phs[-1].strip('<>')}'s issue.")
    capsys.readouterr()
    # Header-less text needs an explicit acknowledgement.
    assert main(["restore", str(answer), "--mapping", str(mfile), "--key-file", str(key)]) == 2
    capsys.readouterr()
    args = [
        "restore",
        str(answer),
        "--mapping",
        str(mfile),
        "--key-file",
        str(key),
        "--assume-mapping",
    ]
    assert main(args) == 0
    assert "<" not in capsys.readouterr().out


def test_cli_wrong_mapping_detected(home, capsys):
    key = home / "keys" / "k.key"
    main(["keygen", str(key)])
    a, b = home / "a.txt", home / "b.txt"
    a.write_text("Orla: Zandrelcorp\nOrla: ok\n")
    b.write_text("Bram: Quaxmoor\nBram: ok\n")
    main(["redact", str(a), "-o", str(home / "a.out"), "--key-file", str(key)])
    main(["redact", str(b), "-o", str(home / "b.out"), "--key-file", str(key)])
    b_id = (home / "b.out").read_text().splitlines()[0].split("mapping=")[1]
    b_map = home / "home" / "data" / "mappings" / f"{b_id}.ormap"
    rc = main(["restore", str(home / "a.out"), "--mapping", str(b_map), "--key-file", str(key)])
    assert rc == ExitCode.MAPPING


def test_cli_unheaded_unknown_placeholders_warn_and_strict_fails(home, capsys):
    key = home / "keys" / "k.key"
    main(["keygen", str(key)])
    a = home / "a.txt"
    a.write_text("Orla: Zandrelcorp\nOrla: ok\n")
    main(["redact", str(a), "-o", str(home / "a.out"), "--key-file", str(key)])
    mid = (home / "a.out").read_text().splitlines()[0].split("mapping=")[1]
    m = home / "home" / "data" / "mappings" / f"{mid}.ormap"
    ans = home / "ans.txt"
    ans.write_text("see <C42> and <P77>")
    capsys.readouterr()
    base = ["restore", str(ans), "--mapping", str(m), "--key-file", str(key), "--assume-mapping"]
    assert main(base) == 0
    assert "right mapping" in capsys.readouterr().err
    assert main([*base, "--strict"]) == ExitCode.MAPPING


def test_cli_no_secret_fails_clearly(home, capsys, monkeypatch):
    monkeypatch.setattr(crypto, "_prompt", lambda confirm: None)
    a = home / "a.txt"
    a.write_text("Orla: hi\nOrla: ok\n")
    assert main(["redact", str(a)]) == ExitCode.USAGE
    assert "--no-mapping" in capsys.readouterr().err


def test_cli_passphrase_from_env(home, capsys, monkeypatch):
    monkeypatch.setenv("OFFRECORD_PASSPHRASE", "a synthetic test passphrase")
    a = home / "a.txt"
    a.write_text("Orla: Zandrelcorp\nOrla: ok\n")
    assert main(["redact", str(a), "-o", str(home / "a.out")]) == ExitCode.OK
    assert "a synthetic test passphrase" not in capsys.readouterr().err


def test_cli_project_mapping_in(home, capsys):
    key = home / "keys" / "k.key"
    main(["keygen", str(key)])
    m = home / "proj" / "project.ormap"
    m.parent.mkdir()
    a, b = home / "a.txt", home / "b.txt"
    a.write_text("Orla: Zandrelcorp signed\nOrla: ok\n")
    b.write_text("Bram: Zandrelcorp again\nBram: ok\n")
    assert main(["redact", str(a), "-o", str(home / "a.out"), "--key-file", str(key),
                 "--mapping-out", str(m), "--mapping-scope", "project"]) == 0  # fmt: skip
    assert main(["redact", str(b), "-o", str(home / "b.out"), "--key-file", str(key),
                 "--mapping-in", str(m)]) == 0  # fmt: skip
    pa = next(w for w in (home / "a.out").read_text().split() if w.startswith("<N"))
    assert pa in (home / "b.out").read_text()


def test_cli_audit_log_encrypted_and_readable(home, capsys):
    key = home / "keys" / "k.key"
    main(["keygen", str(key)])
    a = home / "a.txt"
    a.write_text(
        "Orla Venkatesh: the Zandrelcorp meeting at $42,000. Pricing next.\nBram Okafor: ok\n"
    )
    cfg = home / "mixed.yaml"
    cfg.write_text("topics:\n  mode: mixed\n")  # "Pricing next." is then flagged, not redacted
    argv = ["redact", str(a), "-o", str(home / "a.out"), "--key-file", str(key), "--audit"]
    assert main([*argv, "--config", str(cfg)]) == 0
    err = capsys.readouterr().err
    logs = list((home / "home" / "data" / "mappings").glob("*.oraudit"))
    assert len(logs) == 1 and "Zandrelcorp" not in logs[0].read_text(errors="replace")
    assert stat.S_IMODE(os.stat(logs[0]).st_mode) == 0o600
    assert "Zandrelcorp" not in err
    assert main(["audit", str(logs[0]), "--key-file", str(key)]) == 0
    import json as _json

    entries = _json.loads(capsys.readouterr().out)["entries"]
    originals = {e["original"] for e in entries}
    assert "Zandrelcorp" in originals and any(e["action"] == "flagged" for e in entries)
    assert all(e["category"] and e["detector"] for e in entries)


def test_cli_audit_never_written_next_to_output(home, capsys):
    key = home / "keys" / "k.key"
    main(["keygen", str(key)])
    a = home / "a.txt"
    a.write_text("Orla: Zandrelcorp\nOrla: ok\n")
    main(["redact", str(a), "-o", str(home / "a.out"), "--key-file", str(key), "--audit"])
    assert not list(home.glob("*.oraudit"))
