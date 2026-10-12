"""Unit tests for the session and bill tools, with the LIS client faked out."""

from __future__ import annotations

from datetime import datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from va_lis_client.models import (
    Legislation,
    LegislationEvent,
    LegislationNumberEntry,
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
    make_amended_bill,
    make_bill_version,
    make_conference_bill,
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


def test_get_session_lists_the_milestones_oldest_first(fake_client):
    fake_client.session_details = {
        20261: Session(
            SessionID=59,
            SessionCode="20261",
            DisplayName="Regular Session",
            SessionYear=2026,
            SessionTypeID=1,
            SessionType="Regular",
            IsDefault=False,
            IsActive=True,
            SessionEvents=[
                {
                    "SessionEventID": 302,
                    "DisplayName": "Session Start",
                    "ActualDate": "2026-01-14T00:00:00",
                    "EventTypeID": 1,
                },
                {
                    "SessionEventID": 306,
                    "DisplayName": "Reconvene",
                    "ProjectedDate": "2026-04-22T00:00:00",
                    "EventTypeID": 9,
                },
                {
                    "SessionEventID": 303,
                    "DisplayName": "Prefile Date",
                    "ActualDate": "2025-11-17T00:00:00",
                    "EventTypeID": 2,
                },
            ],
        )
    }

    result = server.get_session(20261)

    assert result["session_code"] == 20261
    assert result["session_id"] == 59
    assert result["events"] == [
        {"name": "Prefile Date", "date": "2025-11-17T00:00:00"},
        {"name": "Session Start", "date": "2026-01-14T00:00:00"},
        {"name": "Reconvene", "projected_date": "2026-04-22T00:00:00"},
    ]


def test_get_session_unknown_code_points_at_list_sessions(fake_client):
    with pytest.raises(ToolError) as excinfo:
        server.get_session(19001)

    message = str(excinfo.value)
    assert "19001" in message
    assert "list_sessions" in message


def test_get_bill_versions_links_the_files_of_each_version(fake_client):
    fake_client.legislative_numbers = [
        LegislationNumberEntry(LegislationNumber="HB1", LegislationID=98525)
    ]
    fake_client.bill_versions = {
        98525: [
            make_bill_version(257719, "HB1", "Introduced", when=datetime(2025, 11, 17, 7, 28)),
            make_bill_version(
                261000,
                "HB1SC1",
                "Substitute",
                description="Commerce and Labor Substitute",
                chamber="S",
                sponsor="Committee",
                disposition="Offered",
                files=False,
            ),
        ]
    }

    result = server.get_bill_versions("hb0001", 20261)

    assert result["bill_number"] == "HB1"
    assert result["count"] == 2
    introduced, substitute = result["versions"]
    assert introduced == {
        "document_code": "HB1",
        "version": "Introduced",
        "date": "2025-11-17T07:28:00",
        "chamber": "H",
        "sponsor": "House",
        "disposition": "Posted",
        "pdf": "https://files.test/257719.PDF",
        "html": "https://files.test/257719.HTML",
        "impact_statements": ["https://files.test/257719-fis.PDF"],
    }
    # The version's chamber, not the bill's, and the name behind the type.
    assert substitute == {
        "document_code": "HB1SC1",
        "version": "Substitute",
        "description": "Commerce and Labor Substitute",
        "chamber": "S",
        "sponsor": "Committee",
        "disposition": "Offered",
    }
    # Only a request for one bill by id fills the file links.
    assert (
        "get_bill_versions_by_id",
        {"legislation_id": 98525, "legislation_ids": None},
    ) in fake_client.calls


def test_get_bill_versions_unknown_bill_names_it(fake_client):
    with pytest.raises(ToolError) as excinfo:
        server.get_bill_versions("HB999", 20261)

    message = str(excinfo.value)
    assert "HB999" in message
    assert "20261" in message


AMENDED_ROWS = [
    make_amended_bill(
        99920,
        "HB1045",
        "Alcoholic Beverage Control Authority; performing arts facility license.",
        (
            ("HB1045AS1", "S", "Rehabilitation and Social Services Amendment", "Adopted"),
            ("HB1045EDOC", "S", "Senate Amendment", "Engrossed"),
        ),
    ),
    # LIS repeats the bill once per summary version, amendments and all.
    make_amended_bill(
        99920,
        "HB1045",
        "Alcoholic Beverage Control Authority; performing arts facility license.",
        (
            ("HB1045AS1", "S", "Rehabilitation and Social Services Amendment", "Adopted"),
            ("HB1045EDOC", "S", "Senate Amendment", "Engrossed"),
        ),
        version="SUMMARY AS PASSED",
    ),
    make_amended_bill(
        98536,
        "SB2",
        "Paid family and medical leave insurance program.",
        (("SB2AS1", "S", "Finance and Appropriations Amendment", "Not Adopted"),),
        chamber="S",
    ),
]


def test_list_amended_bills_collapses_the_repeats_and_lists_the_amendments(fake_client):
    fake_client.amended = AMENDED_ROWS

    result = server.list_amended_bills(20261, amended_by="S")

    assert result["amended_by"] == "S"
    assert result["total_matching"] == 2
    assert fake_client.calls == [
        ("get_amended_legislation", {"session_code": 20261, "chamber_code": "S"})
    ]
    house_bill, senate_bill = result["bills"]
    assert house_bill == {
        "legislation_id": 99920,
        "bill_number": "HB1045",
        "description": "Alcoholic Beverage Control Authority; performing arts facility license.",
        # The bill's chamber. The amendments below are the Senate's.
        "chamber": "H",
        "status": "Acts of Assembly Chapter",
        "chief_patron": "Betsy B. Carr",
        "amendments": [
            {
                "document_code": "HB1045AS1",
                "chamber": "S",
                "description": "Rehabilitation and Social Services Amendment",
                "disposition": "Adopted",
                "date": "2026-02-23T09:45:00",
            },
            {
                "document_code": "HB1045EDOC",
                "chamber": "S",
                "description": "Senate Amendment",
                "disposition": "Engrossed",
                "date": "2026-02-23T09:45:00",
            },
        ],
    }
    assert senate_bill["amendments"][0]["disposition"] == "Not Adopted"


def test_list_amended_bills_query_and_pagination(fake_client):
    fake_client.amended = AMENDED_ROWS

    by_query = server.list_amended_bills(20261, query="LEAVE")
    paged = server.list_amended_bills(20261, limit=1, offset=1)

    assert "amended_by" not in by_query
    assert [b["bill_number"] for b in by_query["bills"]] == ["SB2"]
    assert paged["total_matching"] == 2
    assert [b["bill_number"] for b in paged["bills"]] == ["SB2"]


def test_list_conference_bills_sends_the_session_id_never_the_code(fake_client):
    fake_client.conference = [
        make_conference_bill(98529, "HB5", "Employment; paid sick leave, civil penalties."),
        make_conference_bill(98891, "SB137", "Obstructing health care facility.", "Failed"),
    ]

    result = server.list_conference_bills(20261, member_id=544)

    # LIS ignores a session code here and answers for every session.
    assert fake_client.calls[-1] == (
        "get_conference_legislation",
        {"session_id": 59, "chamber_code": None, "member_id": 544},
    )
    assert result["total_matching"] == 2
    assert result["bills"] == [
        {
            "legislation_id": 98529,
            "bill_number": "HB5",
            "description": "Employment; paid sick leave, civil penalties.",
            "chamber": "H",
            "type": "B",
            "status": "Acts of Assembly Chapter",
        },
        {
            "legislation_id": 98891,
            "bill_number": "SB137",
            "description": "Obstructing health care facility.",
            "chamber": "S",
            "type": "B",
            "status": "Failed",
        },
    ]
