from core.recruitment_mail_store import summarize_selection_tracking_events
from services.recruitment_mail_agent import _failure_review_result, prefilter_decision
from services.recruitment_semantics import (
    classify_context,
    extract_interview_schedule,
    redact_sensitive_text,
)
from workers import recruitment_mail_worker as worker_module


def test_recruiter_questionnaire_has_no_lifecycle_event():
    body = """Full Name:
Experience:
Current Company:
Expected CTC:
Offer in Hand:
Date of Joining:
Contact No:
"""
    result = classify_context("Candidate details required", body)
    assert result["email_intent"] == "RECRUITER_QUESTIONNAIRE"
    assert result["is_job_outcome"] is False
    assert result["lifecycle_event"] == "NONE"
    assert prefilter_decision("Candidate details required", body)["qualified"] is False


def test_payslip_joining_date_is_historical_metadata():
    text = "PAYSLIP FOR THE MONTH OF APRIL 2026 Employee ID MT-11822 Date Of Joining: 04-11-2023 Working Days: 30"
    result = classify_context("Payslip", "Attached", attachments=[{"filename": "April.pdf", "text": text}])
    assert result["document_type"] == "PAYSLIP"
    assert result["historical_employment_evidence"] is True
    assert result["lifecycle_event"] == "NONE"


def test_joining_confirmation_is_not_joined():
    result = classify_context("Joining", "Your joining date is confirmed as 25 July 2026.")
    assert result["email_intent"] == "JOINING_CONFIRMATION"
    assert result["lifecycle_event"] == "JOINING_CONFIRMED"


def test_actual_employment_start_is_joined():
    result = classify_context("Welcome", "We confirm that you officially joined ABC Technologies today.")
    assert result["email_intent"] == "ACTUAL_JOINING_CONFIRMATION"
    assert result["lifecycle_event"] == "JOINED"


def test_offer_statement_is_offer_received():
    result = classify_context("Your offer", "We are pleased to offer you the Software Engineer position.")
    assert result["email_intent"] == "OFFER_LETTER"
    assert result["lifecycle_event"] == "OFFER_LETTER_RECEIVED"


def test_released_offer_body_is_offer_received_even_with_future_joining_date():
    result = classify_context(
        "Offer has been released",
        "Your offer has been successfully released. The expected joining date is 14 September 2026.",
    )
    assert result["email_intent"] == "OFFER_LETTER"
    assert result["lifecycle_event"] == "OFFER_LETTER_RECEIVED"


def test_offer_question_is_not_an_offer_event():
    result = classify_context("Candidate details", "Do you currently have an offer in hand?")
    assert result["lifecycle_event"] == "NONE"
    assert result["is_question"] is True


def test_joining_date_request_is_not_confirmation():
    result = classify_context("Joining", "Please share your date of joining.")
    assert result["lifecycle_event"] == "NONE"
    assert result["email_intent"] == "CANDIDATE_DETAILS_REQUEST"


def test_naukri_job_ad_keywords_do_not_create_outcome():
    result = classify_context(
        "Job | AWS Devops in Hyderabad",
        "Shortlisted candidates may receive an offer. Joining details will follow. Experience: 5 years. Location: Hyderabad. Skills: AWS. Job description attached.",
        sender_email="jobs@naukri.com",
    )
    assert result["email_intent"] == "JOB_ADVERTISEMENT"
    assert result["lifecycle_event"] == "NONE"


def test_interview_confirmation_routes_to_interview_domain_only():
    result = classify_context(
        "L1 interview confirmed",
        "Your L1 technical interview is confirmed for 20 July 2026 at 3:00 PM IST.",
    )
    assert result["email_intent"] == "INTERVIEW_CONFIRMATION"
    assert result["business_domain"] == "INTERVIEW_TRACKING"
    assert result["interview_event"] == "INTERVIEW_CONFIRMED"
    assert result["lifecycle_event"] == "NONE"


