"""A candidate's history in one list, from the records that already hold it."""

from __future__ import annotations

import pytest

from datetime import datetime, timezone

from features import candidate_store as cs
from services import candidate_timeline as timeline

PERSON = ["p1", "slot-a", "slot-b", "slot-c"]


def _rows():
    proof = {"id": "pay-1", "uploaded_at": "2099-06-26T14:33:26+00:00", "note": ""}
    return [
        {"id": "p1", "name": "Synthetic Candidate", "created_at": "2099-06-01T09:00:00+00:00",
         "payment_proofs": [proof]},
        {"id": "slot-a", "name": "Synthetic Candidate", "slot_confirmed": True,
         "slot_confirmed_at": "2099-09-18T05:31:55+00:00", "created_at": "2099-09-18T05:31:55+00:00",
         "date": "2099-09-18", "time": "16:30", "time_end": "17:30", "interview_round": "L1",
         "booking_idempotency_key": "k1", "payment_proofs": [proof],
         "interview_attendance_status": "attended", "interview_attended_at": "2099-09-18T11:33:59+00:00",
         "interview_attended_by": "operations_admin", "interview_attendance_remark": "went well"},
        {"id": "slot-b", "name": "Synthetic Candidate", "slot_confirmed": True,
         "slot_confirmed_at": "2099-09-10T08:00:00+00:00", "created_at": "2099-09-10T08:00:00+00:00",
         "date": "2099-09-12", "time": "11:00", "time_end": "11:30",
         "interview_booking_source": "ai_auto_booked", "interview_source_message_id": "gm-7",
         "interview_company": "Example Corp", "payment_proofs": [proof],
         "interview_previous_sittings": [{"date": "2099-09-11", "time": "10:00", "time_end": "10:30",
                                          "moved_at": "2099-09-10T12:00:00+00:00"}],
         "interview_attendance_status": "cancelled", "interview_attended_at": "2099-09-11T08:00:00+00:00",
         "interview_attended_by": "operations_admin"},
        {"id": "slot-c", "name": "Synthetic Candidate", "slot_confirmed": True,
         "slot_confirmed_at": "2099-09-05T08:00:00+00:00", "date": "2099-09-06", "time": "15:00",
         "time_end": "15:30", "superseded_at": "2099-09-05T09:00:00+00:00",
         "superseded_by_booking_id": "slot-b"},
        # Someone else with the same name: never part of this history.
        {"id": "other", "name": "Synthetic Candidate", "slot_confirmed": True,
         "slot_confirmed_at": "2099-09-19T08:00:00+00:00", "date": "2099-09-20", "time": "10:00"},
    ]


ALERTS = {
    "p1": [{"id": "n1", "classification": "interview_confirmed", "candidate_status": "Interview Automatically Booked",
            "booking_status": "Auto Booked",
            "email_subject": "L1 interview", "company_name": "Example Corp", "booking_id": "slot-b",
            "gmail_message_id": "gm-7", "ai_recruitment_event_id": "ev-1",
            # Database timestamps arrive as datetimes, not ISO text.
            "email_received_at": datetime(2099, 9, 10, 7, 59, tzinfo=timezone.utc)},
           {"id": "n2", "classification": "interview_cancelled", "candidate_status": "Automatic Booking Blocked",
            "email_subject": "Cancelled: interview", "gmail_message_id": "gm-8",
            "booking_block": {"title": "Cancellation not applied",
                              "reason": "We found more than one booking for this candidate."},
            "email_received_at": "2099-09-14T07:00:00+00:00"}],
    "slot-a": [{"id": "n1", "classification": "interview_confirmed"}],   # the same alert again
}
EVENTS = {
    "p1": [{"id": "ev-1", "primary_status": "INTERVIEW_SCHEDULED", "created_at": "2099-09-10T07:59:30+00:00"},
           {"id": "ev-2", "primary_status": "SHORTLISTED", "company_name": "Example Corp",
            "job_title": "Engineer", "subject": "You are shortlisted", "mailbox_message_id": "mm-9",
            "created_at": "2099-09-01T10:00:00+00:00"}],
}


@pytest.fixture
def history(monkeypatch):
    monkeypatch.setattr(cs, "all_booking_rows", _rows)
    monkeypatch.setattr(cs, "candidate_identity_ids", lambda cid, **_kwargs: list(PERSON))
    return timeline.candidate_timeline(
        "p1", alerts_for=lambda cid: ALERTS.get(cid, []), events_for=lambda cid: EVENTS.get(cid, []),
    )


def _titles(entries):
    return [entry["title"] for entry in entries]


def test_it_is_newest_first(history):
    stamps = [entry["at"] for entry in history]
    assert stamps == sorted(stamps, reverse=True)


def test_every_kind_of_happening_is_there(history):
    kinds = {entry["kind"] for entry in history}
    assert {"created", "booking", "rescheduled", "superseded", "attendance", "cancelled",
            "payment", "alert", "mail"} <= kinds


def test_a_booking_says_where_it_came_from(history):
    booked = {entry["booking_id"]: entry for entry in history if entry["kind"] == "booking"}
    assert booked["slot-a"]["source"] == "Booking form"
    assert booked["slot-b"]["source"] == "Gmail"
    assert booked["slot-c"]["source"] == "Daily Ops"
    assert booked["slot-a"]["detail"] == "2099-09-18 16:30-17:30 · L1"


def test_a_move_names_both_times(history):
    (moved,) = [entry for entry in history if entry["kind"] == "rescheduled"]
    assert moved["detail"] == "From 2099-09-11 10:00-10:30 to 2099-09-12 11:00-11:30 · Example Corp"


