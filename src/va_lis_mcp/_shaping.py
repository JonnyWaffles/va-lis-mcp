"""Payload shaping: LIS model objects in, compact JSON-ready dicts out.

Every helper here is pure. None of them reach the network, the service, or the
MCP server object, so the dependency runs one way: :mod:`va_lis_mcp.server`
imports this module, and this module imports nothing back.

The compactors rename LIS's PascalCase fields to snake_case, flatten the joins
the service already resolved, and drop empty values so the JSON an LLM reads
carries only the fields that have content.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from va_lis_client import (
    BillVote,
    CommitteeSeat,
    MemberVote,
    RollCallEntry,
    strip_html,
)
from va_lis_client.models import (
    CHIEF_PATRON,
    Committee,
    DocketDetail,
    DocketItem,
    LegislationSummaryItem,
    Member,
    MemberLegislation,
    Patron,
    Schedule,
    Session,
    Vote,
)

MAX_LIST_LIMIT = 200

# A ballot's ResponseCode, and the label the tools report it as. "X" is
# absent from LIS tally strings, so the member groups never sum to the tally.
RESPONSE_LABELS = {"Y": "yea", "N": "nay", "A": "abstain", "X": "not_voting"}


# ----------------------------------------------------------------------------
# Payload shaping
# ----------------------------------------------------------------------------


def compact_session(session: Session) -> dict[str, Any]:
    return {
        "session_code": int(session.SessionCode),
        "session_id": session.SessionID,
        "display_name": session.DisplayName,
        "year": session.SessionYear,
        "type": session.SessionType,
        "is_active": session.IsActive,
        "is_default": session.IsDefault,
    }


def compact_bill_row(item: LegislationSummaryItem) -> dict[str, Any]:
    chief = next(
        (p.MemberDisplayName for p in item.Patrons if p.PatronTypeID == CHIEF_PATRON),
        None,
    )

    return drop_empty(
        {
            "legislation_id": item.LegislationID,
            "bill_number": item.LegislationNumber,
            "description": item.Description,
            "chamber": item.ChamberCode,
            "type": item.LegislationTypeCode,
            "status": item.LegislationStatus,
            "chief_patron": chief,
        }
    )


def condense_patrons(patrons: list[Patron]) -> dict[str, list[str]]:
    """Group patron names by role, e.g. {"Chief Patron": [...], "Co-Patron": [...]}."""
    grouped: dict[str, list[str]] = {}

    for patron in patrons:
        name = patron.MemberDisplayName or patron.PatronDisplayName or patron.MemberNumber
        grouped.setdefault(patron.role, []).append(name)

    return grouped


def compact_patron(patron: Patron) -> dict[str, Any]:
    return drop_empty(
        {
            "member_id": patron.MemberID,
            "member_number": patron.MemberNumber,
            "name": patron.MemberDisplayName or patron.PatronDisplayName,
            "role": patron.role,
            "chamber": patron.ChamberCode,
            "by_request": patron.ByRequest or None,
        }
    )


def compact_bill_vote(record: BillVote) -> dict[str, Any]:
    vote = record.vote

    return drop_empty(
        {
            "vote_id": vote.VoteID,
            "date": iso(vote.VoteDate or record.event.EventDate),
            "chamber": record.chamber,
            "type": vote.VoteType,
            "committee": vote.CommitteeName,
            "description": record.event.Description or vote.Description,
            "tally": vote.VoteTally,
            "passed": passed(vote.PassFail),
            "is_roll_call": record.is_roll_call,
            "is_voice": vote.IsVoice,
            "is_block": vote.IsBlock,
            "bills_in_vote": record.bill_count,
            "members_recorded": len(vote.vote_members),
        }
    )


def roll_call_header(vote: Vote) -> dict[str, Any]:
    """The vote-level fields of a roll call; the member groups are added after."""
    description = next(
        (
            row.LegislationActionDescription
            for row in vote.vote_legislation
            if row.LegislationActionDescription
        ),
        None,
    )

    return drop_empty(
        {
            "vote_id": vote.VoteID,
            "date": iso(vote.VoteDate),
            "chamber": vote.ChamberCode,
            "type": vote.VoteType,
            "committee": vote.CommitteeName,
            "description": description or vote.Description,
            "tally": vote.VoteTally,
            "passed": passed(vote.PassFail),
            "statements": [s.VoteStatement for s in vote.VoteStatements if s.VoteStatement],
        }
    )


def compact_voter(entry: RollCallEntry) -> dict[str, Any]:
    return drop_empty(
        {
            "member_id": entry.member.MemberID,
            "name": entry.name,
            "party": entry.party,
            "district": entry.district,
        }
    )


def compact_member_vote(vote: MemberVote) -> dict[str, Any]:
    row = vote.result

    return drop_empty(
        {
            "vote_id": row.VoteID,
            "date": iso(row.VoteDate),
            "bill_number": row.LegislationNumber,
            "description": row.LegislationDescription,
            "response": RESPONSE_LABELS.get(row.ResponseCode or "", row.ResponseCode),
            "chamber": row.ChamberCode,
            "type": row.VoteType,
            "committee": row.CommitteeName,
            "action": row.VoteDescription,
            "passed": passed(row.PassFail),
            "is_block": vote.is_block,
            "bills_in_vote": vote.bills_in_vote if vote.is_block else None,
            "statements": [s.VoteStatement for s in row.VoteStatements if s.VoteStatement],
        }
    )


def compact_member(member: Member) -> dict[str, Any]:
    return drop_empty(
        {
            "member_id": member.MemberID,
            "member_number": member.MemberNumber,
            "name": member.name,
            "chamber": member.ChamberCode,
            "party": member.PartyCode,
            "district": member.DistrictName,
            "status": member.MemberStatus,
            "service_end_date": iso(member.ServiceEndDate),
            "service_end_reason": member.ServiceEndReason,
        }
    )


def compact_member_bill(bill: MemberLegislation, is_chief_patron: bool) -> dict[str, Any]:
    return drop_empty(
        {
            "legislation_id": bill.LegislationID,
            "bill_number": bill.LegislationNumber,
            "description": bill.Description,
            "chamber": bill.ChamberCode,
            "type": bill.LegislationTypeCode,
            "status": bill.LegislationStatus,
            "is_chief_patron": is_chief_patron,
            "summary_version": bill.SummaryVersion,
        }
    )


def compact_seat(seat: CommitteeSeat) -> dict[str, Any]:
    """A member's seat, from the member's side: which committee, in what role."""
    return drop_empty(
        {
            "committee_id": seat.committee.CommitteeID,
            "committee_number": seat.committee.CommitteeNumber,
            "committee": seat.committee.name,
            "role": seat.role,
            "is_subcommittee": seat.committee.is_subcommittee or None,
        }
    )


def compact_committee(committee: Committee) -> dict[str, Any]:
    return drop_empty(
        {
            "committee_id": committee.CommitteeID,
            "number": committee.CommitteeNumber,
            "name": committee.name,
            "chamber": committee.ChamberCode,
            "abbreviation": " ".join((committee.Abbreviation or "").split()) or None,
            "parent_committee_id": committee.ParentCommitteeID,
        }
    )


def compact_committee_member(seat: CommitteeSeat) -> dict[str, Any]:
    """A seat from the committee's side: who holds it, with party and district."""
    return drop_empty(
        {
            "member_id": seat.seat.MemberID,
            "name": seat.name,
            "role": seat.role,
            "party": seat.party,
            "district": seat.district,
        }
    )


