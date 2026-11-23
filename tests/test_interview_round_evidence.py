"""A booking's round comes from its own evidence, or it is "Needs Review".

23 Nov 2026: 109 Daily Ops bookings read "Round not specified". Eleven of them
had the round written in their own invite ("Round 1", "Level 1", "R1-...",
"Screening call"), and the booking simply had not read it; the rest had no
round anywhere in what was stored. The owner's rule: a round is never guessed.
A booking gets the round its invite, notes or screenshot states outright, and
when none does it is "Needs Review" until a person sets it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from features import candidate_store as cs
from features import interview_round_evidence as ev
from services import interview_auto_booking as auto

LATER = (date.today() + timedelta(days=3)).isoformat()


# -- reading a round -------------------------------------------------------

@pytest.mark.parametrize("subject, expected", [
    ("Level 1 Interview: Candidate Alpha", "L1"),
    ("[In-person] Interview : Acme : Round 1 : Candidate Alpha : QA Engineer", "L1"),
    ("R1-Candidate Alpha-Data Engineer-Fabric", "L1"),
    ("CGO _ ACME_L1_ Interview_ React+Next JS - Candidate", "L1"),
    ("Acme Systems – Level 1 – Python +Angular- Video call Interview", "L1"),
    ("CANDIDATE | Acme Interview | MS Teams | IAM - Senior | Tech 1", "L1"),
    ("Round 2 Interview | Full Stack Developer", "L2"),
    ("2nd Round discussion - Data Engineer", "L2"),
    ("Level-II discussion with the panel", "L2"),
    ("Third round interview", "L3"),
    ("HR Round - Candidate Alpha", "HR"),
    ("Final interview with the director", "Final"),
    ("Screening call with Candidate Alpha", "Screening"),
    ("Screening - Recruiter/Candidate - Data Science - Acme", "Screening"),
])
def test_a_subject_that_names_the_round_gives_that_round(subject, expected):
    result = ev.resolve_round([("email.subject", subject)])
    assert (result["round"], result["status"], result["confidence"]) == (expected, "evidenced", "high")
    assert result["evidence_source"] == "email.subject"


@pytest.mark.parametrize("text", [
    "NTT DATA : Teams Meeting:: Technical Round",       # technical, no number
    "C: Technical Interview with Candidate || Full Stack Developer",
    "Acme | Interview Schedule - TP1",                   # not a round label
    "Please find the details below for the Managerial interview",
    "Interview with Acme Innovation",
    "Career Consultation - Candidate Alpha",
    "Discussion With Candidate for ServiceNow",
])
def test_words_that_do_not_name_a_round_give_needs_review(text):
    result = ev.resolve_round([("email.subject", text)])
    assert result["round"] == ""
    assert result["status"] == ev.NEEDS_REVIEW_STATUS
    assert result["reason"].startswith("no round is named")


@pytest.mark.parametrize("body", [
    "You've moved to the next round I will share the link soon",  # "round I" is a sentence
    "Requirements: Level 3 support experience, ITIL",              # a job description
    "3rd Floor, Business Park, Church Street",
    "Candidate ID: 123",
])
def test_a_body_does_not_turn_unrelated_numbers_into_a_round(body):
    assert ev.resolve_round([("email.body", body)])["round"] == ""


def test_a_body_that_names_the_round_is_medium_confidence():
    result = ev.resolve_round([
        ("email.subject", "Interview Invite - Fullstack Developer"),
        ("email.body", "Please join the 1st Technical Level discussion (Video Call) below."),
    ])
    assert (result["round"], result["confidence"], result["evidence_source"]) == ("L1", "medium", "email.body")


def test_two_different_rounds_are_not_picked_between():
    result = ev.resolve_round([
        ("email.subject", "Round 1 Interview"),
        ("email.body", "If you clear this, Round 2 will be with the manager."),
    ])
    assert result["round"] == ""
    assert result["status"] == ev.NEEDS_REVIEW_STATUS
    assert "different rounds" in result["reason"]


def test_nothing_to_read_says_so():
    result = ev.resolve_round([("booking.notes", ""), ("booking.invite_title", None)])
    assert result == {**result, "round": "", "status": "needs_review",
                      "reason": "no stored text to read a round from"}


# -- bookings record it ----------------------------------------------------

@pytest.fixture
def store(monkeypatch):
    data = {"candidates": []}
    monkeypatch.setattr(cs, "_load", lambda *args, **kwargs: data)
    monkeypatch.setattr(cs, "_save", lambda payload: data.update(payload))
    return data


def booking(**overrides):
    base = {"id": "b1", "name": "Candidate Alpha", "date": LATER, "time": "15:00", "time_end": "15:30",
            "slot_confirmed": True, "booking_type": "Interview", "interview_round": "",
            "interview_role": "", "notes": ""}
    base.update(overrides)
    return base


def test_a_booking_whose_invite_names_the_round_gets_it_with_its_evidence(store):
    store["candidates"].append(booking(interview_role="Round 1 Interview | Full Stack Developer"))

    saved = cs.record_interview_round_evidence("b1")

    assert saved["interview_round"] == "L1"
    assert saved["interview_round_needs_review"] is False
    evidence = store["candidates"][0]["interview_round_evidence"]
    assert (evidence["status"], evidence["round"], evidence["evidence_source"], evidence["phrase"]) == (
        "evidenced", "L1", "booking.invite_title", "Round 1")
    history = store["candidates"][0]["interview_round_history"]
    assert history[-1]["from"] == "" and history[-1]["to"] == "L1" and history[-1]["source"] == "evidence"


def test_a_booking_with_no_round_in_its_evidence_is_needs_review(store):
    store["candidates"].append(booking(interview_role="Interview with Acme", notes="client interview support"))

    saved = cs.record_interview_round_evidence("b1")

    assert saved["interview_round"] == ""
    assert saved["interview_round_needs_review"] is True
    evidence = store["candidates"][0]["interview_round_evidence"]
    assert evidence["status"] == "needs_review"
    assert evidence["round"] == ""
    assert "booking.invite_title" in evidence["sources_checked"]


def test_linked_email_evidence_is_read_too(store):
    store["candidates"].append(booking())
    saved = cs.record_interview_round_evidence(
        "b1", sources=[("source_email.subject", "Aspire – Level 1 – Video call Interview")])
    assert saved["interview_round"] == "L1"


def test_a_booking_that_has_a_round_or_is_an_assessment_is_left_alone(store):
    store["candidates"] += [
        booking(id="has", interview_round="L2", interview_role="Round 1 Interview"),
        booking(id="test", booking_type="Assessment", interview_round="Assessment"),
        booking(id="blank-assessment", booking_type="Assessment"),
    ]
    before = [dict(r) for r in store["candidates"]]
    for cid in ("has", "test", "blank-assessment"):
        cs.record_interview_round_evidence(cid)
    assert store["candidates"] == before


def test_a_person_setting_the_round_closes_the_review(store):
    store["candidates"].append(booking())
    cs.record_interview_round_evidence("b1")
    cs.set_interview_round("b1", interview_round="HR", by="operations_admin")
    evidence = store["candidates"][0]["interview_round_evidence"]
    assert (evidence["status"], evidence["round"], evidence["reviewed_by"]) == ("reviewed", "HR", "operations_admin")


def test_a_new_slot_booking_reads_its_round_or_is_marked(store, monkeypatch):
    monkeypatch.setattr(cs, "create_candidate", lambda record, **kw: (
        store["candidates"].append({**record, "id": "new"}) or store["candidates"][-1]))
    cs.create_interview_slot(name="Candidate Beta", date=LATER, time="11:00", time_end="11:30",
                             technology="Java", notes="L2 interview with Acme")
    assert store["candidates"][-1]["interview_round"] == "L2"

    store["candidates"].clear()
    cs.create_interview_slot(name="Candidate Beta", date=LATER, time="12:00", time_end="12:30",
                             technology="Java", notes="Interview slots group")
    assert store["candidates"][-1]["interview_round_evidence"]["status"] == "needs_review"


def test_a_second_booking_does_not_inherit_the_previous_round(monkeypatch):
    captured = {}
    monkeypatch.setattr(cs, "create_candidate", lambda record, **kw: captured.setdefault("r", record))
    cs._duplicate_candidate_slot(booking(interview_round="L1"), date=LATER, time="10:00")
    assert captured["r"]["interview_round"] == ""


def test_auto_booking_reads_the_round_from_the_invite_when_the_model_left_it_out():
    result = {"interview": {"round": None}, "job": {"title": "Developer"}}
    message = {"subject": "Round 1 Interview |Full Stack Developer |Candidate", "body_text": "Hello"}
    assert auto.normalize_booking_round(result, message) == "L1"
    assert result["interview"]["round"] == "L1"


def test_auto_booking_does_not_guess_from_technical():
    result = {"interview": {"round": "Technical"}}
    message = {"subject": "NTT DATA : Teams Meeting:: Technical Round", "body_text": "your interview (Technical round)"}
    assert auto.normalize_booking_round(result, message) == ""
    assert result["interview"]["round"] is None


# -- the same name everywhere ----------------------------------------------

def _roster():
    return [
        booking(id="l1", interview_round="L1"),
        booking(id="nr"),
        booking(id="asmt", booking_type="Assessment", interview_round="Assessment"),
    ]


@pytest.mark.parametrize("value", ["Needs Review", "needs_review", "Unspecified"])
def test_the_needs_review_level_selects_bookings_with_no_round(value):
    rows = cs._filter_interview_rows(_roster(), filter_round=[value])
    assert [r["id"] for r in rows] == ["nr"]


def test_other_level_filters_are_unchanged():
    assert [r["id"] for r in cs._filter_interview_rows(_roster(), filter_round=["L1"])] == ["l1"]
    assert {r["id"] for r in cs._filter_interview_rows(_roster(), filter_round=["Others"])} == {"nr", "asmt"}


def test_bookings_analytics_counts_them_as_needs_review(store):
    store["candidates"] += _roster()
    payload = cs.interview_global_summary(LATER, LATER)
    names = {level["name"] for c in payload["booking_overview"]["by_candidate"] for level in c["levels"]}
    assert "Needs Review" in names and "Unspecified" not in names


# -- bookings that are not interviews (23 Nov) -------------------------------

@pytest.mark.parametrize("title, expected", [
    ("Career Consultation - Candidate Alpha (India Career Growth Pathway)", "Career Consultation"),
    ("BGV Verification | Candidate Alpha | QA Automation | Acme", "BGV Verification"),
    ("Background verification call - Candidate Alpha", "BGV Verification"),
])
def test_an_invite_title_that_names_a_non_interview_meeting_gives_that_type(title, expected):
    assert ev.non_interview_type_from_title(title)[0] == expected


@pytest.mark.parametrize("title", [
    "Interview - Career Consultation track",        # it says interview
    "BGV and Career Consultation",                  # two kinds: not picked between
    "Round 1 Interview | Full Stack Developer",
    "Interview with Acme Innovation",
    "",
])
def test_other_titles_stay_interviews(title):
    assert ev.non_interview_type_from_title(title) == ("", "")


def test_a_consultation_booking_is_kept_and_typed_not_given_a_round(store):
    store["candidates"].append(booking(
        interview_role="Career Consultation - Candidate Alpha (India Career Growth Pathway)",
        attendance_status=None, interview_round_evidence={"status": "needs_review", "reason": "x"}))

    saved = cs.record_interview_round_evidence("b1")

    row = store["candidates"][0]
    assert len(store["candidates"]) == 1 and row["slot_confirmed"] is True and row["date"] == LATER
    assert row["booking_type"] == "Career Consultation"
    assert row["interview_round"] == ""
    assert row["booking_type_evidence"]["previous_booking_type"] == "Interview"
    assert row["booking_type_evidence"]["phrase"] == "Career Consultation"
    assert row["interview_round_evidence"]["status"] == "not_an_interview"
    assert saved["interview_round_needs_review"] is False


def test_a_booking_with_a_round_is_never_retyped(store):
    store["candidates"].append(booking(interview_round="L1", interview_role="BGV Verification | Candidate"))
    cs.record_interview_round_evidence("b1")
    assert store["candidates"][0]["booking_type"] == "Interview"


def test_non_interview_bookings_leave_the_needs_review_level_and_count_as_their_type(store):
    store["candidates"] += _roster() + [booking(id="bgv", booking_type="BGV Verification")]
    assert [r["id"] for r in cs._filter_interview_rows(store["candidates"], filter_round=["Needs Review"])] == ["nr"]
    payload = cs.interview_global_summary(LATER, LATER)
    names = {level["name"] for c in payload["booking_overview"]["by_candidate"] for level in c["levels"]}
    assert "BGV Verification" in names


def test_attendance_can_be_recorded_for_a_non_interview_booking():
    assert cs.booking_has_no_round({"booking_type": "Career Consultation"})
    assert cs.booking_has_no_round({"booking_type": "Assessment"})
    assert not cs.booking_has_no_round({"booking_type": ""})
    assert not cs.booking_has_no_round({"booking_type": "Interview"})
