"""Run every CLI command in a fresh interpreter with an independent tripwire on the socket
layer (installed via sitecustomize, before offrecord is imported). Any network attempt on any
run-time code path fails this test.

`setup` is the one command allowed to use the network; it is excluded until it exists, and
then only its explicit download path may connect.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SITE = Path(__file__).parent / "netguard_site"
SRC = Path(__file__).parent.parent / "src"

# Extend as commands gain real behaviour (with realistic synthetic arguments).
COMMANDS = [
    ["doctor"],
    ["doctor", "--json"],
    ["redact", "samples/sales-call.txt", "--no-mapping"],
    ["redact", "samples/support-call.vtt", "--no-mapping", "--output-format", "original"],
    ["redact", "samples/finance-review.json", "--no-mapping"],
    ["restore"],
    ["eval", "--set", "tuning", "--system", "baseline"],
    ["eval", "--set", "tuning", "--system", "tier0"],
    ["review"],
    ["--version"],
]


@pytest.mark.parametrize("argv", COMMANDS, ids=lambda a: " ".join(a))
def test_cli_never_touches_network(argv, tmp_path):
    trip = tmp_path / "tripwire.log"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SITE), str(SRC)])
    env["OFFRECORD_TRIPWIRE_FILE"] = str(trip)
    proc = subprocess.run(
        [sys.executable, "-m", "offrecord", *argv],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=SRC.parent,
    )
    assert proc.returncode != 7, proc.stderr
    assert not trip.exists(), f"network attempt(s): {trip.read_text()}"


def test_tripwire_itself_works(tmp_path):
    """Guard against a silently broken tripwire making the test above vacuous."""
    trip = tmp_path / "tripwire.log"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SITE)
    env["OFFRECORD_TRIPWIRE_FILE"] = str(trip)
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('127.0.0.1', 9))\n"
        "except OSError:\n"
        "    pass\n"
    )
    subprocess.run([sys.executable, "-c", code], env=env, check=True, timeout=30)
    assert trip.read_text().strip() == "create_connection"


def _installed(name: str) -> bool:
    from offrecord.models import installed

    return installed(name)


@pytest.mark.skipif(not _installed("gliner-small-v2.1"), reason="Tier 1 models not installed")
def test_tier1_never_touches_network(tmp_path):
    test_cli_never_touches_network(
        ["redact", "samples/standup.srt", "--no-mapping", "--tier", "1"], tmp_path
    )
