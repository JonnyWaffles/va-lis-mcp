"""Unit tests for the tool functions with the LIS client faked out.

Fakes are built from the real ``va_lis_client`` model classes so field names
stay honest against the client package.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from va_lis_client import LISClientError
from va_lis_client.models import (
    Legislation,
    LegislationEvent,
    LegislationEventType,
    LegislationSession,
    LegislationStatus,
    LegislationSummary,
    LegislationSummaryItem,
    LegislationTextDetail,
    LegislationTextItem,
    Partner,
    Patron,
    Session,
)

from va_lis_mcp import server


def _patron(name: str, role_id: int = 1, role: str = "Chief Patron") -> Patron:
    return Patron(
        LegislationID=98525,
        ChamberCode="H",
        MemberID=419,
        MemberNumber="H0173",
        PatronTypeID=role_id,
        Name=role,
        MemberDisplayName=name,
    )


def _bill_row(
    legislation_id: int,
    number: str,
    description: str,
    chamber: str = "H",
) -> LegislationSummaryItem:
    return LegislationSummaryItem(
        LegislationID=legislation_id,
        LegislationNumber=number,
        Description=description,
        ChamberCode=chamber,
        LegislationTypeCode="B",
        LegislationStatus="In Committee",
        Patrons=[_patron("Jeion A. Ward")],
    )


def _text_item(text_id: int, document_code: str, version: str) -> LegislationTextItem:
    return LegislationTextItem(
        LegislationID=98525,
        LegislationNumber="HB1",
        Description=version,
        SessionID=59,
        LegislationTextID=text_id,
        DocumentCode=document_code,
        LegislationVersionID=1,
        Version=version,
    )


def _text_detail(text_id: int, document_code: str, body: str) -> LegislationTextDetail:
    return LegislationTextDetail(
        LegislationTextID=text_id,
        LegislationVersionID=3,
        LegislationVersion="Enrolled",
        LegislationID=98525,
        LegislationNumber="HB1",
        DocumentCode=document_code,
        DraftText=body,
    )


HB1 = _bill_row(98525, "HB1", "Minimum wage; increases incrementally")
HB2 = _bill_row(98526, "HB2", "Firearms; universal background checks")
SB10 = _bill_row(98600, "SB10", "Elections; same-day registration", chamber="S")


class FakeClient:
    """Stands in for LISClient; returns canned model instances and records calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.default_session: Session | None = None
        self.sessions: list[Session] = []
        self.bills: list[LegislationSummaryItem] = []
        self.bill: Legislation | None = None
        self.summaries: list[LegislationSummary] = []
        self.texts: list[LegislationTextItem] = []
        self.text_details: list[LegislationTextDetail] = []
        self.events: list[LegislationEvent] = []
        self.event_types: list[LegislationEventType] = []
        self.statuses: list[LegislationStatus] = []
        self.partner: Partner | None = None

    def _record(self, name: str, **kwargs) -> None:
        self.calls.append((name, kwargs))

    def get_default_session(self):
        self._record("get_default_session")
        return self.default_session

    def get_sessions(self, year=""):
        self._record("get_sessions", year=year)
        return self.sessions

    def get_session_bills(self, session_code=None, session_id=None):
        self._record("get_session_bills", session_code=session_code)
        return self.bills

    def get_bill(self, legislation_id):
        self._record("get_bill", legislation_id=legislation_id)
        return self.bill

    def get_bill_summaries(self, *, legislation_number=None, session_code=None, **_):
        self._record(
            "get_bill_summaries",
            legislation_number=legislation_number,
            session_code=session_code,
        )
        return self.summaries

    def get_bill_texts(self, *, legislation_number=None, session_code=None, **_):
        self._record(
            "get_bill_texts",
            legislation_number=legislation_number,
            session_code=session_code,
        )
        return self.texts

    def get_bill_text_detail(self, *, legislation_id, session_code):
        self._record(
            "get_bill_text_detail",
            legislation_id=legislation_id,
            session_code=session_code,
        )
        return self.text_details

    def get_bill_events(self, legislation_id):
        self._record("get_bill_events", legislation_id=legislation_id)
        return self.events

    def get_event_types(self):
        self._record("get_event_types")
        return self.event_types

    def get_legislation_statuses(self):
        self._record("get_legislation_statuses")
        return self.statuses

    def check_api_key(self):
        self._record("check_api_key")
        return self.partner


@pytest.fixture(autouse=True)
def reset_caches(monkeypatch):
    server._bill_list_cache.clear()
    monkeypatch.setattr(server, "_event_type_index", None)
    monkeypatch.setattr(server, "_status_index", None)


