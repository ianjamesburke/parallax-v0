"""Suite-wide safety rail: unit tests must never make real HTTP calls."""
from __future__ import annotations

import os

import httpx
import httpx._api
import pytest


@pytest.fixture(autouse=True)
def _block_live_http(monkeypatch):
    """Fail closed unless a deliberately live test opts in.

    Individual request-shape tests replace ``httpx.post``/``get`` with local
    fakes, so this guard remains out of their way while catching omissions.
    """
    if os.environ.get("PARALLAX_LIVE_TESTS") == "1":
        return

    def blocked(*args, **kwargs):
        pytest.fail("real HTTP request attempted; mock it or set PARALLAX_LIVE_TESTS=1")

    monkeypatch.setattr(httpx, "request", blocked)
    monkeypatch.setattr(httpx, "stream", blocked)
    # Module-level convenience functions (`httpx.post`, etc.) resolve these
    # globals in `httpx._api`, not through the package attributes above.
    monkeypatch.setattr(httpx._api, "request", blocked)
    monkeypatch.setattr(httpx._api, "stream", blocked)
    monkeypatch.setattr(httpx.Client, "request", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "request", blocked)
