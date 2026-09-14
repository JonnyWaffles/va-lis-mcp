"""Canned ``va_lis_client`` model instances for the tool tests.

Builders are named for the model they return. The module level constants
are the shared cast (one session, three bills, four members, four
committees) that every test category draws on.
"""

from __future__ import annotations

from datetime import datetime

from va_lis_client.models import (
    CalendarDetail,
    CalendarItem,
    Committee,
    CommitteeMember,
    DocketDetail,
    DocketListItem,
    LegislationEvent,
    LegislationSummaryItem,
    LegislationTextDetail,
    LegislationTextItem,
    Member,
    MemberLegislation,
    MemberVoteResult,
    Patron,
    Schedule,
    Session,
    Vote,
)


def make_session(session_id: int, code: str) -> Session:
    return Session(
        SessionID=session_id,
        SessionCode=code,
        DisplayName="Regular Session",
        SessionYear=int(code[:4]),
        SessionTypeID=1,
        SessionType="Regular",
        IsDefault=False,
        IsActive=True,
    )


def make_patron(
    name: str,
    role_id: int = 1,
    role: str = "Chief Patron",
    member_id: int = 419,
) -> Patron:
    return Patron(
        LegislationID=98525,
        ChamberCode="H",
        MemberID=member_id,
        MemberNumber="H0173",
        PatronTypeID=role_id,
        Name=role,
        MemberDisplayName=name,
    )


def make_bill_row(
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
        Patrons=[make_patron("Jeion A. Ward")],
    )


def make_text_item(text_id: int, document_code: str, version: str) -> LegislationTextItem:
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


def make_text_detail(text_id: int, document_code: str, body: str) -> LegislationTextDetail:
    return LegislationTextDetail(
        LegislationTextID=text_id,
        LegislationVersionID=3,
        LegislationVersion="Enrolled",
        LegislationID=98525,
        LegislationNumber="HB1",
        DocumentCode=document_code,
        DraftText=body,
    )


def make_event(
    event_id: int,
    code: str,
    *,
    when: datetime | None = None,
    description: str | None = None,
    status: str | None = None,
    vote_id: int | None = None,
    tally: str | None = None,
    actor: str = "House",
    committee: str | None = None,
) -> LegislationEvent:
    return LegislationEvent(
        LegislationEventID=event_id,
        EventCode=code,
        EventDate=when,
        Description=description,
        LegislationID=98525,
        LegislationNumber="HB1",
        ChamberCode=code[0],
        Status=status,
        ActorType=actor,
        CommitteeName=committee,
        VoteID=vote_id,
        VoteTally=tally,
    )


def make_member(
    member_id: int,
    number: str,
    name: str,
    *,
    chamber: str = "H",
    party: str = "D",
    district: str = "1st",
    list_name: str | None = None,
    status: str = "Active",
    ended: datetime | None = None,
    reason: str | None = None,
    email: str | None = None,
) -> Member:
    return Member(
        MemberID=member_id,
        MemberNumber=number,
        MemberDisplayName=name,
        ListDisplayName=list_name,
        PatronDisplayName=name.split()[-1],
        ChamberCode=chamber,
        PartyCode=party,
        DistrictName=district,
        MemberStatus=status,
        ServiceEndDate=ended,
        ServiceEndReason=reason,
        GABEmailAddress=email,
    )


def make_vote(
    vote_id: int,
    responses: dict[int, str],
    *,
    when: datetime | None = None,
    chamber: str = "H",
    vote_type: str = "Floor",
    committee: str | None = None,
    tally: str | None = "(2-Y 1-N 0-A)",
    pass_fail: str | None = "P",
    is_voice: bool = False,
    is_block: bool = False,
    bills: tuple[str, ...] = ("HB1",),
    action: str | None = None,
    statements: list[tuple[int, str]] = (),
) -> Vote:
    return Vote(
        VoteID=vote_id,
        VoteDate=when,
        ChamberCode=chamber,
        VoteTypeID=3 if vote_type == "Floor" else 1,
        VoteType=vote_type,
        CommitteeID=14 if committee else None,
        CommitteeName=committee,
        VoteTally=tally,
        PassFail=pass_fail,
        IsVoice=is_voice,
        IsBlock=is_block,
        # A ballot id looks nothing like a person id, as in the real payload.
        VoteMember=[
            {"VoteMemberID": 10_000_000 + member_id, "MemberID": member_id, "ResponseCode": code}
            for member_id, code in responses.items()
        ],
        VoteLegislation=[
            {
                "VoteLegislationID": n,
                "LegislationNumber": number,
                "LegislationActionDescription": action if n == 1 else None,
            }
            for n, number in enumerate(bills, start=1)
        ],
        # VoteMemberID on a statement really holds a MemberID.
        VoteStatements=[
            {"VoteStatementID": n, "VoteMemberID": member_id, "VoteStatement": text}
            for n, (member_id, text) in enumerate(statements, start=1)
        ],
    )


