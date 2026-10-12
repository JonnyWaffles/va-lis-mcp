"""A stand in for ``LISClient`` that returns canned models and records calls."""

from __future__ import annotations

from va_lis_client.models import (
    AmendedLegislation,
    BillVersion,
    CalendarDetail,
    CalendarItem,
    ChamberStatistic,
    Committee,
    CommitteeMember,
    CommitteeStatistic,
    Communication,
    CommunicationListItem,
    ConferenceLegislation,
    Contact,
    DocketDetail,
    DocketListItem,
    Legislation,
    LegislationCategory,
    LegislationEvent,
    LegislationEventType,
    LegislationNumberEntry,
    LegislationSearchResult,
    LegislationStatus,
    LegislationSummary,
    LegislationSummaryItem,
    LegislationTextDetail,
    LegislationTextItem,
    Member,
    MemberLegislation,
    MemberVoteResult,
    MinutesBook,
    MinutesBookListItem,
    Partner,
    Patron,
    Personnel,
    Schedule,
    Session,
    ShallowVote,
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
        self.session_details: dict[int, Session] = {}
        self.search_results: list[LegislationSearchResult] = []
        self.most_viewed: list[LegislationSearchResult] = []
        self.categories: list[LegislationCategory] = []
        self.legislative_numbers: list[LegislationNumberEntry] = []
        # Keyed by LegislationID.
        self.bill_versions: dict[int, list[BillVersion]] = {}
        self.amended: list[AmendedLegislation] = []
        self.conference: list[ConferenceLegislation] = []
        # Keyed by the chamber that voted.
        self.shallow_votes: dict[str, list[ShallowVote]] = {}
        self.minutes_books: list[MinutesBookListItem] = []
        self.minutes_book_details: dict[int, MinutesBook] = {}
        self.communications: list[CommunicationListItem] = []
        self.communication_details: dict[int, Communication] = {}
        # Keyed by CommitteeID, which LIS calls the AffiliationID.
        self.personnel: dict[int, list[Personnel]] = {}
        # Keyed by MemberID.
        self.contacts: dict[int, list[Contact]] = {}
        self.chamber_statistics: list[ChamberStatistic] = []
        self.committee_statistics: dict[str, list[CommitteeStatistic]] = {}

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

    def get_session_by_code(self, session_code):
        self._record("get_session_by_code", session_code=session_code)
        return self.session_details.get(int(session_code))

    def search_legislation(self, **criteria):
        self._record("search_legislation", **criteria)
        return list(self.search_results)

    def get_most_frequent_legislation(self, session_id):
        self._record("get_most_frequent_legislation", session_id=session_id)
        return list(self.most_viewed)

    def get_legislation_categories(self):
        self._record("get_legislation_categories")
        return list(self.categories)

    def get_legislative_numbers(self, session_code=None, session_id=None):
        self._record("get_legislative_numbers", session_code=session_code)
        return list(self.legislative_numbers)

    def get_bill_versions_by_id(self, legislation_id=None, *, legislation_ids=None):
        self._record(
            "get_bill_versions_by_id",
            legislation_id=legislation_id,
            legislation_ids=legislation_ids,
        )
        return list(self.bill_versions.get(legislation_id, []))

    def get_amended_legislation(self, *, session_code=None, session_id=None, chamber_code=None):
        self._record(
            "get_amended_legislation",
            session_code=session_code,
            chamber_code=chamber_code,
        )
        return list(self.amended)

    def get_conference_legislation(self, session_id=None, *, chamber_code=None, member_id=None):
        self._record(
            "get_conference_legislation",
            session_id=session_id,
            chamber_code=chamber_code,
            member_id=member_id,
        )
        return list(self.conference)

    def get_shallow_votes(
        self,
        chamber_code,
        *,
        session_code=None,
        session_id=None,
        legislation_id=None,
        member_id=None,
        start_date=None,
        end_date=None,
        vote_classification_id=None,
    ):
        self._record(
            "get_shallow_votes",
            chamber_code=chamber_code,
            session_code=session_code,
            legislation_id=legislation_id,
            start_date=start_date,
            end_date=end_date,
        )
        return list(self.shallow_votes.get(chamber_code, []))

    def get_minutes_books(
        self,
        *,
        session_code=None,
        session_id=None,
        committee_id=None,
        chamber_code=None,
        start_date=None,
        end_date=None,
    ):
        self._record(
            "get_minutes_books",
            session_code=session_code,
            committee_id=committee_id,
            chamber_code=chamber_code,
            start_date=start_date,
            end_date=end_date,
        )
        rows = self.minutes_books
        if committee_id is not None:
            rows = [b for b in rows if b.CommitteeID == committee_id]

        if chamber_code is not None:
            rows = [b for b in rows if b.ChamberCode == chamber_code]

        return list(rows)

    def get_minutes_book(self, minutes_book_id=None, calendar_id=None):
        self._record("get_minutes_book", minutes_book_id=minutes_book_id)
        return self.minutes_book_details.get(minutes_book_id)

    def get_communications(self, *, session_code=None, session_id=None, chamber_code=None):
        self._record("get_communications", session_code=session_code, chamber_code=chamber_code)
        return [
            c for c in self.communications if chamber_code is None or c.ChamberCode == chamber_code
        ]

    def get_communication(self, communication_id=None, **_):
        self._record("get_communication", communication_id=communication_id)
        return self.communication_details.get(communication_id)

    def get_personnel(
        self, *, staff_role_type_id=None, affiliation_id=None, effective_begin_date=None
    ):
        self._record(
            "get_personnel",
            affiliation_id=affiliation_id,
            staff_role_type_id=staff_role_type_id,
        )
        return list(self.personnel.get(affiliation_id, []))

    def get_contacts(self, owner_id=None, owner_type_id=None):
        self._record("get_contacts", owner_id=owner_id, owner_type_id=owner_type_id)
        return list(self.contacts.get(owner_id, []))

    def get_chamber_statistics(self, session_code=None, session_id=None):
        self._record("get_chamber_statistics", session_code=session_code)
        return list(self.chamber_statistics)

    def get_committee_statistics(self, chamber_code, *, session_code=None, session_id=None):
        self._record(
            "get_committee_statistics",
            chamber_code=chamber_code,
            session_code=session_code,
        )
        return list(self.committee_statistics.get(chamber_code, []))


def call_names(client: FakeClient) -> list[str]:
    return [name for name, _ in client.calls]
