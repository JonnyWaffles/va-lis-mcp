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
    MinutesRecord,
    RollCallEntry,
    strip_html,
)
from va_lis_client.models import (
    CHIEF_PATRON,
    AmendedLegislation,
    BillVersion,
    ChamberStatistic,
    Committee,
    CommitteeStatistic,
    CommunicationCategory,
    CommunicationListItem,
    ConferenceLegislation,
    Contact,
    DocketDetail,
    DocketItem,
    LegislationSearchResult,
    LegislationSummaryItem,
    Member,
    MemberLegislation,
    MinutesBookListItem,
    Patron,
    Personnel,
    Schedule,
    Session,
    SessionEvent,
    ShallowVote,
    Vote,
)

MAX_LIST_LIMIT = 200

# A ballot's ResponseCode, and the label the tools report it as. "X" is
# absent from LIS tally strings, so the member groups never sum to the tally.
RESPONSE_LABELS = {"Y": "yea", "N": "nay", "A": "abstain", "X": "not_voting"}

# StaffRoleTypeID behind each staff role name (verified live 2026-10-12). A
# committee's personnel rows carry 1 and 2; the other five name chamber
# officers.
STAFF_ROLES = {
    1: "Committee Clerk",
    2: "Committee Staff",
    3: "Clerk of the Senate",
    4: "Clerk of the House of Delegates",
    5: "President of the Senate",
    6: "Senate Majority Leader",
    7: "President Pro Tempore",
}

# The committee statistics name each count twice, once per originating
# chamber of the bill. These are the column stems behind the H and S prefixes.
_COMMITTEE_COUNTS = {
    "referred": "Referred",
    "reported": "Reported",
    "failed": "Failed",
    "incorporated": "Incorporated",
    "in_committee": "InCommittee",
    "in_subcommittee": "InSubCommittee",
    "continued": "Continued",
    "continued_from": "ContinuedFrom",
}


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


def compact_session_event(event: SessionEvent) -> dict[str, Any]:
    return drop_empty(
        {
            "name": event.DisplayName,
            "date": iso(event.ActualDate),
            "projected_date": iso(event.ProjectedDate),
        }
    )


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


def one_row_per_bill(rows: list[LegislationSearchResult]) -> list[LegislationSearchResult]:
    """Collapse search rows to one per bill, keeping the furthest-along summary.

    LIS sends a search row per summary version and repeats some rows outright.
    The rank picks the winner, not the arrival order, and each bill keeps the
    place it first appeared in.
    """
    best: dict[int, LegislationSearchResult] = {}

    for row in rows:
        current = best.get(row.LegislationID)
        if current is None or row.summary_rank > current.summary_rank:
            best[row.LegislationID] = row

    return list(best.values())


def first_row_per_bill(rows: list[Any]) -> list[Any]:
    """Drop the repeats LIS sends for a bill, keeping the first row and the order."""
    unique: dict[int, Any] = {}

    for row in rows:
        unique.setdefault(row.LegislationID, row)

    return list(unique.values())


def compact_search_row(
    row: LegislationSearchResult,
    include_summary: bool = False,
) -> dict[str, Any]:
    payload = {
        "legislation_id": row.LegislationID,
        "bill_number": row.LegislationNumber,
        "description": row.Description,
        "chamber": row.ChamberCode,
        "type": row.LegislationTypeCode,
        "status": row.LegislationStatus,
        "chapter": row.ChapterNumber,
        # A subcommittee row names itself and its parent; report the parent
        # as the committee so the field reads the same on both kinds of row.
        "committee": row.ParentCommitteeName or row.CommitteeName,
        "subcommittee": row.CommitteeName if row.ParentCommitteeName else None,
        "committee_number": row.CommitteeNumber,
        "chief_patron": chief_patron(row.Patrons),
    }

    if include_summary:
        payload["summary_version"] = row.SummaryVersion
        payload["summary"] = strip_html(row.LegislationSummary) if row.LegislationSummary else None

    return drop_empty(payload)


