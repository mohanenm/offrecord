"""Test configuration.

All tests run with the run-time network guard installed: no test may touch the network.
"""

from __future__ import annotations

import pytest

from offrecord import netguard


@pytest.fixture(autouse=True, scope="session")
def _no_network_for_tests():
    # Subprocesses stay allowed in tests (the no-network test spawns the CLI).
    netguard.install(block_processes=False)
    yield
    netguard.uninstall()


@pytest.fixture(autouse=True)
def _fresh_attempt_counter():
    netguard.reset_attempts()
    yield
    # CLI tests switch the shared guard into process-blocking mode; reset it so test order
    # does not matter (tests that spawn the CLI need subprocesses).
    netguard.install(block_processes=False)


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path_factory, monkeypatch):
    # Never pick up the developer's real deny-list (~/.config/offrecord/config.yaml).
    monkeypatch.setenv("OFFRECORD_CONFIG", str(tmp_path_factory.mktemp("cfg") / "absent.yaml"))
