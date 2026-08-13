"""MCP server exposing the Virginia Legislative Information System (LIS) API.

Wraps :mod:`va_lis_client` with eight read-only tools shaped for LLM
consumption: human identifiers in (bill number + session code), compact JSON
out, HTML stripped from bill text and summaries.

Run over stdio (the transport MCP hosts launch)::

    va-lis-mcp

or serve over HTTP with the ASGI app in :mod:`va_lis_mcp.asgi`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

from mcp.server import MCPServer
from va_lis_client import LISClient, LISClientError
from va_lis_client.models import (
    Legislation,
    LegislationEventType,
    LegislationStatus,
    LegislationSummaryItem,
    LegislationTextItem,
    Patron,
    Session,
)

_REGISTRATION_URL = "https://lis.virginia.gov/apiregistration"
_TEXT_CHAR_LIMIT = 15_000
_MAX_LIST_LIMIT = 200
_BILL_LIST_TTL_SECONDS = 15 * 60
_BILL_NUMBER_RE = re.compile(r"([A-Z]+)(\d+)")

_INSTRUCTIONS = """\
Read-only tools for the Virginia General Assembly's Legislative Information
System (LIS): sessions, bills, summaries, full bill text, and action history.

Identifiers:
- session_code encodes year + sequence: 20261 = 2026 Regular Session,
  20262 = 2026 Special Session I. Use list_sessions to discover codes.
- Bill numbers are unpadded: HB1, not HB0001. Tools normalize input
  (hb0001 -> HB1) before querying.

Session semantics:
- get_current_session returns the GA's *working* session. During the interim
  that is the upcoming session, not the one that just ended. For retrospective
  work on a finished session, pass its explicit session_code.
- Even-year bills may be carried over into the following odd-year session
  (never the reverse, at most once). A carried-over bill keeps its numeric
  LegislationID in both sessions, so a bill is identified by
  (session_code, bill_number); the numeric ID alone is ambiguous across the
  pair. get_bill's "sessions" list is the carry-over lineage record.