def compact_bill_version(version: BillVersion) -> dict[str, Any]:
    return drop_empty(
        {
            "document_code": version.DocumentCode,
            "version": version.Version,
            # The specific name ("Education Substitute"), when it says more
            # than the version type ("Substitute").
            "description": version.Description if version.Description != version.Version else None,
            "date": iso(version.DraftDate),
            "chamber": version.ChamberCode,
            "sponsor": version.Sponsor,
            "disposition": version.TextDisposition,
            "pdf": next(iter(file_urls(version.PdfFile)), None),
            "html": next(iter(file_urls(version.HtmlFile)), None),
            "impact_statements": file_urls(version.ImpactFile),
        }
    )


def compact_amended_bill(row: AmendedLegislation) -> dict[str, Any]:
    return drop_empty(
        {
            "legislation_id": row.LegislationID,
            "bill_number": row.LegislationNumber,
            "description": row.Description,
            "chamber": row.ChamberCode,
            "status": row.LegislationStatus,
            "chief_patron": chief_patron(row.Patrons),
            "amendments": [
                drop_empty(
                    {
                        "document_code": amendment.DocumentCode,
                        "chamber": amendment.ChamberCode,
                        "description": amendment.Description,
                        "disposition": amendment.TextDisposition,
                        "date": iso(amendment.VersionDate),
                    }
                )
                for amendment in row.Amendments
            ],
        }
    )


