"""Unit tests for check_api_key and the error handling shared by every tool."""

from __future__ import annotations

import pytest
from va_lis_client import LISClientError
from va_lis_client.models import Partner

from va_lis_mcp import server


def test_check_api_key_valid(fake_client):
    fake_client.partner = Partner(
        PartnerID=1,
        IdentityID=2,
        OrganizationName="Example Agency",
        ContactName="Jane Smith",
        APIKey="00000000-0000-0000-0000-000000000000",
        IsActive=True,
        IsPublic=False,
    )

    result = server.check_api_key()

    assert result == {
        "valid": True,
        "organization": "Example Agency",
        "contact": "Jane Smith",
    }


def test_check_api_key_unregistered(fake_client):
    fake_client.partner = None

    result = server.check_api_key()

    assert result["valid"] is False
    assert "apiregistration" in result["message"]


def test_missing_api_key_error(monkeypatch):
    monkeypatch.delenv("LIS_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        server.check_api_key()

    message = str(excinfo.value)
    assert "LIS_API_KEY" in message
    assert "lis.virginia.gov/apiregistration" in message


def test_lis_client_error_is_wrapped(fake_client, monkeypatch):
    def boom(**kwargs):
        raise LISClientError("request GET /x failed after 8 retries")

    monkeypatch.setattr(fake_client, "get_session_bills", boom)

    with pytest.raises(RuntimeError, match="LIS API error"):
        server.list_bills(20261)
