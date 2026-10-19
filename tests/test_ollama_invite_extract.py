import json
from datetime import date, timedelta

import features.ollama_invite_extract as invite_extract

from features.ollama_invite_extract import (
    INVITE_EXTRACTION_PROMPT,
    _date_time_agree,
    _ollama_only_test_mode,
    normalize_time_to_12h,
    validate_12h_time_format,
)


def test_normalizes_malformed_24_hour_with_pm_suffix():
    assert normalize_time_to_12h("14:30 PM") == "02:30 PM"
    assert normalize_time_to_12h("15:00 PM") == "03:00 PM"


def test_rejects_non_12_hour_output():
    assert validate_12h_time_format("02:30 PM") is True
    assert validate_12h_time_format("14:30 PM") is False


def test_dual_source_verification_requires_exact_date_and_start_time():
    ocr = {"interview_date": "2026-07-25", "start_time": "10:30"}
    vision = {"interview_date": "2026-07-25", "start_time": "10:30 AM"}
    wrong_time = {"interview_date": "2026-07-25", "start_time": "01:00 AM"}

    assert _date_time_agree(ocr, vision) is True
    assert _date_time_agree(ocr, wrong_time) is False


def test_vision_prompt_prioritizes_explicit_invite_date_over_relative_ui_label():
    assert "explicit interview date" in INVITE_EXTRACTION_PROMPT
    assert "Ignore those relative labels" in INVITE_EXTRACTION_PROMPT
    assert "leave interview_date empty" in INVITE_EXTRACTION_PROMPT


def test_ollama_only_mode_is_explicit_and_reversible(monkeypatch):
    monkeypatch.delenv("INVITE_EXTRACTION_MODE", raising=False)
    assert _ollama_only_test_mode() is False

    monkeypatch.setenv("INVITE_EXTRACTION_MODE", "ollama_only")
    assert _ollama_only_test_mode() is True


def test_disabling_ocr_does_not_enable_ollama_test_mode(monkeypatch):
    monkeypatch.delenv("INVITE_EXTRACTION_MODE", raising=False)
    monkeypatch.setenv("OCR_ENABLED", "false")

    assert _ollama_only_test_mode() is False


def test_ollama_only_mode_never_calls_ocr(monkeypatch):
    # The extractor rewrites a date more than a week in the past to next year,
    # so a hard-coded day only survives for seven days after it. Anchor the
    # fixture to a future day instead of a calendar date that expires.
    upcoming = (date.today() + timedelta(days=14)).isoformat()
    monkeypatch.setenv("INVITE_EXTRACTION_MODE", "ollama_only")
    monkeypatch.setattr(invite_extract, "_is_ollama_available", lambda: True)

    def fail_if_called(_image):
        raise AssertionError("OCR must not run in Ollama-only mode")

    monkeypatch.setattr(invite_extract, "_run_tesseract_ocr", fail_if_called)
    monkeypatch.setattr(
        invite_extract,
        "call_ollama_vision_model",
        lambda *_args, **_kwargs: json.dumps(
            {
                "interview_date": upcoming,
                "start_time": "10:30 AM",
                "end_time": "11:15 AM",
                "interview_round": "L1",
                "confidence_score": 91,
                "missing_fields": [],
                "warnings": [],
                "looks_like_interview_invite": True,
                "is_payment_screenshot": False,
            }
        ),
    )

    result = invite_extract.extract_interview_invite_with_ollama(b"image", "image/jpeg")

    assert result["ollama_only_test"] is True
    assert result["extraction_method"] == "ollama_only_test"
    assert result["interview_date"] == upcoming
    assert result["auto_booking_safe"] is False
    assert result["manual_fields_required"] is True
    assert result["backup_model"] == ""


def test_ollama_only_mode_does_not_fall_back_to_another_vision_model(monkeypatch):
    monkeypatch.setenv("INVITE_EXTRACTION_MODE", "ollama_only")
    monkeypatch.setattr(invite_extract, "_is_ollama_available", lambda: True)
    calls = []

    def no_response(model, *_args, **_kwargs):
        calls.append(model)
        return None

    monkeypatch.setattr(invite_extract, "call_ollama_vision_model", no_response)
    result = invite_extract.extract_interview_invite_with_ollama(b"image", "image/jpeg")

    assert calls == [invite_extract.OLLAMA_VISION_MODEL]
    assert result["ollama_only_test"] is True
    assert result["auto_booking_safe"] is False


