"""Integration tests: drive the server through the in-memory MCP client."""

from __future__ import annotations

import json

import pytest
from mcp import Client
from va_lis_client.models import LegislationSummaryItem, Member, Patron

from va_lis_mcp import server

EXPECTED_TOOLS = {
    # sessions
    "get_current_session",
    "list_sessions",
    # bills
    "list_bills",
    "get_bill",
    "get_bill_patrons",
    "get_bill_summaries",
    "get_bill_text",
    "get_bill_history",
    # votes
    "get_bill_votes",
    "get_roll_call",
    "get_member_votes",
    # members
    "list_members",
    "get_member",
    "get_member_bills",
    "get_member_committees",
    # committees
    "list_committees",
    "get_committee",
    # meetings
    "list_meetings",
    "list_dockets",
    "list_floor_calendars",
    "get_floor_calendar",
    # diagnostics
    "check_api_key",
}


class FakeBillsClient:
    def __init__(
        self,
        bills: list[LegislationSummaryItem],
        members: list[Member] | None = None,
    ) -> None:
        self._bills = bills
        self._members = members or []

    def get_session_bills(self, session_code=None, session_id=None):
        return self._bills

    def get_bill_texts(self, *, legislation_number=None, session_code=None, **_):
        # No published text, so id resolution falls back to the session list.
        return []

    def get_members(self, session_code, chamber_code=None):
        return self._members


@pytest.fixture(autouse=True)
def reset_service():
    server._service = None


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


async def test_large_payload_tools_ship_json_as_text_only(monkeypatch):
    schmidt = Member(
        MemberID=544,
        MemberNumber="H0402",
        MemberDisplayName="Charlie Schmidt",
        ListDisplayName="Schmidt, Charlie",
        ChamberCode="H",
        PartyCode="R",
        DistrictName="77th",
        MemberStatus="Active",
    )
    monkeypatch.setattr(server, "LISClient", lambda: FakeBillsClient([], [schmidt]))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "list_members",
            {"session_code": 20261, "query": "schmidt", "chamber": "H"},
        )

    assert result.is_error is False
    # structured_output=False: one JSON copy in content, no structuredContent twin.
    assert result.structured_content is None
    data = json.loads(result.content[0].text)
    assert data["count"] == 1
    assert data["members"][0]["member_id"] == 544


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


async def test_enum_parameter_is_validated_by_the_schema(monkeypatch):
    monkeypatch.setattr(server, "LISClient", lambda: FakeBillsClient([]))

    async with Client(server.mcp) as client:
        result = await client.call_tool("list_committees", {"chamber": "X"})

    assert result.is_error is True
    assert "chamber" in result.content[0].text
