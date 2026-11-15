"""Shared pytest defaults: keep the suite light, deterministic and offline."""
import os
import socket

import pytest

os.environ.setdefault("LAYA_ENABLED", "false")
os.environ.setdefault("GH_OPS_LLM_ENABLED", "false")

_real_connect = socket.socket.connect


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """In-process tests must never reach GitHub, Telegram or LLM providers.

    A test that touches the network fails loudly instead of silently sending a
    real Telegram message with credentials loaded from the local .env.
    Subprocess-based CLI tests are unaffected (separate process).
    """
    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host in ("127.0.0.1", "::1", "localhost"):
            return _real_connect(self, address)
        raise RuntimeError(f"test attempted a real network connection to {host!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded)
