"""Prompt 12 is the single authority for whether a mail is an interview."""
import hashlib

import pytest

from core.ai_gateway import AIGatewayError, AIResult
from services import prompt12_classifier as p12
from services.recruitment_semantics import selection_alert_filing

#: sha256 of the owner's text as pasted on 13 Nov 2026 (no trailing newline).
OWNER_PROMPT_SHA256 = "2a8337558148c6003482f6654d966cd4f60612b9df1657f3500cc376f6d0560c"


def test_the_prompt_is_the_owners_text_word_for_word():
    assert hashlib.sha256(p12.PROMPT12.encode("utf-8")).hexdigest() == OWNER_PROMPT_SHA256
    assert p12.PROMPT12_SHA256 == OWNER_PROMPT_SHA256
    assert p12.PROMPT12.endswith("Return ONLY: SCHEDULED, CANCELED, or OTHER.")


def test_it_runs_as_tested(monkeypatch):
    sent = {}

    def fake_chat(**kwargs):
        sent.update(kwargs)
        return AIResult(content="SCHEDULED", model="qwen2.5:7b", duration_ms=5, node_id="rtx4060")

    monkeypatch.setattr(p12, "chat", fake_chat)
    monkeypatch.delenv("PROMPT12_MODEL", raising=False)
    record = p12.classify("L1 interview", "Your L1 interview is on 19 Sep at 5 PM")
    assert sent["model"] == "qwen2.5:7b"
    assert (sent["temperature"], sent["seed"], sent["num_ctx"]) == (0, 12, 8192)
    assert sent["messages"] == [
        {"role": "system", "content": p12.PROMPT12},
        {"role": "user", "content": "SUBJECT: L1 interview\n\nBODY:\nYour L1 interview is on 19 Sep at 5 PM"},
    ]
    assert record["verdict"] == "SCHEDULED"
    assert record["prompt_version"] == "prompt12@" + OWNER_PROMPT_SHA256[:12]


@pytest.mark.parametrize("raw,label", [
    ("SCHEDULED", "SCHEDULED"), ("other", "OTHER"), ("CANCELLED", "CANCELED"),
    ("Answer: CANCELED.", "CANCELED"), ("SCHEDULED or OTHER", None), ("", None), ("maybe", None),
])
def test_only_one_clear_label_counts(raw, label):
    assert p12.parse(raw) == label


def test_no_clear_label_is_retried_never_guessed(monkeypatch):
    monkeypatch.setattr(p12, "chat", lambda **_: AIResult(content="It depends", model="qwen2.5:7b", duration_ms=1))
    with pytest.raises(AIGatewayError) as raised:
        p12.classify("s", "b")
    assert raised.value.code == "PROMPT12_UNPARSED"


def _verdict(label):
    return {"verdict": label, "prompt_version": "prompt12@x"}


def test_other_drops_any_interview_reading():
    # The TCS login OTP of 12 Nov: the old reader called it SELECTED and the
    # selection filing turned that into Interview Shortlisted.
    for status in ("INTERVIEW_SHORTLISTED", "INTERVIEW_CONFIRMED", "FINAL_ROUND_CLEARED", "INTERVIEW_UPDATE"):
        result = {"status": status, "primary_status": status, "is_selection_or_offer_related": True,
                  "should_create_review_record": True}
        assert p12.apply_verdict(result, _verdict("OTHER")) == "dropped"
        assert result["should_create_review_record"] is False
        assert result["ignore_reason"] == "PROMPT12_OTHER"
        assert result["prompt12"]["verdict"] == "OTHER"


def test_other_leaves_selection_and_offer_workflows_alone():
    result = {"status": "OFFER_LETTER_RECEIVED", "primary_status": "OFFER_LETTER_RECEIVED",
              "is_selection_or_offer_related": True, "should_create_review_record": True}
    assert p12.apply_verdict(result, _verdict("OTHER")) == "unchanged"
    assert result["status"] == "OFFER_LETTER_RECEIVED" and result["should_create_review_record"] is True


def test_scheduled_with_an_agreeing_reader_keeps_its_details_and_booking():
    result = {"status": "INTERVIEW_RESCHEDULED", "primary_status": "INTERVIEW_RESCHEDULED",
              "ai_validation_status": "VALIDATED", "interview": {"date": "2026-11-20", "time": "15:00"}}
    assert p12.apply_verdict(result, _verdict("SCHEDULED")) == "agreed"
    assert result["classification"] == "interview_rescheduled"
    assert result["ai_validation_status"] == "VALIDATED"


def test_scheduled_against_another_reading_alerts_but_books_nothing():
    result = {"status": "INTERVIEW_SHORTLISTED", "primary_status": "INTERVIEW_SHORTLISTED",
              "ai_validation_status": "VALIDATED", "is_selection_or_offer_related": False}
    assert p12.apply_verdict(result, _verdict("SCHEDULED")) == "unconfirmed"
    assert (result["status"], result["classification"]) == ("INTERVIEW_CONFIRMED", "interview_confirmed")
    assert result["should_create_review_record"] is True
    assert result["ai_validation_status"] == p12.NOT_BOOKABLE
    assert result["backend_transition_validated"] is False


def test_canceled_is_a_cancellation_and_releases_only_when_the_reader_agrees():
    agreeing = {"status": "INTERVIEW_CANCELLED", "primary_status": "INTERVIEW_CANCELLED", "ai_validation_status": "VALIDATED"}
    assert p12.apply_verdict(agreeing, _verdict("CANCELED")) == "agreed"
    assert agreeing["ai_validation_status"] == "VALIDATED"
    other = {"status": "SELECTED", "primary_status": "SELECTED"}
    assert p12.apply_verdict(other, _verdict("CANCELED")) == "unconfirmed"
    assert other["classification"] == "interview_cancelled" and other["ai_validation_status"] == p12.NOT_BOOKABLE


def test_only_a_matching_verdict_allows_a_new_interview_alert():
    assert p12.verdict_allows("interview_confirmed", {"prompt12": {"verdict": "SCHEDULED"}})
    assert p12.verdict_allows("interview_cancelled", {"prompt12": {"verdict": "CANCELED"}})
    assert not p12.verdict_allows("interview_cancelled", {"prompt12": {"verdict": "SCHEDULED"}})
    assert not p12.verdict_allows("interview_shortlisted", {"prompt12": {"verdict": "SCHEDULED"}})
    assert not p12.verdict_allows("interview_confirmed", {"prompt12": {"verdict": "OTHER"}})
    assert not p12.verdict_allows("interview_confirmed", {})


def test_an_unproven_selection_is_never_refiled_as_an_interview():
    otp = ["OTP for login - Tata Consultancy Services Limited.",
           "You have requested for a new OTP to log on to the TCS Career website."]
    assert selection_alert_filing("job_selection_confirmed", otp) == "not_relevant"
    progress = ["Next steps", "You have cleared the L1 round and are shortlisted for the next interview."]
    assert selection_alert_filing("job_selection_confirmed", progress) == "not_relevant"
