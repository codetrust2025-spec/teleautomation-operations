from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from features import candidate_store
from services import interview_auto_booking as booking


def result(classification="interview_confirmed", confidence=.96, **interview):
    details = {
        "date": "2099-07-20", "time": "03:00 PM", "timezone": "Asia/Kolkata",
        "round": "L1", "mode": "Online", "meeting_link": "https://meet.test/room",
        "location": None,
    }
    details.update(interview)
    return {
        "classification": classification, "classification_source": "OLLAMA",
        "ai_validation_status": "VALIDATED", "confidence": confidence,
        "requires_manual_review": False, "interview": details,
        "evidence": [{"meaning": "INTERVIEW_CONFIRMED", "text": "Interview schedule confirmed"}],
        "candidate": {"email": "candidate@test.invalid"},
        "company": {"name": "Example"}, "job": {"title": "Engineer"},
        "summary": "Confirmed interview", "reason": "Explicit schedule",
    }


@pytest.mark.parametrize(("raw", "expected"), [
    ("12:00 AM", "00:00"), ("12:00 PM", "12:00"), ("1:05 AM", "01:05"),
    ("01:05 PM", "13:05"), ("11:59 PM", "23:59"),
])
def test_12_hour_times_are_normalized(raw, expected):
    assert booking.parse_interview_time(raw) == expected


@pytest.mark.parametrize("raw", ["", "15:00", "13:00 PM", "3 PM", "00:30 AM", "03:60 PM"])
def test_invalid_interview_times_are_blocked(raw):
    with pytest.raises(booking.BookingValidationError, match="12-hour"):
        booking.parse_interview_time(raw)


@pytest.mark.parametrize(("raw", "key"), [("IST", "Asia/Kolkata"), ("Asia/Kolkata", "Asia/Kolkata"), ("America/New_York", "America/New_York")])
def test_supported_timezones_are_explicit(raw, key):
    assert booking.validate_timezone(raw).key == key


@pytest.mark.parametrize("raw", ["", "India/Imaginary", "GMT+25:00", "NOTAZONE"])
def test_invalid_timezones_are_blocked(raw):
    with pytest.raises(booking.BookingValidationError):
        booking.validate_timezone(raw)


def test_non_ist_schedule_is_converted_to_booking_calendar():
    value = result(date="2026-07-20", time="03:00 PM", timezone="America/New_York")
    schedule = booking.normalized_schedule(value, now=datetime(2026, 7, 15, tzinfo=ZoneInfo("America/New_York")))
    assert schedule == {"date": "2026-07-21", "time": "00:30", "time_end": "01:00",
                        "timezone": "Asia/Kolkata", "source_timezone": "America/New_York"}


def test_singapore_calendar_schedule_is_normalized_to_ist():
    value = result(date="2026-07-23", time="08:00 PM", timezone="Asia/Singapore")
    schedule = booking.normalized_schedule(
        value,
        now=datetime(2026, 7, 15, tzinfo=ZoneInfo("Asia/Kolkata")),
    )
    assert schedule == {
        "date": "2026-07-23",
        "time": "17:30",
        "time_end": "18:00",
        "timezone": "Asia/Kolkata",
        "source_timezone": "Asia/Singapore",
    }


def test_trusted_calendar_duration_is_preserved_instead_of_defaulting_to_30_minutes():
    value = result(
        date="2026-07-29", time="09:45 AM", timezone="Asia/Kolkata",
        duration_minutes=45,
    )
    schedule = booking.normalized_schedule(
        value,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Kolkata")),
    )
    assert schedule == {
        "date": "2026-07-29",
        "time": "09:45",
        "time_end": "10:30",
        "timezone": "Asia/Kolkata",
        "source_timezone": "Asia/Kolkata",
    }


def test_explicit_interview_end_time_has_priority_over_default_duration():
    value = result(
        date="2026-07-29", time="09:45 AM", end_time="10:30 AM",
        timezone="Asia/Kolkata",
    )
    schedule = booking.normalized_schedule(
        value,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Kolkata")),
    )
    assert schedule["time"] == "09:45"
    assert schedule["time_end"] == "10:30"


@pytest.mark.parametrize(("field", "value", "code"), [
    ("duration_minutes", "not-a-number", "INVALID_DURATION"),
    ("duration_minutes", 0, "INVALID_DURATION"),
    ("end_time", "09:30 AM", "INVALID_END_TIME"),
])
def test_invalid_interview_end_values_are_rejected(field, value, code):
    details = {
        "date": "2026-07-29",
        "time": "09:45 AM",
        "timezone": "Asia/Kolkata",
        field: value,
    }
    with pytest.raises(booking.BookingValidationError) as exc:
        booking.normalized_schedule(
            result(**details),
            now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Kolkata")),
        )
    assert exc.value.code == code


def test_past_schedule_is_blocked():
    value = result(date="2026-07-14")
    with pytest.raises(booking.BookingValidationError, match="future"):
        booking.normalized_schedule(value, now=datetime(2026, 7, 15, tzinfo=ZoneInfo("Asia/Kolkata")))


# `manual` is the model's own requires_manual_review flag. It no longer vetoes
# a booking: it described the model's confidence in its reading, not the
# evidence, and it refused a Karat reminder at confidence 1.0 with a full
# schedule. Confidence and completeness still gate exactly as before.
@pytest.mark.parametrize(("confidence", "manual", "date_value", "allowed"), [
    (.95, False, "2026-07-20", True), (.85, False, "2026-07-20", True),
    (.79, False, "2026-07-20", False), (.95, True, "2026-07-20", True),
    (.85, False, "", False),
])
def test_confidence_and_completeness_gate(monkeypatch, confidence, manual, date_value, allowed):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    value = result(confidence=confidence, date=date_value)
    value["requires_manual_review"] = manual
    if allowed:
        booking.validate_ai_for_booking(value, "interview_confirmed")
    else:
        with pytest.raises(booking.BookingValidationError):
            booking.validate_ai_for_booking(value, "interview_confirmed")


@pytest.mark.parametrize(("source", "validation"), [("FALLBACK", "VALIDATED"), ("OLLAMA", "UNAVAILABLE"), ("FAILURE_REVIEW", "UNAVAILABLE")])
def test_only_validated_ollama_can_mutate_slots(monkeypatch, source, validation):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    value = result(); value["classification_source"] = source; value["ai_validation_status"] = validation
    with pytest.raises(booking.BookingValidationError, match="validated Ollama"):
        booking.validate_ai_for_booking(value, "interview_confirmed")


