"""Unit tests for the minutes book and communication tools, with the client faked out."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tests.factories import (
    COMMITTEES,
    make_communication,
    make_communication_row,
    make_minutes_book,
    make_minutes_book_row,
)
from tests.fakes import call_names
from va_lis_mcp import server

BOOKS = [
    make_minutes_book_row(3700, datetime(2026, 2, 10)),
    make_minutes_book_row(
        3678,
        datetime(2026, 2, 4),
        chamber="H",
        committee=(8, "Courts of Justice"),
        status="In Progress",
    ),
    make_minutes_book_row(3861, datetime(2026, 4, 22)),
]

MINUTES_CATEGORIES = [
    {
        "MinutesCategoryID": 3055,
        "Sequence": 2,
        "CategoryType": "Order",
        "CategoryDescription": "Order of Business",
        "CategoryText": "Order of Business",
        "MinutesEntries": [
            {
                "MinutesEntryID": 31269,
                "MinutesCategoryID": 3055,
                "EntryText": "Call to Order",
                "MinutesActivities": [
                    {"MinutesActivityID": 36217, "Description": "Called to Order at 12 m."}
                ],
                "MinutesSummaries": [
                    {"MinutesEntryID": 31269, "MinutesSummary": "<p>The Senate met at 12 m.</p>"}
                ],
            }
        ],
    },
    {
        "MinutesCategoryID": 3056,
        "Sequence": 4,
        "CategoryType": "General",
        # The description is the generic kind; the text is the printed heading.
        "CategoryDescription": "Senate Bill with Governor's Recommendations",
        "CategoryText": "Senate Bills with Governor's Recommendations",
        "MinutesEntries": [
            {
                "MinutesEntryID": 31270,
                "MinutesCategoryID": 3056,
                "LegislationID": 98536,
                "LegislationNumber": "SB2",
                "LegislationDescription": "Paid family and medical leave insurance program.",
                "MinutesActivities": [
                    {
                        "MinutesActivityID": 36240,
                        "Description": "Senate concurred in Governor's recommendation",
                        "EventCode": "S7300",
                        "VoteID": 302328,
                        "VoteTally": "(18-Y 18-N 0-A, Chair votes Yea)",
                    }
                ],
            },
            {
                "MinutesEntryID": 31271,
                "MinutesCategoryID": 3056,
                "LegislationID": 98560,
                "LegislationNumber": "SB24",
                "LegislationDescription": "Discovery materials or evidence.",
                "MinutesActivities": [
                    {
                        "MinutesActivityID": 36241,
                        "Description": "Passed by for the day",
                        "VoteID": 302400,
                        "VoteTally": "(Voice Vote)",
                    },
                    {
                        "MinutesActivityID": 36242,
                        "Description": "Senate concurred in Governor's recommendation",
                        "VoteID": 302401,
                        "VoteTally": "block vote (21-Y 18-N 0-A)",
                        "IsBlock": True,
                    },
                ],
            },
        ],
    },
]


def test_list_minutes_books_sorts_newest_first_and_marks_floor_books(fake_client):
    fake_client.minutes_books = BOOKS

    result = server.list_minutes_books(20261)

    assert result["total_matching"] == 3
    floor, _, committee = result["books"]
    assert [b["minutes_book_id"] for b in result["books"]] == [3861, 3700, 3678]
    assert floor == {
        "minutes_book_id": 3861,
        "date": "2026-04-22T00:00:00",
        "chamber": "S",
        "is_floor": True,
        "status": "Closed",
        "calendar_id": 24861,
        "html": "https://files.test/3861.HTML",
    }
    assert committee["committee_id"] == 8
    assert committee["committee"] == "Courts of Justice"
    assert committee["status"] == "In Progress"
    assert "is_floor" not in committee
    assert "calendar_id" not in committee


def test_list_minutes_books_picks_one_kind(fake_client):
    fake_client.minutes_books = BOOKS

    committee = server.list_minutes_books(20261, kind="Committee")
    floor = server.list_minutes_books(20261, kind="Floor", limit=1, offset=1)

    assert [b["minutes_book_id"] for b in committee["books"]] == [3678]
    assert floor["total_matching"] == 2
    assert [b["minutes_book_id"] for b in floor["books"]] == [3700]


def test_list_minutes_books_resolves_the_committee_and_sends_the_dates(fake_client):
    fake_client.committees = COMMITTEES
    fake_client.minutes_books = BOOKS

    result = server.list_minutes_books(
        20261, committee="H08", start_date="2026-02-01", end_date="2026-02-10"
    )

    assert [b["minutes_book_id"] for b in result["books"]] == [3678]
    assert fake_client.calls[-1] == (
        "get_minutes_books",
        {
            "session_code": 20261,
            "committee_id": 8,
            "chamber_code": None,
            "start_date": date(2026, 2, 1),
            "end_date": date(2026, 2, 10),
        },
    )


def test_list_minutes_books_rejects_bad_dates(fake_client):
    with pytest.raises(ToolError, match="start_date"):
        server.list_minutes_books(20261, start_date="Feb 1")

    with pytest.raises(ToolError, match="before"):
        server.list_minutes_books(20261, start_date="2026-02-10", end_date="2026-02-01")

    assert fake_client.calls == []


def test_get_minutes_book_flattens_the_actions_with_their_votes(fake_client):
    fake_client.minutes_book_details = {
        3861: make_minutes_book(3861, datetime(2026, 4, 22), MINUTES_CATEGORIES)
    }

    result = server.get_minutes_book(3861)

    assert result["minutes_book_id"] == 3861
    assert result["is_floor"] is True
    assert result["total_matching"] == 3  # the call to order names no bill
    assert "note" not in result
    tiebreak, voice, block = result["records"]
    assert tiebreak == {
        "bill_number": "SB2",
        "description": "Paid family and medical leave insurance program.",
        "section": "Senate Bills with Governor's Recommendations",
        "action": "Senate concurred in Governor's recommendation",
        # The chair's tiebreak survives here and nowhere else in LIS.
        "tally": "(18-Y 18-N 0-A, Chair votes Yea)",
        "vote_id": 302328,
    }
    assert voice["bill_number"] == "SB24"
    assert voice["is_voice"] is True
    assert "is_block" not in voice
    assert block["vote_id"] == 302401
    assert block["is_block"] is True
    # The records come from the cached book, not from a second request.
    assert call_names(fake_client).count("get_minutes_book") == 1


def test_get_minutes_book_can_add_the_procedural_entries_and_paginate(fake_client):
    fake_client.minutes_book_details = {
        3861: make_minutes_book(3861, datetime(2026, 4, 22), MINUTES_CATEGORIES)
    }

    result = server.get_minutes_book(3861, include_procedural=True, limit=2)

    assert result["total_matching"] == 4
    assert result["count"] == 2
    assert result["records"][0] == {
        "section": "Order of Business",
        "entry": "Call to Order",
        "action": "Called to Order at 12 m.",
        "notes": ["The Senate met at 12 m."],
    }
    assert result["records"][1]["bill_number"] == "SB2"


def test_get_minutes_book_explains_a_book_with_no_records(fake_client):
    fake_client.minutes_book_details = {1: make_minutes_book(1, datetime(1994, 1, 1), [])}

    result = server.get_minutes_book(1)

    assert result["records"] == []
    assert result["html"] == "https://files.test/1.HTML"
    assert "html file" in result["note"]


def test_get_minutes_book_unknown_id(fake_client):
    with pytest.raises(ToolError, match="list_minutes_books"):
        server.get_minutes_book(12345)


COMMUNICATIONS = [
    make_communication_row(4351, "HN10114", datetime(2026, 1, 14, 21, 49)),
    make_communication_row(4519, "SN10422", datetime(2026, 4, 22, 14, 48), chamber="S"),
    make_communication_row(4520, "HN10422", datetime(2026, 4, 22, 17, 7)),
]


def test_list_communications_sorts_newest_first(fake_client):
    fake_client.communications = COMMUNICATIONS

    result = server.list_communications(20261)

    assert result["total_matching"] == 3
    assert [c["communication_id"] for c in result["communications"]] == [4520, 4519, 4351]
    assert result["communications"][0] == {
        "communication_id": 4520,
        "date": "2026-04-22T17:07:00",
        "chamber": "H",
        "reference": "HN10422",
        "number": 1,
        "html": "https://files.test/4520.HTML",
    }


def test_list_communications_filters_by_the_sending_chamber_and_paginates(fake_client):
    fake_client.communications = COMMUNICATIONS

    senate = server.list_communications(20261, chamber="S")
    paged = server.list_communications(20261, limit=1, offset=2)

    assert [c["communication_id"] for c in senate["communications"]] == [4519]
    assert ("get_communications", {"session_code": 20261, "chamber_code": "S"}) in fake_client.calls
    assert [c["communication_id"] for c in paged["communications"]] == [4351]


def test_get_communication_lists_each_statement_with_its_bills(fake_client):
    fake_client.communication_details = {
        4520: make_communication(
            4520,
            "HN10422",
            datetime(2026, 4, 22, 17, 7),
            [
                {
                    "CommunicationCategoryID": 1701,
                    "CategoryCode": "HN2300",
                    # LIS sends two sentences that disagree; Description is right.
                    "CategoryDescription": "THE HOUSE HAS STRICKEN FROM ITS CALENDAR THE BILL:",
                    "Description": "THE HOUSE HAS PASSED BY FOR THE DAY THE FOLLOWING SENATE BILL:",
                    "CommunicationLegislation": [
                        {
                            "CommunicationLegislationID": 20115,
                            "LegislationID": 98536,
                            "LegislationNumber": "SB2",
                            "LegislationDescription": "Paid family and medical leave.",
                            "DocumentCode": "SB2ER",
                            "Patrons": [
                                {"MemberID": 509, "PatronTypeID": 4, "Name": "Co-Patron"},
                                {
                                    "MemberID": 217,
                                    "PatronTypeID": 1,
                                    "Name": "Chief Patron",
                                    "MemberDisplayName": "Jennifer B. Boysko",
                                },
                            ],
                        }
                    ],
                },
                # A category with no bills is normal, and says nothing.
                {
                    "CommunicationCategoryID": 1702,
                    "CategoryDescription": "THE HOUSE HAS AGREED TO THE FOLLOWING:",
                },
            ],
        )
    }

    result = server.get_communication(4520)

    assert result["communication_id"] == 4520
    assert result["session_code"] == 20261
    assert result["reference"] == "HN10422"
    assert result["count"] == 1
    assert "note" not in result
    assert result["statements"] == [
        {
            "statement": "THE HOUSE HAS PASSED BY FOR THE DAY THE FOLLOWING SENATE BILL:",
            "bills": [
                {
                    "bill_number": "SB2",
                    "description": "Paid family and medical leave.",
                    "document_code": "SB2ER",
                    "chief_patron": "Jennifer B. Boysko",
                }
            ],
        }
    ]


def test_get_communication_points_an_old_message_at_its_file(fake_client):
    fake_client.communication_details = {
        3625: make_communication(
            3625, "SN10403", datetime(2019, 4, 3), [], chamber="S", session_code="20191"
        )
    }

    result = server.get_communication(3625)

    assert result["statements"] == []
    assert result["session_code"] == 20191
    assert result["html"] == "https://files.test/3625.HTML"
    assert "May 2024" in result["note"]


def test_get_communication_unknown_id(fake_client):
    with pytest.raises(ToolError, match="list_communications"):
        server.get_communication(1)
