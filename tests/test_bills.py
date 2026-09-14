"""Unit tests for the session and bill tools, with the LIS client faked out."""

from __future__ import annotations

from datetime import datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from va_lis_client.models import (
    Legislation,
    LegislationEvent,
    LegislationSession,
    LegislationStatus,
    LegislationSummary,
    Patron,
    Session,
)

from tests.factories import (
    HB1,
    HB2,
    SB10,
    make_event,
    make_patron,
    make_text_detail,
    make_text_item,
)
from tests.fakes import call_names
from va_lis_mcp import server


def test_get_current_session_includes_note(fake_client):
    fake_client.default_session = Session(
        SessionID=61,
        SessionCode="20271",
        DisplayName="Regular Session",
        SessionYear=2027,
        SessionTypeID=1,
        SessionType="Regular",
        IsDefault=True,
        IsActive=True,
    )

    result = server.get_current_session()

    assert result["session_code"] == 20271
    assert "working session" in result["note"]


def test_list_bills_filters_case_insensitively(fake_client):
    fake_client.bills = [HB1, HB2, SB10]

    result = server.list_bills(20261, query="MINIMUM WAGE")

    assert result["total_matching"] == 1
    assert result["count"] == 1
    assert result["bills"][0]["bill_number"] == "HB1"
    assert result["bills"][0]["chief_patron"] == "Jeion A. Ward"


def test_list_bills_filters_on_bill_number(fake_client):
    fake_client.bills = [HB1, HB2, SB10]

    result = server.list_bills(20261, query="sb1")

    assert [b["bill_number"] for b in result["bills"]] == ["SB10"]


def test_list_bills_paginates(fake_client):
    fake_client.bills = [HB1, HB2, SB10]

    result = server.list_bills(20261, limit=1, offset=1)

    assert result["total_matching"] == 3
    assert result["count"] == 1
    assert result["offset"] == 1
    assert result["bills"][0]["bill_number"] == "HB2"


def test_session_bill_list_is_cached(fake_client):
    fake_client.bills = [HB1]

    server.list_bills(20261)
    server.list_bills(20261)

    assert call_names(fake_client).count("get_session_bills") == 1


def test_get_bill_not_found_names_bill_and_session(fake_client):
    fake_client.bills = [HB1]

    with pytest.raises(ToolError) as excinfo:
        server.get_bill("HB999", 20261)

    message = str(excinfo.value)
    assert "HB999" in message
    assert "20261" in message


def test_get_bill_condenses_patrons_and_resolves_status(fake_client):
    fake_client.bills = [HB1]
    fake_client.bill = Legislation(
        LegislationID=98525,
        LegislationNumber="HB1",
        Description="Minimum wage; increases incrementally",
        LegislationTitle="An Act to amend and reenact ...",
        ChamberCode="H",
        LegislationTypeCode="B",
        LegislationStatusID=38,
        IntroductionDate=datetime(2025, 11, 17, 7, 28),
        Patrons=[
            make_patron("Jeion A. Ward"),
            make_patron("Alex Q. Askew", role_id=4, role="Co-Patron"),
            make_patron("Nadarius E. Clark", role_id=4, role="Co-Patron"),
        ],
        Sessions=[LegislationSession(SessionID=59, SessionCode="20261", IsPrefile=True)],
    )
    fake_client.statuses = [
        LegislationStatus(
            LegislationStatusID=38,
            Name="Awaiting Governor's Action",
            DisplayName="Awaiting Governor's Action",
        )
    ]

    result = server.get_bill("hb0001", 20261)

    assert result["patrons"] == {
        "Chief Patron": ["Jeion A. Ward"],
        "Co-Patron": ["Alex Q. Askew", "Nadarius E. Clark"],
    }
    assert result["sessions"] == [{"session_code": 20261, "is_prefile": True}]
    assert result["status"] == "Awaiting Governor's Action"
    assert result["introduction_date"] == "2025-11-17T07:28:00"
    assert "offered_date" not in result


def test_get_bill_names_the_role_of_a_patron_sent_without_one(fake_client):
    # The thin patron shape carries no Name; the role comes from PatronTypeID.
    fake_client.bills = [HB1]
    fake_client.bill = Legislation(
        LegislationID=98525,
        LegislationNumber="HB1",
        Description="Minimum wage; increases incrementally",
        ChamberCode="H",
        LegislationTypeCode="B",
        Patrons=[Patron(MemberID=419, PatronTypeID=1, MemberDisplayName="Jeion A. Ward")],
    )

    result = server.get_bill("HB1", 20261)

    assert result["patrons"] == {"Chief Patron": ["Jeion A. Ward"]}


def test_get_bill_patrons_lists_member_ids_and_roles(fake_client):
    fake_client.texts = [make_text_item(257719, "HB1", "Introduced")]
    fake_client.bill_patrons = [
        make_patron("Jeion A. Ward"),
        make_patron("Alex Q. Askew", role_id=4, role="Co-Patron", member_id=486),
    ]

    result = server.get_bill_patrons("hb0001", 20261)

    assert result["bill_number"] == "HB1"
    assert result["count"] == 2
    assert result["patrons"][0] == {
        "member_id": 419,
        "member_number": "H0173",
        "name": "Jeion A. Ward",
        "role": "Chief Patron",
        "chamber": "H",
    }
    assert result["patrons"][1]["member_id"] == 486
    assert result["patrons"][1]["role"] == "Co-Patron"
    # Resolved through the text list, so the 3 MB session list was not fetched.
    assert ("get_bill_patrons", {"legislation_id": 98525}) in fake_client.calls
    assert "get_session_bills" not in call_names(fake_client)


