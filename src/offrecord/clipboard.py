"""Clipboard access for ``offrecord paste`` / ``unpaste`` (DECISIONS D-061).

Uses the platform's clipboard helper, started by absolute path with no shell and a minimal
environment, under ``netguard.allow_processes`` (network blocking stays on). Clipboard text is
transcript content: it is never logged or echoed. Supported: macOS (pbcopy/pbpaste), Wayland
(wl-copy/wl-paste), X11 (xclip or xsel).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

from offrecord import netguard
from offrecord.errors import InputLimitError, UsageError
from offrecord.limits import DEFAULT_LIMITS

TIMEOUT = 10  # seconds
# Only what the helpers need to find the display; nothing else is passed on.
_ENV_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XAUTHORITY", "LANG", "LC_ALL")


@dataclass(frozen=True)
class Backend:
    name: str
    copy: tuple[str, ...]
    paste: tuple[str, ...]


def _find(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return os.path.realpath(p)
    return None


def backend() -> Backend:
    if sys.platform == "darwin":
        if os.path.exists("/usr/bin/pbcopy") and os.path.exists("/usr/bin/pbpaste"):
            return Backend("macos", ("/usr/bin/pbcopy",), ("/usr/bin/pbpaste",))
    elif sys.platform.startswith("linux"):
        if os.environ.get("WAYLAND_DISPLAY"):
            c, p = _find("wl-copy"), _find("wl-paste")
            if c and p:
                return Backend("wayland", (c,), (p, "--no-newline"))
        if os.environ.get("DISPLAY"):
            x = _find("xclip")
            if x:
                return Backend("xclip", (x, "-selection", "clipboard"),
                               (x, "-selection", "clipboard", "-o"))  # fmt: skip
            x = _find("xsel")
            if x:
                return Backend("xsel", (x, "--clipboard", "--input"),
                               (x, "--clipboard", "--output"))  # fmt: skip
    raise UsageError(
        "no supported clipboard helper found (macOS pbcopy, wl-clipboard, xclip or xsel); "
        "use stdin/stdout instead: `offrecord redact -`"
    )


def _run(argv: tuple[str, ...], data: bytes | None) -> bytes:
    env = {k: os.environ[k] for k in _ENV_KEYS if k in os.environ}
    with netguard.allow_processes([argv[0]]):
        try:
            res = subprocess.run(  # noqa: S603 - fixed absolute argv, no shell
                list(argv), input=data, capture_output=True, timeout=TIMEOUT, env=env,
                check=False, close_fds=True,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired):
            raise UsageError("the clipboard helper failed to run") from None
    if res.returncode != 0:
        raise UsageError("the clipboard helper reported an error")
    return res.stdout


def read_text(max_bytes: int = DEFAULT_LIMITS.max_bytes) -> str:
    data = _run(backend().paste, None)
    if len(data) > max_bytes:
        raise InputLimitError("clipboard content is larger than the input limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise UsageError("clipboard content is not UTF-8 text") from None
    if not text.strip():
        raise UsageError("the clipboard is empty")
    return text


def write_text(text: str) -> None:
    _run(backend().copy, text.encode("utf-8"))
