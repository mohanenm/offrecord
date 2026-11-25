"""Independent network tripwire for subprocess tests.

Loaded via PYTHONPATH before any offrecord code runs, so import-time attempts are caught too.
Does not import offrecord. On any attempt it appends a line to $OFFRECORD_TRIPWIRE_FILE and
raises OSError.
"""

import os
import socket

_path = os.environ.get("OFFRECORD_TRIPWIRE_FILE")


def _trip(kind):
    if _path:
        with open(_path, "a", encoding="ascii") as f:
            f.write(kind + "\n")
    raise OSError("tripwire: network attempt (" + kind + ")")


_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_sendto = socket.socket.sendto


def _is_unix(sock):
    return sock.family == getattr(socket, "AF_UNIX", None)


def _connect(self, address):
    if _is_unix(self):
        return _orig_connect(self, address)
    _trip("connect")


def _connect_ex(self, address):
    if _is_unix(self):
        return _orig_connect_ex(self, address)
    _trip("connect_ex")


def _sendto(self, *args):
    if _is_unix(self):
        return _orig_sendto(self, *args)
    _trip("sendto")


socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
socket.socket.sendto = _sendto
socket.create_connection = lambda *a, **k: _trip("create_connection")
socket.getaddrinfo = lambda *a, **k: _trip("getaddrinfo")
socket.gethostbyname = lambda *a, **k: _trip("gethostbyname")
