"""MCP server exposing the Virginia Legislative Information System (LIS) API.

Wraps :class:`va_lis_client.LISService` with read-only tools shaped for LLM
consumption: human identifiers in (bill number + session code, member id,
committee number or name), compact JSON out, HTML stripped from bill text,
summaries, and agendas. Bill number normalization, id resolution, the
reference joins (roster, statuses), and every cache live in the service; this
module defines the tools, and :mod:`va_lis_mcp._shaping` turns the models they
get back into the compact dicts they return.

The tools cover bills (list, detail, summaries, text, history, patrons),
votes (per bill with the roll call, and per member), members with their
bills and committee seats, committees with their rosters, and meetings (the
master schedule, Senate dockets, and floor calendars).

Run over stdio (the transport MCP hosts launch)::

    va-lis-mcp

or serve over HTTP with the ASGI app in :mod:`va_lis_mcp.asgi`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from va_lis_client import (
    LISClient,
    LISClientError,
    LISError,
    LISService,
    normalize_bill_number,
    strip_html,
)
from va_lis_client.models import CHIEF_PATRON, PATRON_ROLES, Member

from va_lis_mcp._shaping import (
    RESPONSE_LABELS,
    compact_bill_row,
    compact_bill_vote,
    compact_committee,
    compact_committee_member,
    compact_docket_item,
    compact_meeting,
    compact_member,
    compact_member_bill,
    compact_member_vote,
    compact_patron,
    compact_seat,
    compact_session,
    compact_voter,
    condense_patrons,
    docket_header,
    drop_empty,
    iso,
    matches,
    paginate,
    parse_date,
    roll_call_header,
)

_REGISTRATION_URL = "https://lis.virginia.gov/apiregistration"
_TEXT_CHAR_LIMIT = 15_000

# An event's resulting Status (a closed 52-name vocabulary) is the reliable
# passage signal. The event type reference's IsPassage flag is not: LIS reuses
# one EventCode for both the "passed" and the "defeated" outcome of a floor
# vote (verified live 2026-09-08, e.g. H5000), so the code alone cannot mark
# passage.
_PASSAGE_STATUSES = {"Passed", "Passed House", "Passed Senate", "Passed Both"}

# ScheduleTypeID behind each schedule type name.
_MEETING_TYPE_IDS = {
    "Committee": 1,
    "Chamber": 2,
    "Conference": 3,
    "Caucus": 4,
    "Other": 5,
    "Docket": 6,
}

# Patron role name -> PatronTypeID, the reverse of the client's vocabulary.
_PATRON_ROLE_IDS = {name: role_id for role_id, name in PATRON_ROLES.items()}

_INSTRUCTIONS = """\
Read-only tools for the Virginia General Assembly's Legislative Information
System (LIS): sessions, bills, summaries, full bill text, action history,
votes and roll calls, members, committees, and meetings.

Identifiers:
- session_code encodes year + sequence: 20261 = 2026 Regular Session,
  20262 = 2026 Special Session I. Use list_sessions to discover codes.
- Bill numbers are unpadded: HB1, not HB0001. Tools normalize input
  (hb0001 -> HB1) before querying.
- member_id is LIS's stable id for a person. Get one from list_members
  (search by name), a roll call, or a patron list.