def _install_invite_flow(monkeypatch, ocr_text, vision_response=None):
    monkeypatch.delenv("INVITE_EXTRACTION_MODE", raising=False)
    monkeypatch.setenv("OCR_ENABLED", "true")
    monkeypatch.setattr(invite_extract, "_is_ollama_available", lambda: True)
    monkeypatch.setattr(invite_extract, "_run_tesseract_ocr", lambda _image: ocr_text)
    monkeypatch.setattr(
        invite_extract,
        "call_ollama_vision_model",
        lambda *_args, **_kwargs: vision_response,
    )
    monkeypatch.setattr(
        invite_extract,
        "_fallback_to_existing_ocr",
        lambda *_args, **_kwargs: invite_extract._empty_extraction(),
    )


def test_explicit_labeled_invite_uses_original_image_without_vision(monkeypatch):
    ocr_text = """
    accenture
    Hi Rama Krishnam Raju,
    We can confirm that your Skills Interview is all set.
    Date: 30-Jul-2099
    Time: 03:00 PM until! 04:00 PM GMT+05:30 India Standard Time
    The conversation will be Virtual Interview
    """
    _install_invite_flow(monkeypatch, ocr_text)

    def vision_must_not_run(*_args, **_kwargs):
        raise AssertionError("Explicit labelled OCR must not be vetoed by vision")

    monkeypatch.setattr(invite_extract, "call_ollama_vision_model", vision_must_not_run)
    original = b"original-full-resolution-image"
    result = invite_extract.extract_interview_invite_with_ollama(original, "image/jpeg")

    assert result["date"] == "2099-07-30"
    assert result["time"] == "15:00"
    assert result["time_end"] == "16:00"
    assert result["timezone"] == "Asia/Kolkata"
    assert result["auto_booking_safe"] is True
    assert result["manual_fields_required"] is False
    assert result["diagnostics"]["input_bytes"] == len(original)
    assert result["diagnostics"]["input_transport"] == "original_upload"
    assert result["diagnostics"]["image_compressed"] is False


def test_matching_ai_and_ocr_extraction_remains_successful(monkeypatch):
    ocr_text = "Interview scheduled 30-Jul-2099 at 03:00 PM IST"
    vision = json.dumps(
        {
            "interview_date": "2099-07-30",
            "start_time": "03:00 PM",
            "end_time": "03:30 PM",
            "timezone": "Asia/Kolkata",
            "confidence_score": 92,
            "looks_like_interview_invite": True,
        }
    )
    _install_invite_flow(monkeypatch, ocr_text, vision)

    result = invite_extract.extract_interview_invite_with_ollama(b"original", "image/jpeg")

    assert result["auto_booking_safe"] is True
    assert result["interview_date"] == "2099-07-30"
    assert result["start_time"] == "03:00 PM"


def test_matching_date_time_with_missing_timezone_is_not_reported_as_conflict(monkeypatch):
    ocr_text = "Interview scheduled 30-Jul-2099 at 03:00 PM"
    vision = json.dumps(
        {
            "interview_date": "2099-07-30",
            "start_time": "03:00 PM",
            "timezone": "Asia/Kolkata",
            "confidence_score": 92,
            "looks_like_interview_invite": True,
        }
    )
    _install_invite_flow(monkeypatch, ocr_text, vision)

    result = invite_extract.extract_interview_invite_with_ollama(
        b"original", "image/jpeg"
    )

    assert result["auto_booking_safe"] is False
    assert result["manual_fields_required"] is True
    assert result["failure_stage"] == "cross_source_verification"
    assert "verification_conflict" not in result
    assert "timezone" in result["failure_reason"].lower()
    assert all("date/time" not in warning for warning in result["warnings"])


