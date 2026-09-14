"""Unit tests for the meeting, docket, and calendar tools, with the client faked out."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tests.factories import (
    COMMITTEES,
    make_calendar_detail,
    make_calendar_item,
    make_docket_detail,
    make_docket_row,
    make_meeting,
)
from va_lis_mcp import server

MEETINGS = [
    make_meeting(
        2,
        datetime(2026, 2, 4, 9, 0),
        owner_id=8,
        owner="House Courts of Justice",
        number="H08",
        time="9:00 AM",
        room="House Room C",
        description="<p>Agenda: <a href='https://example.test'>HB1</a></p>",
    ),
    make_meeting(
        1,
        datetime(2026, 2, 2, 8, 30),
        owner_id=8,
        owner="House Courts of Justice",
        number="H08",
        cancelled=True,
    ),
    make_meeting(
        3, datetime(2026, 2, 3, 12, 0), type_id=2, kind="Chamber", owner="House of Delegates"
    ),
]


def test_list_meetings_resolves_the_committee_and_strips_html(fake_client):
    fake_client.committees = COMMITTEES
    fake_client.schedules = MEETINGS

    result = server.list_meetings("2026-02-02", "2026-02-06", committee="H08")

    assert result["start_date"] == "2026-02-02"
    assert result["end_date"] == "2026-02-06"
    assert result["total_matching"] == 2
    first, second = result["meetings"]  # sorted by date
    assert first["schedule_id"] == 1
    assert first["cancelled"] is True
    assert "time" not in first
    assert second["time"] == "9:00 AM"
    assert second["room"] == "House Room C"
    assert second["description"] == "Agenda: HB1"
    assert "cancelled" not in second
    assert (
        "get_schedules",
        {
            "start_date": date(2026, 2, 2),
            "end_date": date(2026, 2, 6),
            "owner_id": 8,
            "schedule_type_id": None,
        },
    ) in fake_client.calls


def test_list_meetings_filters_by_type_and_paginates(fake_client):
    fake_client.schedules = MEETINGS

    floor = server.list_meetings("2026-02-02", "2026-02-06", meeting_type="Chamber")

    assert [m["schedule_id"] for m in floor["meetings"]] == [3]
    assert floor["meetings"][0]["owner"] == "House of Delegates"
    assert fake_client.calls[-1][1]["schedule_type_id"] == 2

    paged = server.list_meetings("2026-02-02", "2026-02-06", limit=1, offset=1)

    assert paged["total_matching"] == 3
    assert [m["schedule_id"] for m in paged["meetings"]] == [3]


def test_list_meetings_rejects_bad_dates(fake_client):
    with pytest.raises(ToolError, match="start_date"):
        server.list_meetings("Feb 2", "2026-02-06")

    with pytest.raises(ToolError, match="before"):
        server.list_meetings("2026-02-06", "2026-02-02")

    assert fake_client.calls == []


def test_list_dockets_groups_bills_by_docket(fake_client):
    fake_client.committees = COMMITTEES
    fake_client.dockets = {
        202: [make_docket_row(21123), make_docket_row(21117, when=datetime(2026, 3, 2, 16, 30))]
    }
    fake_client.docket_details = {
        21123: make_docket_detail(21123, ("HB128", "HB148")),
        21117: make_docket_detail(
            21117, ("SB5",), when=datetime(2026, 3, 2, 16, 30), room=None, schedule_time=None
        ),
    }

    result = server.list_dockets("Courts of Justice", 20261)

    assert result["committee_number"] == "S13"
    assert result["committee"] == "Courts of Justice"
    assert result["count"] == 2
    first, second = result["dockets"]
    assert first["docket_id"] == 21123
    assert first["date"] == "2026-03-09T16:30:00"
    assert first["schedule_time"] == "8:00 AM"  # the schedule disagrees with the docket
    assert first["room"] == "Senate Room A"
    assert [b["bill_number"] for b in first["bills"]] == ["HB128", "HB148"]
    assert first["bills"][0]["chief_patron"] == "Jeion A. Ward"
    assert "room" not in second
    assert second["bills"] == [
        {
            "bill_number": "SB5",
            "description": "SB5; a description.",
            "chief_patron": "Jeion A. Ward",
        }
    ]


def test_list_dockets_rejects_a_house_committee(fake_client):
    fake_client.committees = COMMITTEES

    with pytest.raises(ToolError):
        server.list_dockets("H08", 20261)


def test_list_floor_calendars_for_one_chamber(fake_client):
    fake_client.calendars = [
        make_calendar_item(21146, "HC10314", datetime(2026, 3, 14, 10, 0)),
        make_calendar_item(30000, "SC10314", datetime(2026, 3, 14, 10, 0), chamber="S"),
    ]

    result = server.list_floor_calendars("H", 20261)

    assert result["count"] == 1
    assert result["calendars"] == [
        {"calendar_id": 21146, "reference": "HC10314", "date": "2026-03-14T10:00:00"}
    ]


def test_get_floor_calendar_flattens_bills_with_their_votes(fake_client):
    fake_client.calendar_details = {
        20885: make_calendar_detail(20885, "HC20114", datetime(2026, 1, 14, 12, 0))
    }

    result = server.get_floor_calendar(20885)

    assert result["reference"] == "HC20114"
    assert result["session_code"] == 20261
    assert result["count"] == 1  # the order-of-business entry names no bill
    [bill] = result["bills"]
    assert bill["bill_number"] == "SJ209"
    assert bill["section"] == "Resolutions"
    assert bill["actions"] == [
        {"description": "Reported from Committee... March 13, 2026", "vote_id": 302080}
    ]


def test_get_floor_calendar_unknown_id(fake_client):
    with pytest.raises(ToolError, match="12345"):
        server.get_floor_calendar(12345)