def test_an_attendance_mark_says_who_and_what(history):
    (attended,) = [entry for entry in history if entry["kind"] == "attendance"]
    assert attended["title"] == "Marked attended"
    assert attended["detail"].endswith("went well · by operations_admin")


def test_a_payment_copied_onto_every_slot_is_one_entry(history):
    assert [entry["kind"] for entry in history].count("payment") == 1


def test_the_mail_that_made_a_booking_is_that_booking_not_a_second_entry(history):
    """The Gmail booking, its alert and its recruitment event are one happening."""
    alerts = [entry for entry in history if entry["kind"] == "alert"]
    assert [entry["mail_id"] for entry in alerts] == ["gm-8"]
    booked = next(entry for entry in history if entry["kind"] == "booking" and entry["booking_id"] == "slot-b")
    assert booked["mail_id"] == "gm-7" and booked["event_id"] == "ev-1"
    assert booked["detail"].endswith("L1 interview")
    assert "Interview scheduled" not in _titles(history)
    assert "Interview Automatically Booked" not in _titles(history)


def test_one_clock_orders_every_record(history):
    """ISO text from booking rows and datetimes from the database used to sort
    as text, putting a later mail below an earlier booking on the same day."""
    assert all(entry["at"].endswith("+00:00") and "T" in entry["at"] for entry in history)
    moments = [datetime.fromisoformat(entry["at"]) for entry in history]
    assert moments == sorted(moments, reverse=True)


def test_a_repeated_detail_is_said_once(monkeypatch):
    monkeypatch.setattr(cs, "all_booking_rows", lambda: [])
    monkeypatch.setattr(cs, "candidate_identity_ids", lambda cid, **_kwargs: ["p1"])
    entries = timeline.candidate_timeline("p1", alerts_for=lambda _cid: [], events_for=lambda _cid: [
        {"id": "ev-9", "primary_status": "INTERVIEW_CONFIRMED", "company_name": "Same Words",
         "job_title": "Same Words", "subject": "Same Words", "created_at": "2099-09-21 06:08:00+00:00"}])
    assert entries[0]["detail"] == "Same Words"
    assert entries[0]["at"] == "2099-09-21T06:08:00+00:00"


def test_a_blocked_alert_reads_in_plain_english(history):
    blocked = next(entry for entry in history if entry["mail_id"] == "gm-8")
    assert blocked["title"] == "Cancellation not applied"
    assert blocked["detail"].startswith("We found more than one booking for this candidate.")


def test_a_recruitment_event_without_an_alert_still_shows(history):
    (event,) = [entry for entry in history if entry["kind"] == "mail"]
    assert event["title"] == "Shortlisted"
    assert event["event_id"] == "ev-2"
    assert event["detail"] == "Example Corp · Engineer · You are shortlisted"


def test_someone_else_with_the_same_name_is_not_in_it(history):
    assert all(entry["booking_id"] != "other" for entry in history)


def test_the_endpoint_serves_one_persons_timeline(monkeypatch):
    """Through the real route: the same entries, and 404 for nobody."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core import recruitment_mail_api as api

    monkeypatch.setenv("AI_INTERVIEW_OFFER_TRACKING_ENABLED", "true")
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setattr(api.store, "ensure_schema", lambda: None)
    monkeypatch.setattr(cs, "all_booking_rows", _rows)
    monkeypatch.setattr(cs, "candidate_identity_ids", lambda cid, **_kwargs: list(PERSON))
    monkeypatch.setattr(cs, "get_candidate", lambda cid: {"id": cid} if cid in PERSON else None)
    monkeypatch.setattr(api.store, "list_notifications",
                        lambda *, filters, limit: (ALERTS.get(filters["candidate_id"], []), 0))
    monkeypatch.setattr(api.store, "list_events", lambda *, candidate_id, limit: EVENTS.get(candidate_id, []))
    monkeypatch.setattr(api.store, "candidate_ids_with_mail",
                        lambda ids: {cid for cid in ids if cid in ALERTS or cid in EVENTS})
    app = FastAPI()
    api.install_recruitment_mail_routes(app)
    client = TestClient(app)

    body = client.get("/api/candidates/p1/timeline").json()

    assert body["status"] == "ok"
    assert body["entries"][0]["title"] == "Marked attended"
    assert [entry["mail_id"] for entry in body["entries"] if entry["kind"] == "alert"] == ["gm-8"]
    assert client.get("/api/candidates/nobody/timeline").status_code == 404


def test_mail_is_only_asked_of_the_ids_that_hold_it(monkeypatch):
    """One candidate has an id per booking row; asking each of them for mail
    took 1.7 s. Only the ids that hold mail are asked, and never an id that is
    not this person's."""
    monkeypatch.setattr(cs, "all_booking_rows", _rows)
    monkeypatch.setattr(cs, "candidate_identity_ids", lambda cid, **_kwargs: list(PERSON))
    asked = []

    def alerts(cid):
        asked.append(cid)
        return ALERTS.get(cid, [])

    narrowed = timeline.candidate_timeline(
        "p1", alerts_for=alerts, events_for=lambda cid: EVENTS.get(cid, []),
        mail_owners=lambda ids: {"p1", "someone-else"},
    )
    everyone = timeline.candidate_timeline(
        "p1", alerts_for=lambda cid: ALERTS.get(cid, []), events_for=lambda cid: EVENTS.get(cid, []),
    )

    assert asked == ["p1"]
    assert narrowed == everyone