def test_virtual_interview_invite_with_concrete_schedule_is_confirmed():
    subject = "Virtual Interview - Senior Full Stack Engineer (Front-End Focused)"
    body = (
        "Hello Charan, Please join the Virtual Interview at 12.30 pm on "
        "21st July, 2026. Microsoft Teams meeting "
        "https://teams.microsoft.com/meet/000111222333444 Meeting ID: 428 762."
    )
    result = classify_context(subject, body, sender_email="recruiter@example.com")
    route = prefilter_decision(subject, body, sender_email="recruiter@example.com")
    assert result["email_intent"] == "INTERVIEW_CONFIRMATION"
    assert result["interview_event"] == "INTERVIEW_CONFIRMED"
    assert result["business_domain"] == "INTERVIEW_TRACKING"
    assert route["qualified"] is True
    assert route["status"] == "INTERVIEW_CONFIRMED"
    schedule = extract_interview_schedule(subject, body)
    assert schedule == {
        "date": "2026-07-21", "time": "12:30 PM",
        "end_time": None, "duration_minutes": None, "timezone": None,
        "mode": "Microsoft Teams", "round": None, "location": None,
        "meeting_link": "https://teams.microsoft.com/meet/000111222333444",
    }


def test_schedule_preserves_explicit_hexaware_time_range():
    schedule = extract_interview_schedule(
        "Interview scheduled with Hexaware Technologies",
        (
            "You have a Video Interview scheduled with Hexaware Technologies "
            "for Application Front end Lead and L2 Interview on "
            "Wed 29 Jul 2026 09:45 AM - 10:30 AM (IST)"
        ),
    )
    assert schedule["date"] == "2026-07-29"
    assert schedule["time"] == "09:45 AM"
    assert schedule["end_time"] == "10:30 AM"
    assert schedule["duration_minutes"] == 45
    assert schedule["timezone"] == "Asia/Kolkata"


def test_schedule_extracts_apostrophe_two_digit_year():
    schedule = extract_interview_schedule(
        "KPMG L1 Interview",
        "Date & Time: 7 Jul'26 (Tue) || 7:30 PM Microsoft Teams "
        "https://teams.microsoft.com/meet/example",
    )
    assert schedule["date"] == "2026-07-07"
    assert schedule["time"] == "07:30 PM"


def test_schedule_extracts_indian_numeric_date():
    schedule = extract_interview_schedule(
        "L3 Interview | 03/07/2026",
        "Please join the interview at 12:15 PM IST.",
    )
    assert schedule["date"] == "2026-07-03"
    assert schedule["time"] == "12:15 PM"
    assert schedule["timezone"] == "Asia/Kolkata"


def test_tcs_interview_checklist_never_becomes_appointment_letter():
    body = """We are delighted to invite you for a discussion. The interview will be video based using MS Teams.
Interview Details: Date: 22nd July26 Time: 2:30 PM - 3 PM
Link to join: https://teams.microsoft.com/meet/room
Mandatory checklist: Mark sheet, last 3 month's payslip, Experience Letter/Relieving Letter/Appointment Letter of all your previous companies.
"""
    semantic=classify_context("TCS Interview_ 22nd July26",body,sender_email="hr@tcs.com")
    routed=prefilter_decision("TCS Interview_ 22nd July26",body,sender_email="hr@tcs.com")
    assert semantic["interview_event"]=="INTERVIEW_CONFIRMED"
    assert semantic["is_historical_information"] is False
    assert routed["status"]=="INTERVIEW_CONFIRMED"
    assert all(item["meaning"]!="APPOINTMENT_LETTER_RECEIVED" for item in routed["evidence"])


def test_virtual_interview_training_with_schedule_is_not_candidate_invite():
    result = classify_context(
        "Virtual interview preparation workshop",
        "Learn interview tips tomorrow at 12:30 PM in our Microsoft Teams webinar.",
        sender_email="events@example.com",
    )
    assert result["interview_event"] == "NONE"
    assert result["business_domain"] == "NONE"


def test_interview_reschedule_and_cancellation_are_distinct_events():
    moved = classify_context("Interview update", "Your interview has been moved to 21 July at 11:00 AM IST.")
    cancelled = classify_context("Interview cancelled", "Your interview scheduled for July 20 has been cancelled.")
    assert moved["interview_event"] == "INTERVIEW_RESCHEDULED"
    assert cancelled["interview_event"] == "INTERVIEW_CANCELLED"


def test_interview_invitation_document_has_specific_document_type():
    result = classify_context(
        "Interview details", "See attachment",
        attachments=[{"filename": "Interview Invitation.pdf", "text": "Interview schedule"}],
    )
    assert result["document_type"] == "INTERVIEW_INVITATION_DOCUMENT"