def test_different_start_times_still_report_verification_conflict(monkeypatch):
    ocr_text = "Interview scheduled 30-Jul-2099 at 03:00 PM IST"
    vision = json.dumps(
        {
            "interview_date": "2099-07-30",
            "start_time": "04:00 PM",
            "timezone": "Asia/Kolkata",
            "confidence_score": 92,
            "looks_like_interview_invite": True,
        }
    )
    _install_invite_flow(monkeypatch, ocr_text, vision)

    result = invite_extract.extract_interview_invite_with_ollama(
        b"original", "image/jpeg"
    )

    assert result["auto_booking_safe"] is False
    assert result["verification_conflict"]["ocr"]["start_time"] == "03:00 PM"
    assert result["verification_conflict"]["vision"]["start_time"] == "04:00 PM"
    assert "date/time" in result["warnings"][-1]


def test_missing_date_requires_manual_fallback_with_exact_reason(monkeypatch, caplog):
    _install_invite_flow(monkeypatch, "Interview\nTime: 03:00 PM IST")

    result = invite_extract.extract_interview_invite_with_ollama(b"image", "image/jpeg")

    assert result["auto_booking_safe"] is False
    assert result["manual_fields_required"] is True
    assert result["failure_stage"] == "vision"
    assert "explicit supported interview date" in result["failure_reason"]
    assert "vision_failed" in caplog.text


def test_missing_start_time_requires_manual_fallback(monkeypatch):
    _install_invite_flow(monkeypatch, "Interview\nDate: 30-Jul-2026\nIST")

    result = invite_extract.extract_interview_invite_with_ollama(b"image", "image/jpeg")

    assert result["auto_booking_safe"] is False
    assert result["manual_fields_required"] is True
    assert "explicit supported interview start time" in result["failure_reason"]


def test_ambiguous_timezone_requires_manual_fallback(monkeypatch):
    _install_invite_flow(
        monkeypatch,
        "Interview\nDate: 30-Jul-2026\nTime: 03:00 PM\nCST",
    )

    result = invite_extract.extract_interview_invite_with_ollama(b"image", "image/jpeg")

    assert result["auto_booking_safe"] is False
    assert result["manual_fields_required"] is True
    assert "timezone was missing, ambiguous, or unsupported" in result["failure_reason"]


def test_invalid_image_logs_failure_and_exposes_manual_fields(monkeypatch, caplog):
    _install_invite_flow(monkeypatch, "")

    result = invite_extract.extract_interview_invite_with_ollama(b"not-an-image", "image/jpeg")

    assert result["auto_booking_safe"] is False
    assert result["manual_fields_required"] is True
    assert result["failure_stage"] == "vision"
    assert result["failure_reason"]
    assert "vision_failed" in caplog.text


# 19 Oct: in AI-only mode an invite the AI could not read came back as "not an
# invite", which the booking form refuses outright, so the manual date and time
# could not be entered. Unread is not judged: the form must offer manual entry.
def test_ai_only_vision_failure_is_unread_not_judged_not_an_invite(monkeypatch):
    monkeypatch.delenv("INVITE_EXTRACTION_MODE", raising=False)
    monkeypatch.setattr(invite_extract, "processing_mode", lambda: "ai")
    monkeypatch.setattr(invite_extract, "_is_ollama_available", lambda: True)
    monkeypatch.setattr(invite_extract, "call_ollama_vision_model", lambda *_a, **_k: None)
    result = invite_extract.extract_interview_invite_with_ollama(b"image", "image/jpeg")
    assert result["failure_stage"] == "vision"
    assert result["manual_fields_required"] is True
    assert result["auto_booking_safe"] is False
    assert result["looks_like_interview_invite"] is True


def test_ai_only_unavailable_model_is_unread_not_judged_not_an_invite(monkeypatch):
    monkeypatch.delenv("INVITE_EXTRACTION_MODE", raising=False)
    monkeypatch.setattr(invite_extract, "processing_mode", lambda: "ai")
    monkeypatch.setattr(invite_extract, "_is_ollama_available", lambda: False)
    result = invite_extract.extract_interview_invite_with_ollama(b"image", "image/jpeg")
    assert result["failure_stage"] == "ollama_unavailable"
    assert result["looks_like_interview_invite"] is True