@pytest.fixture
def fake_client(monkeypatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setattr(server, "LISClient", lambda: client)
    return client


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hb0001", "HB1"),
        ("HB1", "HB1"),
        (" sb 10 ", "SB10"),
        ("hj0005", "HJ5"),
        ("sb0230", "SB230"),
    ],
)
def test_normalize_bill_number(raw, expected):
    assert server._normalize_bill_number(raw) == expected


def test_normalize_bill_number_rejects_garbage():
    with pytest.raises(ValueError, match="does not look like a bill number"):
        server._normalize_bill_number("1HB")


def test_get_current_session_includes_note(fake_client):
    fake_client.default_session = Session(
        SessionID=61,
        SessionCode="20271",
        DisplayName="Regular Session",
        SessionYear=2027,
        SessionTypeID=1,
        SessionType="Regular",
        IsDefault=True,
        IsActive=True,
    )

    result = server.get_current_session()

    assert result["session_code"] == 20271
    assert "working session" in result["note"]


def test_list_bills_filters_case_insensitively(fake_client):
    fake_client.bills = [HB1, HB2, SB10]

    result = server.list_bills(20261, query="MINIMUM WAGE")

    assert result["total_matching"] == 1
    assert result["count"] == 1
    assert result["bills"][0]["bill_number"] == "HB1"
    assert result["bills"][0]["chief_patron"] == "Jeion A. Ward"


def test_list_bills_filters_on_bill_number(fake_client):
    fake_client.bills = [HB1, HB2, SB10]

    result = server.list_bills(20261, query="sb1")

    assert [b["bill_number"] for b in result["bills"]] == ["SB10"]


def test_list_bills_paginates(fake_client):
    fake_client.bills = [HB1, HB2, SB10]

    result = server.list_bills(20261, limit=1, offset=1)

    assert result["total_matching"] == 3
    assert result["count"] == 1
    assert result["offset"] == 1
    assert result["bills"][0]["bill_number"] == "HB2"


def test_session_bill_list_is_cached(fake_client):
    fake_client.bills = [HB1]

    server.list_bills(20261)
    server.list_bills(20261)

    names = [name for name, _ in fake_client.calls]
    assert names.count("get_session_bills") == 1


def test_get_bill_not_found_names_bill_and_session(fake_client):
    fake_client.bills = [HB1]

    with pytest.raises(ValueError) as excinfo:
        server.get_bill("HB999", 20261)

    message = str(excinfo.value)
    assert "HB999" in message
    assert "20261" in message


def test_get_bill_condenses_patrons_and_resolves_status(fake_client):
    fake_client.bills = [HB1]
    fake_client.bill = Legislation(
        LegislationID=98525,
        LegislationNumber="HB1",
        Description="Minimum wage; increases incrementally",
        LegislationTitle="An Act to amend and reenact ...",
        ChamberCode="H",
        LegislationTypeCode="B",
        LegislationStatusID=38,
        IntroductionDate=datetime(2025, 11, 17, 7, 28),
        Patrons=[
            _patron("Jeion A. Ward"),
            _patron("Alex Q. Askew", role_id=4, role="Co-Patron"),
            _patron("Nadarius E. Clark", role_id=4, role="Co-Patron"),
        ],
        Sessions=[LegislationSession(SessionID=59, SessionCode="20261", IsPrefile=True)],
    )
    fake_client.statuses = [
        LegislationStatus(
            LegislationStatusID=38,
            Name="Awaiting Governor's Action",
            DisplayName="Awaiting Governor's Action",
        )
    ]

    result = server.get_bill("hb0001", 20261)

    assert result["patrons"] == {
        "Chief Patron": ["Jeion A. Ward"],
        "Co-Patron": ["Alex Q. Askew", "Nadarius E. Clark"],
    }
    assert result["sessions"] == [{"session_code": 20261, "is_prefile": True}]
    assert result["status"] == "Awaiting Governor's Action"
    assert result["introduction_date"] == "2025-11-17T07:28:00"
    assert "offered_date" not in result


def test_get_bill_summaries_strips_html_and_normalizes_number(fake_client):
    fake_client.summaries = [
        LegislationSummary(
            LegislationID=98525,
            SessionID=59,
            LegislationSummaryID=134212,
            SummaryVersion="SUMMARY AS PASSED",
            LegislationNumber="HB1",
            Summary='<p class="sumtext"><b>Minimum wage.</b> Increases the minimum wage.</p>',
            IsActive=True,
        )
    ]

    result = server.get_bill_summaries("HB0001", 20261)

    assert result["bill_number"] == "HB1"
    summary = result["summaries"][0]
    assert summary["summary"] == "Minimum wage. Increases the minimum wage."
    assert summary["is_active"] is True
    assert ("get_bill_summaries", {"legislation_number": "HB1", "session_code": 20261}) in (
        fake_client.calls
    )


