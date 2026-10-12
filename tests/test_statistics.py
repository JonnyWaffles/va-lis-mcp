"""Unit tests for the statistics tools, with the LIS client faked out."""

from __future__ import annotations

from va_lis_client.models import ChamberStatistic, CommitteeStatistic

from va_lis_mcp import server


def test_get_session_statistics_adds_the_total_and_keeps_the_zeros(fake_client):
    fake_client.chamber_statistics = [
        ChamberStatistic(
            ChamberCode="H",
            LegislationTypeCode="B",
            Introduced=1530,
            PassedHouse=852,
            PassedSenate=735,
            Passed=728,
            Pending=255,
            Incorporated=54,
            Failed=494,
            Approved=711,
            Veto=16,
            AwaitingGovAction=0,
            ContinuedFromLastSession=3,
            ContinuedToNextSession=257,
        ),
        ChamberStatistic(ChamberCode="S", LegislationTypeCode="R", Introduced=12),
    ]

    result = server.get_session_statistics(20261)

    assert result["count"] == 2
    house_bills, senate_resolutions = result["statistics"]
    assert house_bills == {
        "chamber": "H",
        "type": "B",
        "introduced": 1530,
        "continued_from_last_session": 3,
        "total": 1533,
        "passed_house": 852,
        "passed_senate": 735,
        "passed": 728,
        "approved": 711,
        "vetoed": 16,
        "awaiting_governor": 0,
        "failed": 494,
        "incorporated": 54,
        "pending": 255,
        "continued_to_next_session": 257,
    }
    # A count LIS did not send is left out, not reported as zero.
    assert senate_resolutions == {"chamber": "S", "type": "R", "introduced": 12, "total": 12}
    assert ("get_chamber_statistics", {"session_code": 20261}) in fake_client.calls


def test_get_committee_statistics_splits_the_counts_by_the_bills_chamber(fake_client):
    fake_client.committee_statistics = {
        "H": [
            CommitteeStatistic(
                CommitteeID=1,
                CommitteeNumber="H01",
                CommitteeName="Agriculture, Chesapeake and Natural Resources",
                HReferred=90,
                SReferred=18,
                HInCommittee=6,
                SInCommittee=0,
                HInSubCommittee=6,
                SInSubCommittee=0,
                HReported=60,
                SReported=17,
                HIncorporated=3,
                SIncorporated=0,
                HContinued=6,
                SContinued=0,
                HContinuedFrom=0,
                SContinuedFrom=0,
                HFailed=2,
                SFailed=1,
            )
        ]
    }

    result = server.get_committee_statistics("H", 20261)

    assert result["chamber"] == "H"
    assert result["count"] == 1
    assert result["committees"] == [
        {
            "committee_id": 1,
            "number": "H01",
            "name": "Agriculture, Chesapeake and Natural Resources",
            "house_bills": {
                "referred": 90,
                "reported": 60,
                "failed": 2,
                "incorporated": 3,
                "in_committee": 6,
                "in_subcommittee": 6,
                "continued": 6,
                "continued_from": 0,
            },
            # Senate bills sent to this House committee after crossover.
            "senate_bills": {
                "referred": 18,
                "reported": 17,
                "failed": 1,
                "incorporated": 0,
                "in_committee": 0,
                "in_subcommittee": 0,
                "continued": 0,
                "continued_from": 0,
            },
        }
    ]
    assert (
        "get_committee_statistics",
        {"chamber_code": "H", "session_code": 20261},
    ) in fake_client.calls
