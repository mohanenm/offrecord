from __future__ import annotations

import _socket
import socket
import subprocess
import sys

import pytest

from offrecord import netguard
from offrecord.errors import NetworkBlockedError


def test_guard_installed_in_tests():
    assert netguard.is_installed()


@pytest.mark.parametrize("family", [socket.AF_INET, socket.AF_INET6])
@pytest.mark.parametrize("kind", [socket.SOCK_STREAM, socket.SOCK_DGRAM])
def test_inet_socket_creation_blocked_and_counted(family, kind):
    with pytest.raises(NetworkBlockedError):
        socket.socket(family, kind)
    assert netguard.attempts() == 1


def test_raw_socket_module_blocked():
    with pytest.raises(NetworkBlockedError):
        _socket.socket(socket.AF_INET, socket.SOCK_STREAM)


@pytest.mark.parametrize(
    "call",
    [
        lambda: socket.create_connection(("127.0.0.1", 9)),
        lambda: socket.getaddrinfo("localhost", 80),
        lambda: socket.gethostbyname("localhost"),
        lambda: socket.getnameinfo(("127.0.0.1", 80), 0),
        lambda: _socket.getaddrinfo("localhost", 80),
        lambda: _socket.getnameinfo(("127.0.0.1", 80), 0),
    ],
)
def test_resolution_and_connect_blocked(call):
    with pytest.raises(NetworkBlockedError):
        call()


def test_blocked_error_is_connection_error():
    assert issubclass(NetworkBlockedError, ConnectionError)


def test_message_has_no_address():
    with pytest.raises(NetworkBlockedError) as ei:
        socket.create_connection(("127.0.0.1", 4242))
    assert "127.0.0.1" not in str(ei.value)
    assert "4242" not in str(ei.value)


def test_unix_sockets_allowed():
    a, b = socket.socketpair()
    try:
        a.sendall(b"ok")
        assert b.recv(2) == b"ok"
    finally:
        a.close()
        b.close()


def test_offline_env_set():
    import os

    assert os.environ.get("HF_HUB_OFFLINE") == "1"


def test_process_blocking_mode():
    netguard.install(block_processes=True)  # already installed: switches mode
    try:
        with pytest.raises(NetworkBlockedError):
            subprocess.run([sys.executable, "-c", "pass"], check=False)
    finally:
        netguard.install(block_processes=False)
    netguard.reset_attempts()
    subprocess.run([sys.executable, "-c", "pass"], check=True)


def test_self_test_does_not_count():
    assert netguard.self_test() is True
    assert netguard.attempts() == 0