def compact_meeting(meeting: Schedule) -> dict[str, Any]:
    return drop_empty(
        {
            "schedule_id": meeting.ScheduleID,
            "date": iso(meeting.ScheduleDate),
            "time": meeting.ScheduleTime or None,
            "type": meeting.ScheduleType,
            "owner": meeting.OwnerName,
            "committee_number": meeting.CommitteeNumber,
            "room": meeting.RoomDescription,
            "description": strip_html(meeting.Description) if meeting.Description else None,
            "cancelled": meeting.IsCancelled or None,
            "link": meeting.LinkURL,
        }
    )


def docket_header(docket: DocketDetail) -> dict[str, Any]:
    """The docket-level fields; ``bills`` is filled by the caller."""
    schedule = docket.schedule

    payload = drop_empty(
        {
            "docket_id": docket.DocketID,
            "date": iso(docket.DocketDate),
            "time": docket.MeetingTime or None,
            "schedule_time": schedule.ScheduleTime if schedule else None,
            "room": schedule.RoomDescription if schedule else None,
            "description": docket.Description,
        }
    )
    payload["bills"] = []
    return payload


def compact_docket_item(item: DocketItem) -> dict[str, Any]:
    chief = next(
        (
            p.MemberDisplayName or p.PatronDisplayName
            for p in item.Patrons
            if p.PatronTypeID == CHIEF_PATRON
        ),
        None,
    )

    return drop_empty(
        {
            "bill_number": item.LegislationNumber,
            "description": item.LegislationDescription,
            "chief_patron": chief,
        }
    )


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------


def matches(needle: str, *fields: str | None) -> bool:
    """True when the lowercased needle appears in any of the fields."""
    return any(needle in (field or "").lower() for field in fields)


def paginate(items: list[Any], limit: int, offset: int) -> tuple[list[Any], int]:
    """Clamp ``limit`` to the cap and ``offset`` to zero, then slice one page."""
    limit = max(1, min(limit, MAX_LIST_LIMIT))
    offset = max(0, offset)
    return items[offset : offset + limit], offset


def parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a YYYY-MM-DD date, got {value!r}.") from exc


def passed(pass_fail: str | None) -> bool | None:
    """LIS's PassFail code as a boolean; None when the code is absent or unknown."""
    return {"P": True, "F": False}.get(pass_fail or "")


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def drop_empty(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove None and empty-container values; False and 0 are kept."""
    return {k: v for k, v in payload.items() if v is not None and v != [] and v != {}}
