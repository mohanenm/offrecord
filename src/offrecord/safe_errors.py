"""Content-free reporting of exceptions and warnings.

Unexpected exceptions are reported by type name and code location only. Exception messages and
local variables are never printed: third-party messages (decode errors, JSON errors, regex
errors) can embed fragments of the input.
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
import warnings
from types import TracebackType
from typing import Any, TextIO

from offrecord.errors import OffrecordError

_debug = False


def set_debug(flag: bool) -> None:
    global _debug
    _debug = flag


def _frames(tb: TracebackType | None) -> list[str]:
    out = []
    for fs in traceback.extract_tb(tb):
        # File, line and function name only. Source lines are code, but we keep output minimal.
        out.append(f"  {fs.filename}:{fs.lineno} in {fs.name}")
    return out


def format_exception(exc: BaseException) -> str:
    """Return a content-free description of ``exc``."""
    if isinstance(exc, OffrecordError):
        return f"error[{exc.code}]: {exc.detail}"
    tb = exc.__traceback__
    last = traceback.extract_tb(tb)[-1] if tb is not None else None
    where = f" at {last.filename}:{last.lineno}" if last is not None else ""
    msg = (
        f"internal error ({type(exc).__name__}){where}. "
        "The message is withheld because it may contain input data."
    )
    if _debug:
        msg += "\nstack (no messages, no locals):\n" + "\n".join(_frames(tb))
    else:
        msg += " Rerun with --debug for a content-free stack trace."
    return msg


def report(exc: BaseException, stream: TextIO | None = None) -> None:
    stream = stream if stream is not None else sys.stderr
    print(f"offrecord: {format_exception(exc)}", file=stream)


def _excepthook(
    exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
) -> None:
    if exc.__traceback__ is None:
        exc.__traceback__ = tb
    report(exc)


def _showwarning(
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: TextIO | None = None,
    line: str | None = None,
) -> None:
    # Category and location only; warning text from libraries may quote input.
    stream = file if file is not None else sys.stderr
    print(f"offrecord: warning ({category.__name__}) at {filename}:{lineno}", file=stream)


def _thread_hook(args: threading.ExceptHookArgs) -> None:
    if args.exc_value is not None:
        report(args.exc_value)


def _unraisable_hook(unraisable: Any) -> None:
    # e.g. exceptions in __del__ or callbacks: type and location only.
    exc = unraisable.exc_value
    name = type(exc).__name__ if exc is not None else "unknown"
    print(f"offrecord: ignored internal error ({name})", file=sys.stderr)


def install_hooks() -> None:
    """Replace every stderr path Python and the stdlib use to print exception text."""
    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook
    sys.unraisablehook = _unraisable_hook
    warnings.showwarning = _showwarning
    # Logging: no last-resort handler (it prints messages, which libraries may fill with data),
    # a NullHandler on the root logger, and no tracebacks from handler errors.
    logging.lastResort = None  # type: ignore[assignment]
    logging.raiseExceptions = False
    root = logging.getLogger()
    if not any(isinstance(h, logging.NullHandler) for h in root.handlers):
        root.addHandler(logging.NullHandler())
