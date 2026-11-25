"""init, denylist, --prompt, paste/unpaste, run summary, clipboard process allowance.
Synthetic data only."""

from __future__ import annotations

import io
import os
import subprocess
import sys

import pytest
import yaml

from offrecord import clipboard, netguard
from offrecord.cli import main
from offrecord.errors import ExitCode, NetworkBlockedError

SECRET_NAME = "Quillfeather Analytics"  # noqa: S105 - a fake client name


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFRECORD_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OFFRECORD_CONFIG", str(tmp_path / "home" / "config" / "config.yaml"))
    monkeypatch.setenv("OFFRECORD_PASSPHRASE", "correct horse battery staple test")
    return tmp_path / "home"


@pytest.fixture
def fake_clipboard(monkeypatch):
    box = {"text": ""}

    def run(argv, data):
        if data is None:
            return box["text"].encode()
        box["text"] = data.decode()
        return b""

    monkeypatch.setattr(clipboard, "backend", lambda: clipboard.Backend("fake", ("/c",), ("/p",)))
    monkeypatch.setattr(clipboard, "_run", run)
    return box


def test_denylist_add_import_count_never_echo(home, tmp_path, capsys):
    assert main(["denylist", "add", SECRET_NAME, "--alias", "QFA"]) == ExitCode.OK
    assert main(["denylist", "add", SECRET_NAME.lower(), "--alias", "Quill"]) == ExitCode.OK
    csvf = tmp_path / "crm.csv"
    csvf.write_text("name,category,aliases\nCorvanta,BRAND,core vanta\nProject Bluefin,CODENAME,\n")
    assert main(["denylist", "import", str(csvf)]) == ExitCode.OK
    assert main(["denylist", "count"]) == ExitCode.OK
    cap = capsys.readouterr()
    for secret in (SECRET_NAME, "QFA", "Corvanta", "Bluefin", "Quill"):
        assert secret not in cap.out + cap.err
    assert "3 deny-list entries" in cap.err
    cfg_path = home / "config" / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    first = data["denylist"][0]
    assert first["aliases"] == ["QFA", "Quill"]  # merged, not duplicated
    assert oct(cfg_path.stat().st_mode & 0o777) == "0o600"


def test_denylist_rejects_unknown_category(home, capsys):
    with pytest.raises(SystemExit) as e:
        main(["denylist", "add", "X", "--category", "WIDGET"])
    assert e.value.code == 2


def test_init_writes_config_used_by_redact(home, tmp_path, monkeypatch, capsys):
    answers = (
        "Vexley Holdings\nQuillfeather Analytics (QFA/Quill)\n\nProject Bluefin\nOrla Venkatesh\n\n"
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(answers))
    assert main(["init", "--yes"]) == ExitCode.OK
    cap = capsys.readouterr()
    assert "Marrowind" not in cap.err and "Bluefin" not in cap.err
    t = tmp_path / "call.txt"
    t.write_text("Bram: MWL renewal and Bluefin timing are on track.\nOrla: ok\n")
    assert main(["redact", str(t), "--no-mapping"]) == ExitCode.OK
    cap = capsys.readouterr()
    assert "MWL" not in cap.out and "Bluefin" not in cap.out
    assert "default config" in cap.err


def test_init_refuses_non_tty(home, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert main(["init"]) == ExitCode.USAGE


def test_prompt_wraps_and_redacts_instruction(home, tmp_path, capsys):
    assert main(["denylist", "add", SECRET_NAME, "--alias", "QFA"]) == ExitCode.OK
    t = tmp_path / "call.txt"
    t.write_text("Bram: the QFA deal closed at $42,000.\nOrla: great\n")
    assert main(["redact", str(t), "--no-mapping", "--prompt", "Summarise the QFA call"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Summarise the <")
    assert "QFA" not in out and "42,000" not in out
    assert "copy the placeholders exactly" in out and "--- transcript ---" in out
    # the instruction and the transcript share one placeholder for the client
    ph = out.split("Summarise the ", 1)[1].split(" ", 1)[0]
    assert out.count(ph) >= 2


def test_summary_counts_categories_without_text(tmp_path, capsys):
    t = tmp_path / "call.txt"
    t.write_text("Orla Venkatesh: email orla@quill.example, budget $42,000.\nBram: ok\n")
    assert main(["redact", str(t), "--no-mapping"]) == ExitCode.OK
    err = capsys.readouterr().err
    assert "redacted" in err and "email" in err and "amount" in err
    assert "orla" not in err.lower() and "42,000" not in err


def test_paste_unpaste_round_trip(home, fake_clipboard, capsys):
    fake_clipboard["text"] = "Orla Venkatesh: call me on 415-555-0142 about Corvanta.\nBram: ok\n"
    assert main(["paste"]) == ExitCode.OK
    red = fake_clipboard["text"]
    assert "415-555-0142" not in red and "Orla" not in red
    ph = next(
        w.strip(".,") for w in red.split() if w.startswith("<") and w.strip(".,").endswith(">")
    )
    fake_clipboard["text"] = f"Summary: follow up with {ph} next week."
    assert main(["unpaste"]) == ExitCode.OK
    restored = fake_clipboard["text"]
    assert ph not in restored and "Summary: follow up with" in restored
    cap = capsys.readouterr()
    assert "415" not in cap.err and "Orla" not in cap.err


def test_unpaste_without_mapping_is_usage_error(home, fake_clipboard):
    fake_clipboard["text"] = "anything"
    assert main(["unpaste"]) == ExitCode.USAGE


def test_allow_processes_is_narrow():
    netguard.install(block_processes=True)
    exe = os.path.realpath(sys.executable)
    with netguard.allow_processes([exe]):
        subprocess.run([exe, "-c", "pass"], check=True)
    with pytest.raises(NetworkBlockedError):
        subprocess.run([exe, "-c", "pass"], check=True)
    with netguard.allow_processes(["/usr/bin/true"]), pytest.raises(NetworkBlockedError):
        subprocess.run([exe, "-c", "pass"], check=True)
    with pytest.raises(ValueError), netguard.allow_processes(["pbcopy"]):
        pass
    netguard.reset_attempts()


def test_clipboard_backend_unsupported_is_clear(monkeypatch):
    monkeypatch.setattr(sys, "platform", "sunos5")
    with pytest.raises(Exception, match="clipboard"):
        clipboard.backend()
