from __future__ import annotations

import os
import stat

import pytest

from offrecord.errors import InputDecodeError, InputError, InputLimitError, OutputExistsError
from offrecord.limits import Limits
from offrecord.safeio import (
    decode_text,
    ensure_private_dir,
    private_tempdir,
    read_input_text,
    write_atomic,
)

CANARY = "ZQXCANARY7731"


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_write_atomic_owner_only(tmp_path):
    out = tmp_path / "out.txt"
    write_atomic(out, b"hello")
    assert out.read_bytes() == b"hello"
    assert mode(out) == 0o600


def test_write_atomic_owner_only_despite_permissive_umask(tmp_path):
    old = os.umask(0)
    try:
        out = tmp_path / "out.txt"
        write_atomic(out, b"x")
    finally:
        os.umask(old)
    assert mode(out) == 0o600


def test_no_overwrite_without_force(tmp_path):
    out = tmp_path / "out.txt"
    out.write_bytes(b"original")
    with pytest.raises(OutputExistsError):
        write_atomic(out, b"new")
    assert out.read_bytes() == b"original"
    write_atomic(out, b"new", force=True)
    assert out.read_bytes() == b"new"


def test_no_overwrite_through_symlink(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"keep")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OutputExistsError):
        write_atomic(link, b"new")
    assert target.read_bytes() == b"keep"


def test_no_temp_files_left_behind(tmp_path):
    out = tmp_path / "out.txt"
    write_atomic(out, b"x")
    with pytest.raises(OutputExistsError):
        write_atomic(out, b"y")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.txt"]


def test_private_tempdir_cleaned_on_error():
    with pytest.raises(RuntimeError), private_tempdir() as d:
        assert mode(d) == 0o700
        (d / "f").write_text("x")
        saved = d
        raise RuntimeError
    assert not saved.exists()


def test_ensure_private_dir(tmp_path):
    d = ensure_private_dir(tmp_path / "a" / "b")
    assert mode(d) == 0o700


def test_decode_reports_offset_not_content():
    data = (CANARY + " ").encode() + b"\xff\xfe" + CANARY.encode()
    with pytest.raises(InputDecodeError) as ei:
        decode_text(data)
    msg = str(ei.value)
    assert CANARY not in msg
    assert str(len(CANARY) + 1) in msg


def test_decode_strips_bom_and_normalises_newlines():
    assert decode_text(b"\xef\xbb\xbfa\r\nb\rc\n") == "a\nb\nc\n"


def test_size_limit(tmp_path):
    p = tmp_path / "in.txt"
    p.write_bytes(b"x" * 101)
    with pytest.raises(InputLimitError):
        read_input_text(p, Limits(max_bytes=100))


def test_line_limit_reports_line_number(tmp_path):
    p = tmp_path / "in.txt"
    p.write_text("short\n" + CANARY * 10 + "\n")
    with pytest.raises(InputLimitError) as ei:
        read_input_text(p, Limits(max_line_chars=20))
    assert "line 2" in str(ei.value)
    assert CANARY not in str(ei.value)


def test_missing_file_error_has_no_path(tmp_path):
    p = tmp_path / (CANARY + ".txt")
    with pytest.raises(InputError) as ei:
        read_input_text(p)
    assert CANARY not in str(ei.value)


def test_directory_rejected(tmp_path):
    with pytest.raises(InputError):
        read_input_text(tmp_path)


def test_private_dir_creates_parents_owner_only(tmp_path):
    d = ensure_private_dir(tmp_path / "a" / "b")
    assert mode(tmp_path / "a") == 0o700
    assert mode(d) == 0o700


def test_private_dir_refuses_symlink(tmp_path):
    from offrecord.errors import OutputError

    real = tmp_path / "real"
    real.mkdir(mode=0o755)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(OutputError):
        ensure_private_dir(link)
    assert mode(real) == 0o755  # target untouched


def test_fifo_input_rejected_without_hanging(tmp_path):
    fifo = tmp_path / "f"
    os.mkfifo(fifo)
    with pytest.raises(InputError):
        read_input_text(fifo)
