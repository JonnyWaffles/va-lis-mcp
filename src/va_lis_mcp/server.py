"""MCP server exposing the Virginia Legislative Information System (LIS) API.

Wraps :class:`va_lis_client.LISService` with read-only tools shaped for LLM
consumption: human identifiers in (bill number + session code, member id,
committee number or name), compact JSON out, HTML stripped from bill text,
summaries, and agendas. Bill number normalization, id resolution, the
reference joins (roster, statuses), and every cache live in the service; this
module defines the tools, and :mod:`va_lis_mcp._shaping` turns the models they
get back into the compact dicts they return.

The tools cover bills (list, keyword search, detail, summaries, text,
versions, history, patrons, amendments, conferences), votes (per bill with
the roll call, per member, and per chamber day), members with their bills,
committee seats, and district offices, committees with their rosters and
staff, meetings (the master schedule, Senate dockets, and floor calendars),
chamber records (minutes books and the communications between the chambers),
and the session statistics LIS publishes.

Run over stdio (the transport MCP hosts launch)::

    va-lis-mcp

or serve over HTTP with the ASGI app in :mod:`va_lis_mcp.asgi`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
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
    compact_amended_bill,
    compact_bill_row,
    compact_bill_version,
    compact_bill_vote,
    compact_chamber_statistic,
    compact_committee,
    compact_committee_member,
    compact_committee_statistic,
    compact_communication_category,
    compact_communication_row,
    compact_conference_bill,
    compact_docket_item,
    compact_meeting,
    compact_member,
    compact_member_bill,
    compact_member_vote,
    compact_minutes_book,
    compact_minutes_record,
    compact_office,
    compact_patron,
    compact_search_row,
    compact_seat,
    compact_session,
    compact_session_event,
    compact_shallow_vote,
    compact_staff,
    compact_voter,
    condense_patrons,
    docket_header,
    drop_empty,
    first_row_per_bill,
    iso,
    matches,
    one_row_per_bill,
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
System (LIS): sessions, bills and keyword search, summaries, full bill text
and versions, action history, votes and roll calls, members, committees,
meetings, minutes books, communications between the chambers, and session
statistics.

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

Finding bills:
- list_bills matches a bill's number and short description. search_bills is
  the full text search over summaries or bill text, and it also filters by
  stage, patron, and chapter number.
- A keyword search on its own includes failed bills. With a stage or patron
  filter LIS leaves out failed and incorporated bills.

Voting records:
- get_bill_votes lists every vote a bill saw (floor, committee, and
  subcommittee, in both chambers); get_roll_call names the members with party
  and district; get_member_votes is the same record from the member's side.
- get_bill_vote_tallies is the fast route to a bill's tallies: two requests,
  no members, and most voice votes missing. list_votes is the same record for
  one chamber on one day.
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

Chamber records:
- A minutes book is the clerk's record of what one body did on one day. A
  vote_id in get_minutes_book belongs to the bill beside it, which a vote_id
  in get_bill_history does not always do. Both chambers publish committee
  books; only the Senate publishes floor books.
- A communication is the formal message one chamber sends the other. It
  names bills and never votes.
- get_session_statistics and get_committee_statistics are counts LIS computes
  itself. They do not always agree with the bill list, so count from
  list_bills when a number has to be exact.

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


@mcp.tool()
def get_session(session_code: int) -> dict[str, Any]:
    """One session with its milestone dates: prefiling, convening, adjournment.

    ``events`` are the dates LIS records for the session, oldest first:
    "Prefile Date", "Session Start", "Adjournment" (the regular session's
    sine die), and "Reconvene" (the one day session that takes up the
    Governor's vetoes and recommendations). ``projected_date`` is the planned
    date LIS holds for an event.
    """
    session = _call(_get_service().client.get_session_by_code, session_code)

    if session is None:
        raise ToolError(
            f"No session has code {session_code}. Use list_sessions to find the codes for a year."
        )

    events = sorted(
        session.SessionEvents,
        key=lambda e: (
            (e.ActualDate or e.ProjectedDate) is None,
            e.ActualDate or e.ProjectedDate,
        ),
    )

    payload = compact_session(session)
    payload["events"] = [compact_session_event(event) for event in events]
    return payload


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


# structured_output=False: see the note on get_bill_text.
@mcp.tool(structured_output=False)
def search_bills(
    session_code: int,
    keyword: str | None = None,
    search_in: Literal["Summary", "Bill Text"] = "Summary",
    category: str | None = None,
    member_id: int | None = None,
    chapter: int | None = None,
    chamber: Literal["H", "S"] | None = None,
    include_summaries: bool = False,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """Search a session's bills by keyword, stage, patron, or chapter number.

    This is the full text search; list_bills only matches the number and the
    short description. ``keyword`` searches the summaries, or the bill text
    when ``search_in`` is "Bill Text", and takes AND, OR, and parentheses
    ("firearm AND ammunition"). LIS drops common words, so "the" finds
    nothing. ``category`` is a stage a bill reached, by name: "In Committee",
    "Passed House", "Continued", "Vetoed", "Acts of Assembly chapters"; an
    unknown name answers with the full list. ``member_id`` narrows to one
    patron in any role. ``chapter`` finds the bill behind an Acts of Assembly
    chapter (350 finds CHAP0350). At least one of the four is required.

    One row per bill, in LIS order, with status, chapter, chief patron, and
    the committee that holds the bill. ``include_summaries`` adds the plain
    text of each bill's furthest-along summary. A keyword search on its own
    includes failed bills; with ``category`` or ``member_id`` LIS leaves out
    failed and incorporated bills. ``limit`` is capped at 200; page with
    ``offset``.
    """
    keyword = (keyword or "").strip() or None

    if keyword is None and category is None and member_id is None and chapter is None:
        raise ToolError(
            "search_bills needs a keyword, a category, a member_id, or a chapter. "
            "Use list_bills for every bill in a session."
        )

    service = _get_service()
    criteria: dict[str, Any] = {"session_code": session_code}
    if keyword:
        # LIS answers nothing to a keyword that names no location (verified
        # live 2026-10-12), so the location always rides along.
        criteria["keyword"] = keyword
        criteria["keyword_location"] = search_in

    if category:
        criteria["category_id"] = _category_id(service, category)

    if member_id is not None:
        criteria["member_id"] = member_id

    if chapter is not None:
        criteria["chapter_number"] = f"CHAP{chapter:04d}"

    bills = _call(service.search_bills, **criteria)

    # The chamber is matched here and never sent. Any criterion beside the
    # keyword makes LIS drop failed and incorporated bills (verified live
    # 2026-10-12: "firearm" found 50 bills, and the two chambers asked for
    # one at a time found 37 between them).
    if chamber:
        bills = [bill for bill in bills if bill.ChamberCode == chamber]

    page, offset = paginate(bills, limit, offset)

    payload: dict[str, Any] = {"session_code": session_code}
    if keyword:
        payload["keyword"] = keyword
        payload["search_in"] = search_in

    payload.update(
        {
            "total_matching": len(bills),
            "count": len(page),
            "offset": offset,
            "bills": [compact_search_row(bill, include_summaries) for bill in page],
        }
    )

    if category or member_id is not None:
        payload["note"] = (
            "LIS leaves failed and incorporated bills out of a search that names a "
            "category or a patron. A keyword search on its own includes them, and "
            "get_member_bills lists every bill a member patrons."
        )
    elif keyword and not bills:
        payload["note"] = (
            "No bill matched. LIS drops common words from a keyword, and a search of "
            'the summaries misses a term found only in the bill text (search_in "Bill Text").'
        )

    return payload


@mcp.tool(structured_output=False)
def list_most_viewed_bills(session_code: int, limit: int = 25) -> dict[str, Any]:
    """The bills of a session that readers of the LIS website open most often.

    LIS sends at most 100, in its own order, with the same fields as
    search_bills. The list measures public attention, not legislative weight.
    """
    service = _get_service()
    session_id = _call(service.session_id, session_code)
    bills = one_row_per_bill(_call(service.client.get_most_frequent_legislation, session_id))

    page, _ = paginate(bills, limit, 0)

    return {
        "session_code": session_code,
        "total_matching": len(bills),
        "count": len(page),
        "bills": [compact_search_row(bill) for bill in page],
    }


@mcp.tool(structured_output=False)
def list_amended_bills(
    session_code: int,
    amended_by: Literal["H", "S"] | None = None,
    query: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """Every bill in a session that carries an amendment, with its amendments.

    ``amended_by`` picks the chamber that offered the amendments, not the
    chamber of the bills: "H" returns the bills with a House amendment, Senate
    bills among them, and only their House amendments. Each amendment has its
    ``document_code`` (HB1005AH1 is the first House amendment to HB1005), a
    ``description`` that names the committee or the floor, and a
    ``disposition`` (Adopted, Not Adopted, Engrossed, Removed, Posted).
    ``query`` is a case-insensitive substring match against the bill number
    and description. ``limit`` is capped at 200; page with ``offset``.
    """
    rows = _call(
        _get_service().client.get_amended_legislation,
        session_code=session_code,
        chamber_code=amended_by,
    )

    # LIS repeats a bill once per summary version, with the same amendments
    # on every copy.
    bills = first_row_per_bill(rows)

    if query:
        needle = query.strip().lower()
        bills = [
            bill for bill in bills if matches(needle, bill.LegislationNumber, bill.Description)
        ]

    page, offset = paginate(bills, limit, offset)

    payload: dict[str, Any] = {"session_code": session_code}
    if amended_by:
        payload["amended_by"] = amended_by

    payload.update(
        {
            "total_matching": len(bills),
            "count": len(page),
            "offset": offset,
            "bills": [compact_amended_bill(bill) for bill in page],
        }
    )
    return payload


@mcp.tool(structured_output=False)
def list_conference_bills(
    session_code: int,
    chamber: Literal["H", "S"] | None = None,
    member_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """The bills of a session that went to a conference committee.

    A conference committee settles the differences when the two chambers pass
    different versions of a bill. ``chamber`` is the bill's chamber of origin,
    and ``member_id`` narrows the list to the conferences one member sat on.
    The rows name no conferees: get_bill_history lists them on the bill's
    events ("House Conferees: ..."). ``limit`` is capped at 200; page with
    ``offset``.
    """
    service = _get_service()

    # The endpoint ignores a session code and then answers for every session
    # back to 1994, so the id is the only safe key.
    session_id = _call(service.session_id, session_code)
    bills = _call(
        service.client.get_conference_legislation,
        session_id,
        chamber_code=chamber,
        member_id=member_id,
    )

    page, offset = paginate(bills, limit, offset)

    return {
        "session_code": session_code,
        "total_matching": len(bills),
        "count": len(page),
        "offset": offset,
        "bills": [compact_conference_bill(bill) for bill in page],
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
# payloads (search results, roll calls, voting records, rosters, schedules,
# minutes books) do the same.
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
def get_bill_versions(bill_number: str, session_code: int) -> dict[str, Any]:
    """Every published version of a bill, oldest first, with links to its files.

    Introduced, each substitute and amendment, engrossed, enrolled, and
    chaptered. Each version carries the address of its ``pdf`` and ``html``
    file and of any fiscal ``impact_statements``. ``chamber`` is the chamber
    that made the version, not the bill's: a Senate substitute for a House
    bill says S. ``sponsor`` is who offered it (House, Senate, Committee,
    Conference, Governor, Member) and ``disposition`` what became of it
    (Offered, Adopted, Rejected, Posted). Pass a ``document_code`` to
    get_bill_text to read that version. A chaptered version's code is its
    chapter number (CHAP0350).
    """
    number = _call(normalize_bill_number, bill_number)

    # Only a request for one bill by id fills the file links, which is the
    # route with_files takes.
    versions = _call(_get_service().bill_versions, [number], session_code, with_files=True)[number]

    return {
        "bill_number": number,
        "session_code": session_code,
        "count": len(versions),
        "versions": [compact_bill_version(version) for version in versions],
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
    member record. Costs one request per vote; get_bill_vote_tallies lists
    the tallies in two requests when the flags are not needed.
    """
    number = _call(normalize_bill_number, bill_number)
    records = _call(_get_service().bill_votes, number, session_code)

    return {
        "bill_number": number,
        "session_code": session_code,
        "count": len(records),
        "votes": [compact_bill_vote(record) for record in records],
    }


@mcp.tool()
def get_bill_vote_tallies(bill_number: str, session_code: int) -> dict[str, Any]:
    """A bill's votes with their tallies and no members, in two requests.

    The fast route to how a bill fared at each stage: committee,
    subcommittee, and floor votes from both chambers, oldest first. It is
    lighter than get_bill_votes and it knows less. Most voice votes are
    missing, there is no pass or fail flag (read ``description`` and
    ``tally``), and a block vote does not say how many bills it covered. An
    empty list can mean the bill was decided by voice votes alone.
    ``referred_to`` names the committee a vote sent the bill on to. Pass a
    ``vote_id`` to get_roll_call for the members.
    """
    number = _call(normalize_bill_number, bill_number)
    votes = _call(_get_service().bill_shallow_votes, number, session_code)

    return {
        "bill_number": number,
        "session_code": session_code,
        "count": len(votes),
        "votes": [compact_shallow_vote(vote, include_bill=False) for vote in votes],
    }


@mcp.tool(structured_output=False)
def list_votes(
    chamber: Literal["H", "S"],
    session_code: int,
    start_date: str,
    end_date: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Every vote a chamber took in a date range: floor, committee, and subcommittee.

    Dates are ``YYYY-MM-DD`` and inclusive; ``end_date`` defaults to
    ``start_date``, which gives one day. Keep the range short, since a chamber
    takes about 50 votes on a session day. ``chamber`` is the chamber that
    voted, so a row can name a bill from the other chamber. Rows carry the
    tally and no members: pass a row's ``bill_number`` and ``vote_id`` to
    get_roll_call for those. A block vote names no bill, most voice votes are
    missing, and ``classification`` "Attendance" marks a quorum call.
    ``limit`` is capped at 200; page with ``offset``.
    """
    start = _date(start_date, "start_date")
    end = _date(end_date, "end_date") if end_date else start

    if end < start:
        raise ToolError("end_date must not be before start_date.")

    votes = _call(
        _get_service().client.get_shallow_votes,
        chamber,
        session_code=session_code,
        start_date=start,
        # LIS reads its end date as exclusive; the tool's is inclusive.
        end_date=end + timedelta(days=1),
    )
    votes = sorted(votes, key=lambda v: (v.VoteDate is None, v.VoteDate, v.VoteID))

    page, offset = paginate(votes, limit, offset)

    return {
        "chamber": chamber,
        "session_code": session_code,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_matching": len(votes),
        "count": len(page),
        "offset": offset,
        "votes": [compact_shallow_vote(vote) for vote in page],
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
    party: Literal["D", "R", "I"] | None = None,
) -> dict[str, Any]:
    """The session's member roster, searchable by name, chamber, and party.

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

    if party:
        members = [member for member in members if member.PartyCode == party]

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
    get_member_committees for seats, get_member_bills for patronage,
    get_member_votes for the voting record, and get_member_office for the
    district office.
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


@mcp.tool()
def get_member_office(member_id: int) -> dict[str, Any]:
    """A member's district office: postal address, phone, and email.

    The district office is the contact detail LIS publishes nowhere else;
    get_member has the Capitol room, phone, and email. The record is not
    session scoped, so there is no session code. Many members have no record,
    and ``found`` is false for them.
    """
    office = _call(_get_service().member_office, member_id)

    if office is None:
        return {
            "member_id": member_id,
            "found": False,
            "note": (
                "LIS holds no district office for this MemberID. Many sitting members "
                "have none; get_member has the Capitol room, phone, and email."
            ),
        }

    return {"member_id": member_id, "found": True, **compact_office(office)}


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


@mcp.tool()
def get_committee_staff(
    committee: str,
    chamber: Literal["H", "S"] | None = None,
) -> dict[str, Any]:
    """The clerks and staff who serve a committee.

    ``committee`` is a number ("H08", "S13") or any part of a name; pass
    ``chamber`` or the number when both chambers use the name. ``role`` is
    Committee Clerk or Committee Staff, the Division of Legislative Services
    staff assigned to the committee. The list is not session scoped: it names
    who serves now, and ``since`` is when each assignment began. LIS sends no
    phone number or email for staff.
    """
    service = _get_service()
    found = _call(service.resolve_committee, committee, chamber)
    staff = _call(service.committee_staff, found.CommitteeID)

    payload = compact_committee(found)
    payload["count"] = len(staff)
    payload["staff"] = [compact_staff(person) for person in staff]
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
# Chamber records
# ----------------------------------------------------------------------------


@mcp.tool(structured_output=False)
def list_minutes_books(
    session_code: int,
    committee: str | None = None,
    chamber: Literal["H", "S"] | None = None,
    kind: Literal["Floor", "Committee"] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """The published minutes books of a session, newest first.

    A minutes book is the clerk's record of what one body did on one day.
    Both chambers publish committee books. Only the Senate publishes floor
    books (``is_floor`` true), so read House floor action from
    list_floor_calendars and get_bill_history. ``committee`` is a number or
    name, and it excludes every floor book; ``kind`` picks the floor books or
    the committee books. Dates are ``YYYY-MM-DD`` and inclusive. A book with
    ``status`` "In Progress" can still change. Pass a ``minutes_book_id`` to
    get_minutes_book for the bills and votes. ``limit`` is capped at 200;
    page with ``offset``.
    """
    start = _date(start_date, "start_date") if start_date else None
    end = _date(end_date, "end_date") if end_date else None

    if start and end and end < start:
        raise ToolError("end_date must not be before start_date.")

    books = _call(
        _get_service().minutes_books,
        session_code,
        committee=committee,
        chamber_code=chamber,
        start_date=start,
        end_date=end,
    )

    if kind:
        books = [book for book in books if book.is_floor == (kind == "Floor")]

    books = sorted(
        books,
        key=lambda b: (b.MinutesDate is not None, b.MinutesDate, b.MinutesBookID),
        reverse=True,
    )

    page, offset = paginate(books, limit, offset)

    return {
        "session_code": session_code,
        "total_matching": len(books),
        "count": len(page),
        "offset": offset,
        "books": [compact_minutes_book(book) for book in page],
    }


@mcp.tool(structured_output=False)
def get_minutes_book(
    minutes_book_id: int,
    include_procedural: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """One minutes book: each action a chamber or committee took that day, in order.

    One record per action, with the bill, the ``section`` of business it fell
    under, the ``action`` taken, and the ``tally``. A ``vote_id`` here belongs
    to the bill beside it, which a ``vote_id`` in get_bill_history does not
    always do: pass it with the bill number and the session code to
    get_roll_call. The tally keeps a presiding officer's tiebreak ("18-Y
    18-N 0-A, Chair votes Yea"), which the vote record drops. ``is_block``
    marks one motion that disposed of many bills, and ``is_voice`` a vote
    that names no members. ``include_procedural`` adds the entries that name
    no bill: the call to order, recesses, communications received. A floor
    day runs to about 200 records; ``limit`` is capped at 200, page with
    ``offset``.
    """
    service = _get_service()
    book = _call(service.minutes_book, minutes_book_id)

    if book is None:
        raise ToolError(
            f"No minutes book has MinutesBookID {minutes_book_id}. "
            "Use list_minutes_books to find the books of a session."
        )

    # The book is cached by now, so this second read costs no request.
    records = _call(service.minutes_records, minutes_book_id, bills_only=not include_procedural)

    # A category's text is the heading the clerk printed ("Senate Bills
    # Vetoed by Governor"); its description is the generic kind.
    sections = {
        category.MinutesCategoryID: category.CategoryText or category.CategoryDescription
        for category in book.MinutesCategories
    }

    page, offset = paginate(records, limit, offset)

    payload = compact_minutes_book(book)
    payload.update(
        {
            "total_matching": len(records),
            "count": len(page),
            "offset": offset,
            "records": [
                compact_minutes_record(record, sections.get(record.entry.MinutesCategoryID))
                for record in page
            ],
        }
    )

    if not records:
        payload["note"] = (
            "LIS holds no records for this book. An older book has only its html file, "
            "and include_procedural adds the entries that name no bill."
        )

    return payload


@mcp.tool(structured_output=False)
def list_communications(
    session_code: int,
    chamber: Literal["H", "S"] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """The messages the two chambers sent each other in a session, newest first.

    A communication is the formal notice a clerk sends the other chamber:
    "The House of Delegates has passed the following Senate bills".
    ``chamber`` is the chamber that sent it. The rows name no bills; pass a
    ``communication_id`` to get_communication for those. ``reference`` is
    unique within one session only. ``limit`` is capped at 200; page with
    ``offset``.
    """
    rows = _call(
        _get_service().client.get_communications,
        session_code=session_code,
        chamber_code=chamber,
    )
    rows = sorted(
        rows,
        key=lambda r: (r.CommunicationDate is not None, r.CommunicationDate, r.CommunicationID),
        reverse=True,
    )

    page, offset = paginate(rows, limit, offset)

    return {
        "session_code": session_code,
        "total_matching": len(rows),
        "count": len(page),
        "offset": offset,
        "communications": [compact_communication_row(row) for row in page],
    }


@mcp.tool(structured_output=False)
def get_communication(communication_id: int) -> dict[str, Any]:
    """One communication between the chambers: each statement and the bills it names.

    ``statements`` are the clerk's sentences in order ("THE SENATE HAS PASSED
    THE FOLLOWING HOUSE BILLS:"), each with its bills, their chief patrons,
    and the ``document_code`` of the text version acted on. A communication
    records what a chamber did, not how it voted: it carries no vote ids.
    LIS holds structured content from May 2024 on. An older communication
    comes back with no statements and an ``html`` link to the original.
    """
    detail = _call(_get_service().client.get_communication, communication_id)

    if detail is None:
        raise ToolError(
            f"No communication has CommunicationID {communication_id}. "
            "Use list_communications to find the communications of a session."
        )

    statements = [compact_communication_category(category) for category in detail.categories]

    payload = compact_communication_row(detail)
    if detail.SessionCode:
        payload["session_code"] = int(detail.SessionCode)

    payload["count"] = len(statements)
    payload["statements"] = statements
    if not statements:
        payload["note"] = (
            "LIS holds no structured content for this communication. Before May 2024 "
            "the html file is the record."
        )

    return payload


# ----------------------------------------------------------------------------
# Statistics
# ----------------------------------------------------------------------------


@mcp.tool()
def get_session_statistics(session_code: int) -> dict[str, Any]:
    """LIS's own bill counts for a session, by chamber of origin and legislation type.

    One row per chamber and ``type`` (B bill, J joint resolution, R
    resolution); a type with no bills has no row. ``total`` is ``introduced``
    plus ``continued_from_last_session``, and ``passed`` counts chaptered
    plus vetoed bills. LIS computes these counts itself and they do not
    always agree with the bill list: a total can miss by a bill, the outcome
    columns add up differently in each session, ``passed_house`` and
    ``passed_senate`` are not a funnel, and ``continued_to_next_session``
    overcounts (``pending`` matches the next session). Count from list_bills
    when a number has to be exact.
    """
    rows = _call(_get_service().client.get_chamber_statistics, session_code=session_code)

    return {
        "session_code": session_code,
        "count": len(rows),
        "statistics": [compact_chamber_statistic(row) for row in rows],
    }


@mcp.tool()
def get_committee_statistics(chamber: Literal["H", "S"], session_code: int) -> dict[str, Any]:
    """LIS's own referral counts for each standing committee of one chamber.

    ``house_bills`` and ``senate_bills`` split each count by the bill's
    chamber of origin, so ``senate_bills`` on a House committee counts the
    Senate bills sent to it after crossover. The counts are referrals, not
    bills, and the columns do not add up: ``failed`` counts only what the
    committee itself killed, and ``in_subcommittee`` is a part of
    ``in_committee``. Subcommittees have no rows. LIS names no bill behind a
    count; get_bill_history is the record of where one bill went.
    """
    rows = _call(
        _get_service().client.get_committee_statistics,
        chamber,
        session_code=session_code,
    )

    return {
        "chamber": chamber,
        "session_code": session_code,
        "count": len(rows),
        "committees": [compact_committee_statistic(row) for row in rows],
    }


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


def _category_id(service: LISService, name: str) -> int:
    """The LegislationCategoryID behind a search category name, ignoring case."""
    categories = _call(service.client.get_legislation_categories)
    wanted = name.strip().lower()

    for category in categories:
        if (category.Name or "").lower() == wanted:
            return category.LegislationCategoryID

    names = ", ".join(sorted(category.Name for category in categories if category.Name))
    raise ToolError(f"No search category is named {name!r}. The categories are: {names}.")


def _date(value: str, name: str) -> date:
    """A ``YYYY-MM-DD`` argument as a date, or a ToolError that names the argument."""
    try:
        return parse_date(value, name)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