All data comes from the live LIS REST API and requires the LIS_API_KEY
environment variable (register at https://lis.virginia.gov/apiregistration).
Use check_api_key to verify the key: LIS heartbeat endpoints accept any key,
so a working heartbeat proves nothing.
"""

# In-process caches: bill lists are thousands of rows and three tools need
# them for id resolution; the reference lists are static vocabularies.
_bill_list_cache: dict[int, tuple[float, list[LegislationSummaryItem]]] = {}
_event_type_index: dict[str, LegislationEventType] | None = None
_status_index: tuple[dict[str, LegislationStatus], dict[int, LegislationStatus]] | None = None

mcp = MCPServer("va-lis", instructions=_INSTRUCTIONS)


@mcp.tool()
def get_current_session() -> dict[str, Any]:
    """The General Assembly's current working session.

    During the interim this is the upcoming session, not the one that just
    ended; continued bills and new prefiles accumulate there. For
    retrospective work on a finished session, pass that session's code to the
    other tools instead of relying on this default.
    """
    session = _call(_get_client().get_default_session)

    if session is None:
        raise RuntimeError("LIS did not report a default session.")

    payload = _compact_session(session)
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
    sessions = _call(_get_client().get_sessions, year)

    return {"year": year, "sessions": [_compact_session(s) for s in sessions]}


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
    bills = _session_bills(session_code)

    if query:
        needle = query.strip().lower()
        bills = [
            bill
            for bill in bills
            if needle in bill.LegislationNumber.lower()
            or needle in (bill.Description or "").lower()
            or needle in (bill.LegislationTitle or "").lower()
        ]

    limit = max(1, min(limit, _MAX_LIST_LIMIT))
    offset = max(0, offset)
    page = bills[offset : offset + limit]

    return {
        "session_code": session_code,
        "total_matching": len(bills),
        "count": len(page),
        "offset": offset,
        "bills": [_compact_bill_row(bill) for bill in page],
    }


@mcp.tool()
def get_bill(bill_number: str, session_code: int) -> dict[str, Any]:
    """Full detail for one bill: title, status, dates, patrons, session lineage.

    ``patrons`` groups member names by role (Chief Patron, Co-Patron, ...).
    ``sessions`` lists every session the bill appears in; two entries mean it
    was carried over from the even-year session into the odd-year session.
    """
    item = _resolve_bill(bill_number, session_code)
    bill = _call(_get_client().get_bill, item.LegislationID)

    if bill is None:
        raise ValueError(
            f"Bill {item.LegislationNumber} (LegislationID {item.LegislationID}) "
            "has no detail record in LIS."
        )

    return _drop_empty(
        {
            "legislation_id": bill.LegislationID,
            "bill_number": bill.LegislationNumber,
            "description": bill.Description,
            "title": bill.LegislationTitle,
            "chamber": bill.ChamberCode,
            "type": bill.LegislationTypeCode,
            "status": _status_display(bill, item),
            "introduction_date": _iso(bill.IntroductionDate),
            "offered_date": _iso(bill.OfferedDate),
            "sessions": [
                {"session_code": int(s.SessionCode), "is_prefile": s.IsPrefile}
                for s in bill.Sessions
            ],
            "patrons": _condense_patrons(bill.Patrons),
        }
    )


@mcp.tool()
def get_bill_summaries(bill_number: str, session_code: int) -> dict[str, Any]:
    """Plain-text summaries of a bill (as introduced, as passed, ...).

    The entry with ``is_active`` true is the current summary. An empty list
    means LIS has published no summary for that bill and session.
    """
    number = _normalize_bill_number(bill_number)
    summaries = _call(
        _get_client().get_bill_summaries,
        legislation_number=number,
        session_code=session_code,
    )

    return {
        "bill_number": number,
        "session_code": session_code,
        "summaries": [
            _drop_empty(
                {
                    "version": summary.SummaryVersion,
                    "date": _iso(summary.SummaryDate),
                    "is_active": summary.IsActive,
                    "summary": _strip_html(summary.Summary) if summary.Summary else None,
                }
            )
            for summary in summaries
        ],
    }


@mcp.tool()
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
    number = _normalize_bill_number(bill_number)
    texts = _call(
        _get_client().get_bill_texts,
        legislation_number=number,
        session_code=session_code,
    )

    if not texts:
        raise ValueError(
            f"No text versions found for {number} in session {session_code}. "
            "Check the bill number and session code."
        )

    versions = [
        _drop_empty(
            {
                "document_code": t.DocumentCode,
                "version": t.Version or t.Description,
                "date": _iso(t.DraftDate),
            }
        )
        for t in texts
    ]
    selected = _pick_text_version(texts, document_code)

    details = _call(
        _get_client().get_bill_text_detail,
        legislation_id=selected.LegislationID,
        session_code=session_code,
    )
    detail = next((d for d in details if d.LegislationTextID == selected.LegislationTextID), None)

    if detail is None or not detail.DraftText:
        raise ValueError(
            f"LIS returned no text body for {selected.DocumentCode} in session {session_code}."
        )

    text = _strip_html(detail.DraftText)
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
    and ``vote_tally``. ``status`` is the bill's status after each event.
    """
    item = _resolve_bill(bill_number, session_code)
    events = _call(_get_client().get_bill_events, item.LegislationID)

    event_types = _event_types_by_code()
    statuses = _statuses_by_name()

    entries = []
    for event in events:
        event_type = event_types.get(event.EventCode) if event.EventCode else None
        status = statuses.get(event.Status) if event.Status else None
        entries.append(
            _drop_empty(
                {
                    "date": _iso(event.EventDate),
                    "description": event.Description
                    or (event_type.LegislationDescription if event_type else None),
                    "actor": event.ActorType,
                    "committee": event.CommitteeName,
                    "vote_tally": event.VoteTally,
                    "status": status.DisplayName if status else event.Status,
                    # Only flag passage events; omitting the key on the rest
                    # keeps the timeline compact.
                    "is_passage": (event_type.IsPassage or None) if event_type else None,
                }
            )
        )

    return {
        "bill_number": item.LegislationNumber,
        "session_code": session_code,
        "legislation_id": item.LegislationID,
        "count": len(entries),
        "events": entries,
    }


@mcp.tool()
def check_api_key() -> dict[str, Any]:
    """Validate the configured LIS API key.

    LIS heartbeat endpoints accept any key, so this is the only real check:
    it asks the PartnerAuthentication service whether the key is registered
    and active.
    """
    partner = _call(_get_client().check_api_key)

    if partner is None:
        return {
            "valid": False,
            "message": (
                "The configured LIS_API_KEY is not a registered partner key. "
                f"Register at {_REGISTRATION_URL}."
            ),
        }

    return _drop_empty(
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


def _get_client() -> LISClient:
    """A fresh client per call; the HTTP session underneath is shared module state.

    Never instantiated at import time: MCP hosts launch servers at app boot,
    and a missing key must fail the tool call, not the process.
    """
    try:
        return LISClient()
    except LISClientError as exc:
        raise RuntimeError(
            f"LIS_API_KEY is not set. Register for a free key at {_REGISTRATION_URL} "
            "and set the LIS_API_KEY environment variable for this server."
        ) from exc


def _call(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Invoke a client method, rewrapping LISClientError so messages stay readable."""
    try:
        return fn(*args, **kwargs)
    except LISClientError as exc:
        raise RuntimeError(f"LIS API error: {exc}") from exc


def _compact_session(session: Session) -> dict[str, Any]:
    return {
        "session_code": int(session.SessionCode),
        "session_id": session.SessionID,
        "display_name": session.DisplayName,
        "year": session.SessionYear,
        "type": session.SessionType,
        "is_active": session.IsActive,
        "is_default": session.IsDefault,
    }


def _session_bills(session_code: int) -> list[LegislationSummaryItem]:
    """Bill list for a session, cached for a short TTL."""
    now = time.monotonic()
    cached = _bill_list_cache.get(session_code)
    if cached is not None and now - cached[0] < _BILL_LIST_TTL_SECONDS:
        return cached[1]

    bills = _call(_get_client().get_session_bills, session_code=session_code)
    _bill_list_cache[session_code] = (now, bills)
    return bills


def _compact_bill_row(item: LegislationSummaryItem) -> dict[str, Any]:
    chief = next(
        (p.MemberDisplayName for p in item.Patrons if p.PatronTypeID == 1),
        None,
    )

    return _drop_empty(
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


def _resolve_bill(bill_number: str, session_code: int) -> LegislationSummaryItem:
    """Resolve a human bill number to its session bill record (and LegislationID)."""
    number = _normalize_bill_number(bill_number)

    for item in _session_bills(session_code):
        if item.LegislationNumber.upper() == number:
            return item

    raise ValueError(
        f"Bill {number} not found in session {session_code}. Check the bill number "
        "and session code (use list_sessions; 20261 = 2026 Regular Session)."
    )


def _normalize_bill_number(bill_number: str) -> str:
    """Uppercase and unpad: 'hb0001' -> 'HB1' (padded numbers return nothing from LIS)."""
    candidate = bill_number.strip().upper().replace(" ", "")

    match = _BILL_NUMBER_RE.fullmatch(candidate)
    if match is None:
        raise ValueError(
            f"{bill_number!r} does not look like a bill number (expected a chamber "
            "prefix plus digits, e.g. HB1, SB234, HJ5)."
        )

    prefix, digits = match.groups()
    return f"{prefix}{int(digits)}"


def _status_display(bill: Legislation, item: LegislationSummaryItem) -> str | None:
    """Best available status label; the detail endpoint's status text is often null."""
    if bill.LegislationStatus:
        return bill.LegislationStatus

    if bill.LegislationStatusID is not None:
        status = _statuses_by_id().get(bill.LegislationStatusID)
        if status is not None:
            return status.DisplayName

    return item.LegislationStatus


def _condense_patrons(patrons: list[Patron]) -> dict[str, list[str]]:
    """Group patron names by role, e.g. {"Chief Patron": [...], "Co-Patron": [...]}."""
    grouped: dict[str, list[str]] = {}

    for patron in patrons:
        name = patron.MemberDisplayName or patron.PatronDisplayName or patron.MemberNumber
        grouped.setdefault(patron.Name, []).append(name)

    return grouped


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _drop_empty(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove None and empty-container values; False and 0 are kept."""
    return {k: v for k, v in payload.items() if v is not None and v != [] and v != {}}


def _pick_text_version(
    texts: list[LegislationTextItem],
    document_code: str | None,
) -> LegislationTextItem:
    if document_code is None:
        # LegislationTextID is a monotonically increasing surrogate key, so the
        # highest ID is the most recent version (DraftDate is not always set).
        return max(texts, key=lambda t: t.LegislationTextID)

    wanted = document_code.strip().upper()
    for text in texts:
        if (text.DocumentCode or "").upper() == wanted:
            return text

    available = ", ".join(t.DocumentCode or "?" for t in texts)
    raise ValueError(f"No text version {wanted!r} for this bill; available versions: {available}.")


def _strip_html(markup: str) -> str:
    """Flatten HTML to readable plain text using only the stdlib parser."""
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()

    text = extractor.text()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class _TextExtractor(HTMLParser):
    """Collects text content, dropping tags and <script>/<style> bodies."""

    _SKIP_CONTENT = {"script", "style"}
    _BLOCK = {"p", "div", "br", "li", "tr", "table", "section", "h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_CONTENT:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_CONTENT and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def _event_types_by_code() -> dict[str, LegislationEventType]:
    """The 3,912-row event type reference, fetched once per process.

    Reference rows come back with LegislationEventTypeID null, so EventCode is
    the join key onto events.
    """
    global _event_type_index

    if _event_type_index is None:
        types = _call(_get_client().get_event_types)
        _event_type_index = {t.EventCode: t for t in types if t.EventCode}

    return _event_type_index


def _statuses_by_name() -> dict[str, LegislationStatus]:
    """Status reference keyed by internal Name.

    Events carry the status name and a null LegislationStatusID, so the name
    is the per-event join key into the 52-status vocabulary.
    """
    return _status_indexes()[0]


def _statuses_by_id() -> dict[int, LegislationStatus]:
    return _status_indexes()[1]


def _status_indexes() -> tuple[dict[str, LegislationStatus], dict[int, LegislationStatus]]:
    global _status_index

    if _status_index is None:
        statuses = _call(_get_client().get_legislation_statuses)
        _status_index = (
            {s.Name: s for s in statuses},
            {s.LegislationStatusID: s for s in statuses},
        )

    return _status_index
