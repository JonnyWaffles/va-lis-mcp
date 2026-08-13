"""Integration tests: drive the server through the in-memory MCP client."""

from __future__ import annotations

import pytest
from mcp import Client
from va_lis_client.models import LegislationSummaryItem, Patron

from va_lis_mcp import server

EXPECTED_TOOLS = {
    "get_current_session",
    "list_sessions",
    "list_bills",
    "get_bill",
    "get_bill_summaries",
    "get_bill_text",
    "get_bill_history",
    "check_api_key",
}


class FakeBillsClient:
    def __init__(self, bills: list[LegislationSummaryItem]) -> None:
        self._bills = bills

    def get_session_bills(self, session_code=None, session_id=None):
        return self._bills


@pytest.fixture(autouse=True)
def reset_bill_cache():
    server._bill_list_cache.clear()


async def test_all_tools_registered():
    async with Client(server.mcp) as client:
        result = await client.list_tools()

    tools = {tool.name: tool for tool in result.tools}
    assert set(tools) == EXPECTED_TOOLS
    for tool in tools.values():
        assert tool.description, f"{tool.name} has no description"


async def test_list_bills_end_to_end(monkeypatch):
    hb1 = LegislationSummaryItem(
        LegislationID=98525,
        LegislationNumber="HB1",
        Description="Minimum wage; increases incrementally",
        ChamberCode="H",
        LegislationTypeCode="B",
        LegislationStatus="Awaiting Governor's Action",
        Patrons=[
            Patron(
                LegislationID=98525,
                ChamberCode="H",
                MemberID=419,
                MemberNumber="H0173",
                PatronTypeID=1,
                Name="Chief Patron",
                MemberDisplayName="Jeion A. Ward",
            )
        ],
    )
    monkeypatch.setattr(server, "LISClient", lambda: FakeBillsClient([hb1]))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "list_bills",
            {"session_code": 20261, "query": "wage"},
        )

    assert result.is_error is False
    data = result.structured_content
    assert data["total_matching"] == 1
    assert data["bills"][0]["bill_number"] == "HB1"
    assert data["bills"][0]["chief_patron"] == "Jeion A. Ward"


async def test_tool_error_carries_readable_message(monkeypatch):
    monkeypatch.setattr(server, "LISClient", lambda: FakeBillsClient([]))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_bill",
            {"bill_number": "HB999", "session_code": 20261},
        )

    assert result.is_error is True
    assert "HB999" in result.content[0].text
    assert "20261" in result.content[0].text