def compact_conference_bill(row: ConferenceLegislation) -> dict[str, Any]:
    return drop_empty(
        {
            "legislation_id": row.LegislationID,
            "bill_number": row.LegislationNumber,
            "description": row.Description,
            "chamber": row.ChamberCode,
            "type": row.LegislationTypeCode,
            "status": row.LegislationStatus,
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


def compact_shallow_vote(vote: ShallowVote, include_bill: bool = True) -> dict[str, Any]:
    """A vote without its members. ``include_bill`` is off when the caller named the bill."""
    payload = {
        "vote_id": vote.VoteID,
        "date": iso(vote.VoteDate),
        "chamber": vote.ChamberCode,
        "type": vote.VoteType,
        "committee": vote.CommitteeName,
    }

    if include_bill:
        # A block vote writes "BLOCK" where the bill number belongs.
        payload["bill_number"] = None if vote.is_block else vote.LegislationNumber

        # "Attendance" marks a quorum call, which is a vote about no bill.
        if vote.ClassificationName != "Legislation":
            payload["classification"] = vote.ClassificationName

    payload.update(
        {
            "description": vote.Description,
            "tally": vote.VoteTally,
            "is_block": vote.is_block or None,
            "is_voice": vote.IsVoice or None,
            "referred_to": vote.ReferToCommitteeName,
        }
    )
    return drop_empty(payload)


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


def compact_minutes_book(book: MinutesBookListItem) -> dict[str, Any]:
    """The book-level fields, shared by the list rows and the detail header."""
    calendar = book.MinutesCalendars[0] if book.MinutesCalendars else None
    html = book.html_file

    return drop_empty(
        {
            "minutes_book_id": book.MinutesBookID,
            "date": iso(book.MinutesDate),
            "chamber": book.ChamberCode,
            "committee_id": book.CommitteeID,
            "committee": book.CommitteeName,
            # LIS's own IsCommittee flag is false on every row; a floor book
            # is the one with no committee.
            "is_floor": book.is_floor or None,
            "status": book.MinutesStatus,
            "calendar_id": calendar.CalendarID if calendar else None,
            "html": html.FileURL if html else None,
        }
    )


def compact_minutes_record(record: MinutesRecord, section: str | None) -> dict[str, Any]:
    entry = record.entry
    notes = [strip_html(s.summary) for s in entry.MinutesSummaries if s.summary]

    return drop_empty(
        {
            "bill_number": record.bill_number,
            "description": entry.LegislationDescription,
            "section": section,
            # A procedural entry names itself ("Recess") where a bill would be.
            "entry": entry.EntryText,
            "action": record.description,
            "tally": record.tally,
            "vote_id": record.vote_id,
            "is_block": record.is_block or None,
            "is_voice": record.is_voice or None,
            "notes": [note for note in notes if note],
        }
    )


def compact_communication_row(row: CommunicationListItem) -> dict[str, Any]:
    """The message-level fields, shared by the list rows and the detail header."""
    html = row.html_file

    return drop_empty(
        {
            "communication_id": row.CommunicationID,
            "date": iso(row.CommunicationDate),
            "chamber": row.ChamberCode,
            "reference": row.ReferenceNumber,
            "number": row.CommunicationNumber,
            "html": html.FileURL if html else None,
        }
    )


def compact_communication_category(category: CommunicationCategory) -> dict[str, Any]:
    """One sentence of a communication, with the bills it applies to."""
    return {
        # label prefers the description field that is correct when the two
        # LIS sends disagree.
        "statement": category.label,
        "bills": [
            drop_empty(
                {
                    "bill_number": bill.LegislationNumber,
                    "description": bill.LegislationDescription,
                    "document_code": bill.DocumentCode,
                    "chief_patron": chief_patron(bill.Patrons),
                }
            )
            for bill in category.bills
        ],
    }


def compact_staff(person: Personnel) -> dict[str, Any]:
    return drop_empty(
        {
            "name": person.FullName,
            "role": STAFF_ROLES.get(person.StaffRoleTypeID or 0),
            "since": iso(person.EffectiveBeginDate),
        }
    )


def compact_office(office: Contact) -> dict[str, Any]:
    lines = [office.Address1, office.Address2, office.Address3]

    return drop_empty(
        {
            "address": [line for line in lines if line],
            "city": office.City or None,
            "state": office.StateCode or None,
            "zip": office.ZipCode or None,
            "phone": office.PhoneNumber or None,
            "email": office.EmailAddress or None,
        }
    )


def compact_chamber_statistic(row: ChamberStatistic) -> dict[str, Any]:
    return drop_empty(
        {
            "chamber": row.ChamberCode,
            "type": row.LegislationTypeCode,
            "introduced": row.Introduced,
            "continued_from_last_session": row.ContinuedFromLastSession,
            "total": row.total,
            "passed_house": row.PassedHouse,
            "passed_senate": row.PassedSenate,
            "passed": row.Passed,
            "approved": row.Approved,
            "vetoed": row.Veto,
            "awaiting_governor": row.AwaitingGovAction,
            "failed": row.Failed,
            "incorporated": row.Incorporated,
            "pending": row.Pending,
            "continued_to_next_session": row.ContinuedToNextSession,
        }
    )


def compact_committee_statistic(row: CommitteeStatistic) -> dict[str, Any]:
    """One committee's referral counts, split by the bill's chamber of origin."""
    return {
        "committee_id": row.CommitteeID,
        "number": row.CommitteeNumber,
        "name": row.CommitteeName,
        "house_bills": _committee_counts(row, "H"),
        "senate_bills": _committee_counts(row, "S"),
    }


def _committee_counts(row: CommitteeStatistic, prefix: str) -> dict[str, int]:
    counts = {key: getattr(row, prefix + stem) for key, stem in _COMMITTEE_COUNTS.items()}

    return drop_empty(counts)


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------


def chief_patron(patrons: list[Patron]) -> str | None:
    """The chief patron's name, or None when the list names none."""
    return next(
        (
            patron.MemberDisplayName or patron.PatronDisplayName
            for patron in patrons
            if patron.PatronTypeID == CHIEF_PATRON
        ),
        None,
    )


def file_urls(files: list[Any] | None) -> list[str]:
    """The addresses of a version's files. LIS sends null where it has none."""
    return [f.FileURL for f in files or [] if f.FileURL]


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
