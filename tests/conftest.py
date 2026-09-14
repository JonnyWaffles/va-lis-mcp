"""Fixtures shared by every tool test module."""

from __future__ import annotations

import pytest

from tests.fakes import FakeClient
from va_lis_mcp import server


@pytest.fixture(autouse=True)
def reset_service(monkeypatch):
    # The service owns every cache, so a fresh instance per test resets them all.
    monkeypatch.setattr(server, "_service", None)


@pytest.fixture
def fake_client(monkeypatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setattr(server, "LISClient", lambda: client)
    return client