def test_trusted_authenticated_calendar_can_mutate_slots(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    value = result()
    value.update({
        "classification_source": "ICALENDAR_VERIFIED", "ai_validation_status": "NOT_REQUIRED",
        "calendar_validation_status": "TRUSTED", "validation_status": "VALIDATED",
    })
    booking.validate_ai_for_booking(value, "interview_confirmed")


def test_untrusted_calendar_cannot_mutate_slots(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    value = result()
    value.update({
        "classification_source": "ICALENDAR_VERIFIED", "ai_validation_status": "NOT_REQUIRED",
        "calendar_validation_status": "UNTRUSTED", "validation_status": "VALIDATED",
    })
    with pytest.raises(booking.BookingValidationError):
        booking.validate_ai_for_booking(value, "interview_confirmed")


def test_trusted_structured_email_can_mutate_slots(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    value=result()
    value.update({"classification_source":"STRUCTURED_EMAIL_VERIFIED",
                  "structured_validation_status":"TRUSTED","validation_status":"VALIDATED",
                  "ai_validation_status":"NOT_REQUIRED"})
    booking.validate_ai_for_booking(value,"interview_confirmed")


class FakeSlotStore:
    """A stand-in store that actually persists, the way the real one does.

    A fake whose booking call only returns a dict cannot tell a booking that was
    stored from one that was silently dropped — which is exactly the failure
    these tests now have to be able to see.
    """

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def writer(self, booking_id=None, *, capture=None, confirmed=True, **overrides):
        """Build a fake assign/update_interview_slot that stores what it books."""
        def _write(**kwargs):
            if capture is not None:
                capture.append(kwargs)
            row_id = str(booking_id or kwargs.get("candidate_id") or "slot1")
            row = {
                "id": row_id,
                **{key: value for key, value in kwargs.items() if key != "candidate_id"},
                "slot_confirmed": confirmed,
                **overrides,
            }
            row.setdefault("date", "")
            row.setdefault("time", "")
            row.setdefault("time_end", "")
            self.rows[row_id] = dict(row)
            return row
        return _write

    def assert_slot_persisted(self, candidate_id, *, date, time, time_end=""):
        row = self.rows.get(str(candidate_id))
        if not candidate_store.slot_row_matches(row, date=date, time=time, time_end=time_end):
            raise candidate_store.SlotNotPersistedError(
                candidate_id=str(candidate_id), date=date, time=time,
                time_end=time_end, stored=row,
            )
        return dict(row)


_SLOT_STORE: FakeSlotStore | None = None


def slot_writer(booking_id=None, **kwargs):
    """Fake booking call that persists, so the store invariant can verify it."""
    assert _SLOT_STORE is not None, "install_store_fakes must run first"
    return _SLOT_STORE.writer(booking_id, **kwargs)


def install_store_fakes(monkeypatch, *, rows=None, payment_reason=None, conflicts=None):
    global _SLOT_STORE
    _SLOT_STORE = FakeSlotStore()
    candidate = {"id": "c1", "name": "Rahul", "reference": "Owner", "payment": 10000, "expected_payment": 20000, "service_type": "profile_service"}
    monkeypatch.setattr(booking.mail_store, "record_interview_analysis", lambda **kwargs: {"id": "ia1"})
    monkeypatch.setattr(booking.mail_store, "booking_audit_for_message", lambda *args, **kwargs: None)
    audits = []
    monkeypatch.setattr(booking.mail_store, "record_booking_audit", lambda **kwargs: audits.append(kwargs) or {"id": "audit1", **kwargs})
    monkeypatch.setattr(booking.mail_store, "attach_booking_to_notification", lambda *args, **kwargs: {"id": "n1", **kwargs})
    monkeypatch.setattr(booking.mail_store, "audit", lambda **kwargs: None)
    monkeypatch.setattr(booking.candidate_store, "get_candidate", lambda _cid: candidate)
    monkeypatch.setattr(booking.candidate_store, "candidate_identity_ids", lambda _cid, **kwargs: [
        "c1", *[str(row['id']) for row in (rows or []) if row.get('name') == 'Rahul']])
    monkeypatch.setattr(booking.candidate_store, "list_candidates", lambda **kwargs: list(rows or []))
    # `_candidate_slots` reads the stored rows now rather than the collapsed
    # display list, because the collapse returns one row per person and hid 117
    # confirmed bookings from the gates. The fake has to hold the same rows on
    # both paths or these tests exercise an empty store.
    monkeypatch.setattr(booking.candidate_store, "_load", lambda **kwargs: {"candidates": list(rows or [])})
    monkeypatch.setattr(booking.candidate_store, "_with_computed", lambda item: dict(item))
    monkeypatch.setattr(booking.candidate_store, "slot_confirm_block_reason", lambda _row: payment_reason)
    monkeypatch.setattr(booking.candidate_store, "find_interview_slot_conflicts", lambda *args, **kwargs: list(conflicts or []))
    monkeypatch.setattr(booking.candidate_store, "attach_public_slot_screenshot", lambda *args, **kwargs: {"id": "proof1"})
    monkeypatch.setattr(booking.candidate_store, "assert_slot_persisted", _SLOT_STORE.assert_slot_persisted)
    return candidate, audits


def execute(value):
    return booking.execute_auto_booking(
        mailbox={"id": "mb1", "candidate_id": "c1", "email_address": "candidate@test.invalid"},
        message={"provider_message_id": "gm1", "provider_thread_id": "gt1"},
        event={"mailbox_message_id": "mm1", "notification": {"id": "n1", "email_analysis_id": "ma1"}},
        result=value, correlation_id="corr1",
    )


def execute_event(value, *, message_id, thread_id="gt1", notification_id="n1"):
    return booking.execute_auto_booking(
        mailbox={"id": "mb1", "candidate_id": "c1", "email_address": "candidate@test.invalid"},
        message={"provider_message_id": message_id, "provider_thread_id": thread_id},
        event={"mailbox_message_id": f"mm-{message_id}", "notification": {"id": notification_id, "email_analysis_id": "ma1"}},
        result=value, correlation_id=f"corr-{message_id}",
    )


def execute_manual(value):
    return booking.execute_manual_approved_booking(
        mailbox={"id": "mb1", "candidate_id": "c1", "email_address": "candidate@test.invalid"},
        message={"provider_message_id": "gm1", "provider_thread_id": "gt1"},
        event={"mailbox_message_id": "mm1", "notification": {"id": "n1", "email_analysis_id": "ma1"}},
        result=value, reviewer="admin@test", correlation_id="manual1",
    )


def test_valid_confirmed_interview_books_without_approval(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _candidate, audits = install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", slot_writer("slot1"))
    outcome = execute(result())
    assert outcome["status"] == "Auto Booked"
    assert outcome["booking"]["time"] == "15:00"
    assert outcome["booking"]["interview_booking_source"] == "ai_auto_booked"
    assert outcome["notification"]["schedule"]["time"] == "15:00"
    assert outcome["notification"]["schedule"]["source_timezone"] == "Asia/Kolkata"
    assert audits[-1]["auto_booked"] is True


def test_auto_booking_projects_decision_then_verified_persisted_outcome(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _candidate, _audits = install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", slot_writer("slot1"))
    transitions = []
    monkeypatch.setattr(
        booking.mail_store, "record_automation_state",
        lambda **kwargs: transitions.append(kwargs),
    )

    outcome = execute(result())

    assert outcome["automation_state"] == "AUTO_BOOKED"
    assert [row["state"] for row in transitions] == ["AUTO_BOOK", "AUTO_BOOKED"]
    assert transitions[-1]["booking_id"] == "slot1"


def test_auto_booking_uses_the_canonical_mailbox_candidate_identity(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _candidate, audits = install_store_fakes(monkeypatch)
    monkeypatch.setattr(
        booking.mail_store, "canonical_candidate_id",
        lambda candidate_id: "canonical-candidate" if candidate_id == "c1" else candidate_id,
    )
    canonical = {
        "id": "canonical-candidate", "name": "Rahul", "reference": "Owner",
        "payment": 10000, "expected_payment": 20000, "service_type": "profile_service",
    }
    monkeypatch.setattr(booking.candidate_store, "get_candidate", lambda candidate_id: canonical if candidate_id == "canonical-candidate" else None)
    monkeypatch.setattr(booking.candidate_store, "candidate_identity_ids", lambda _candidate_id, **kwargs: ["canonical-candidate"])
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", slot_writer("slot1"))

    outcome = execute(result())

    assert outcome["status"] == "Auto Booked"
    assert audits[-1]["candidate_id"] == "canonical-candidate"


def test_final_auto_book_decision_supersedes_a_stale_review_flag(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _candidate, audits = install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", slot_writer("slot1"))
    value = result()
    value.update(requires_manual_review=True, automation_decision="AUTO_BOOK")

    outcome = execute(value)

    assert outcome["status"] == "Auto Booked"
    assert audits[-1]["auto_booked"] is True


@pytest.mark.parametrize(
    ("decision", "expected_state"),
    [("AUTO_IGNORE", "AUTO_IGNORE"), ("AI_RETRY_PENDING", "AI_RETRY_PENDING")],
)
def test_non_booking_final_decision_never_creates_a_slot(monkeypatch, decision, expected_state):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _candidate, audits = install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", lambda **_kwargs: pytest.fail("must not book"))
    value = result()
    value["automation_decision"] = decision

    outcome = execute(value)

    assert outcome["status"] == "Blocked"
    assert outcome["failure_code"] == "NOT_ACTIONABLE"
    assert outcome["automation_state"] == expected_state
    assert audits[-1]["auto_booked"] is False


def test_invalid_round_text_is_not_saved_as_a_round(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", slot_writer("slot1"))
    value = result(round="Interview Invite: Candidate | Candidate ID: 123")

    outcome = execute(value)

    assert outcome["booking"]["interview_round"] == ""
    assert value["interview"]["round"] is None


def test_auto_booking_attaches_generated_email_evidence_to_exact_slot(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", slot_writer("slot1"))
    attached = {}

    def capture(candidate_id, **kwargs):
        attached.update(candidate_id=candidate_id, **kwargs)
        return {"id": "proof-evidence", "url": "/candidates/slot1/proofs/proof-evidence"}

    monkeypatch.setattr(booking.candidate_store, "attach_public_slot_screenshot", capture)
    outcome = execute(result())
    assert outcome["status"] == "Auto Booked"
    assert outcome["evidence_snapshot"]["id"] == "proof-evidence"
    assert attached["candidate_id"] == "slot1"
    assert attached["mime_type"] == "image/png"
    assert attached["source"] == "AI Mail Monitoring"
    assert attached["data"].startswith(b"\x89PNG\r\n\x1a\n")


def test_evidence_snapshot_failure_never_rolls_back_valid_booking(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _candidate, audits = install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", slot_writer("slot1"))
    monkeypatch.setattr(
        booking.candidate_store,
        "attach_public_slot_screenshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("storage unavailable")),
    )
    outcome = execute(result())
    assert outcome["status"] == "Auto Booked"
    assert outcome["booking"]["id"] == "slot1"
    assert outcome["evidence_snapshot"] is None
    assert audits[-1]["auto_booked"] is True


def test_manual_approval_books_fallback_interview_through_safety_checks(monkeypatch):
    _candidate, audits = install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", slot_writer("slot1"))
    value = result(date="2099-07-21", time="12:30 PM")
    value.update({"classification_source": "FALLBACK", "ai_validation_status": "UNAVAILABLE", "requires_manual_review": True})
    value["evidence"] = [{"meaning": "INTERVIEW_CONFIRMED", "text": "Join on 21 July at 12:30 PM"}]
    outcome = execute_manual(value)
    assert outcome["status"] == "Approved & Booked"
    assert outcome["booking"]["time"] == "12:30"
    assert outcome["booking"]["interview_booking_source"] == "candidate_booked"
    assert audits[-1]["validation_status"] == "MANUAL_APPROVED"
    assert audits[-1]["auto_booked"] is True


def test_legacy_automatic_booking_note_gets_daily_ops_source_label():
    row = {
        "slot_confirmed": True,
        "notes": "Automatically booked from validated interview email (AI Mail Monitoring).",
    }
    assert booking.candidate_store.interview_booking_source(row) == "ai_auto_booked"


def test_unmarked_confirmed_slot_gets_candidate_booked_source_label():
    assert booking.candidate_store.interview_booking_source({"slot_confirmed": True}) == "candidate_booked"


def test_manual_approval_never_books_past_interview(monkeypatch):
    _candidate, audits = install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", lambda **kwargs: pytest.fail("past interview must not book"))
    value = result(date="2000-01-01", time="12:30 PM")
    value["evidence"] = [{"meaning": "INTERVIEW_CONFIRMED", "text": "Past interview"}]
    outcome = execute_manual(value)
    assert outcome["status"] == "Blocked"
    assert outcome["failure_code"] == "PAST_INTERVIEW"
    assert audits[-1]["auto_booked"] is False


def test_manual_approval_requires_source_email_evidence(monkeypatch):
    install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", lambda **kwargs: pytest.fail("missing evidence must not book"))
    value = result(date="2099-07-21")
    value["evidence"] = []
    outcome = execute_manual(value)
    assert outcome["failure_code"] == "MISSING_EVIDENCE"


def test_payment_failure_creates_blocked_audit_without_booking(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _candidate, audits = install_store_fakes(monkeypatch, payment_reason="Record at least payment received")
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", lambda **kwargs: pytest.fail("must not book"))
    outcome = execute(result())
    assert outcome["failure_code"] == "PAYMENT_VALIDATION_FAILED"
    assert audits[-1]["payment_status"] == "BLOCKED"


def test_duplicate_booking_is_blocked(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    row = {
        "id": "c1", "name": "Rahul", "slot_confirmed": True,
        "date": "2099-07-20", "time": "15:00",
        "interview_source_message_id": "gm1",
    }
    install_store_fakes(monkeypatch, rows=[row])
    outcome = execute(result())
    assert outcome["failure_code"] == "DUPLICATE_BOOKING"
    assert outcome["status"] == "Duplicate Ignored"
    assert outcome["event_type"] == "duplicate_booking_ignored"


def test_past_historical_interview_is_skipped_before_ai_gate(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _candidate, audits = install_store_fakes(monkeypatch)
    value = result(date="2000-07-07")
    value.update({
        "_historical_reprocess": True,
        "classification_source": "FALLBACK",
        "ai_validation_status": "UNAVAILABLE",
        "requires_manual_review": True,
    })
    outcome = execute(value)
    assert outcome["status"] == "Historical Skipped"
    assert outcome["event_type"] == "historical_interview_skipped"
    assert outcome["failure_code"] == "PAST_INTERVIEW"
    assert audits[-1]["validation_status"] == "SKIPPED"
    assert audits[-1]["payment_status"] == "NOT_CHECKED"


def test_past_historical_interview_does_not_need_user_notification():
    value = result(date="2000-07-07")
    value["_historical_reprocess"] = True

    assert booking.should_suppress_historical_notification(
        value, "interview_confirmed",
    ) is True


def test_future_historical_interview_remains_actionable():
    value = result(date="2099-07-07")
    value["_historical_reprocess"] = True

    assert booking.should_suppress_historical_notification(
        value, "interview_confirmed",
    ) is False


def test_incomplete_historical_interview_is_review_only(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _candidate, audits = install_store_fakes(monkeypatch)
    value = result(date="", timezone="")
    value.update({
        "_historical_reprocess": True,
        "classification_source": "FALLBACK",
        "ai_validation_status": "UNAVAILABLE",
        "requires_manual_review": True,
    })
    outcome = execute(value)
    assert outcome["status"] == "Review Required"
    assert outcome["event_type"] == "historical_interview_review_required"
    assert outcome["failure_code"] == "HISTORICAL_SCHEDULE_INCOMPLETE"
    assert audits[-1]["validation_status"] == "REVIEW_REQUIRED"


def test_future_historical_interview_still_uses_live_safety_gates(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)
    value = result(date="2099-07-07")
    value.update({
        "_historical_reprocess": True,
        "classification_source": "FALLBACK",
        "ai_validation_status": "UNAVAILABLE",
        "requires_manual_review": True,
    })
    outcome = execute(value)
    assert outcome["status"] == "Blocked"
    assert outcome["failure_code"] == "AI_NOT_VALIDATED"


def test_different_interview_overlap_is_allowed(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch, conflicts=[{"id": "other", "name": "Other"}])
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", slot_writer("slot1"))
    outcome = execute(result())
    assert outcome["status"] == "Auto Booked"
    assert outcome["audit"]["conflict_status"] == "NOT_REQUIRED"


def test_candidate_email_mismatch_is_blocked(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)
    value = result(); value["candidate"]["email"] = "someone-else@test.invalid"
    assert execute(value)["failure_code"] == "CANDIDATE_MAPPING_FAILED"


def test_reschedule_updates_existing_booking_and_preserves_previous(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    old = {"id": "slot1", "name": "Rahul", "slot_confirmed": True, "date": "2026-07-19", "time": "14:00"}
    _candidate, audits = install_store_fakes(monkeypatch, rows=[old])
    monkeypatch.setattr(booking.candidate_store, "update_interview_slot", slot_writer("slot1"))
    outcome = execute(result("interview_rescheduled"))
    assert outcome["status"] == "Rescheduled"
    assert audits[-1]["previous_booking"]["date"] == "2026-07-19"


def test_cancellation_keeps_audit_and_does_not_touch_payment(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    old = {"id": "slot1", "name": "Rahul", "slot_confirmed": True, "date": "2026-07-19", "time": "14:00"}
    _candidate, audits = install_store_fakes(monkeypatch, rows=[old], payment_reason="must not be evaluated")
    monkeypatch.setattr(booking.candidate_store, "cancel_interview_slot", lambda **kwargs: {"id": "slot1", "slot_confirmed": False})
    outcome = execute(result("interview_cancelled", date=None, time=None, timezone=None))
    assert outcome["status"] == "Cancelled"
    assert audits[-1]["payment_status"] == "NOT_REQUIRED"


def test_reschedule_resolves_correct_slot_from_thread_not_first_row(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    wrong = {"id": "slot1", "name": "Rahul", "slot_confirmed": True, "date": "2026-07-19", "time": "14:00", "interview_source_thread_id": "other"}
    target = {"id": "slot2", "name": "Rahul", "slot_confirmed": True, "date": "2026-07-20", "time": "15:00", "interview_source_thread_id": "gt1"}
    _candidate, audits = install_store_fakes(monkeypatch, rows=[wrong, target])
    monkeypatch.setattr(booking.candidate_store, "update_interview_slot", slot_writer())
    outcome = execute(result("interview_rescheduled", date="2099-07-21", time="11:00 AM"))
    assert outcome["status"] == "Rescheduled"
    assert outcome["booking"]["id"] == "slot2"
    assert audits[-1]["previous_booking"]["id"] == "slot2"


def test_cancellation_with_multiple_unidentified_slots_is_blocked(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    rows = [
        {"id": "slot1", "name": "Rahul", "slot_confirmed": True, "date": "2026-07-19", "time": "14:00"},
        {"id": "slot2", "name": "Rahul", "slot_confirmed": True, "date": "2026-07-20", "time": "15:00"},
    ]
    install_store_fakes(monkeypatch, rows=rows)
    value = result("interview_cancelled", date=None, time=None, timezone=None, round=None)
    value["company"]["name"] = None
    value["job"]["title"] = None
    outcome = execute(value)
    assert outcome["failure_code"] == "BOOKING_AMBIGUOUS"


def test_cancellation_resolves_new_gmail_thread_by_stable_requisition_id(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    wrong = {
        "id": "slot-l2", "name": "Rahul", "slot_confirmed": True,
        "date": "2099-07-28", "time": "14:00", "interview_round": "L2",
        "interview_role": "Another interview",
    }
    target = {
        "id": "slot-l1", "name": "Rahul", "slot_confirmed": True,
        "date": "2099-07-31", "time": "14:00", "interview_round": "L1",
        "interview_role": "L1-CGEMJP00347400-React UI Developer-Rahul",
        "interview_source_thread_id": "old-gmail-thread",
    }
    _candidate, audits = install_store_fakes(monkeypatch, rows=[wrong, target])
    monkeypatch.setattr(
        booking.candidate_store,
        "cancel_interview_slot",
        lambda **kwargs: {"id": kwargs["candidate_id"], "slot_confirmed": False},
    )
    value = result(
        "interview_cancelled", date=None, time=None, timezone=None, round="L1",
    )
    value.update({
        "classification_source": "STRUCTURED_EMAIL_VERIFIED",
        "structured_validation_status": "TRUSTED",
        "validation_status": "VALIDATED",
        "ai_validation_status": "NOT_REQUIRED",
    })
    value["job"]["title"] = "L1-CGEMJP00347400-React UI Developer-Rahul"

    outcome = booking.execute_auto_booking(
        mailbox={
            "id": "mb1", "candidate_id": "c1",
            "email_address": "candidate@test.invalid",
        },
        message={
            "provider_message_id": "cancel-message",
            "provider_thread_id": "new-gmail-thread",
            "subject": "Canceled: L1-CGEMJP00347400-React UI Developer-Rahul",
            "body": "The interview was cancelled.",
        },
        event={
            "mailbox_message_id": "mm1",
            "notification": {"id": "n1", "email_analysis_id": "ma1"},
        },
        result=value,
        correlation_id="cancel-corr",
    )

    assert outcome["status"] == "Cancelled"
    assert outcome["booking"]["id"] == "slot-l1"
    assert audits[-1]["previous_booking"]["id"] == "slot-l1"


def test_duplicate_gmail_message_does_not_mutate_booking_twice(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.mail_store, "booking_audit_for_message", lambda *args: {"id": "audit1", "auto_booked": True, "booking_id": "slot1", "booking_status": "Auto Booked"})
    monkeypatch.setattr(booking.candidate_store, "assert_slot_persisted", lambda *_args, **_kwargs: {"id": "slot1", "slot_confirmed": True, "date": "2099-07-20", "time": "15:00", "time_end": "15:30"})
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", lambda **kwargs: pytest.fail("must not book twice"))
    outcome = execute(result())
    assert outcome["duplicate"] is True


def test_persisted_lifecycle_retry_does_not_trust_a_previous_failed_audit(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_AUTO_BOOKING_ENABLED', 'true')
    _, audits = install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.mail_store, 'booking_audit_for_message', lambda *args: {
        'id': 'failed', 'auto_booked': False, 'booking_id': None, 'booking_status': 'Processing Failed',
    })
    incoming = booking.interview_lifecycle.LifecycleEvent.from_payload('c1', result(), {'provider_message_id': 'gm1'})
    claim = booking.interview_lifecycle.LifecycleClaim(
        booking.interview_lifecycle.TransitionDecision.IDEMPOTENT, incoming, 'key', 'slot1', 'APPLIED')
    monkeypatch.setattr(booking.interview_lifecycle, 'claim', lambda *args: claim)
    monkeypatch.setattr(booking.candidate_store, 'assert_slot_persisted', lambda *args, **kwargs: {'id': 'slot1', 'slot_confirmed': True})
    monkeypatch.setattr(booking.candidate_store, 'assign_interview_slot', lambda **kwargs: pytest.fail('retry booked twice'))
    outcome = execute(result())
    assert outcome['status'] == 'Auto Booked'
    assert audits[-1]['auto_booked'] and audits[-1]['booking_id'] == 'slot1'
    assert audits[-1]['lifecycle_transition_key'] == incoming.idempotency_key


def test_past_replay_does_not_repoint_a_successful_notification(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_AUTO_BOOKING_ENABLED', 'true')
    _, audits = install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.mail_store, 'booking_audit_for_message', lambda *args: {
        'id': 'original', 'auto_booked': True, 'booking_id': 'slot1', 'booking_status': 'Auto Booked',
    })
    monkeypatch.setattr(booking.mail_store, 'attach_booking_to_notification', lambda *args, **kwargs: pytest.fail('repointed original alert'))
    value = result(date='2020-01-01')
    value['_historical_reprocess'] = True
    outcome = execute(value)
    assert outcome['status'] == 'Historical Skipped'
    assert audits[-1]['booking_id'] is None and not audits[-1]['auto_booked']


def test_lifecycle_stale_replay_cannot_mutate_a_newer_interview(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)
    incoming = booking.interview_lifecycle.LifecycleEvent.from_payload(
        "c1", result(), {"provider_message_id": "old", "provider_thread_id": "gt1"},
    )
    monkeypatch.setattr(
        booking.interview_lifecycle, "claim",
        lambda *_args, **_kwargs: booking.interview_lifecycle.LifecycleClaim(
            booking.interview_lifecycle.TransitionDecision.STOP_STALE, incoming, "lifecycle-1",
        ),
    )
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", lambda **_kwargs: pytest.fail("stale replay must not assign"))

    outcome = execute(result())

    assert outcome["status"] == "Blocked"
    assert outcome["failure_code"] == "STALE_INTERVIEW_EVENT"


def test_pending_lifecycle_retry_does_not_create_a_second_slot(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    existing = {
        "id": "slot1", "name": "Rahul", "slot_confirmed": True,
        "date": "2099-07-20", "time": "15:00", "time_end": "15:30",
        "interview_source_message_id": "gm1",
    }
    install_store_fakes(monkeypatch, rows=[existing])
    incoming = booking.interview_lifecycle.LifecycleEvent.from_payload(
        "c1", result(), {"provider_message_id": "gm1", "provider_thread_id": "gt1"},
    )
    monkeypatch.setattr(
        booking.interview_lifecycle, "claim",
        lambda *_args, **_kwargs: booking.interview_lifecycle.LifecycleClaim(
            booking.interview_lifecycle.TransitionDecision.IDEMPOTENT, incoming, "lifecycle-1", "", "PENDING",
        ),
    )
    monkeypatch.setattr(
        booking.candidate_store, "assert_slot_persisted", lambda *_args, **_kwargs: dict(existing),
    )
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", lambda **_kwargs: pytest.fail("retry must not create a second slot"))

    outcome = execute(result())

    assert outcome["status"] == "Auto Booked"
    assert outcome["booking"]["id"] == "slot1"


def test_retry_recovers_persisted_slot_before_audit_without_assigning_again(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    existing = {
        "id": "slot1", "name": "Rahul", "slot_confirmed": True,
        "date": "2099-07-20", "time": "15:00", "time_end": "15:30",
        "interview_source_message_id": "gm1",
    }
    _candidate, audits = install_store_fakes(monkeypatch, rows=[existing])
    incoming = booking.interview_lifecycle.LifecycleEvent.from_payload(
        "c1", result(), {"provider_message_id": "gm1", "provider_thread_id": "gt1"},
    )
    claim = booking.interview_lifecycle.LifecycleClaim(
        booking.interview_lifecycle.TransitionDecision.IDEMPOTENT, incoming, "lifecycle-1", "", "PENDING",
    )
    monkeypatch.setattr(booking.interview_lifecycle, "claim", lambda *_args, **_kwargs: claim)
    monkeypatch.setattr(booking.interview_lifecycle, "mark_applied", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(booking.candidate_store, "assert_slot_persisted", lambda *_args, **_kwargs: dict(existing))
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", lambda **_kwargs: pytest.fail("crash retry must recover, not assign"))

    outcome = execute(result())

    assert outcome["status"] == "Auto Booked"
    assert outcome["booking"]["id"] == "slot1"
    assert audits[-1]["booking_id"] == "slot1"


# ── the blocked row must carry why ──────────────────────────────────────────

def test_a_blocked_booking_tells_the_notification_why(monkeypatch):
    """The reason reaching the UI is the one the validator decided, not a
    guess reconstructed from the status text."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch, payment_reason="Record at least payment received")

    outcome = execute(result())

    assert outcome["status"] == "Blocked"
    assert outcome["failure_code"] == "PAYMENT_VALIDATION_FAILED"
    assert outcome["block_reason"]["reason_code"] == "PAYMENT_NOT_CLEARED"
    reason = outcome["notification"]["block_reason"]
    # The validator said which requirement failed; the words say the same.
    assert reason["reason"] == (
        "The candidate hasn't paid enough yet for a booking, so no booking was created."
    )
    assert reason["reason_code"] == "PAYMENT_NOT_CLEARED"
    # The exact validator branch survives alongside the operator-facing text.
    assert reason["internal_code"] == "PAYMENT_VALIDATION_FAILED"


def test_a_duplicate_booking_says_it_is_already_booked(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch, rows=[{
        "id": "c1", "slot_confirmed": True,
        "date": "2099-07-20", "time": "15:00",
        "interview_source_message_id": "gm1",
    }])

    outcome = execute(result())

    assert outcome["failure_code"] == "DUPLICATE_BOOKING"
    assert outcome["notification"]["block_reason"]["reason_code"] == "DUPLICATE_BOOKING"
    # Duplicates are found by the calendar event or source mail, never by the
    # round, so the words no longer claim a round.
    assert "is already booked, so it was not booked again" in (
        outcome["notification"]["block_reason"]["reason"]
    )


def test_a_low_confidence_block_reads_as_confidence_not_as_a_schedule_problem(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)

    outcome = execute(result(confidence=.10))

    assert outcome["failure_code"] == "LOW_CONFIDENCE"
    assert outcome["notification"]["block_reason"] == {
        "reason_code": "LOW_CONFIDENCE",
        "reason": (
            "We weren't sure enough about this email to act on it, so no booking was created."
        ),
        "internal_code": "LOW_CONFIDENCE",
    }


def test_a_past_interview_block_names_the_date_that_already_passed(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)

    outcome = execute(result(date="2020-01-02", time="09:30 AM"))

    assert outcome["failure_code"] == "PAST_INTERVIEW"
    assert outcome["notification"]["block_reason"]["reason"] == (
        "This interview time (2 Jan 2020, 9:30 AM) has already passed, so no booking was created."
    )


def test_an_unparseable_schedule_reads_as_a_missing_date_or_time(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)

    outcome = execute(result(date="20th July"))

    assert outcome["failure_code"] == "INVALID_DATE"
    assert outcome["notification"]["block_reason"]["reason_code"] == "MISSING_DATE_TIME"


def test_an_unknown_candidate_reads_as_a_candidate_problem(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.candidate_store, "get_candidate", lambda _cid: None)

    outcome = execute(result())

    assert outcome["failure_code"] == "CANDIDATE_MAPPING_FAILED"
    assert outcome["notification"]["block_reason"]["reason_code"] == "CANDIDATE_NOT_FOUND"


def test_a_payment_block_is_not_reported_as_a_scheduling_problem(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch, payment_reason="Balance pending")

    outcome = execute(result())

    assert outcome["failure_code"] == "PAYMENT_VALIDATION_FAILED"
    assert outcome["notification"]["block_reason"]["reason_code"] == "PAYMENT_NOT_CLEARED"


def test_a_successful_booking_carries_no_blocking_reason(monkeypatch):
    """A row that later books must not keep showing why it once failed."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)
    monkeypatch.setattr(
        booking.candidate_store, "assign_interview_slot",
        slot_writer("slot1"),
    )

    outcome = execute(result())

    assert outcome["status"] == "Auto Booked"
    assert "block_reason" not in outcome["notification"]


# ── an organiser moving the meeting ─────────────────────────────────────────
#
# Reproduces the Capgemini invite that reached Production: one calendar UID
# sent three times as SEQUENCE 0 (15:30), 2 (13:00) and 4 (11:30). Only the
# first was booked; the second fought the first for a slot and was blocked;
# the third never got read at all.

CAL_UID = "040000008200E00074C5B7101A82E00800000000E0C705AD6123DD01"


def revision(sequence, time, **over):
    value = result(time=time, **over)
    value["calendar"] = {"uid": CAL_UID, "sequence": sequence, "method": "REQUEST"}
    return value


def booked_slot(time, sequence, **over):
    row = {
        "id": "c1", "slot_confirmed": True, "date": "2099-07-20", "time": time,
        "interview_round": "L1", "interview_calendar_uid": CAL_UID,
        "interview_calendar_sequence": str(sequence),
        "interview_source_thread_id": "gt1",
    }
    row.update(over)
    return row


def test_a_later_revision_moves_the_booking_instead_of_making_a_second_one(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch, rows=[booked_slot("15:30", 0)])
    updates = []
    monkeypatch.setattr(
        booking.candidate_store, "update_interview_slot",
        slot_writer("c1", capture=updates),
    )
    monkeypatch.setattr(
        booking.candidate_store, "assign_interview_slot",
        lambda **kwargs: pytest.fail("a revision must not create a second booking"),
    )

    outcome = execute(revision(4, "11:30 AM"))

    assert outcome["status"] == "Rescheduled"
    assert updates[-1]["time"] == "11:30"
    assert updates[-1]["time_end"] == "12:00"
    assert updates[-1]["candidate_id"] == "c1"          # same candidate
    assert updates[-1]["interview_round"] == "L1"       # same round


def test_the_calendar_identity_is_carried_onto_the_booking(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)
    saved = []
    monkeypatch.setattr(
        booking.candidate_store, "assign_interview_slot",
        slot_writer("slot1", capture=saved),
    )

    execute(revision(0, "03:30 PM"))

    assert saved[-1]["interview_calendar_uid"] == CAL_UID
    assert saved[-1]["interview_calendar_sequence"] == "0"


def test_each_revision_in_turn_lands_on_the_final_time(monkeypatch):
    """0 -> 15:30 books; 2 -> 13:00 moves it; 4 -> 11:30 moves it again."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    state = {"time": None, "sequence": None}
    rows = []

    def _assign(**kwargs):
        state.update(time=kwargs["time"], sequence=kwargs["interview_calendar_sequence"])
        rows.append(booked_slot(kwargs["time"], kwargs["interview_calendar_sequence"]))
        return slot_writer("c1")(**kwargs)

    def _update(**kwargs):
        state.update(time=kwargs["time"], sequence=kwargs["interview_calendar_sequence"])
        rows[:] = [booked_slot(kwargs["time"], kwargs["interview_calendar_sequence"])]
        return slot_writer("c1")(**kwargs)

    install_store_fakes(monkeypatch, rows=rows)
    monkeypatch.setattr(booking.candidate_store, "list_candidates", lambda **kw: list(rows))
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", _assign)
    monkeypatch.setattr(booking.candidate_store, "update_interview_slot", _update)

    assert execute(revision(0, "03:30 PM"))["status"] == "Auto Booked"
    assert state["time"] == "15:30"
    assert execute(revision(2, "01:00 PM"))["status"] == "Rescheduled"
    assert state["time"] == "13:00"
    assert execute(revision(4, "11:30 AM"))["status"] == "Rescheduled"
    assert state["time"] == "11:30"
    # One booking throughout, never two.
    assert len(rows) == 1


def test_a_revision_allows_another_interview_to_overlap(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(
        monkeypatch, rows=[booked_slot("15:30", 0)], conflicts=[{"id": "someone-else"}],
    )
    monkeypatch.setattr(booking.candidate_store, "update_interview_slot", slot_writer("c1"))

    outcome = execute(revision(2, "01:00 PM"))

    assert outcome["status"] == "Rescheduled"
    assert outcome["audit"]["conflict_status"] == "NOT_REQUIRED"


def test_an_unrelated_calendar_event_is_still_a_new_booking(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(
        monkeypatch,
        rows=[booked_slot("15:30", 0, interview_calendar_uid="some-other-event")],
    )
    saved = []
    monkeypatch.setattr(
        booking.candidate_store, "assign_interview_slot",
        slot_writer("slot2", capture=saved),
    )

    assert execute(revision(1, "11:30 AM"))["status"] == "Auto Booked"
    assert saved, "a different event must still create its own booking"


def test_different_calendar_invites_can_share_the_exact_same_time(monkeypatch):
    """Nitin-style parallel invites must not block each other by time."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    rows = []
    _candidate, audits = install_store_fakes(monkeypatch, rows=rows)
    monkeypatch.setattr(booking.candidate_store, "list_candidates", lambda **_kwargs: list(rows))

    def _assign(**kwargs):
        booking_id = f"slot-{len(rows) + 1}"
        stored = slot_writer(booking_id)(**kwargs)
        rows.append(stored)
        return stored

    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", _assign)
    first = result()
    first["calendar"] = {"uid": "nitin-invite-one", "sequence": 0}
    second = result()
    second["calendar"] = {"uid": "nitin-invite-two", "sequence": 0}

    one = execute_event(first, message_id="nitin-gm-1", thread_id="nitin-thread-1")
    two = execute_event(second, message_id="nitin-gm-2", thread_id="nitin-thread-2")

    assert one["status"] == two["status"] == "Auto Booked"
    assert [row["interview_calendar_uid"] for row in rows] == [
        "nitin-invite-one", "nitin-invite-two",
    ]
    assert {(row["date"], row["time"], row["time_end"]) for row in rows} == {
        ("2099-07-20", "15:00", "15:30"),
    }
    assert [audit["conflict_status"] for audit in audits] == ["NOT_REQUIRED", "NOT_REQUIRED"]


def test_an_invite_without_a_calendar_uid_behaves_exactly_as_before(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)
    saved = []
    monkeypatch.setattr(
        booking.candidate_store, "assign_interview_slot",
        slot_writer("slot1", capture=saved),
    )

    assert execute(result())["status"] == "Auto Booked"
    assert saved[-1]["interview_calendar_uid"] == ""


def test_booking_metadata_is_accepted_by_the_real_slot_functions():
    """Every booking splats _booking_metadata into the candidate store. The
    other tests here mock those functions, so a key the store does not accept
    passes them and still raises TypeError in Production on every booking."""
    import inspect

    from features import candidate_store

    meta = booking._booking_metadata(
        {"calendar": {"uid": "UID-1", "sequence": 4}}, {}, {},
    )
    for name in ("assign_interview_slot", "update_interview_slot"):
        parameters = inspect.signature(getattr(candidate_store, name)).parameters
        unsupported = sorted(set(meta) - set(parameters))
        assert not unsupported, f"{name} cannot accept {unsupported}"


def test_the_calendar_identity_actually_persists(tmp_path, monkeypatch):
    """Storing a field the record normaliser drops would leave every revision
    unable to find the booking it supersedes."""
    from features import candidate_store

    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    row = candidate_store.create_candidate({"name": "Slot Owner", "phone": "9000000001"})
    candidate_store.assign_interview_slot(
        candidate_id=row["id"], date="2099-07-20", time="15:30", time_end="16:00",
        interview_round="L1", interview_calendar_uid="UID-1",
        interview_calendar_sequence="0",
    )
    stored = candidate_store.get_candidate(row["id"])
    assert stored["interview_calendar_uid"] == "UID-1"
    assert stored["interview_calendar_sequence"] == "0"

    candidate_store.update_interview_slot(
        candidate_id=row["id"], date="2099-07-20", time="11:30", time_end="12:00",
        interview_calendar_sequence="4",
    )
    moved = candidate_store.get_candidate(row["id"])
    assert moved["time"] == "11:30"
    assert moved["interview_calendar_uid"] == "UID-1"   # identity survives the move
    assert moved["interview_calendar_sequence"] == "4"


def test_a_second_slot_for_one_candidate_keeps_the_calendar_identity(tmp_path, monkeypatch):
    """Booking a candidate who already has a confirmed slot clones the record.
    If the clone drops the calendar identity, a revision cannot find it and a
    replay books the same event twice."""
    from features import candidate_store

    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    row = candidate_store.create_candidate({"name": "Two Slots", "phone": "9000000002"})
    candidate_store.assign_interview_slot(
        candidate_id=row["id"], date="2099-07-20", time="13:00", time_end="13:30",
        interview_round="L1", interview_calendar_uid="UID-FIRST",
        interview_calendar_sequence="0",
    )
    second = candidate_store.assign_interview_slot(
        candidate_id=row["id"], date="2099-07-21", time="17:00", time_end="18:00",
        interview_round="L2", interview_calendar_uid="UID-SECOND",
        interview_calendar_sequence="0",
    )
    assert second["id"] != row["id"], "a second slot clones the candidate record"
    assert second["interview_calendar_uid"] == "UID-SECOND"
    stored = candidate_store.get_candidate(second["id"])
    assert stored["interview_calendar_uid"] == "UID-SECOND"


CAPGEMINI_ATS_UID = "CDVCAPGB@50250631444abd548e03c35cff206722@ca467375-9795-49c7-874a-d3950f3fd624"
RECRUITER_INVITE_UID = "040000008200E00074C5B7101A82E0080000000060B80A34D724DD01000000000000000010000000096F06FC596A12409713ABBDB5CE1ADC"


def test_cancellation_never_lands_on_the_only_slot_of_a_different_calendar_event(monkeypatch):
    """Production incident 2026-08-05: Capgemini's ATS cancelled its own 3:30 PM
    event, but that slot had already left the confirmed list, so the lone
    remaining slot — an unrelated 5:30 PM invite from the recruiter's own
    calendar — was cancelled instead."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    unrelated = {
        "id": "slot-530", "name": "Rahul", "slot_confirmed": True,
        "date": "2099-08-06", "time": "17:30", "time_end": "18:00",
        "interview_calendar_uid": RECRUITER_INVITE_UID,
    }
    _candidate, audits = install_store_fakes(monkeypatch, rows=[unrelated])
    cancelled = []
    monkeypatch.setattr(
        booking.candidate_store, "cancel_interview_slot",
        lambda **kwargs: cancelled.append(kwargs) or {"id": kwargs["candidate_id"]},
    )
    value = result("interview_cancelled", date=None, time=None, timezone=None, round=None)
    value["calendar"] = {"uid": CAPGEMINI_ATS_UID, "method": "CANCEL", "sequence": 81866364}
    outcome = execute(value)
    assert outcome["failure_code"] == "BOOKING_NOT_FOUND"
    assert cancelled == [], "an unrelated calendar event must never be cancelled"
    assert audits[-1]["booking_status"] == "Blocked"


def test_cancellation_still_applies_to_the_only_slot_of_the_same_calendar_event(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    target = {
        "id": "slot-330", "name": "Rahul", "slot_confirmed": True,
        "date": "2099-08-06", "time": "15:30", "time_end": "16:00",
        "interview_calendar_uid": CAPGEMINI_ATS_UID,
    }
    install_store_fakes(monkeypatch, rows=[target])
    monkeypatch.setattr(
        booking.candidate_store, "cancel_interview_slot",
        lambda **kwargs: {"id": kwargs["candidate_id"]},
    )
    value = result("interview_cancelled", date=None, time=None, timezone=None, round=None)
    value["calendar"] = {"uid": CAPGEMINI_ATS_UID, "method": "CANCEL", "sequence": 81866364}
    outcome = execute(value)
    assert outcome["status"] == "Cancelled"
    assert outcome["booking"]["id"] == "slot-330"


@pytest.mark.parametrize(("slot_uid", "event_uid"), [
    ("", CAPGEMINI_ATS_UID),   # slot predates calendar-identity capture
    (RECRUITER_INVITE_UID, ""),  # plain-text cancellation with no ICS
    ("", ""),
])
def test_single_slot_cancellation_is_unchanged_when_either_uid_is_absent(monkeypatch, slot_uid, event_uid):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    row = {
        "id": "slot-only", "name": "Rahul", "slot_confirmed": True,
        "date": "2099-08-06", "time": "17:30", "time_end": "18:00",
        "interview_calendar_uid": slot_uid,
    }
    install_store_fakes(monkeypatch, rows=[row])
    monkeypatch.setattr(
        booking.candidate_store, "cancel_interview_slot",
        lambda **kwargs: {"id": kwargs["candidate_id"]},
    )
    value = result("interview_cancelled", date=None, time=None, timezone=None, round=None)
    if event_uid:
        value["calendar"] = {"uid": event_uid, "method": "CANCEL", "sequence": 1}
    outcome = execute(value)
    assert outcome["status"] == "Cancelled"
    assert outcome["booking"]["id"] == "slot-only"


def test_reschedule_never_moves_the_only_slot_of_a_different_calendar_event(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    unrelated = {
        "id": "slot-530", "name": "Rahul", "slot_confirmed": True,
        "date": "2099-08-06", "time": "17:30", "time_end": "18:00",
        "interview_calendar_uid": RECRUITER_INVITE_UID,
    }
    install_store_fakes(monkeypatch, rows=[unrelated])
    moved = []
    monkeypatch.setattr(
        booking.candidate_store, "update_interview_slot",
        lambda **kwargs: moved.append(kwargs) or {"id": kwargs["candidate_id"]},
    )
    value = result("interview_rescheduled", date="2099-08-07", time="11:00 AM")
    value["calendar"] = {"uid": CAPGEMINI_ATS_UID, "method": "REQUEST", "sequence": 3}
    outcome = execute(value)
    assert outcome["failure_code"] == "BOOKING_NOT_FOUND"
    assert moved == [], "an unrelated calendar event must never be rescheduled"
# ── A missed interview is not a licence to cancel the next one ──────────────

MISSED_INTERVIEW_MAIL = {
    "provider_message_id": "gm-missed", "provider_thread_id": "gt1",
    "subject": "Missed Interview Opportunity (Platform Engineer / Example)",
    "body": (
        "Hi Rahul, Looks like you missed your interview for the "
        "\"Platform Engineer\" role at \"Example\". No problem, we can help you "
        "get it rescheduled quickly."
    ),
    "sent_at": "2026-09-03T14:02:00+00:00",  # 7:32 PM IST
}


def execute_mail(value, message):
    return booking.execute_auto_booking(
        mailbox={"id": "mb1", "candidate_id": "c1", "email_address": "candidate@test.invalid"},
        message=message,
        event={"mailbox_message_id": "mm-missed", "notification": {"id": "n1", "email_analysis_id": "ma1"}},
        result=value, correlation_id="corr-missed",
    )


def _cancellation():
    return result("interview_cancelled", date=None, time=None, timezone=None, round=None)


def test_a_missed_interview_mail_never_cancels_a_booking_that_had_not_started(monkeypatch):
    """Production read a mail like this as a cancellation on 11 Sep for an
    interview missed on 3 Sep, and every booking that candidate held by then was
    a later one. A single match would have cancelled a live interview."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    later = {"id": "slot-later", "name": "Rahul", "slot_confirmed": True,
             "date": "2026-09-07", "time": "19:30"}
    _candidate, audits = install_store_fakes(monkeypatch, rows=[later])
    cancelled = []
    monkeypatch.setattr(
        booking.candidate_store, "cancel_interview_slot",
        lambda **kwargs: cancelled.append(kwargs) or {"id": kwargs["candidate_id"]},
    )

    outcome = execute_mail(_cancellation(), MISSED_INTERVIEW_MAIL)

    assert outcome["failure_code"] == "BOOKING_NOT_FOUND"
    assert cancelled == [], "an interview that had not started cannot have been missed"
    assert audits[-1]["booking_status"] == "Blocked"


def test_a_missed_interview_mail_still_releases_the_interview_it_is_about(monkeypatch):
    """A production slot stayed confirmed for an interview the mail said the
    candidate had missed. Releasing that one is what reading these mails as
    cancellations is for, and it still happens -- the real case arrived at
    4:10 AM IST for a slot that had started at 2:30 AM IST."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    missed = {"id": "slot-missed", "name": "Rahul", "slot_confirmed": True,
              "date": "2026-09-03", "time": "12:30"}
    install_store_fakes(monkeypatch, rows=[missed])
    monkeypatch.setattr(booking.candidate_store, "cancel_interview_slot",
                        lambda **kwargs: {"id": kwargs["candidate_id"]})

    outcome = execute_mail(_cancellation(), MISSED_INTERVIEW_MAIL)

    assert outcome["status"] == "Cancelled"
    assert outcome["booking"]["id"] == "slot-missed"


def test_a_missed_interview_mail_chooses_the_interview_that_had_started(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    rows = [
        {"id": "slot-missed", "name": "Rahul", "slot_confirmed": True,
         "date": "2026-09-03", "time": "12:30"},
        {"id": "slot-later", "name": "Rahul", "slot_confirmed": True,
         "date": "2026-09-09", "time": "18:30"},
    ]
    install_store_fakes(monkeypatch, rows=rows)
    monkeypatch.setattr(booking.candidate_store, "cancel_interview_slot",
                        lambda **kwargs: {"id": kwargs["candidate_id"]})

    outcome = execute_mail(_cancellation(), MISSED_INTERVIEW_MAIL)

    assert outcome["status"] == "Cancelled"
    assert outcome["booking"]["id"] == "slot-missed"


def test_a_plain_cancellation_still_reaches_a_booking_that_has_not_happened(monkeypatch):
    """The guard reads the mail, not the classification."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    upcoming = {"id": "slot-upcoming", "name": "Rahul", "slot_confirmed": True,
                "date": "2099-08-06", "time": "15:30"}
    install_store_fakes(monkeypatch, rows=[upcoming])
    monkeypatch.setattr(booking.candidate_store, "cancel_interview_slot",
                        lambda **kwargs: {"id": kwargs["candidate_id"]})
    plain = {**MISSED_INTERVIEW_MAIL, "subject": "Interview canceled: Thu, August 27",
             "body": "The interview has been cancelled by the organiser."}

    outcome = execute_mail(_cancellation(), plain)

    assert outcome["status"] == "Cancelled"
    assert outcome["booking"]["id"] == "slot-upcoming"