def _event(event_id, candidate_id, status, *, canonical=None, validation="AUTO_VALIDATED", review="AUTO_VALIDATED"):
    return {
        "id": event_id,
        "candidate_id": candidate_id,
        "canonical_candidate_id": canonical,
        "primary_status": status,
        "validation_status": validation,
        "review_status": review,
    }


def test_repeated_joining_emails_count_one_candidate():
    summary = summarize_selection_tracking_events([
        _event("e1", "c1", "JOINING_CONFIRMED"),
        _event("e2", "c1", "JOINING_CONFIRMED"),
        _event("e3", "c1", "JOINING_CONFIRMED"),
    ])
    assert summary["metrics"]["joining_confirmed"] == 1


def test_joining_confirmed_never_inflates_joined_metric():
    summary = summarize_selection_tracking_events([
        _event(f"e{i}", f"c{i}", "JOINING_CONFIRMED") for i in range(5)
    ])
    assert summary["metrics"]["joining_confirmed"] == 5
    assert summary["metrics"]["joined"] == 0


def test_duplicate_candidate_ids_count_as_one_canonical_person():
    summary = summarize_selection_tracking_events([
        _event("e1", "alias-a", "JOINING_CONFIRMED", canonical="person-1"),
        _event("e2", "alias-b", "JOINING_CONFIRMED", canonical="person-1"),
    ])
    assert summary["metrics"]["joining_confirmed"] == 1


def test_ollama_failure_creates_retry_pending_none_event():
    result = _failure_review_result(
        {"subject": "Joining", "body": "Your joining date is confirmed as 25 July 2026."},
        RuntimeError("offline"),
    )
    assert result["primary_status"] == "MANUAL_REVIEW_REQUIRED"
    assert result["lifecycle_event"] == "NONE"
    assert result["validation_status"] == "RETRY_PENDING"


def test_ollama_recovery_reprocesses_idempotently(monkeypatch):
    calls = []
    row = {
        "id": "stored-1", "mailbox_id": "mb1", "mailbox_candidate_id": "c1",
        "provider_message_id": "gmail-1", "subject": "Offer", "body_text": "Offer body",
        "attachments": [],
    }
    monkeypatch.setattr("core.ai_gateway.health", lambda **_kwargs: {"endpoint_reachable": True, "model_available": True})
    claimed = [row]
    monkeypatch.setattr(worker_module.store, "claim_ai_messages", lambda **_kwargs: [claimed.pop(0)] if claimed else [])
    monkeypatch.setattr(worker_module.store, "stored_message", lambda *_args: {"processing_status": "EVENT_CREATED"})
    monkeypatch.setattr(worker_module.store, "schedule_ai_retry", lambda message_id, **kwargs: calls.append((message_id, kwargs)))
    monkeypatch.setattr(worker_module, "process_message", lambda *args, **kwargs: {"id": "same-event", "validation_status": "AUTO_VALIDATED"})
    worker_module.RecruitmentMailWorker().process_ai_recovery()
    assert calls == [("stored-1", {"succeeded": True})]


def test_ollama_recovery_backs_off_when_hidden_retry_remains_pending(monkeypatch):
    calls = []
    row = {
        "id": "stored-1", "mailbox_id": "mb1", "mailbox_candidate_id": "c1",
        "provider_message_id": "gmail-1", "subject": "Ambiguous mail", "body_text": "Body",
        "attachments": [],
    }
    monkeypatch.setattr("core.ai_gateway.health", lambda **_kwargs: {"endpoint_reachable": True, "model_available": True})
    claimed = [row]
    monkeypatch.setattr(worker_module.store, "claim_ai_messages", lambda **_kwargs: [claimed.pop(0)] if claimed else [])
    monkeypatch.setattr(worker_module.store, "stored_message", lambda *_args: {"processing_status": "AI_RETRY_PENDING"})
    monkeypatch.setattr(worker_module.store, "schedule_ai_retry", lambda message_id, **kwargs: calls.append((message_id, kwargs)))
    monkeypatch.setattr(worker_module, "process_message", lambda *args, **kwargs: None)
    worker_module.RecruitmentMailWorker().process_ai_recovery()
    assert calls == [("stored-1", {"succeeded": False})]


def test_detection_evidence_masks_sensitive_identifiers():
    redacted = redact_sensitive_text("PAN: ABCDE1234F Bank Account Number: 123456789012 UAN: 123456789012")
    assert "ABCDE1234F" not in redacted
    assert "123456789012" not in redacted
