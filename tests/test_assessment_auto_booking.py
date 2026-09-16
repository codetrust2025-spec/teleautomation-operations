"""An assessment reaches the roster as an assessment, once.

The mail body is the production Glider shape with the candidate's name
replaced. Its window -- 7:05 PM to 8:30 AM for a one-hour test -- is the case
that had no status, no slot and no end: analysed five times, booked never.
"""
from datetime import datetime

import pytest

from services import assessment_auto_booking as booking
from services import assessment_schedule as schedule
from services import recruitment_mail_agent as agent

IST = schedule.LOCAL_ZONE
BEFORE_THE_WINDOW = datetime(2026, 9, 15, 14, 0, tzinfo=IST)
AFTER_THE_WINDOW = datetime(2026, 9, 16, 12, 0, tzinfo=IST)

BODY = (
    "Hi Test Candidate, This is reminder from Intelliswift to complete the pending assessment. "
    "The details are below - Assessment Name: Automation Number of Question(s): 16 "
    "Start time: Tuesday, 15-Sep-2026 07:05 PM (IST) End time: Wednesday, 16-Sep-2026 08:30 AM (IST) "
    "Time limit: 60 min(s) Once you are ready to take the assessment, please click on the "
    "\"Start Assessment\" link below."
)
MESSAGE = {
    "provider_message_id": "glider-1", "provider_thread_id": "glider-thread",
    "sender_email": "assistant@glider.ai", "subject": "Intelliswift invitation for assessment",
    "body": BODY, "attachments": [],
}
EVENT = {"id": "event-1", "mailbox_message_id": "message-1", "notification": {}}
MAILBOX = {"id": "mailbox-1", "candidate_id": "candidate-1", "email_address": "candidate@test.invalid"}
RESULT = {"status": "ASSESSMENT_INVITED", "classification": "assessment_invited",
          "company": {"name": "Intelliswift"}, "interview": {}}


@pytest.fixture
def store(monkeypatch):
    """The candidate store and the audit trail, as far as booking touches them."""
    state = {"candidate": {"id": "candidate-1", "name": "Test Candidate"}, "slots": [], "booked": [], "audits": []}

    def assign(**kwargs):
        row = {"id": f"slot-{len(state['booked']) + 1}", "slot_confirmed": True, **kwargs}
        row["date"], row["time"], row["time_end"] = kwargs["date"], kwargs["time"], kwargs["time_end"]
        state["booked"].append(kwargs)
        state["slots"].append(row)
        return row

    monkeypatch.setattr(booking.candidate_store, "get_candidate", lambda cid: state["candidate"])
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", lambda **kw: assign(**kw))
    monkeypatch.setattr(booking, "_confirmed_slots", lambda candidate: list(state["slots"]))
    monkeypatch.setattr(booking.mail_store, "record_interview_analysis", lambda **kw: {"id": "analysis-1"})
    monkeypatch.setattr(booking.mail_store, "canonical_classification", lambda result: "assessment_invited")
    monkeypatch.setattr(booking.mail_store, "record_booking_audit",
                        lambda **kw: state["audits"].append(kw) or {"id": f"audit-{len(state['audits'])}", **kw})
    monkeypatch.setattr(booking.mail_store, "attach_booking_to_notification", lambda *a, **k: {"id": "notification-1"})
    return state


def run(now=BEFORE_THE_WINDOW, message=None):
    return booking.execute_assessment_booking(
        mailbox=MAILBOX, message=message or MESSAGE, event=EVENT, result=RESULT, now=now)


def test_the_production_window_books_its_opening_hour_as_an_assessment(store):
    outcome = run()

    assert outcome["status"] == booking.BOOKED
    assert outcome["event_type"] == "assessment_auto_booked"
    booked = store["booked"][0]
    assert (booked["date"], booked["time"], booked["time_end"]) == ("2026-09-15", "19:05", "20:05")
    assert booked["booking_type"] == "Assessment"
    assert booked["assessment_key"]
    assert booked["interview_booking_source"] == "ai_auto_booked"


def test_the_reminder_finds_the_booking_instead_of_making_a_second_one(store):
    first = run()
    reminder = run(message={**MESSAGE, "provider_message_id": "glider-2",
                            "subject": "Reminder - Intelliswift invitation for assessment"})

    assert reminder["status"] == "Already Booked"
    assert reminder["duplicate"] is True
    assert reminder["booking"]["id"] == first["booking"]["id"]
    assert len(store["booked"]) == 1


def test_a_different_assessment_is_not_the_same_assessment(store):
    run()
    other = run(message={**MESSAGE, "provider_message_id": "glider-3", "body": BODY.replace(
        "End time: Wednesday, 16-Sep-2026 08:30 AM (IST)", "End time: Friday, 18-Sep-2026 08:30 AM (IST)")})

    assert other["status"] == booking.BOOKED
    assert len(store["booked"]) == 2


