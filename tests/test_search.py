"""Unit tests for the bill search tools, with the LIS client faked out."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from va_lis_client.models import LegislationCategory

from tests.factories import make_search_row
from tests.fakes import call_names
from va_lis_mcp import server

# LIS sends a row per summary version, so HB19 arrives twice.
HB19_INTRODUCED = make_search_row(
    98642,
    "HB19",
    "Firearms; purchase after assault",
    summary="<p>As introduced.</p>",
)
HB19 = make_search_row(
    98642,
    "HB19",
    "Firearms; purchase after assault",
    status="Acts of Assembly Chapter",
    version="SUMMARY AS PASSED",
    summary='<p class="sumtext"><b>Purchase of firearm.</b> Adds to the definition.</p>',
    chapter="CHAP0527",
)
HB7 = make_search_row(
    98630,
    "HB7",
    "Firearms; waiting period",
    status="Failed",
    committee=(77, "H15002", "Subcommittee #2"),
    parent="Public Safety",
)
SB27 = make_search_row(
    98700,
    "SB27",
    "Firearm industry members; standards",
    chamber="S",
    status="Continued",
    committee=(26, "S05", "Finance and Appropriations"),
)
# HB19's introduced row arrives first, so the rank has to pick the passed one,
# and the exact duplicate of HB7 has to vanish.
ROWS = [HB19_INTRODUCED, HB7, HB19, HB7, SB27]

CATEGORIES = [
    LegislationCategory(LegislationCategoryID=3, Name="In Committee"),
    LegislationCategory(LegislationCategoryID=8, Name="Passed House"),
]


def test_search_bills_sends_the_keyword_with_its_location(fake_client):
    fake_client.search_results = ROWS

    result = server.search_bills(20261, keyword="  firearm ")

    assert result["keyword"] == "firearm"
    assert result["search_in"] == "Summary"
    assert result["total_matching"] == 3
    assert "note" not in result
    # LIS answers nothing to a keyword that names no location.
    assert fake_client.calls == [
        (
            "search_legislation",
            {"session_code": 20261, "keyword": "firearm", "keyword_location": "Summary"},
        )
    ]

    chaptered, failed, senate = result["bills"]
    assert chaptered == {
        "legislation_id": 98642,
        "bill_number": "HB19",
        "description": "Firearms; purchase after assault",
        "chamber": "H",
        "type": "B",
        "status": "Acts of Assembly Chapter",
        "chapter": "CHAP0527",
        "chief_patron": "Jeion A. Ward",
    }
    # A subcommittee row reports its parent as the committee.
    assert failed["committee"] == "Public Safety"
    assert failed["subcommittee"] == "Subcommittee #2"
    assert failed["committee_number"] == "H15002"
    assert senate["committee"] == "Finance and Appropriations"
    assert "subcommittee" not in senate


def test_search_bills_matches_the_chamber_here_and_keeps_failed_bills(fake_client):
    fake_client.search_results = ROWS

    result = server.search_bills(20261, keyword="firearm", search_in="Bill Text", chamber="H")

    # A chamber sent to LIS drops failed bills, so it never leaves the tool.
    [(_, criteria)] = fake_client.calls
    assert criteria == {
        "session_code": 20261,
        "keyword": "firearm",
        "keyword_location": "Bill Text",
    }
    assert [(b["bill_number"], b["status"]) for b in result["bills"]] == [
        ("HB19", "Acts of Assembly Chapter"),
        ("HB7", "Failed"),
    ]


def test_search_bills_can_add_the_plain_text_summary(fake_client):
    fake_client.search_results = ROWS

    result = server.search_bills(20261, keyword="firearm", include_summaries=True, limit=1)

    [bill] = result["bills"]
    assert result["total_matching"] == 3
    assert bill["summary_version"] == "SUMMARY AS PASSED"
    assert "Adds to the definition." in bill["summary"]
    assert "<" not in bill["summary"]


def test_search_bills_resolves_the_filters_and_notes_what_lis_leaves_out(fake_client):
    fake_client.search_results = [HB19]
    fake_client.categories = CATEGORIES

    result = server.search_bills(20261, category=" passed house ", member_id=544, chapter=527)

    assert fake_client.calls[-1] == (
        "search_legislation",
        {
            "session_code": 20261,
            "category_id": 8,
            "member_id": 544,
            "chapter_number": "CHAP0527",
        },
    )
    assert "keyword" not in result
    assert "failed and incorporated" in result["note"]


def test_search_bills_unknown_category_lists_the_names(fake_client):
    fake_client.categories = CATEGORIES

    with pytest.raises(ToolError, match="In Committee, Passed House"):
        server.search_bills(20261, category="Passed Hose")

    assert "search_legislation" not in call_names(fake_client)


@pytest.mark.parametrize("keyword", [None, "   "])
def test_search_bills_needs_a_criterion(fake_client, keyword):
    with pytest.raises(ToolError, match="list_bills"):
        server.search_bills(20261, keyword=keyword, chamber="H")

    # An unfiltered search downloads the whole session, so nothing is sent.
    assert fake_client.calls == []


def test_search_bills_explains_a_keyword_that_finds_nothing(fake_client):
    result = server.search_bills(20261, keyword="the")

    assert result["total_matching"] == 0
    assert result["bills"] == []
    assert "common words" in result["note"]


def test_search_bills_paginates(fake_client):
    fake_client.search_results = ROWS

    result = server.search_bills(20261, keyword="firearm", limit=1, offset=2)

    assert result["total_matching"] == 3
    assert result["offset"] == 2
    assert [b["bill_number"] for b in result["bills"]] == ["SB27"]


def test_list_most_viewed_bills_sends_the_session_id_and_collapses_repeats(fake_client):
    fake_client.most_viewed = [SB27, HB19_INTRODUCED, HB19]

    result = server.list_most_viewed_bills(20261)

    # The endpoint has no session code parameter.
    assert ("get_most_frequent_legislation", {"session_id": 59}) in fake_client.calls
    assert result["total_matching"] == 2
    assert [(b["bill_number"], b["status"]) for b in result["bills"]] == [
        ("SB27", "Continued"),
        ("HB19", "Acts of Assembly Chapter"),
    ]

    assert server.list_most_viewed_bills(20261, limit=1)["count"] == 1
