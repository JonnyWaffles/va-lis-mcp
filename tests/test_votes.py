"""Unit tests for the vote tools, with the LIS client faked out."""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.factories import HB1, ROSTER, make_event, make_member_vote, make_vote
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

    with pytest.raises(LookupError) as excinfo:
        server.get_member_votes(999, 20261)

    message = str(excinfo.value)
    assert "999" in message
    assert "list_members" in message
    assert "get_member_votes" not in call_names(fake_client)
