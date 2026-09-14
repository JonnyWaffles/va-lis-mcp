"""Unit tests for the committee tools, with the LIS client faked out."""

from __future__ import annotations

import pytest
from va_lis_client import CommitteeNotFoundError
from va_lis_client.models import Committee

from tests.factories import COMMITTEES, ROSTER, SEATS
from va_lis_mcp import server


def test_list_committees_filters_by_chamber_and_includes_subcommittees(fake_client):
    fake_client.committees = COMMITTEES

    result = server.list_committees(chamber="H")

    assert [c["number"] for c in result["committees"]] == ["H08", "H21"]
    assert result["committees"][0]["abbreviation"] == "HCJ"

    with_subs = server.list_committees(chamber="H", include_subcommittees=True)

    assert with_subs["committees"][-1] == {
        "committee_id": 41,
        "number": "H08001",
        "name": "HCJ Sub: Criminal",
        "chamber": "H",
        "parent_committee_id": 8,
    }


def test_get_committee_resolves_a_name_and_joins_the_roster(fake_client):
    fake_client.members = ROSTER
    fake_client.committees = COMMITTEES
    fake_client.committee_members = SEATS
    fake_client.committee_details = {
        8: Committee(
            CommitteeID=8,
            Name="Courts of Justice",
            CommitteeNumber="H08",
            ChamberCode="H",
            MeetingNote="Monday, Wednesday, and Friday, 1 hour after adjournment",
        )
    }

    result = server.get_committee("courts", 20261, chamber="H")

    assert result["committee_id"] == 8
    assert result["number"] == "H08"
    assert result["session_code"] == 20261
    assert result["meeting_note"] == "Monday, Wednesday, and Friday, 1 hour after adjournment"
    assert result["count"] == 2
    assert result["members"] == [
        # The chair is not on the fixture roster, so the seat has no party.
        {"member_id": 186, "name": "Patrick A. Hope", "role": "Chair"},
        {
            "member_id": 544,
            "name": "Charlie Schmidt",
            "role": "Member",
            "party": "R",
            "district": "77th",
        },
    ]
    assert result["subcommittees"] == [
        {"committee_id": 41, "number": "H08001", "name": "HCJ Sub: Criminal"}
    ]
    assert ("get_committee", {"committee_id": 8, "session_id": 59}) in fake_client.calls


def test_get_committee_ambiguous_name_names_both_matches(fake_client):
    fake_client.committees = COMMITTEES

    with pytest.raises(CommitteeNotFoundError) as excinfo:
        server.get_committee("Courts of Justice", 20261)

    message = str(excinfo.value)
    assert "H08" in message
    assert "S13" in message