- Committees are addressed by number ("H08", "S13") or by name ("Courts of
  Justice"). Both chambers have some names, so pass the chamber or the number
  when a name is ambiguous.

Session semantics:
- get_current_session returns the GA's *working* session. During the interim
  that is the upcoming session, not the one that just ended. For retrospective
  work on a finished session, pass its explicit session_code.
- Even-year bills may be carried over into the following odd-year session
  (never the reverse, at most once). A carried-over bill keeps its numeric
  LegislationID in both sessions, so a bill is identified by
  (session_code, bill_number); the numeric ID alone is ambiguous across the
  pair. get_bill's "sessions" list is the carry-over lineage record.

Voting records:
- get_bill_votes lists every vote a bill saw (floor, committee, and
  subcommittee, in both chambers); get_roll_call names the members with party
  and district; get_member_votes is the same record from the member's side.
- A block vote is one motion that disposes of many uncontroversial bills at
  once, and a voice vote records no members. Neither is a member's verdict on
  one bill, so get_roll_call leaves them out and get_member_votes flags them
  with is_block. Response "not_voting" (LIS code X) is omitted from tally
  strings, so member groups never sum to the tally.
- Vote statements are corrections a member filed ("recorded as yea, intended
  to vote nay"); the recorded roll call itself is never amended.

Members and committees:
- The session roster deliberately includes members who left or arrived
  mid-session; they cast the votes recorded before they left.
- The committee list is not session scoped; committee membership is.
- LIS has no "bills referred to a committee" endpoint. Read a bill's
  committee from get_bill_history.

Meetings:
- Schedule times are free text ("15 minutes after adjournment") and may be
  blank. Cancelled meetings stay listed with cancelled true.
- Dockets (committee agendas) exist for Senate committees only; House
  committee agendas are not in the API. Floor calendars cover both chambers.

All data comes from the live LIS REST API and requires the LIS_API_KEY
environment variable (register at https://lis.virginia.gov/apiregistration).
Use check_api_key to verify the key: LIS heartbeat endpoints accept any key,
so a working heartbeat proves nothing.
"""

# Built lazily on the first tool call; the service owns every cache (session
# bill lists, rosters, seat lists, dockets, and the static vocabularies), so
# one instance serves the whole process.
_service: LISService | None = None

mcp = MCPServer("va-lis", instructions=_INSTRUCTIONS)


# ----------------------------------------------------------------------------
# Sessions
# ----------------------------------------------------------------------------


@mcp.tool()
def get_current_session() -> dict[str, Any]:
    """The General Assembly's current working session.

    During the interim this is the upcoming session, not the one that just
    ended; continued bills and new prefiles accumulate there. For
    retrospective work on a finished session, pass that session's code to the
    other tools instead of relying on this default.
    """
    session = _call(_get_service().client.get_default_session)

    if session is None:
        raise ToolError("LIS did not report a default session.")

    payload = compact_session(session)
    payload["note"] = (
        "This is the GA's working session. During the interim it is the upcoming "
        "session, not the one that just ended; pass an explicit session_code for "
        "retrospective work on a finished session."
    )
    return payload


@mcp.tool()
def list_sessions(year: int) -> dict[str, Any]:
    """Legislative sessions for a calendar year.

    Returns each session's code, e.g. 20261 = 2026 Regular Session and
    20262 = 2026 Special Session I. The reference list reaches back to 1994.
    """
    sessions = _call(_get_service().client.get_sessions, year)

    return {"year": year, "sessions": [compact_session(s) for s in sessions]}


# ----------------------------------------------------------------------------
# Bills
# ----------------------------------------------------------------------------


@mcp.tool()
def list_bills(
    session_code: int,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List bills in a session, optionally filtered by a search term.

    ``query`` is a case-insensitive substring match against the bill number,
    description, and title. ``limit`` is capped at 200; page through larger
    result sets with ``offset``. ``total_matching`` counts all matches, not
    just the returned page.
    """
    bills = _call(_get_service().session_bills, session_code)

    if query:
        needle = query.strip().lower()
        bills = [
            bill
            for bill in bills
            if matches(needle, bill.LegislationNumber, bill.Description, bill.LegislationTitle)
        ]

    page, offset = paginate(bills, limit, offset)

    return {
        "session_code": session_code,
        "total_matching": len(bills),
        "count": len(page),
        "offset": offset,
        "bills": [compact_bill_row(bill) for bill in page],
    }


@mcp.tool()
def get_bill(bill_number: str, session_code: int) -> dict[str, Any]:
    """Full detail for one bill: title, status, dates, patrons, session lineage.

    ``patrons`` groups member names by role (Chief Patron, Co-Patron, ...);
    get_bill_patrons returns the same list with member ids. ``sessions``
    lists every session the bill appears in; two entries mean it was carried
    over from the even-year session into the odd-year session.
    """
    service = _get_service()
    bill = _call(service.get_bill, bill_number, session_code)

    return drop_empty(
        {
            "legislation_id": bill.LegislationID,
            "bill_number": bill.LegislationNumber,
            "description": bill.Description,
            "title": bill.LegislationTitle,
            "chamber": bill.ChamberCode,
            "type": bill.LegislationTypeCode,
            "status": _call(service.bill_status_label, bill),
            "introduction_date": iso(bill.IntroductionDate),
            "offered_date": iso(bill.OfferedDate),
            "sessions": [
                {"session_code": int(s.SessionCode), "is_prefile": s.IsPrefile}
                for s in bill.Sessions
            ],
            "patrons": condense_patrons(bill.Patrons),
        }
    )


@mcp.tool()
def get_bill_patrons(bill_number: str, session_code: int) -> dict[str, Any]:
    """Every patron of a bill with their member ids, in LIS display order.

    ``role`` is Chief Patron, Chief Co-Patron, or Co-Patron. ``member_id``
    chains into get_member, get_member_bills, get_member_votes, and
    get_member_committees. get_bill lists the same names grouped by role,
    without ids.
    """
    number = _call(normalize_bill_number, bill_number)
    patrons = _call(_get_service().bill_patrons, number, session_code)

    return {
        "bill_number": number,
        "session_code": session_code,
        "count": len(patrons),
        "patrons": [compact_patron(p) for p in patrons],
    }


@mcp.tool()
def get_bill_summaries(bill_number: str, session_code: int) -> dict[str, Any]:
    """Plain-text summaries of a bill (as introduced, as passed, ...).

    The entry with ``is_active`` true is the current summary. An empty list
    means LIS has published no summary for that bill and session.
    """
    number = _call(normalize_bill_number, bill_number)
    summaries = _call(
        _get_service().client.get_bill_summaries,
        legislation_number=number,
        session_code=session_code,
    )

    return {
        "bill_number": number,
        "session_code": session_code,
        "summaries": [
            drop_empty(
                {
                    "version": summary.SummaryVersion,
                    "date": iso(summary.SummaryDate),
                    "is_active": summary.IsActive,
                    "summary": strip_html(summary.Summary) if summary.Summary else None,
                }
            )
            for summary in summaries
        ],
    }


# structured_output=False drops the duplicate structuredContent copy: an output
# schema makes the SDK send the payload twice, and bill text runs to 15,000
# characters. The same JSON still ships as text in content. The other large
# payloads (roll calls, voting records, rosters, schedules) do the same.
@mcp.tool(structured_output=False)
def get_bill_text(
    bill_number: str,
    session_code: int,
    document_code: str | None = None,
) -> dict[str, Any]:
    """Full text of one bill version, HTML stripped to plain text.

    ``document_code`` selects a version (e.g. "HB1" = introduced,
    "HB1ER" = enrolled); the default is the most recent version.
    ``available_versions`` lists every version that can be requested. Long
    texts are cut at 15,000 characters with ``truncated`` set true.
    Amendment markup (additions and strikethrough deletions) is flattened
    to plain text.
    """
    number = _call(normalize_bill_number, bill_number)
    service = _get_service()
    texts = _call(service.bill_text_versions, number, session_code)

    versions = [
        drop_empty(
            {
                "document_code": t.DocumentCode,
                "version": t.Version or t.Description,
                "date": iso(t.DraftDate),
            }
        )
        for t in texts
    ]

    selected, detail = _call(service.bill_text, number, session_code, document_code, versions=texts)

    text = strip_html(detail.DraftText)
    truncated = len(text) > _TEXT_CHAR_LIMIT
    if truncated:
        text = text[:_TEXT_CHAR_LIMIT]

    return {
        "bill_number": number,
        "session_code": session_code,
        "document_code": selected.DocumentCode,
        "version": detail.LegislationVersion or selected.Version,
        "truncated": truncated,
        "available_versions": versions,
        "text": text,
    }


@mcp.tool()
def get_bill_history(bill_number: str, session_code: int) -> dict[str, Any]:
    """Chronological action history for a bill, from prefiling to governor action.

    Passage dates live here (the bill detail's passage date fields are always
    null in LIS): look for events with ``is_passage`` true and read ``date``
    and ``vote_tally``. ``is_passage`` marks the events whose resulting
    status is a passage status (Passed House, Passed Senate, ...). ``status``
    is the bill's status after each event. ``committee`` names the committee
    that acted; this is the only place LIS records which committee holds a
    bill. ``vote_id`` links an event to its vote: pass it to get_roll_call.
    """
    number = _call(normalize_bill_number, bill_number)
    service = _get_service()
    legislation_id = _call(service.resolve_bill_id, number, session_code)
    events = _call(service.client.get_bill_events, legislation_id)

    statuses = _call(service.statuses_by_name)

    entries = []
    for event in events:
        status = statuses.get(event.Status) if event.Status else None
        entries.append(
            drop_empty(
                {
                    "date": iso(event.EventDate),
                    "description": event.Description,
                    "actor": event.ActorType,
                    "committee": event.CommitteeName,
                    "vote_tally": event.VoteTally,
                    "vote_id": event.VoteID,
                    "status": status.DisplayName if status else event.Status,
                    # Only flag passage events; omitting the key on the rest
                    # keeps the timeline compact.
                    "is_passage": (event.Status in _PASSAGE_STATUSES) or None,
                }
            )
        )

    return {
        "bill_number": number,
        "session_code": session_code,
        "legislation_id": legislation_id,
        "count": len(entries),
        "events": entries,
    }


# ----------------------------------------------------------------------------
# Votes
# ----------------------------------------------------------------------------


@mcp.tool()
def get_bill_votes(bill_number: str, session_code: int) -> dict[str, Any]:
    """Every recorded vote on a bill, in both chambers, in chronological order.

    Floor, committee, and subcommittee votes all appear. ``is_roll_call`` is
    true when the recorded members voted on this bill alone; it is false for
    a voice vote (no members recorded) and for a block vote (one motion that
    disposed of ``bills_in_vote`` bills at once, which is not a position on
    any one of them). Pass a ``vote_id`` to get_roll_call for the member by
    member record. Costs one request per vote.
    """
    number = _call(normalize_bill_number, bill_number)
    records = _call(_get_service().bill_votes, number, session_code)

    return {
        "bill_number": number,
        "session_code": session_code,
        "count": len(records),
        "votes": [compact_bill_vote(record) for record in records],
    }


@mcp.tool(structured_output=False)
def get_roll_call(
    bill_number: str,
    session_code: int,
    vote_id: int | None = None,
) -> dict[str, Any]:
    """Who voted which way on a bill, with party and district, one entry per vote.

    Members are grouped under ``yea``, ``nay``, ``abstain``, and
    ``not_voting``. ``statements`` are corrections members filed ("recorded
    as yea. Intended to vote nay."); the recorded response still stands. Only
    votes that record members on this bill alone are included: voice votes
    and block votes are left out (get_bill_votes lists those too).
    ``vote_id`` narrows the result to one vote.
    """
    number = _call(normalize_bill_number, bill_number)
    entries = _call(_get_service().roll_call, number, session_code, vote_id=vote_id)

    roll_calls: dict[int, dict[str, Any]] = {}
    for entry in entries:
        label = RESPONSE_LABELS.get(entry.response or "")
        if label is None:
            continue

        payload = roll_calls.get(entry.vote.VoteID)
        if payload is None:
            payload = roll_calls[entry.vote.VoteID] = roll_call_header(entry.vote)

        payload.setdefault(label, []).append(compact_voter(entry))

    result: dict[str, Any] = {
        "bill_number": number,
        "session_code": session_code,
        "count": len(roll_calls),
        "roll_calls": list(roll_calls.values()),
    }
    if not roll_calls:
        result["note"] = (
            "No attributable roll call. Voice votes record no members and block "
            "votes record positions on a bundle of bills, not on this one; "
            "get_bill_votes lists every vote with those flags."
        )

    return result


@mcp.tool(structured_output=False)
def get_member_votes(
    member_id: int,
    session_code: int,
    bill_number: str | None = None,
    exclude_block_votes: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Every vote one member cast in a session, or their votes on one bill.

    Floor, committee, and subcommittee votes all appear, in LIS order. A row
    is one (vote, bill) pair: a block vote that disposed of 105 bills appears
    105 times, each with ``is_block`` true and ``bills_in_vote`` set. A block
    vote is a real vote, but not a considered position on any one bill;
    ``exclude_block_votes`` drops them. ``total_matching`` counts rows and
    ``distinct_votes`` counts votes cast. Attendance roll calls are omitted.
    ``limit`` is capped at 200; page with ``offset``.
    """
    service = _get_service()
    member = _roster_member(service, member_id, session_code)

    if bill_number is not None:
        number = _call(normalize_bill_number, bill_number)
        votes = _call(service.member_votes_on, member_id, number, session_code)
    else:
        number = None
        votes = _call(service.member_votes, member_id, session_code)

    if exclude_block_votes:
        votes = [vote for vote in votes if not vote.is_block]

    page, offset = paginate(votes, limit, offset)

    payload: dict[str, Any] = {
        "member_id": member_id,
        "member": member.name,
        "session_code": session_code,
    }
    if number is not None:
        payload["bill_number"] = number

    payload.update(
        {
            "total_matching": len(votes),
            "distinct_votes": len({vote.result.VoteID for vote in votes}),
            "count": len(page),
            "offset": offset,
            "votes": [compact_member_vote(vote) for vote in page],
        }
    )
    return payload


# ----------------------------------------------------------------------------
# Members
# ----------------------------------------------------------------------------


@mcp.tool(structured_output=False)
def list_members(
    session_code: int,
    query: str | None = None,
    chamber: Literal["H", "S"] | None = None,
) -> dict[str, Any]:
    """The session's member roster, searchable by name.

    ``query`` matches any part of a name, ignoring case ("schmidt",
    "Charlie Schmidt", "Schmidt, C"); omit it for the whole roster. The
    roster deliberately includes members who left or arrived mid-session
    (``service_end_date`` and ``service_end_reason`` mark a departure): they
    cast the votes recorded before they left. ``member_id`` is the key for
    the member tools.
    """
    service = _get_service()

    if query:
        members = _call(service.find_members, query, session_code)
    else:
        roster = _call(service.members_by_id, session_code)
        members = sorted(roster.values(), key=lambda m: (m.list_name or m.name).lower())

    if chamber:
        members = [member for member in members if member.ChamberCode == chamber]

    return {
        "session_code": session_code,
        "count": len(members),
        "members": [compact_member(member) for member in members],
    }


@mcp.tool()
def get_member(member_id: int, session_code: int) -> dict[str, Any]:
    """One member's roster record: name, chamber, party, district, status, contact.

    Reads the session roster, which is the authoritative source (LIS's by-id
    endpoint answers nothing for some sitting members). Use
    get_member_committees for seats, get_member_bills for patronage, and
    get_member_votes for the voting record.
    """
    member = _roster_member(_get_service(), member_id, session_code)

    return drop_empty(
        {
            "session_code": session_code,
            **compact_member(member),
            "service_begin_date": iso(member.ServiceBeginDate),
            "email": member.email or None,
            "phone": member.GABPhoneNumber,
            "room": member.RoomNumber,
        }
    )


@mcp.tool(structured_output=False)
def get_member_bills(
    member_id: int,
    session_code: int,
    role: Literal["Chief Patron", "Chief Co-Patron", "Co-Patron"] | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Every bill a member patrons in a session, in any role.

    The session bill list names only chief patrons, so this is the route to
    a member's co-patronage. ``is_chief_patron`` marks the bills the member
    sponsors; ``role`` restricts the list to one role. ``query`` is a
    case-insensitive substring match against the bill number, description,
    and title. ``limit`` is capped at 200; page with ``offset``.
    """
    service = _get_service()
    member = _roster_member(service, member_id, session_code)

    role_id = _PATRON_ROLE_IDS[role] if role else None
    bills = _call(service.member_bills, member_id, session_code, role=role_id)

    if role is None:
        chief = _call(service.member_bills, member_id, session_code, role=CHIEF_PATRON)
        chief_ids = {bill.LegislationID for bill in chief}
    elif role_id == CHIEF_PATRON:
        chief_ids = {bill.LegislationID for bill in bills}
    else:
        chief_ids = set()

    if query:
        needle = query.strip().lower()
        bills = [
            bill
            for bill in bills
            if matches(needle, bill.LegislationNumber, bill.Description, bill.LegislationTitle)
        ]

    page, offset = paginate(bills, limit, offset)

    payload: dict[str, Any] = {
        "member_id": member_id,
        "member": member.name,
        "session_code": session_code,
    }
    if role is not None:
        payload["role"] = role

    payload.update(
        {
            "total_matching": len(bills),
            "count": len(page),
            "offset": offset,
            "bills": [compact_member_bill(bill, bill.LegislationID in chief_ids) for bill in page],
        }
    )
    return payload


@mcp.tool()
def get_member_committees(
    member_id: int,
    session_code: int,
    include_subcommittees: bool = False,
) -> dict[str, Any]:
    """Every committee seat one member holds in a session, with the role.

    LIS has no member-first committee endpoint, so this reads every seat
    list in the member's chamber: 14 requests cold for a House member, 67
    with subcommittees, all cached afterwards.
    """
    service = _get_service()
    member = _roster_member(service, member_id, session_code)
    seats = _call(
        service.member_committees,
        member_id,
        session_code,
        include_subcommittees=include_subcommittees,
    )

    return {
        "member_id": member_id,
        "member": member.name,
        "session_code": session_code,
        "count": len(seats),
        "seats": [compact_seat(seat) for seat in seats],
    }


# ----------------------------------------------------------------------------
# Committees
# ----------------------------------------------------------------------------


@mcp.tool()
def list_committees(
    chamber: Literal["H", "S"] | None = None,
    include_subcommittees: bool = False,
) -> dict[str, Any]:
    """The standing committees of one or both chambers, optionally with subcommittees.

    The list is not session scoped: it describes the committees that exist
    now. Who sits on them is session scoped; see get_committee. Use a
    committee's ``number`` ("H08") or name with get_committee, list_meetings,
    and list_dockets.
    """
    committees = _call(
        _get_service().committees,
        chamber,
        include_subcommittees=include_subcommittees,
    )

    return {
        "count": len(committees),
        "committees": [compact_committee(committee) for committee in committees],
    }


@mcp.tool()
def get_committee(
    committee: str,
    session_code: int,
    chamber: Literal["H", "S"] | None = None,
) -> dict[str, Any]:
    """A committee's roster for a session: each member's role, party, and district.

    ``committee`` is a number ("H08", "S13") or any part of a name ("Courts
    of Justice", "courts"). Both chambers have some names, so pass
    ``chamber`` or the number when a name is ambiguous. Seats come back in
    LIS order, chair first. ``meeting_note`` is the committee's regular
    meeting time when LIS records one, and ``subcommittees`` lists the
    subcommittees, which this tool accepts by number too.
    """
    service = _get_service()
    found = _call(service.resolve_committee, committee, chamber)
    seats = _call(service.committee_members, found.CommitteeID, session_code)

    subcommittees = [
        row
        for row in _call(service.committees, found.ChamberCode, include_subcommittees=True)
        if row.ParentCommitteeID == found.CommitteeID
    ]

    # The list nulls MeetingNote; only the by-id call fills it.
    session_id = _call(service.session_id, session_code)
    detail = _call(service.client.get_committee, found.CommitteeID, session_id)

    payload = compact_committee(found)
    payload["session_code"] = session_code
    if detail is not None and detail.MeetingNote:
        payload["meeting_note"] = detail.MeetingNote

    payload["count"] = len(seats)
    payload["members"] = [compact_committee_member(seat) for seat in seats]
    if subcommittees:
        payload["subcommittees"] = [
            {"committee_id": row.CommitteeID, "number": row.CommitteeNumber, "name": row.name}
            for row in subcommittees
        ]

    return payload


# ----------------------------------------------------------------------------
# Meetings
# ----------------------------------------------------------------------------


@mcp.tool(structured_output=False)
def list_meetings(
    start_date: str,
    end_date: str,
    committee: str | None = None,
    chamber: Literal["H", "S"] | None = None,
    meeting_type: Literal["Committee", "Chamber", "Conference", "Caucus", "Docket", "Other"]
    | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Meetings on the master schedule in a date range: committees, floor, caucuses.

    Dates are ``YYYY-MM-DD`` and inclusive; keep the range short, since a
    week in session is about 150 meetings. ``committee`` narrows the list to
    one committee by number or name (``chamber`` disambiguates a name found
    in both chambers). ``time`` is free text as the clerk entered it ("15
    minutes after adjournment") and may be blank. Cancelled meetings stay
    listed with ``cancelled`` true. ``limit`` is capped at 200; page with
    ``offset``.
    """
    try:
        start = parse_date(start_date, "start_date")
        end = parse_date(end_date, "end_date")
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    if end < start:
        raise ToolError("end_date must not be before start_date.")

    service = _get_service()
    owner_id = None
    if committee:
        owner_id = _call(service.resolve_committee, committee, chamber).CommitteeID

    meetings = _call(
        service.client.get_schedules,
        start,
        end,
        owner_id=owner_id,
        schedule_type_id=_MEETING_TYPE_IDS[meeting_type] if meeting_type else None,
    )
    meetings = sorted(meetings, key=lambda m: (m.ScheduleDate is None, m.ScheduleDate))

    page, offset = paginate(meetings, limit, offset)

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_matching": len(meetings),
        "count": len(page),
        "offset": offset,
        "meetings": [compact_meeting(meeting) for meeting in page],
    }


@mcp.tool(structured_output=False)
def list_dockets(committee: str, session_code: int) -> dict[str, Any]:
    """A Senate committee's dockets for a session: each meeting and the bills on it.

    Dockets are the Senate's committee agendas, newest first as LIS lists
    them. The House keeps none in the API: read a House bill's committee from
    get_bill_history and the committee's meetings from list_meetings.
    ``committee`` is a Senate committee number ("S13") or name. A docket and
    its linked schedule can disagree on the hour, so both times are reported.
    Costs one request per docket cold, cached afterwards.
    """
    service = _get_service()
    found = _call(service.resolve_committee, committee, "S")
    entries = _call(service.docket_entries, found.CommitteeID, session_code)

    dockets: dict[int, dict[str, Any]] = {}
    for entry in entries:
        payload = dockets.get(entry.docket.DocketID)
        if payload is None:
            payload = dockets[entry.docket.DocketID] = docket_header(entry.docket)

        payload["bills"].append(compact_docket_item(entry.item))

    return {
        "committee_id": found.CommitteeID,
        "committee_number": found.CommitteeNumber,
        "committee": found.name,
        "session_code": session_code,
        "count": len(dockets),
        "dockets": list(dockets.values()),
    }


@mcp.tool()
def list_floor_calendars(chamber: Literal["H", "S"], session_code: int) -> dict[str, Any]:
    """A chamber's floor calendars for a session, one per sitting.

    A floor calendar is the day's order of business: the bills up for a
    reading or a vote. Pass a ``calendar_id`` to get_floor_calendar for the
    bills on it. Several calendars can share a date.
    """
    calendars = _call(_get_service().client.get_calendars, chamber, session_code)

    return {
        "chamber": chamber,
        "session_code": session_code,
        "count": len(calendars),
        "calendars": [
            drop_empty(
                {
                    "calendar_id": calendar.CalendarID,
                    "reference": calendar.ReferenceNumber,
                    "date": iso(calendar.CalendarDate),
                    "description": calendar.Description,
                }
            )
            for calendar in calendars
        ],
    }


@mcp.tool(structured_output=False)
def get_floor_calendar(calendar_id: int) -> dict[str, Any]:
    """One floor calendar: the bills up for action, by section, with the votes taken.

    ``calendar_id`` comes from list_floor_calendars. Each bill carries its
    calendar section (e.g. "Senate Bills on Third Reading") and the actions
    recorded against it, with a ``vote_id`` where a vote was taken; pass that
    with the bill number and session code to get_roll_call.
    """
    calendar = _call(_get_service().client.get_calendar, calendar_id)

    if calendar is None:
        raise ToolError(f"No floor calendar has CalendarID {calendar_id}.")

    bills = []
    for category in calendar.CalendarCategories:
        for agenda in category.Agendas:
            if not agenda.LegislationNumber:
                continue

            actions = [
                drop_empty(
                    {
                        "description": strip_html(item.Description) if item.Description else None,
                        "vote_id": item.VoteID,
                    }
                )
                for item in agenda.AgendaItems
            ]
            bills.append(
                drop_empty(
                    {
                        "bill_number": agenda.LegislationNumber,
                        "description": agenda.LegislationDescription,
                        "section": category.Description,
                        "actions": [action for action in actions if action],
                    }
                )
            )

    payload = drop_empty(
        {
            "calendar_id": calendar.CalendarID,
            "reference": calendar.ReferenceNumber,
            "date": iso(calendar.CalendarDate),
            "chamber": calendar.ChamberCode,
            "session_code": int(calendar.SessionCode) if calendar.SessionCode else None,
        }
    )
    payload["count"] = len(bills)
    payload["bills"] = bills
    return payload


# ----------------------------------------------------------------------------
# Diagnostics
# ----------------------------------------------------------------------------


@mcp.tool()
def check_api_key() -> dict[str, Any]:
    """Validate the configured LIS API key.

    LIS heartbeat endpoints accept any key, so this is the only real check:
    it asks the PartnerAuthentication service whether the key is registered
    and active.
    """
    partner = _call(_get_service().client.check_api_key)

    if partner is None:
        return {
            "valid": False,
            "message": (
                "The configured LIS_API_KEY is not a registered partner key. "
                f"Register at {_REGISTRATION_URL}."
            ),
        }

    return drop_empty(
        {
            "valid": partner.IsActive,
            "organization": partner.OrganizationName,
            "contact": partner.ContactName,
            "message": None if partner.IsActive else "The key is registered but inactive.",
        }
    )


def main() -> None:
    """Entry point for the ``va-lis-mcp`` console script (stdio transport)."""
    mcp.run()


# ----------------------------------------------------------------------------
# Service access and error handling
# ----------------------------------------------------------------------------


def _get_service() -> LISService:
    """The process-wide service, built on the first tool call.

    Never instantiated at import time: MCP hosts launch servers at app boot,
    and a missing key must fail the tool call, not the process. Once built,
    the instance persists so its caches survive across calls.
    """
    global _service

    if _service is None:
        _service = LISService(_get_client())

    return _service


def _get_client() -> LISClient:
    """Build the transport client, rewrapping a missing key into a clear message."""
    try:
        return LISClient()
    except LISClientError as exc:
        raise ToolError(
            f"LIS_API_KEY is not set. Register for a free key at {_REGISTRATION_URL} "
            "and set the LIS_API_KEY environment variable for this server."
        ) from exc


def _call(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Invoke a client or service call, converting its errors into ToolError.

    Every error a tool raises has to be a ToolError to reach the caller. mcp
    replaces anything else with "Error executing tool <name>" and sends no
    detail (see UnexpectedToolError), so a message written for the caller is
    lost unless it is raised as a ToolError. Chaining with ``from`` still puts
    the original and its traceback in the server's log.
    """
    try:
        return fn(*args, **kwargs)
    except LISClientError as exc:
        raise ToolError(f"LIS API error: {exc}") from exc
    except LISError as exc:
        # BillNotFoundError, CommitteeNotFoundError, InvalidBillNumberError and
        # friends already read well; pass their text straight through.
        raise ToolError(str(exc)) from exc


def _roster_member(service: LISService, member_id: int, session_code: int) -> Member:
    """The roster row for a member, or a readable error naming the session."""
    member = _call(service.members_by_id, session_code).get(member_id)

    if member is None:
        raise ToolError(
            f"MemberID {member_id} is not on the session {session_code} roster. "
            "Use list_members to search the roster by name."
        )

    return member
