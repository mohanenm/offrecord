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


def test_misspelt_config_key_fails_instead_of_leaving_text_readable(home, tmp_path, capsys):
    cfg = tmp_path / "typo.yaml"
    cfg.write_text("protekt: confidential\ndenylist:\n  - name: Vexley Holdings\n")
    t = tmp_path / "call.txt"
    t.write_text("Bram: the budget is $40,000 for Vexley Holdings.\nOrla: ok\n")
    assert main(["redact", str(t), "--no-mapping", "--config", str(cfg)]) == ExitCode.USAGE
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "unknown config key: protekt (did you mean protect?)" in cap.err
    assert "Vexley" not in cap.err and "confidential" not in cap.err


def test_init_refuses_non_tty(home, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert main(["init"]) == ExitCode.USAGE


def test_prompt_wraps_and_redacts_instruction(home, tmp_path, capsys):
    assert main(["denylist", "add", SECRET_NAME, "--alias", "QFA"]) == ExitCode.OK
    t = tmp_path / "call.txt"
    t.write_text("Bram: the QFA invoice came to $42,000.\nOrla: great\n")
    assert main(["redact", str(t), "--no-mapping", "--prompt", "Summarise the QFA call"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Summarise the <")
    assert "QFA" not in out
    assert "copy the placeholders exactly" in out and "--- transcript ---" in out
    # the instruction and the transcript share one placeholder for the client
    ph = out.split("Summarise the ", 1)[1].split(" ", 1)[0]
    assert out.count(ph) >= 2


def test_summary_counts_categories_without_text(tmp_path, capsys):
    t = tmp_path / "call.txt"
    t.write_text("Orla Venkatesh: email orla@quill.example, call 415-555-0142.\nBram: ok\n")
    assert main(["redact", str(t), "--no-mapping"]) == ExitCode.OK
    err = capsys.readouterr().err
    assert "redacted" in err and "email" in err and "phone" in err
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


def test_unpaste_strict_failure_leaves_clipboard(home, fake_clipboard, capsys):
    fake_clipboard["text"] = "Orla Venkatesh: call me about Corvanta.\nBram: ok\n"
    assert main(["paste"]) == ExitCode.OK
    answer = "Summary: ask <P1> and <P99>."
    fake_clipboard["text"] = answer
    assert main(["unpaste", "--strict"]) == ExitCode.MAPPING
    assert fake_clipboard["text"] == answer
    capsys.readouterr()
    assert main(["unpaste", "--strict", "--stdout"]) == ExitCode.MAPPING
    assert capsys.readouterr().out == ""


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


def test_clipboard_input_checked_like_file_input(monkeypatch):
    from offrecord.errors import InputDecodeError, InputLimitError
    from offrecord.limits import Limits

    box = {"data": b""}
    monkeypatch.setattr(clipboard, "backend", lambda: clipboard.Backend("fake", ("/c",), ("/p",)))
    monkeypatch.setattr(clipboard, "_run", lambda argv, data: box["data"])

    box["data"] = b"\xef\xbb\xbfOrla: hi\r\nBram: ok\r"  # BOM, CRLF and a bare CR
    assert clipboard.read_text() == "Orla: hi\nBram: ok\n"

    box["data"] = b"Orla Venkatesh: \xff\xfe"
    with pytest.raises(InputDecodeError) as e:
        clipboard.read_text()
    assert "Orla" not in str(e.value) and "offset 16" in str(e.value)

    box["data"] = b"Bram: ok\n" + b"x" * 50
    with pytest.raises(InputLimitError, match="line 2") as e2:
        clipboard.read_text(Limits(max_line_chars=40))
    assert "Bram" not in str(e2.value)


def test_clipboard_backend_unsupported_is_clear(monkeypatch):
    monkeypatch.setattr(sys, "platform", "sunos5")
    with pytest.raises(Exception, match="clipboard"):
        clipboard.backend()


def test_review_draft_labels_round_trip(home, tmp_path, capsys):
    from offrecord.evaluation.runner import load_markup_dir, score
    from offrecord.redact.pipeline import tier0_system

    src = tmp_path / "call.txt"
    src.write_text(
        "Orla Venkatesh: Call me on 555-0142 about the Quillfeather kickoff.\n"
        "Bram: Sure. Zorbleth will join too.\n"
    )
    private = tmp_path / "private"
    private.mkdir()
    out = private / "call.txt"
    assert main(["review", str(src), "--draft-labels"]) == ExitCode.USAGE  # needs -o
    assert main(["review", str(src), "--draft-labels", "-o", str(out)]) == ExitCode.OK
    draft = out.read_text()
    assert "[[PHONE:555-0142]]" in draft and "?:" in draft  # unknown word left unresolved
    assert "555-0142" not in capsys.readouterr().err
    with pytest.raises(Exception, match="unresolved draft label"):
        load_markup_dir(private)
    import re

    out.write_text(re.sub(r"\[\[[A-Z]+\?:(.*?)\]\]", r"[[PERSON:\1]]", draft))
    items, cfg = load_markup_dir(private)
    assert score(items, tier0_system(cfg), "own draft").overall.recall == 1.0


def test_draft_labels_never_span_a_line_break():
    from offrecord.model import Document, Span, Turn, turn_field_id
    from offrecord.review import draft_markup

    d = Document("txt", (Turn(0, None, "ship to 12 Elm\nRoad please"),))
    out = draft_markup(d, [Span(turn_field_id(0, "text"), 8, 19, "LOCATION", "t", 0.9)])
    assert out == "ship to [[LOCATION:12 Elm]]\n[[LOCATION:Road]] please\n"


def test_half_open_markup_is_an_error():
    from offrecord.errors import InputFormatError
    from offrecord.evaluation.labels import strip_markup

    with pytest.raises(InputFormatError, match="line 3"):
        strip_markup("ship to [[LOCATION:12 Elm", 2, "line")


def test_init_can_choose_confidential_protection(home, monkeypatch, capsys):
    from offrecord import userconfig

    monkeypatch.setattr(sys, "stdin", io.StringIO("Vexley Holdings\n\n\n\n\n\ny\n"))
    assert main(["init", "--yes"]) == ExitCode.OK
    data = yaml.safe_load(userconfig.default_config_path().read_text())
    assert data["protect"] == "confidential"