def test_get_bill_summaries_strips_html_and_normalizes_number(fake_client):
    fake_client.summaries = [
        LegislationSummary(
            LegislationID=98525,
            SessionID=59,
            LegislationSummaryID=134212,
            SummaryVersion="SUMMARY AS PASSED",
            LegislationNumber="HB1",
            Summary='<p class="sumtext"><b>Minimum wage.</b> Increases the minimum wage.</p>',
            IsActive=True,
        )
    ]

    result = server.get_bill_summaries("HB0001", 20261)

    assert result["bill_number"] == "HB1"
    summary = result["summaries"][0]
    assert summary["summary"] == "Minimum wage. Increases the minimum wage."
    assert summary["is_active"] is True
    assert ("get_bill_summaries", {"legislation_number": "HB1", "session_code": 20261}) in (
        fake_client.calls
    )


def test_get_bill_text_strips_html_and_truncates(fake_client):
    long_body = "<p>" + "x" * (server._TEXT_CHAR_LIMIT + 500) + "</p>"
    fake_client.texts = [
        make_text_item(257719, "HB1", "Introduced"),
        make_text_item(266240, "HB1ER", "Enrolled"),
    ]
    fake_client.text_details = [
        make_text_detail(257719, "HB1", "<p>short intro text</p>"),
        make_text_detail(266240, "HB1ER", long_body),
    ]

    result = server.get_bill_text("hb0001", 20261)

    assert result["document_code"] == "HB1ER"  # highest text id wins by default
    assert result["truncated"] is True
    assert len(result["text"]) == server._TEXT_CHAR_LIMIT
    assert "<" not in result["text"]
    codes = [v["document_code"] for v in result["available_versions"]]
    assert codes == ["HB1", "HB1ER"]


def test_get_bill_text_short_body_is_not_truncated(fake_client):
    fake_client.texts = [make_text_item(257719, "HB1", "Introduced")]
    fake_client.text_details = [
        make_text_detail(257719, "HB1", "<p>Be it enacted, <em class='new'>now</em>.</p>")
    ]

    result = server.get_bill_text("HB1", 20261, document_code="hb1")

    assert result["truncated"] is False
    assert result["text"] == "Be it enacted, now."


def test_get_bill_text_unknown_version_lists_available(fake_client):
    fake_client.texts = [
        make_text_item(257719, "HB1", "Introduced"),
        make_text_item(266240, "HB1ER", "Enrolled"),
    ]

    with pytest.raises(ToolError) as excinfo:
        server.get_bill_text("HB1", 20261, document_code="HB1S1")

    message = str(excinfo.value)
    assert "HB1S1" in message
    assert "HB1ER" in message


def test_get_bill_history_flags_passage_and_joins_statuses(fake_client):
    fake_client.bills = [HB1]
    fake_client.events = [
        LegislationEvent(
            LegislationEventID=1,
            EventCode="H0201",
            EventDate=datetime(2026, 1, 20),
            Description="Reported from Commerce and Energy",
            Status="Reported Out-House",
            ActorType="Committee",
            CommitteeName="Commerce and Energy",
        ),
        LegislationEvent(
            LegislationEventID=2,
            EventCode="H5000",
            EventDate=datetime(2026, 2, 1),
            Status="Passed House",
            ActorType="House",
            VoteTally="(64-Y 34-N)",
        ),
    ]
    fake_client.statuses = [
        LegislationStatus(LegislationStatusID=3, Name="Reported Out-House", DisplayName="In House"),
        LegislationStatus(LegislationStatusID=4, Name="Passed House", DisplayName="Passed House"),
    ]

    result = server.get_bill_history("HB1", 20261)

    assert result["legislation_id"] == 98525
    assert result["count"] == 2
    first, second = result["events"]
    assert first["status"] == "In House"  # joined on status Name, not the raw internal name
    assert first["committee"] == "Commerce and Energy"
    assert "is_passage" not in first
    assert second["is_passage"] is True  # resulting status "Passed House" marks passage
    assert second["vote_tally"] == "(64-Y 34-N)"
    # LIS sent no description, and the ambiguous event type reference no
    # longer fills one in, so the key is absent.
    assert "description" not in second


def test_get_bill_history_links_events_to_their_votes(fake_client):
    fake_client.bills = [HB1]
    fake_client.events = [
        make_event(1, "H1401", description="Referred to Committee on Commerce and Energy"),
        make_event(2, "H5000", vote_id=294006, tally="(64-Y 34-N 0-A)", status="Passed House"),
    ]

    result = server.get_bill_history("HB1", 20261)

    first, second = result["events"]
    assert "vote_id" not in first
    assert second["vote_id"] == 294006


def test_reference_lists_are_fetched_once(fake_client):
    fake_client.bills = [HB1]
    fake_client.events = []

    server.get_bill_history("HB1", 20261)
    server.get_bill_history("HB1", 20261)

    names = call_names(fake_client)
    assert names.count("get_legislation_statuses") == 1
    # The 2.19 MB event type reference is not fetched at all any more: LIS
    # reuses one EventCode for both vote outcomes, so its IsPassage flag
    # cannot mark passage.
    assert names.count("get_event_types") == 0