def make_member_vote(
    vote_id: int,
    bill: str | None,
    response: str = "Y",
    *,
    when: datetime | None = None,
    committee: str | None = None,
    classification: str | None = "Legislation",
    description: str | None = None,
    action: str | None = None,
    pass_fail: str | None = "P",
) -> MemberVoteResult:
    return MemberVoteResult(
        VoteID=vote_id,
        VoteDate=when,
        ChamberCode="H",
        VoteTypeID=1 if committee else 3,
        VoteType="Committee" if committee else "Floor",
        CommitteeID=14 if committee else None,
        CommitteeName=committee,
        LegislationID=98525 if bill else None,
        LegislationNumber=bill,
        LegislationDescription=description,
        ResponseCode=response,
        PassFail=pass_fail,
        VoteDescription=action,
        ClassificationName=classification,
    )


def make_member_bill(
    legislation_id: int,
    number: str,
    description: str,
    *,
    version: str = "SUMMARY AS INTRODUCED",
    status: str = "In Committee",
) -> MemberLegislation:
    return MemberLegislation(
        LegislationID=legislation_id,
        LegislationNumber=number,
        Description=description,
        ChamberCode="H",
        LegislationTypeCode="B",
        LegislationStatus=status,
        SessionID=59,
        SummaryVersion=version,
    )


def make_committee(
    committee_id: int,
    number: str,
    name: str,
    *,
    chamber: str = "H",
    parent: int | None = None,
    abbreviation: str | None = None,
) -> Committee:
    return Committee(
        CommitteeID=committee_id,
        Name=name,
        CommitteeNumber=number,
        ChamberCode=chamber,
        ParentCommitteeID=parent,
        Abbreviation=abbreviation,
    )


def make_seat(
    committee_id: int,
    member_id: int,
    title: str = "Member",
    role_id: int = 6,
    name: str | None = None,
) -> CommitteeMember:
    return CommitteeMember(
        CommitteeMemberID=33_000 + member_id,
        CommitteeID=committee_id,
        MemberID=member_id,
        MemberDisplayName=name,
        CommitteeRoleID=role_id,
        CommitteeRoleTitle=title,
    )


def make_meeting(
    schedule_id: int,
    when: datetime,
    *,
    owner_id: int | None = None,
    owner: str | None = None,
    number: str | None = None,
    type_id: int = 1,
    kind: str = "Committee",
    time: str | None = None,
    room: str | None = None,
    cancelled: bool = False,
    description: str | None = None,
) -> Schedule:
    return Schedule(
        ScheduleID=schedule_id,
        OwnerID=owner_id,
        OwnerName=owner,
        CommitteeNumber=number,
        ScheduleTypeID=type_id,
        ScheduleType=kind,
        ScheduleDate=when,
        ScheduleTime=time,
        RoomDescription=room,
        IsCancelled=cancelled,
        Description=description,
    )


def make_docket_row(
    docket_id: int,
    committee_id: int = 202,
    when: datetime = datetime(2026, 3, 9, 16, 30),
) -> DocketListItem:
    return DocketListItem(
        DocketID=docket_id,
        CommitteeID=committee_id,
        DocketDate=when,
        ChamberCode="S",
        CommitteeName="Courts of Justice",
    )


