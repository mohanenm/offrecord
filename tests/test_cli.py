from __future__ import annotations

import contextlib
import json

import pytest

from offrecord import netguard
from offrecord.cli import main
from offrecord.errors import ExitCode


def test_version(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["--version"])
    assert ei.value.code == 0
    assert "offrecord" in capsys.readouterr().out


def test_no_command_is_usage_error(capsys):
    assert main([]) == ExitCode.USAGE
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout stays clean


def test_unknown_command_is_usage_error(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["frobnicate"])
    assert ei.value.code == ExitCode.USAGE
    assert capsys.readouterr().out == ""


def test_doctor_runs(capsys):
    rc = main(["doctor"])
    out = capsys.readouterr().out
    assert rc == ExitCode.OK
    assert "network_guard" in out


def test_doctor_json(capsys):
    main(["doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    names = {c["name"] for c in data}
    assert {"python", "ram", "network_guard", "tier0"} <= names


def test_doctor_simulated_low_ram(monkeypatch, capsys):
    monkeypatch.setenv("OFFRECORD_SIMULATE_RAM_MB", "2048")
    main(["doctor", "--json"])
    data = {c["name"]: c for c in json.loads(capsys.readouterr().out)}
    assert "Tier 0" in data["hardware_tier"]["detail"]


def test_network_attempt_fails_run(monkeypatch, capsys):
    import socket

    from offrecord import doctor

    def leaky():
        # A library swallowing the error must not hide the attempt.
        with contextlib.suppress(ConnectionError):
            socket.create_connection(("127.0.0.1", 9))
        return []

    monkeypatch.setattr(doctor, "run_checks", leaky)
    monkeypatch.setattr(doctor, "format_checks", lambda c: "")
    rc = main(["doctor"])
    assert rc == ExitCode.NETWORK
    assert "network attempt" in capsys.readouterr().err
    netguard.reset_attempts()


def test_eval_runs_on_tuning_set(capsys):
    rc = main(["eval", "--set", "tuning", "--system", "baseline", "--json"])
    assert rc == ExitCode.OK
    data = json.loads(capsys.readouterr().out)
    assert data[0]["labels"] > 0
    assert 0.0 <= data[0]["recall"] <= 1.0


def test_eval_refuses_sealed_set_without_final_check(capsys):

    assert main(["eval", "--set", "sealed", "--system", "null"]) == ExitCode.USAGE
    # With --final-check it still needs the pinned set and its key.
    assert main(["eval", "--set", "sealed", "--final-check", "--system", "null"]) == ExitCode.USAGE
    # The first sealed set is spent: it is validation2 now, from its own folder only.
    assert main(["eval", "--set", "validation2", "--system", "null"]) == ExitCode.OK
    capsys.readouterr()


def test_eval_unknown_system(capsys):
    assert main(["eval", "--set", "tuning", "--system", "nope"]) == ExitCode.USAGE


MARKER = "ZQXCANARY7731"


def test_redact_requires_mapping_decision(tmp_path, capsys):
    p = tmp_path / "in.txt"
    p.write_text("Orla: hi\n")
    assert main(["redact", str(p)]) == ExitCode.USAGE


def test_redact_to_stdout_and_no_content_on_stderr(tmp_path, capsys):
    p = tmp_path / "in.txt"
    p.write_text(f"Orla Venkatesh: call me on 415-555-0142, ref {MARKER}\nBram Okafor: ok\n")
    assert main(["redact", str(p), "--no-mapping"]) == ExitCode.OK
    cap = capsys.readouterr()
    assert "415-555-0142" not in cap.out and "Orla" not in cap.out
    assert cap.out.startswith("# offrecord mapping=none")
    assert MARKER not in cap.err and "Orla" not in cap.err


def test_redact_output_file_owner_only_and_no_clobber(tmp_path, capsys):
    import os
    import stat

    p = tmp_path / "in.vtt"
    p.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Orla>call 415-555-0142\n")
    out = tmp_path / "out.vtt"
    args = ["redact", str(p), "--no-mapping", "--output-format", "original", "-o", str(out)]
    assert main(args) == ExitCode.OK
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert "555-0142" not in out.read_text() and out.read_text().startswith("WEBVTT")
    assert main(args) == ExitCode.OUTPUT
    assert main([*args, "--force"]) == ExitCode.OK


def test_redact_out_dir_project_scope(tmp_path, capsys):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("Orla: the Zandrelcorp meeting\nOrla: ok\n")
    b.write_text("Bram: Zandrelcorp again\nBram: ok\n")
    out = tmp_path / "out"
    out.mkdir()
    rc = main(["redact", str(a), str(b), "--no-mapping", "--out-dir", str(out),
               "--mapping-scope", "project"])  # fmt: skip
    assert rc == ExitCode.OK
    ra = (out / "001-redacted.txt").read_text()
    rb = (out / "002-redacted.txt").read_text()
    assert "Zandrelcorp" not in ra + rb
    pa = next(w for w in ra.split() if w.startswith("<N"))
    assert pa in rb


def test_redact_malformed_input_error_has_no_content(tmp_path, capsys):
    p = tmp_path / "in.json"
    p.write_text('{"segments": [{"text": "' + MARKER + '"')
    assert main(["redact", str(p), "--no-mapping"]) == ExitCode.INPUT
    assert MARKER not in capsys.readouterr().err


def test_review_markup_with_suggestions(tmp_path, capsys):
    p = tmp_path / "m.txt"
    p.write_text("[[PERSON:Orla Venkatesh]]: the [[CLIENT:Amber Holdings]] deal\n"
                 "[[PERSON:Bram Okafor]]: ok\n")  # fmt: skip
    assert main(["review", str(p), "--markup", "--suggest"]) == ExitCode.OK
    out = capsys.readouterr().out
    assert "ORIGINAL" in out and "REDACTED" in out and "missed label" in out


def test_eval_warns_about_trackable_private_folder(tmp_path, capsys):
    from pathlib import Path

    root = Path(__file__).parent.parent
    main(["eval", "--dir", str(tmp_path), "--system", "null"])  # outside the repository
    assert "warning" not in capsys.readouterr().err
    main(["eval", "--dir", str(root / "eval" / "user_set"), "--system", "null"])  # tracked
    assert "not git-ignored" in capsys.readouterr().err