def test_a_closed_window_waits_for_a_person_instead_of_inventing_a_time(store):
    outcome = run(now=AFTER_THE_WINDOW)

    assert outcome["status"] == booking.PENDING
    assert outcome["failure_code"] == "ASSESSMENT_WINDOW_CLOSED"
    assert outcome["block_reason"]["reason_code"] == "ASSESSMENT_WINDOW_CLOSED"
    assert not store["booked"]


def test_a_stated_time_is_booked_exactly(store):
    outcome = run(message={**MESSAGE, "body": "Your assessment is scheduled on 18-Sep-2026 at 3:00 PM. Duration: 45 minutes"})

    assert outcome["status"] == booking.BOOKED
    assert (store["booked"][0]["time"], store["booked"][0]["time_end"]) == ("15:00", "15:45")


def test_an_existing_booking_is_not_overlapped(store):
    store["slots"].append({"id": "interview-1", "date": "2026-09-15", "time": "19:00",
                           "time_end": "20:00", "slot_confirmed": True})

    outcome = run()

    assert outcome["status"] == booking.BOOKED
    assert store["booked"][0]["time"] == "20:05"


def test_a_missing_candidate_is_reported_not_guessed(store, monkeypatch):
    monkeypatch.setattr(booking.candidate_store, "get_candidate", lambda cid: None)

    outcome = run()

    assert outcome["status"] == booking.PENDING
    assert outcome["failure_code"] == "CANDIDATE_MAPPING_FAILED"
    assert not store["booked"]


def test_every_outcome_is_audited(store):
    run()
    run(now=AFTER_THE_WINDOW, message={**MESSAGE, "provider_message_id": "glider-4",
                                       "body": BODY.replace("Assessment Name: Automation", "Assessment Name: Later")})

    assert [audit["booking_status"] for audit in store["audits"]] == [booking.BOOKED, booking.PENDING]
    assert store["audits"][0]["auto_booked"] is True
    assert store["audits"][1]["failure_code"] == "ASSESSMENT_WINDOW_CLOSED"


def proposed(status, *, quote, date, time):
    """A complete model result proposing `status`, as the schema requires."""
    return {
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": True, "is_selection_or_offer_related": True,
        "should_create_review_record": True, "status": status, "confidence": 0.95,
        "ignore_reason": None,
        "candidate": {"name": None, "email": None},
        "company": {"name": "Intelliswift", "domain": "glider.ai"},
        "job": {"title": "Automation", "employment_type": None, "location": None},
        "recruiter": {"name": None, "email": None},
        "interview": {"date": date, "time": time, "timezone": "IST", "mode": None,
                      "round": None, "location": None, "meeting_link": None},
        "offer": {"offer_detected": False, "offer_letter_detected": False,
                  "appointment_letter_detected": False, "offer_date": None, "offered_ctc": None,
                  "currency": None, "joining_date": None, "offer_expiry_date": None},
        "attachments": [],
        "evidence": [{"source": "EMAIL_BODY", "meaning": status, "text": quote}],
        "risk_flags": [], "requires_manual_review": False,
        "summary": "Detected.", "recommended_action": "Review.",
    }


def test_the_pipeline_reaches_the_assessment_booking(monkeypatch):
    """Drive `process_message`, because calling the booking module directly is
    what let a NameError in the hand-off reach production: the mail was
    classified, the booking crashed, and every test here still passed."""
    calls = []
    monkeypatch.setattr(agent.store, "insert_message", lambda mailbox, decoded, score: ({"id": "stored-message"}, True))
    for name in ("is_duplicate_content", "is_duplicate_offer_attachment", "is_duplicate_thread_status"):
        monkeypatch.setattr(agent.store, name, lambda *args, **kwargs: False)
    monkeypatch.setattr(agent.store, "mark_message_status", lambda *a, **k: None)
    monkeypatch.setattr(agent.store, "save_attachment", lambda mid, attachment: attachment)
    monkeypatch.setattr(agent.store, "record_analysis", lambda *a, **k: None)
    monkeypatch.setattr(agent.store, "create_event", lambda cid, mid, result, **meta: {
        "id": "event-1", "mailbox_message_id": "stored-message", "candidate_id": cid,
        "classification": "assessment_invited", "primary_status": "ASSESSMENT_INVITED", "notification": {}})
    monkeypatch.setattr("services.recruitment_notifications.notify_detection", lambda event: None)
    monkeypatch.setattr(agent, "analyze", lambda decoded, attachments: (
        {**proposed("ASSESSMENT_INVITED", quote="complete the pending assessment",
                    date="2026-09-15", time="07:05 PM"),
         "classification": "assessment_invited", "candidate_status": "Assessment Pending",
         "primary_status": "ASSESSMENT_INVITED"}, "test-model", 10))
    import services.assessment_auto_booking as module
    monkeypatch.setattr(module, "execute_assessment_booking", lambda **kwargs: calls.append(kwargs) or {
        "status": "Auto Booked", "event_type": "assessment_auto_booked",
        "booking": {"id": "slot-1", "date": "2026-09-15", "time": "19:05", "time_end": "20:05"},
        "audit": {"id": "audit-1"}, "notification": {}, "failure_code": None, "block_reason": None})

    published = []
    monkeypatch.setattr(agent, "_publish", lambda event_type, **payload: published.append((event_type, payload)))

    event = agent.process_message(
        {"id": "mailbox-1", "candidate_id": "candidate-1"},
        {"provider_message_id": "glider-1", "provider_thread_id": "glider-thread",
         "sender_email": "assistant@glider.ai", "subject": MESSAGE["subject"],
         "body": BODY, "sent_at": "2026-09-15T14:00:00Z"},
        [{"filename": "none.txt", "data": None, "checksum": "checksum-1", "text": ""}],
    )

    assert calls, "the assessment never reached its booking path"
    assert calls[0]["message"]["subject"] == MESSAGE["subject"]
    assert isinstance(calls[0]["message"].get("attachments"), list)
    # Everything after the booking call matters too: the branch catches its own
    # exceptions, so asserting only that booking was called let a broken
    # publish -- and with it the whole outcome -- pass as success.
    assert (event or {}).get("auto_booking", {}).get("status") == "Auto Booked"
    booked = [payload for name, payload in published if name == "assessment_auto_booked"]
    assert booked and booked[0]["status"] == "Auto Booked"
    assert booked[0]["booking_type"] == "Assessment"
    assert (booked[0]["interview_date"], booked[0]["start_time"]) == ("2026-09-15", "19:05")
    assert not [name for name, _ in published if name == "mail_processing_failed"]