def make_docket_detail(
    docket_id: int,
    bills: tuple[str, ...],
    *,
    when: datetime = datetime(2026, 3, 9, 16, 30),
    room: str | None = "Senate Room A",
    schedule_time: str | None = "8:00 AM",
) -> DocketDetail:
    items = [
        {
            "DocketItemID": n,
            "LegislationID": 98_000 + n,
            "LegislationNumber": number,
            "LegislationDescription": f"{number}; a description.",
            "Patrons": [
                {
                    "MemberID": 419,
                    "PatronTypeID": 1,
                    "Name": "Chief Patron",
                    "MemberDisplayName": "Jeion A. Ward",
                }
            ],
        }
        for n, number in enumerate(bills, start=1)
    ]
    # A non-bill row, as dockets carry for comments and announcements.
    items.append({"DocketItemID": 900, "Description": "Comments"})

    return DocketDetail(
        DocketID=docket_id,
        DocketDate=when,
        CommitteeID=202,
        CommitteeName="Courts of Justice",
        DocketCategories=[{"CategoryCode": "CSGEN", "DocketItems": items}],
        Schedules=[{"ScheduleID": 1, "ScheduleTime": schedule_time, "RoomDescription": room}],
    )


def make_calendar_item(
    calendar_id: int,
    reference: str,
    when: datetime,
    chamber: str = "H",
) -> CalendarItem:
    return CalendarItem(
        CalendarID=calendar_id,
        ReferenceNumber=reference,
        CalendarDate=when,
        CalendarType="Chamber",
        ChamberCode=chamber,
        SessionCode="20261",
    )


def make_calendar_detail(calendar_id: int, reference: str, when: datetime) -> CalendarDetail:
    return CalendarDetail(
        CalendarID=calendar_id,
        ReferenceNumber=reference,
        CalendarDate=when,
        ChamberCode="H",
        SessionCode="20261",
        CalendarCategories=[
            {
                "CategoryCode": "Order",
                "Description": "Order of Business",
                "Agendas": [{"AgendaID": 1, "Description": "Call to Order"}],
            },
            {
                "CategoryCode": "RCRES",
                "Description": "Resolutions",
                "Agendas": [
                    {
                        "AgendaID": 2,
                        "LegislationNumber": "SJ209",
                        "LegislationDescription": "Governor; confirming appointments.",
                        "AgendaItems": [
                            {
                                "AgendaItemId": 1,
                                "Description": "<p>Reported from Committee... March 13, 2026</p>",
                                "VoteID": 302080,
                            },
                            {"AgendaItemId": 2},
                        ],
                    }
                ],
            },
        ],
    )


SESSION_20261 = make_session(59, "20261")

HB1 = make_bill_row(98525, "HB1", "Minimum wage; increases incrementally")
HB2 = make_bill_row(98526, "HB2", "Firearms; universal background checks")
SB10 = make_bill_row(98600, "SB10", "Elections; same-day registration", chamber="S")

WARD = make_member(419, "H0173", "Jeion A. Ward", district="92nd", list_name="Ward, Jeion A.")
KNIGHT = make_member(
    217,
    "H0206",
    "Barry D. Knight",
    party="R",
    district="98th",
    list_name="Knight, Barry D.",
    status="Inactive",
    ended=datetime(2026, 2, 17),
    reason="Deceased",
)
SCHMIDT = make_member(
    544,
    "H0402",
    "Charlie Schmidt",
    party="R",
    district="77th",
    list_name="Schmidt, Charlie",
    email="delcschmidt@house.virginia.gov",
)
AIRD = make_member(
    509, "S0115", "Lashrecse D. Aird", chamber="S", district="13th", list_name="Aird, Lashrecse D."
)
ROSTER = [WARD, KNIGHT, SCHMIDT, AIRD]

COURTS = make_committee(8, "H08", "Courts of Justice", abbreviation="HCJ     ")
TECH = make_committee(21, "H21", "Communications, Technology and Innovation")
SENATE_COURTS = make_committee(202, "S13", "Courts of Justice", chamber="S")
CRIMINAL_SUB = make_committee(41, "H08001", "HCJ      Sub: Criminal", parent=8)
COMMITTEES = [COURTS, TECH, SENATE_COURTS, CRIMINAL_SUB]

# Committee seats keyed by CommitteeID. Both the member tests (seats from
# the member's side) and the committee tests (the roster) read them.
SEATS = {
    8: [
        make_seat(8, 186, "Chair", 3, "Patrick A. Hope"),
        make_seat(8, 544, name="Charlie Schmidt"),
    ],
    21: [make_seat(21, 544, name="Charlie Schmidt")],
    41: [make_seat(41, 544, name="Charlie Schmidt"), make_seat(41, 186, "Ex-Officio", 7)],
}
