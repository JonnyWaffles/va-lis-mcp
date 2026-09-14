"""Unit tests for the member tools, with the LIS client faked out."""

from __future__ import annotations

import pytest
from va_lis_client.models import CHIEF_PATRON

from tests.factories import COMMITTEES, ROSTER, SEATS, make_member_bill
from tests.fakes import call_names
from va_lis_mcp import server


def test_list_members_searches_by_name_and_filters_by_chamber(fake_client):
    fake_client.members = ROSTER

    result = server.list_members(20261, query="  schmidt ")

    assert result["count"] == 1
    assert result["members"] == [
        {
            "member_id": 544,
            "member_number": "H0402",
            "name": "Charlie Schmidt",
            "chamber": "H",
            "party": "R",
            "district": "77th",
            "status": "Active",
        }
    ]

    senate = server.list_members(20261, chamber="S")

    assert [m["name"] for m in senate["members"]] == ["Lashrecse D. Aird"]
    assert call_names(fake_client).count("get_members") == 1  # the roster is cached


def test_list_members_keeps_departed_members_in_list_name_order(fake_client):
    fake_client.members = ROSTER

    result = server.list_members(20261)

    assert [m["name"] for m in result["members"]] == [
        "Lashrecse D. Aird",
        "Barry D. Knight",
        "Charlie Schmidt",
        "Jeion A. Ward",
    ]
    knight = result["members"][1]
    assert knight["status"] == "Inactive"
    assert knight["service_end_date"] == "2026-02-17T00:00:00"
    assert knight["service_end_reason"] == "Deceased"


def test_get_member_adds_contact_fields(fake_client):
    fake_client.members = ROSTER

    result = server.get_member(544, 20261)

    assert result["session_code"] == 20261
    assert result["name"] == "Charlie Schmidt"
    assert result["email"] == "delcschmidt@house.virginia.gov"
    assert "phone" not in result


def test_get_member_unknown_id(fake_client):
    fake_client.members = ROSTER

    with pytest.raises(LookupError, match="999"):
        server.get_member(999, 20261)


MEMBER_BILL_ROWS = {
    None: [
        make_member_bill(98641, "HB18", "Employee child care", status="Acts of Assembly Chapter"),
        make_member_bill(98923, "HB64", "Firearms; storage"),
        make_member_bill(
            98641,
            "HB18",
            "Employee child care",
            version="SUMMARY AS PASSED",
            status="Acts of Assembly Chapter",
        ),
    ],
    CHIEF_PATRON: [make_member_bill(98923, "HB64", "Firearms; storage")],
}


def test_get_member_bills_marks_chief_patronage_and_sends_the_session_id(fake_client):
    fake_client.members = ROSTER
    fake_client.member_legislation = MEMBER_BILL_ROWS

    result = server.get_member_bills(544, 20261)

    assert result["member"] == "Charlie Schmidt"
    assert "role" not in result
    assert result["total_matching"] == 2  # HB18's two summary rows collapse to one bill
    hb18, hb64 = result["bills"]
    assert hb18["summary_version"] == "SUMMARY AS PASSED"  # the newest row wins
    assert hb18["is_chief_patron"] is False
    assert hb64["is_chief_patron"] is True
    # The endpoint mis-caches session codes, so only the SessionID goes out.
    calls = [kwargs for name, kwargs in fake_client.calls if name == "get_member_legislation"]
    assert calls == [
        {"member_id": 544, "session_id": 59, "patron_type_id": None},
        {"member_id": 544, "session_id": 59, "patron_type_id": CHIEF_PATRON},
    ]


def test_get_member_bills_role_filter_skips_the_second_lookup(fake_client):
    fake_client.members = ROSTER
    fake_client.member_legislation = MEMBER_BILL_ROWS

    result = server.get_member_bills(544, 20261, role="Chief Patron")

    assert result["role"] == "Chief Patron"
    assert [b["bill_number"] for b in result["bills"]] == ["HB64"]
    assert result["bills"][0]["is_chief_patron"] is True
    calls = [kwargs for name, kwargs in fake_client.calls if name == "get_member_legislation"]
    assert calls == [{"member_id": 544, "session_id": 59, "patron_type_id": CHIEF_PATRON}]


def test_get_member_bills_query_and_pagination(fake_client):
    fake_client.members = ROSTER
    fake_client.member_legislation = MEMBER_BILL_ROWS

    filtered = server.get_member_bills(544, 20261, query="FIREARMS")

    assert [b["bill_number"] for b in filtered["bills"]] == ["HB64"]

    paged = server.get_member_bills(544, 20261, limit=1, offset=1)

    assert paged["total_matching"] == 2
    assert [b["bill_number"] for b in paged["bills"]] == ["HB64"]


def test_get_member_committees_lists_seats_with_roles(fake_client):
    fake_client.members = ROSTER
    fake_client.committees = COMMITTEES
    fake_client.committee_members = SEATS

    result = server.get_member_committees(544, 20261)

    assert result["member"] == "Charlie Schmidt"
    assert result["seats"] == [
        {
            "committee_id": 8,
            "committee_number": "H08",
            "committee": "Courts of Justice",
            "role": "Member",
        },
        {
            "committee_id": 21,
            "committee_number": "H21",
            "committee": "Communications, Technology and Innovation",
            "role": "Member",
        },
    ]

    with_subs = server.get_member_committees(544, 20261, include_subcommittees=True)

    assert with_subs["seats"][-1] == {
        "committee_id": 41,
        "committee_number": "H08001",
        "committee": "HCJ Sub: Criminal",  # inner padding collapsed
        "role": "Member",
        "is_subcommittee": True,
    }
