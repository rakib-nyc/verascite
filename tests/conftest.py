"""Test isolation: no credentials, no network.

The unit suite must be credential-independent by construction rather than by
discipline. A live token in the environment already masked a real assertion
once -- the suite passed locally and would have failed in CI, and the divergence
would have surfaced as a contributor's bug report rather than a test failure.

Two guarantees, both enforced here rather than remembered per-test:

1. ``COURTLISTENER_API_TOKEN`` is removed from the environment.
2. Outbound sockets are blocked, so a test that reaches for the network fails
   immediately and by name instead of silently making a real request.

Tests that genuinely need a socket opt in with ``@pytest.mark.live``.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CREDENTIAL_VARS = (
    "COURTLISTENER_API_TOKEN",
    "GOVINFO_API_KEY",
    "COURTLISTENER_TOKEN",
)


def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs real network access and a token")


class NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _isolated_environment(request, monkeypatch):
    for name in CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)

    if request.node.get_closest_marker("live"):
        yield
        return

    def blocked(self, address, *args, **kwargs):
        raise NetworkBlocked(
            f"the unit suite must not reach the network (attempted {address!r}). "
            "Mock the client, or mark the test @pytest.mark.live."
        )

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    yield
