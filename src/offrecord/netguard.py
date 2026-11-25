"""Run-time network guard.

Every command except ``setup`` calls :func:`install` before doing work. Two layers:

1. A ``sys.addaudithook`` hook (fires from C, so it also covers direct ``_socket`` use):
   blocks creating INET/INET6 sockets, connect/bind/sendto on them, all name and address
   resolution (``getaddrinfo``, ``getnameinfo``, ``gethostbyname``, ``gethostbyaddr``), and
   (optionally) creating child processes, which would run without the guard.
2. Python-level patches on the ``socket`` module for clear errors on the common paths.

Every blocked attempt is counted; the CLI fails the run (exit 7) if the count is non-zero,
even if a library swallowed the exception. Unix-domain sockets stay allowed.

Audit hooks cannot be removed, so the hook checks a module flag; :func:`uninstall` (tests and
``setup`` only) clears the flag and restores the patches.

Remaining limitation: native extensions that call the OS socket API directly bypass both
layers. See docs/security.md (R3).
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
from collections.abc import Iterable, Iterator
from typing import Any

from offrecord.errors import NetworkBlockedError

_lock = threading.Lock()
_installed = False
_block_processes = False
_hook_added = False
_attempts = 0
_originals: dict[str, Any] = {}
# Absolute paths of helper programs that may be started while ``allow_processes`` is active
# (the clipboard commands only; DECISIONS D-061). Empty everywhere else.
_allowed_programs: frozenset[str] = frozenset()

_INET = {socket.AF_INET, getattr(socket, "AF_INET6", socket.AF_INET)}
_UNIX_FAMILIES = {getattr(socket, "AF_UNIX", object())}

# Offline switches honoured by common ML libraries (Tier 1/2). Set, never read back.
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
}

_RESOLVE_EVENTS = {
    "socket.getaddrinfo",
    "socket.getnameinfo",
    "socket.gethostbyname",
    "socket.gethostbyaddr",
}
_SOCKET_OP_EVENTS = {"socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg"}
_PROCESS_EVENTS = {
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.fork",
    "os.forkpty",
    "pty.spawn",
}


def _count() -> None:
    global _attempts
    with _lock:
        _attempts += 1


def _blocked(kind: str) -> NetworkBlockedError:
    _count()
    # No host/port in the message: an address can itself be sensitive.
    return NetworkBlockedError(f"network access is disabled at run time (blocked {kind})")


def _family_of(sock: Any) -> Any:
    try:
        return sock.family
    except Exception:  # noqa: BLE001  # pragma: no cover - defensive
        return None


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if not _installed:
        return
    if event == "socket.__new__":
        # args: (sock, family, type, proto)
        if len(args) > 1 and args[1] in _INET:
            raise _blocked("socket creation")
        return
    if event in _SOCKET_OP_EVENTS:
        if args and _family_of(args[0]) not in _UNIX_FAMILIES:
            raise _blocked(event.split(".", 1)[1])
        return
    if event in _RESOLVE_EVENTS:
        raise _blocked("name resolution")
    if _block_processes and event in _PROCESS_EVENTS:
        if event in ("subprocess.Popen", "os.posix_spawn") and _program_allowed(args):
            return
        _count()
        raise NetworkBlockedError("starting child processes is disabled at run time")


def _program_allowed(args: tuple[Any, ...]) -> bool:
    """True if a Popen/posix_spawn audit event starts an allow-listed program by absolute path."""
    if not _allowed_programs or not args:
        return False
    prog = args[0]
    if prog is None and len(args) > 1:  # Popen(executable=None, args=[...])
        argv = args[1]
        prog = argv[0] if isinstance(argv, (list, tuple)) and argv else argv
    try:
        prog = os.fsdecode(prog)
    except TypeError:
        return False
    return prog in _allowed_programs


@contextlib.contextmanager
def allow_processes(programs: Iterable[str]) -> Iterator[None]:
    """Temporarily allow starting exactly these programs (absolute paths, no shell). Network
    blocking is unaffected."""
    global _allowed_programs
    progs = frozenset(programs)
    if any(not os.path.isabs(p) for p in progs):
        raise ValueError("allowed programs must be absolute paths")
    with _lock:
        previous, _allowed_programs = _allowed_programs, progs
    try:
        yield
    finally:
        with _lock:
            _allowed_programs = previous


def _is_unix(sock: socket.socket) -> bool:
    return _family_of(sock) in _UNIX_FAMILIES


def install(block_processes: bool = True) -> None:
    """Install the guard (idempotent). ``block_processes=False`` is for the test suite only."""
    global _installed, _block_processes, _hook_added
    with _lock:
        _block_processes = block_processes
        if _installed:
            return
        for k, v in OFFLINE_ENV.items():
            os.environ[k] = v
        if not _hook_added:
            sys.addaudithook(_audit)
            _hook_added = True
        _originals.update(
            connect=socket.socket.connect,
            connect_ex=socket.socket.connect_ex,
            sendto=socket.socket.sendto,
            sendmsg=getattr(socket.socket, "sendmsg", None),
            create_connection=socket.create_connection,
            getaddrinfo=socket.getaddrinfo,
            getnameinfo=socket.getnameinfo,
            gethostbyname=socket.gethostbyname,
            gethostbyname_ex=socket.gethostbyname_ex,
            gethostbyaddr=socket.gethostbyaddr,
        )
        orig_connect = _originals["connect"]
        orig_connect_ex = _originals["connect_ex"]
        orig_sendto = _originals["sendto"]
        orig_sendmsg = _originals["sendmsg"]

        def connect(self: socket.socket, address: Any) -> Any:
            if _is_unix(self):
                return orig_connect(self, address)
            raise _blocked("connect")

        def connect_ex(self: socket.socket, address: Any) -> Any:
            if _is_unix(self):
                return orig_connect_ex(self, address)
            raise _blocked("connect")

        def sendto(self: socket.socket, *args: Any) -> Any:
            if _is_unix(self):
                return orig_sendto(self, *args)
            raise _blocked("sendto")

        def sendmsg(self: socket.socket, *args: Any) -> Any:
            if _is_unix(self):
                return orig_sendmsg(self, *args)
            raise _blocked("sendmsg")

        def create_connection(*_args: Any, **_kwargs: Any) -> Any:
            raise _blocked("create_connection")

        def resolver(*_args: Any, **_kwargs: Any) -> Any:
            raise _blocked("name resolution")

        socket.socket.connect = connect  # type: ignore[method-assign,assignment]
        socket.socket.connect_ex = connect_ex  # type: ignore[method-assign,assignment]
        socket.socket.sendto = sendto  # type: ignore[method-assign,assignment]
        if orig_sendmsg is not None:
            socket.socket.sendmsg = sendmsg  # type: ignore[method-assign,assignment]
        socket.create_connection = create_connection
        socket.getaddrinfo = resolver
        socket.getnameinfo = resolver
        socket.gethostbyname = resolver
        socket.gethostbyname_ex = resolver
        socket.gethostbyaddr = resolver
        _installed = True


def uninstall() -> None:
    """Disable the guard. Used only by tests and by ``setup``."""
    global _installed
    with _lock:
        if not _installed:
            return
        for name in ("connect", "connect_ex", "sendto"):
            setattr(socket.socket, name, _originals[name])
        if _originals["sendmsg"] is not None:
            socket.socket.sendmsg = _originals["sendmsg"]  # type: ignore[method-assign]
        for name in (
            "create_connection",
            "getaddrinfo",
            "getnameinfo",
            "gethostbyname",
            "gethostbyname_ex",
            "gethostbyaddr",
        ):
            setattr(socket, name, _originals[name])
        _originals.clear()
        _installed = False


def is_installed() -> bool:
    return _installed


def attempts() -> int:
    """Number of blocked attempts since the process started (or last reset)."""
    return _attempts


def reset_attempts() -> None:
    global _attempts
    with _lock:
        _attempts = 0


def self_test() -> bool:
    """Return True if the guard blocks creating an INET socket and resolving a name.

    Nothing can leave the machine even if the guard were broken: the socket is never
    connected, and "localhost" resolves locally. The attempt counter is restored afterwards.
    """
    global _attempts
    was_installed = _installed
    before = _attempts
    install(block_processes=_block_processes)
    ok = True
    try:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.close()
            ok = False
        except NetworkBlockedError:
            pass
        try:
            socket.getaddrinfo("localhost", None)
            ok = False
        except NetworkBlockedError:
            pass
    finally:
        with _lock:
            _attempts = before
        if not was_installed:
            uninstall()
    return ok
