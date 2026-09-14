"""A stand in for ``LISClient`` that returns canned models and records calls."""

from __future__ import annotations

from va_lis_client.models import (
    CalendarDetail,
    CalendarItem,
    Committee,
    CommitteeMember,
    DocketDetail,
    DocketListItem,
    Legislation,
    LegislationEvent,
    LegislationEventType,
    LegislationStatus,
    LegislationSummary,
    LegislationSummaryItem,
    LegislationTextDetail,
    LegislationTextItem,
    Member,
    MemberLegislation,
    MemberVoteResult,
    Partner,
    Patron,
    Schedule,
    Session,
    Vote,
)

from tests.factories import SESSION_20261


class FakeClient:
    """Stands in for LISClient; returns canned model instances and records calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.default_session: Session | None = None
        self.sessions: list[Session] = [SESSION_20261]
        self.bills: list[LegislationSummaryItem] = []
        self.bill: Legislation | None = None
        self.summaries: list[LegislationSummary] = []
        self.texts: list[LegislationTextItem] = []
        self.text_details: list[LegislationTextDetail] = []
        self.events: list[LegislationEvent] = []
        self.event_types: list[LegislationEventType] = []
        self.statuses: list[LegislationStatus] = []
        self.partner: Partner | None = None
        self.votes: dict[int, Vote] = {}
        self.members: list[Member] = []
        self.member_votes: list[MemberVoteResult] = []
        # Keyed by patron type; None holds the unfiltered rows.
        self.member_legislation: dict[int | None, list[MemberLegislation]] = {}
        self.bill_patrons: list[Patron] = []
        self.committees: list[Committee] = []
        self.committee_details: dict[int, Committee] = {}
        self.committee_members: dict[int, list[CommitteeMember]] = {}
        self.schedules: list[Schedule] = []
        self.calendars: list[CalendarItem] = []
        self.calendar_details: dict[int, CalendarDetail] = {}
        self.dockets: dict[int, list[DocketListItem]] = {}
        self.docket_details: dict[int, DocketDetail] = {}

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

    def get_vote(self, vote_id):
        self._record("get_vote", vote_id=vote_id)
        return self.votes.get(vote_id)

    def get_members(self, session_code, chamber_code=None):
        self._record("get_members", session_code=session_code)
        return list(self.members)

    def get_member_votes(self, member_id, session_code):
        self._record("get_member_votes", member_id=member_id, session_code=session_code)
        return list(self.member_votes)

    def get_member_legislation(self, member_id, session_id, patron_type_id=None):
        self._record(
            "get_member_legislation",
            member_id=member_id,
            session_id=session_id,
            patron_type_id=patron_type_id,
        )
        return list(self.member_legislation.get(patron_type_id, []))

    def get_bill_patrons(self, legislation_id):
        self._record("get_bill_patrons", legislation_id=legislation_id)
        return list(self.bill_patrons)

    def get_committees(
        self,
        chamber_code=None,
        *,
        session_code=None,
        parent_committee_id=None,
        include_subcommittees=False,
    ):
        self._record(
            "get_committees",
            chamber_code=chamber_code,
            include_subcommittees=include_subcommittees,
        )
        rows = [c for c in self.committees if chamber_code is None or c.ChamberCode == chamber_code]
        if not include_subcommittees:
            rows = [c for c in rows if c.ParentCommitteeID is None]

        return rows

    def get_committee(self, committee_id, session_id):
        self._record("get_committee", committee_id=committee_id, session_id=session_id)
        return self.committee_details.get(committee_id)

    def get_committee_members(self, committee_id, session_code):
        self._record(
            "get_committee_members",
            committee_id=committee_id,
            session_code=session_code,
        )
        return list(self.committee_members.get(committee_id, []))

    def get_schedules(
        self,
        start_date=None,
        end_date=None,
        *,
        owner_id=None,
        schedule_type_id=None,
        vote_room_id=None,
        schedule_ids=None,
    ):
        self._record(
            "get_schedules",
            start_date=start_date,
            end_date=end_date,
            owner_id=owner_id,
            schedule_type_id=schedule_type_id,
        )
        rows = self.schedules
        if owner_id is not None:
            rows = [s for s in rows if s.OwnerID == owner_id]
        if schedule_type_id is not None:
            rows = [s for s in rows if s.ScheduleTypeID == schedule_type_id]

        return list(rows)

    def get_calendars(self, chamber_code, session_code):
        self._record("get_calendars", chamber_code=chamber_code, session_code=session_code)
        return [c for c in self.calendars if c.ChamberCode == chamber_code]

    def get_calendar(self, calendar_id):
        self._record("get_calendar", calendar_id=calendar_id)
        return self.calendar_details.get(calendar_id)

    def get_dockets(self, committee_id, session_code, chamber_code="S"):
        self._record("get_dockets", committee_id=committee_id, session_code=session_code)
        return list(self.dockets.get(committee_id, []))

    def get_docket(self, docket_id):
        self._record("get_docket", docket_id=docket_id)
        return self.docket_details.get(docket_id)


def call_names(client: FakeClient) -> list[str]:
    return [name for name, _ in client.calls]