class TestTheStatusItself:
    """The mail must reach booking as an assessment, not as an interview."""

    def test_an_assessment_mail_is_promoted_off_the_interview_path(self):
        value = proposed("INTERVIEW_CONFIRMED", quote="complete the pending assessment",
                         date="2026-09-15", time="07:05 PM")

        agent.validate_result(value, {"subject": MESSAGE["subject"], "body": BODY}, [])

        assert value["status"] == "ASSESSMENT_INVITED"
        assert value["classification"] == "assessment_invited"
        assert value["candidate_status"] == "Assessment Pending"
        assert value["promoted_from"] == "INTERVIEW_CONFIRMED"

    def test_a_real_interview_is_left_alone(self):
        body = ("Your technical interview is scheduled on 18-Sep-2026 at 3:00 PM. "
                "There is also an assessment round later in the process.")
        value = proposed("INTERVIEW_CONFIRMED",
                         quote="Your technical interview is scheduled on 18-Sep-2026 at 3:00 PM",
                         date="2026-09-18", time="03:00 PM")

        agent.validate_result(value, {"subject": "Interview scheduled", "body": body}, [])

        assert value["status"] == "INTERVIEW_CONFIRMED"
        assert value["classification"] == "interview_confirmed"


class TestTheRealStoreKeepsTheType:
    """Drive the file-backed store, because a booking that loses its type on
    the way to disk is indistinguishable from an interview on the roster."""

    @pytest.fixture
    def real_store(self, monkeypatch, tmp_path):
        from features import candidate_store as cs
        monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
        monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "proofs"))
        monkeypatch.setattr(cs, "_load_cache", None)
        monkeypatch.setattr(cs, "_load_cache_at", 0.0)
        return cs

    def make(self, cs, name="Test Candidate", phone="9000000001"):
        return cs.create_candidate({
            "name": name, "phone": phone, "technology": "Automation",
            "service_type": "profile_service", "purpose": "interview",
        })

    def test_an_assessment_booking_is_stored_as_one(self, real_store):
        cs = real_store
        candidate = self.make(cs)

        booked = cs.assign_interview_slot(
            candidate_id=candidate["id"], date="2026-09-15", time="19:05", time_end="20:05",
            interview_company="Intelliswift", interview_role="Automation",
            interview_booking_source="ai_auto_booked",
            booking_type="Assessment", assessment_key="key-1",
        )

        stored = cs.get_candidate(booked["id"])
        assert stored["booking_type"] == "Assessment"
        assert stored["assessment_key"] == "key-1"
        assert (stored["date"], stored["time"], stored["time_end"]) == ("2026-09-15", "19:05", "20:05")

    def test_a_second_slot_clones_the_type_with_it(self, real_store):
        cs = real_store
        candidate = self.make(cs)
        cs.assign_interview_slot(candidate_id=candidate["id"], date="2026-09-15", time="10:00",
                                 time_end="11:00", interview_booking_source="ai_auto_booked")

        second = cs.assign_interview_slot(
            candidate_id=candidate["id"], date="2026-09-15", time="19:05", time_end="20:05",
            interview_booking_source="ai_auto_booked", booking_type="Assessment", assessment_key="key-2")

        assert second["id"] != candidate["id"]
        stored = cs.get_candidate(second["id"])
        assert stored["booking_type"] == "Assessment"
        assert stored["assessment_key"] == "key-2"

    def test_an_interview_booking_is_still_an_interview(self, real_store):
        cs = real_store
        candidate = self.make(cs)

        booked = cs.assign_interview_slot(candidate_id=candidate["id"], date="2026-09-18",
                                          time="15:00", time_end="16:00")

        assert cs.get_candidate(booked["id"])["booking_type"] == "Interview"
