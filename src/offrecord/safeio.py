"""Safe file I/O.

- Inputs: size-capped reads, strict UTF-8 (BOM tolerated), decode errors report the byte offset
  only.
- Outputs: atomic (temp file in the destination dir, fsync, rename), owner-only permissions,
  never overwrite without ``force`` (race-free via hard link where supported).
- Temp files: only inside a private (0700) per-run directory, removed on exit, error, Ctrl-C
  and SIGTERM (the CLI converts SIGTERM to SystemExit so ``finally`` blocks run).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

from offrecord.errors import InputDecodeError, InputError, OutputError, OutputExistsError
from offrecord.limits import DEFAULT_LIMITS, Limits

FILE_MODE = 0o600
DIR_MODE = 0o700
_TMP_PREFIX = ".offrecord-"


def read_input_bytes(path: str | os.PathLike[str], limits: Limits = DEFAULT_LIMITS) -> bytes:
    """Read an input file (or stdin for ``-``) with a size cap."""
    cap = limits.max_bytes
    if str(path) == "-":
        data = sys.stdin.buffer.read(cap + 1)
        limits.check_size(len(data))
        return data
    # Open first (non-blocking, so a FIFO swapped in cannot hang us), then fstat the open
    # descriptor: no check-then-open race.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(os.fspath(path), flags)
    except FileNotFoundError:
        raise InputError("input file not found") from None
    except OSError as e:
        raise InputError(f"cannot open input file (errno {e.errno})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise InputError("input is not a regular file")
        limits.check_size(st.st_size)
        chunks = []
        remaining = cap + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    except OSError as e:
        raise InputError(f"cannot read input file (errno {e.errno})") from None
    finally:
        os.close(fd)
    limits.check_size(len(data))  # file may have grown since stat
    return data


def decode_text(data: bytes) -> str:
    """Strict UTF-8 decode (BOM stripped). Normalises CRLF/CR line endings to LF.

    Line-ending normalisation is done here, before any offsets are taken, so that every
    downstream offset is relative to this decoded text.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        # Never include e.object / e.reason details that could carry bytes.
        raise InputDecodeError(
            f"input is not valid UTF-8 (first bad byte at offset {e.start})"
        ) from None
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def read_input_text(path: str | os.PathLike[str], limits: Limits = DEFAULT_LIMITS) -> str:
    text = decode_text(read_input_bytes(path, limits))
    limits.check_lines(text)
    return text


def check_writable(path: str | os.PathLike[str], *, force: bool = False) -> Path:
    """Check that ``write_atomic(path, force=force)`` can be expected to succeed, so callers can
    refuse before writing anything else. Returns the directory the file goes in."""
    dest = Path(path)
    parent = dest.parent if str(dest.parent) else Path(".")
    if not parent.is_dir():
        raise OutputError("output directory does not exist")
    if not force and (dest.exists() or dest.is_symlink()):
        raise OutputExistsError("output file exists; pass --force to overwrite")
    if dest.is_dir():
        raise OutputError("output path is a directory")
    if not os.access(parent, os.W_OK | os.X_OK):
        raise OutputError("cannot create file in output directory (no write permission)")
    return parent


def write_atomic(
    path: str | os.PathLike[str],
    data: bytes,
    *,
    force: bool = False,
    mode: int = FILE_MODE,
) -> None:
    """Write ``data`` to ``path`` atomically with owner-only permissions by default.

    Without ``force`` an existing file is never replaced; the create step uses ``os.link`` so a
    file appearing between the check and the create is still not overwritten.
    """
    dest = Path(path)
    parent = check_writable(dest, force=force)
    try:
        fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=_TMP_PREFIX, suffix=".tmp")
    except OSError as e:
        raise OutputError(f"cannot create file in output directory (errno {e.errno})") from None
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        if force:
            os.replace(tmp, dest)
        else:
            try:
                os.link(tmp, dest)
            except FileExistsError:
                raise OutputExistsError("output file exists; pass --force to overwrite") from None
            except OSError:
                # Filesystem without hard links: fall back to check-then-rename.
                if dest.exists() or dest.is_symlink():
                    raise OutputExistsError(
                        "output file exists; pass --force to overwrite"
                    ) from None
                os.replace(tmp, dest)
            else:
                tmp.unlink()
    except OSError as e:
        # OutputExistsError is not an OSError, so it propagates unchanged.
        raise OutputError(f"cannot write output file (errno {e.errno})") from None
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def ensure_private_dir(path: str | os.PathLike[str], chmod_existing: bool = True) -> Path:
    """Create ``path`` (and missing parents, all 0700) and check the leaf is a real directory,
    not a symlink, owned by the current user. Existing parents are left as they are."""
    p = Path(path)
    missing = []
    cur = p
    while not cur.exists() and not cur.is_symlink():
        missing.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    for d in reversed(missing):
        try:
            d.mkdir(mode=DIR_MODE)
        except FileExistsError:
            pass
        except OSError as e:
            raise OutputError(f"cannot create private directory (errno {e.errno})") from None
    st = os.lstat(p)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise OutputError("private directory path is a symlink or not a directory")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise OutputError("private directory is owned by another user")
    if chmod_existing or missing:
        os.chmod(p, DIR_MODE)
    return p


@contextlib.contextmanager
def private_tempdir() -> Iterator[Path]:
    """A per-run private temp directory, removed on exit, error or interrupt."""
    d = Path(tempfile.mkdtemp(prefix=_TMP_PREFIX))  # mkdtemp creates it 0700
    try:
        os.chmod(d, DIR_MODE)
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
