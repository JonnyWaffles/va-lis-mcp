"""Unit tests for the vote tools, with the LIS client faked out."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tests.factories import (
    HB1,
    ROSTER,
    make_event,
    make_member_vote,
    make_shallow_vote,
    make_vote,
)
from tests.fakes import call_names
from va_lis_mcp import server


def test_get_bill_votes_flags_voice_and_block_votes(fake_client):
    fake_client.bills = [HB1]
    fake_client.events = [
        make_event(
            1, "H1405", vote_id=291609, description="Reported from Commerce and Energy (15-Y 7-N)"
        ),
        make_event(2, "H4110"),  # no VoteID, so no request
        make_event(
            3,
            "H5000",
            vote_id=294006,
            description="Read third time and passed House (64-Y 34-N 0-A)",
        ),
        make_event(4, "S4160", vote_id=300174, description="Passed by for the day"),
        make_event(5, "S5100", vote_id=300173),
    ]
    fake_client.votes = {
        291609: make_vote(
            291609,
            {419: "Y", 544: "N"},
            when=datetime(2026, 1, 20),
            vote_type="Committee",
            committee="Commerce and Energy",
            tally="(15-Y 7-N)",
        ),
        294006: make_vote(
            294006,
            {419: "Y", 544: "N", 217: "X"},
            when=datetime(2026, 2, 3),
            tally="(64-Y 34-N 0-A)",
        ),
        300174: make_vote(
            300174, {}, chamber="S", is_voice=True, tally="(Voice Vote)", pass_fail=None
        ),
        300173: make_vote(
            300173,
            {509: "Y"},
            chamber="S",
            is_block=True,
            bills=tuple(f"HB{n}" for n in range(1, 51)),
            tally="(40-Y 0-N 0-A)",
        ),
    }

    result = server.get_bill_votes("HB1", 20261)

    assert result["count"] == 4
    committee, floor, voice, block = result["votes"]
    assert committee["type"] == "Committee"
    assert committee["committee"] == "Commerce and Energy"
    assert committee["is_roll_call"] is True
    assert floor["description"] == "Read third time and passed House (64-Y 34-N 0-A)"
    assert floor["passed"] is True
    assert floor["members_recorded"] == 3
    assert voice["is_voice"] is True
    assert voice["is_roll_call"] is False
    assert "passed" not in voice
    assert block["chamber"] == "S"
    assert block["is_block"] is True
    assert block["bills_in_vote"] == 50
    assert block["is_roll_call"] is False
    assert call_names(fake_client).count("get_vote") == 4


def test_get_roll_call_groups_members_by_response_with_party_and_district(fake_client):
    fake_client.bills = [HB1]
    fake_client.members = ROSTER
    fake_client.events = [
        make_event(1, "H5000", vote_id=294006),
        make_event(2, "S5100", vote_id=300173),
    ]
    fake_client.votes = {
        294006: make_vote(
            294006,
            {419: "Y", 544: "N", 217: "X"},
            when=datetime(2026, 2, 3),
            tally="(64-Y 34-N 0-A)",
            action="Read third time and passed House (64-Y 34-N 0-A)",
            statements=[(217, "Delegate Knight was recorded as not voting. Intended to vote nay.")],
        ),
        300173: make_vote(300173, {509: "Y"}, chamber="S", is_block=True, bills=("HB1", "HB2")),
    }

    result = server.get_roll_call("HB1", 20261)

    assert result["count"] == 1  # the block vote is not a roll call on HB1
    assert "note" not in result
    [roll_call] = result["roll_calls"]
    assert roll_call["vote_id"] == 294006
    assert roll_call["description"] == "Read third time and passed House (64-Y 34-N 0-A)"
    assert roll_call["tally"] == "(64-Y 34-N 0-A)"
    assert roll_call["yea"] == [
        {"member_id": 419, "name": "Jeion A. Ward", "party": "D", "district": "92nd"}
    ]
    assert roll_call["nay"] == [
        {"member_id": 544, "name": "Charlie Schmidt", "party": "R", "district": "77th"}
    ]
    # "X" is not voting; the departed member still resolves from the roster.
    assert roll_call["not_voting"] == [
        {"member_id": 217, "name": "Barry D. Knight", "party": "R", "district": "98th"}
    ]
    assert "abstain" not in roll_call
    assert roll_call["statements"] == [
        "Delegate Knight was recorded as not voting. Intended to vote nay."
    ]
    assert call_names(fake_client).count("get_members") == 1


def test_get_roll_call_vote_id_narrows_to_one_vote(fake_client):
    fake_client.bills = [HB1]
    fake_client.members = ROSTER
    fake_client.events = [make_event(1, "H5000", vote_id=1), make_event(2, "S5000", vote_id=2)]
    fake_client.votes = {1: make_vote(1, {419: "Y"}), 2: make_vote(2, {509: "N"}, chamber="S")}

    result = server.get_roll_call("HB1", 20261, vote_id=2)

    assert [rc["vote_id"] for rc in result["roll_calls"]] == [2]
    assert result["roll_calls"][0]["nay"][0]["name"] == "Lashrecse D. Aird"


def test_get_roll_call_explains_an_unattributable_vote(fake_client):
    fake_client.bills = [HB1]
    fake_client.members = ROSTER
    fake_client.events = [make_event(1, "S5100", vote_id=300173)]
    fake_client.votes = {
        300173: make_vote(300173, {509: "Y"}, chamber="S", is_block=True, bills=("HB1", "HB2"))
    }

    result = server.get_roll_call("HB1", 20261, vote_id=300173)

    assert result["count"] == 0
    assert result["roll_calls"] == []
    assert "block" in result["note"]


MEMBER_VOTE_ROWS = [
    make_member_vote(
        294006,
        "HB1",
        "Y",
        when=datetime(2026, 2, 3),
        description="Minimum wage; increases incrementally",
        action="Read third time and passed House (64-Y 34-N 0-A)",
    ),
    make_member_vote(297750, "HB150", "Y"),
    make_member_vote(297750, "HB253", "Y"),
    make_member_vote(297750, "HB300", "Y"),
    make_member_vote(291609, "HB1", "N", committee="Commerce and Energy", classification=None),
    make_member_vote(299999, None, "Y", classification="Attendance"),
]


def test_get_member_votes_derives_block_fan_out_and_counts_distinct_votes(fake_client):
    fake_client.members = ROSTER
    fake_client.member_votes = MEMBER_VOTE_ROWS

    result = server.get_member_votes(419, 20261)

    assert result["member"] == "Jeion A. Ward"
    assert result["total_matching"] == 5  # the attendance roll call is dropped
    assert result["distinct_votes"] == 3
    assert result["count"] == 5
    first, block, _, _, committee = result["votes"]
    assert first["response"] == "yea"
    assert first["is_block"] is False
    assert "bills_in_vote" not in first
    assert first["action"] == "Read third time and passed House (64-Y 34-N 0-A)"
    assert first["date"] == "2026-02-03T00:00:00"
    assert block["is_block"] is True
    assert block["bills_in_vote"] == 3
    # A committee vote with a null classification is still a vote on a bill.
    assert committee["committee"] == "Commerce and Energy"
    assert committee["response"] == "nay"


def test_get_member_votes_can_exclude_block_votes_and_paginate(fake_client):
    fake_client.members = ROSTER
    fake_client.member_votes = MEMBER_VOTE_ROWS

    result = server.get_member_votes(419, 20261, exclude_block_votes=True, limit=1, offset=1)

    assert result["total_matching"] == 2
    assert result["distinct_votes"] == 2
    assert result["count"] == 1
    assert [v["vote_id"] for v in result["votes"]] == [291609]


def test_get_member_votes_on_one_bill(fake_client):
    fake_client.members = ROSTER
    fake_client.member_votes = MEMBER_VOTE_ROWS

    result = server.get_member_votes(419, 20261, bill_number="hb0001")

    assert result["bill_number"] == "HB1"
    assert [v["vote_id"] for v in result["votes"]] == [294006, 291609]
    # The 1.9 MB history is fetched once and served from the cache after.
    server.get_member_votes(419, 20261)
    assert call_names(fake_client).count("get_member_votes") == 1


def test_get_member_votes_unknown_member_points_at_the_roster(fake_client):
    fake_client.members = ROSTER

    with pytest.raises(ToolError) as excinfo:
        server.get_member_votes(999, 20261)

    message = str(excinfo.value)
    assert "999" in message
    assert "list_members" in message
    assert "get_member_votes" not in call_names(fake_client)


def test_get_bill_vote_tallies_merges_both_chambers_without_the_rosters(fake_client):
    fake_client.bills = [HB1]
    fake_client.shallow_votes = {
        "H": [
            make_shallow_vote(
                294006,
                "HB1",
                datetime(2026, 2, 3, 7, 35),
                description="Read third time and passed House (64-Y 34-N 0-A)",
            ),
            make_shallow_vote(
                291609,
                "HB1",
                datetime(2026, 1, 20, 12, 30),
                vote_type="Committee",
                committee="Labor and Commerce",
                description="Reported from Labor and Commerce (15-Y 7-N)",
                tally="(15-Y 7-N)",
                referred_to="Appropriations",
            ),
        ],
        # A block vote writes "BLOCK" where the bill number belongs.
        "S": [
            make_shallow_vote(
                300173, "BLOCK", datetime(2026, 3, 4), chamber="S", tally="(40-Y 0-N)"
            )
        ],
    }

    result = server.get_bill_vote_tallies("hb0001", 20261)

    assert result["bill_number"] == "HB1"
    assert result["count"] == 3
    committee, floor, block = result["votes"]  # oldest first, both chambers
    assert committee == {
        "vote_id": 291609,
        "date": "2026-01-20T12:30:00",
        "chamber": "H",
        "type": "Committee",
        "committee": "Labor and Commerce",
        "description": "Reported from Labor and Commerce (15-Y 7-N)",
        "tally": "(15-Y 7-N)",
        "referred_to": "Appropriations",
    }
    assert floor["vote_id"] == 294006
    assert "is_block" not in floor
    assert block["chamber"] == "S"
    assert block["is_block"] is True
    # One request per chamber, and none for a roll call.
    names = call_names(fake_client)
    assert names.count("get_shallow_votes") == 2
    assert "get_vote" not in names
    assert fake_client.calls[-1][1]["legislation_id"] == 98525


def test_list_votes_reads_one_day_and_makes_the_end_date_inclusive(fake_client):
    fake_client.shallow_votes = {
        "H": [
            make_shallow_vote(
                291700,
                "SB10",
                datetime(2026, 1, 20, 14, 0),
                description="Passed House (97-Y 0-N)",
                tally="(97-Y 0-N)",
            ),
            make_shallow_vote(
                291589,
                None,
                datetime(2026, 1, 20, 12, 0),
                description="Attendance",
                tally="quorum present",
                classification="Attendance",
            ),
            make_shallow_vote(291650, "BLOCK", datetime(2026, 1, 20, 13, 0)),
        ]
    }

    result = server.list_votes("H", 20261, "2026-01-20")

    assert result["start_date"] == "2026-01-20"
    assert result["end_date"] == "2026-01-20"
    assert result["total_matching"] == 3
    # LIS reads its end date as exclusive, so the tool sends the day after.
    assert fake_client.calls == [
        (
            "get_shallow_votes",
            {
                "chamber_code": "H",
                "session_code": 20261,
                "legislation_id": None,
                "start_date": date(2026, 1, 20),
                "end_date": date(2026, 1, 21),
            },
        )
    ]
    attendance, block, floor = result["votes"]
    assert attendance["classification"] == "Attendance"
    assert "bill_number" not in attendance
    assert block["is_block"] is True
    assert "bill_number" not in block
    # The chamber that voted, on a bill from the other chamber.
    assert floor["chamber"] == "H"
    assert floor["bill_number"] == "SB10"
    assert "classification" not in floor


def test_list_votes_takes_a_range_and_paginates(fake_client):
    fake_client.shallow_votes = {
        "S": [make_shallow_vote(n, f"SB{n}", datetime(2026, 2, n), chamber="S") for n in (3, 1, 2)]
    }

    result = server.list_votes("S", 20261, "2026-02-01", "2026-02-03", limit=1, offset=1)

    assert fake_client.calls[-1][1]["end_date"] == date(2026, 2, 4)
    assert result["end_date"] == "2026-02-03"
    assert result["total_matching"] == 3
    assert [v["bill_number"] for v in result["votes"]] == ["SB2"]


def test_list_votes_rejects_bad_dates(fake_client):
    with pytest.raises(ToolError, match="start_date"):
        server.list_votes("H", 20261, "January 20")

    with pytest.raises(ToolError, match="before"):
        server.list_votes("H", 20261, "2026-01-20", "2026-01-19")

    assert fake_client.calls == []