def test_get_bill_text_strips_html_and_truncates(fake_client):
    long_body = "<p>" + "x" * (server._TEXT_CHAR_LIMIT + 500) + "</p>"
    fake_client.texts = [
        _text_item(257719, "HB1", "Introduced"),
        _text_item(266240, "HB1ER", "Enrolled"),
    ]
    fake_client.text_details = [
        _text_detail(257719, "HB1", "<p>short intro text</p>"),
        _text_detail(266240, "HB1ER", long_body),
    ]

    result = server.get_bill_text("hb0001", 20261)

    assert result["document_code"] == "HB1ER"  # highest text id wins by default
    assert result["truncated"] is True
    assert len(result["text"]) == server._TEXT_CHAR_LIMIT
    assert "<" not in result["text"]
    codes = [v["document_code"] for v in result["available_versions"]]
    assert codes == ["HB1", "HB1ER"]


def test_get_bill_text_short_body_is_not_truncated(fake_client):
    fake_client.texts = [_text_item(257719, "HB1", "Introduced")]
    fake_client.text_details = [
        _text_detail(257719, "HB1", "<p>Be it enacted, <em class='new'>now</em>.</p>")
    ]

    result = server.get_bill_text("HB1", 20261, document_code="hb1")

    assert result["truncated"] is False
    assert result["text"] == "Be it enacted, now."


def test_get_bill_text_unknown_version_lists_available(fake_client):
    fake_client.texts = [
        _text_item(257719, "HB1", "Introduced"),
        _text_item(266240, "HB1ER", "Enrolled"),
    ]

    with pytest.raises(ValueError) as excinfo:
        server.get_bill_text("HB1", 20261, document_code="HB1S1")

    message = str(excinfo.value)
    assert "HB1S1" in message
    assert "HB1ER" in message


def test_get_bill_history_joins_event_types_and_statuses(fake_client):
    fake_client.bills = [HB1]
    fake_client.events = [
        LegislationEvent(
            LegislationEventID=1,
            EventCode="H0201",
            EventDate=datetime(2026, 1, 20),
            Description="Reported from Commerce and Energy",
            Status="Reported Out-House",
            ActorType="Committee",
            CommitteeName="Commerce and Energy",
        ),
        LegislationEvent(
            LegislationEventID=2,
            EventCode="H5000",
            EventDate=datetime(2026, 2, 1),
            Status="Passed House",
            ActorType="House",
            VoteTally="(64-Y 34-N)",
        ),
    ]
    fake_client.event_types = [
        LegislationEventType(
            EventCode="H5000",
            LegislationDescription="Read third time and passed House",
            IsPassage=True,
        )
    ]
    fake_client.statuses = [
        LegislationStatus(LegislationStatusID=3, Name="Reported Out-House", DisplayName="In House"),
        LegislationStatus(LegislationStatusID=4, Name="Passed House", DisplayName="Passed House"),
    ]

    result = server.get_bill_history("HB1", 20261)

    assert result["legislation_id"] == 98525
    assert result["count"] == 2
    first, second = result["events"]
    assert first["status"] == "In House"  # joined on status Name, not the raw internal name
    assert first["committee"] == "Commerce and Energy"
    assert "is_passage" not in first
    assert second["is_passage"] is True  # joined on EventCode
    assert second["description"] == "Read third time and passed House"  # filled from event type
    assert second["vote_tally"] == "(64-Y 34-N)"


def test_reference_lists_are_fetched_once(fake_client):
    fake_client.bills = [HB1]
    fake_client.events = []

    server.get_bill_history("HB1", 20261)
    server.get_bill_history("HB1", 20261)

    names = [name for name, _ in fake_client.calls]
    assert names.count("get_event_types") == 1
    assert names.count("get_legislation_statuses") == 1


def test_check_api_key_valid(fake_client):
    fake_client.partner = Partner(
        PartnerID=1,
        IdentityID=2,
        OrganizationName="Example Agency",
        ContactName="Jane Smith",
        APIKey="00000000-0000-0000-0000-000000000000",
        IsActive=True,
        IsPublic=False,
    )

    result = server.check_api_key()

    assert result == {
        "valid": True,
        "organization": "Example Agency",
        "contact": "Jane Smith",
    }


def test_check_api_key_unregistered(fake_client):
    fake_client.partner = None

    result = server.check_api_key()

    assert result["valid"] is False
    assert "apiregistration" in result["message"]


def test_missing_api_key_error(monkeypatch):
    monkeypatch.delenv("LIS_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        server.check_api_key()

    message = str(excinfo.value)
    assert "LIS_API_KEY" in message
    assert "lis.virginia.gov/apiregistration" in message


def test_lis_client_error_is_wrapped(fake_client, monkeypatch):
    def boom(**kwargs):
        raise LISClientError("request GET /x failed after 8 retries")

    monkeypatch.setattr(fake_client, "get_session_bills", boom)

    with pytest.raises(RuntimeError, match="LIS API error"):
        server.list_bills(20261)
