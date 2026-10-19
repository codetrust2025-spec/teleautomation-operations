"""Invite reader: queue wait, speed and accuracy safeguards (19 Oct).

Measured on production that morning: one invite read took ~60s (57.6s of it
generating 301 output tokens), an interactive request waited at most 30s for
its node, so any overlap failed with OLLAMA_QUEUE_TIMEOUT; phone screenshots
were shrunk to 768px on the long side; a written past year was moved to this
year and filled; and in AI-only mode the model's own "ambiguous" score was
ignored.
"""
import io
import json
import time
from datetime import date, timedelta

import pytest
from PIL import Image

import features.ollama_invite_extract as invite_extract
from core import ai_gateway


# ── queue ───────────────────────────────────────────────────────────────────
def test_an_interactive_request_waits_out_one_vision_read(monkeypatch):
    monkeypatch.delenv("AI_INTERACTIVE_QUEUE_WAIT_SECONDS", raising=False)
    # One read took ~60s on 19 Oct; the wait must cover at least that.
    assert ai_gateway.queue_wait_seconds(True) >= 120


def test_background_work_keeps_its_short_wait(monkeypatch):
    monkeypatch.delenv("AI_RECRUITMENT_QUEUE_WAIT_SECONDS", raising=False)
    assert ai_gateway.queue_wait_seconds(False) == 30


def test_the_invite_read_is_interactive():
    assert "interview_screenshot_vision" in ai_gateway.INTERACTIVE_WORKLOADS


def test_a_waiting_interactive_request_gets_the_slot_when_a_read_finishes():
    queue = ai_gateway._HostQueue(1)
    assert queue.acquire("node", interactive=False, timeout=0)
    import threading
    got = []
    waiter = threading.Thread(target=lambda: got.append(queue.acquire("node", interactive=True, timeout=5)))
    waiter.start()
    time.sleep(0.2)
    queue.release("node")          # the running read ends
    waiter.join(5)
    assert got == [True]


def test_each_vision_read_is_bounded_by_a_deadline(monkeypatch):
    seen = {}

    class Result:
        content = "{}"
        node_id = node_label = ""
        load_duration_ms = prompt_eval_duration_ms = eval_duration_ms = 0
        prompt_eval_count = eval_count = queued_ms = duration_ms = 0

    def fake_chat(**kwargs):
        seen.update(kwargs)
        return Result()

    monkeypatch.setattr(invite_extract, "chat", fake_chat)
    before = time.monotonic()
    invite_extract.call_ollama_vision_model("m", "aGk=", "p", timeout=200)
    assert before + 199 <= seen["deadline_monotonic"] <= time.monotonic() + 200
    assert seen["num_predict"] == invite_extract.INVITE_NUM_PREDICT


# ── speed: a small answer ───────────────────────────────────────────────────
def test_the_model_is_asked_only_for_what_the_booking_form_uses():
    prompt = invite_extract._get_invite_prompt()
    for unused in ("raw_detected_text", "screenshot_source", "attendee_name", "candidate_phone",
                   "missing_fields", "meeting_link", "service_type", '"client_name"'):
        assert unused not in prompt, unused
    for used in ('"candidate_name"', '"company"', '"interview_round"', '"interview_date"',
                 '"start_time"', '"end_time"', '"timezone_written"', '"confidence_score"'):
        assert used in prompt, used
    assert invite_extract.INVITE_NUM_PREDICT <= 256


# ── accuracy: legible screenshots ───────────────────────────────────────────
@pytest.mark.parametrize("size", [(1080, 2400), (1440, 3200), (1920, 1080), (900, 520)])
def test_a_screenshot_keeps_enough_pixels_to_read(size):
    w, h = invite_extract._vision_size(*size)
    assert w * h <= invite_extract.VISION_MAX_PIXELS * 1.01
    assert max(w, h) <= invite_extract.VISION_MAX_EDGE
    # A phone screenshot keeps about twice the old 768px width; a small one is untouched.
    if size == (1080, 2400):
        assert w >= 640 and h >= 1400
    if size == (900, 520):
        assert (w, h) == size


def test_a_phone_screenshot_is_sent_within_budget():
    buf = io.BytesIO()
    Image.new("RGB", (1080, 2400), "white").save(buf, format="PNG")
    data, changed = invite_extract._optimize_image_for_vision(buf.getvalue())
    assert changed
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG"
    assert img.size == invite_extract._vision_size(1080, 2400)


def test_context_window_holds_prompt_screenshot_and_answer():
    # ~1,900 prompt tokens measured with a 336-token image; 1 MP is <= ~1,000 image tokens.
    assert 1900 + 1000 + invite_extract.INVITE_NUM_PREDICT < ai_gateway.MIN_NUM_CTX


# ── accuracy: nothing guessed ───────────────────────────────────────────────
def _ai_only(monkeypatch, read):
    monkeypatch.delenv("INVITE_EXTRACTION_MODE", raising=False)
    monkeypatch.setattr(invite_extract, "processing_mode", lambda: "ai")
    monkeypatch.setattr(invite_extract, "_is_ollama_available", lambda: True)
    monkeypatch.setattr(invite_extract, "call_ollama_vision_model", lambda *_a, **_k: json.dumps(read))
    return invite_extract.extract_interview_invite_with_ollama(b"image", "image/jpeg")


def _upcoming(days=14):
    return (date.today() + timedelta(days=days)).isoformat()


def test_a_clear_reading_is_confident(monkeypatch):
    result = _ai_only(monkeypatch, {"interview_date": _upcoming(), "start_time": "10:00 PM",
                                    "end_time": "10:45 PM", "confidence_score": 95})
    assert result["auto_booking_safe"] is True
    assert result["manual_fields_required"] is False


def test_a_reading_the_model_calls_ambiguous_is_not_filled(monkeypatch):
    result = _ai_only(monkeypatch, {"interview_date": _upcoming(), "start_time": "10:00 PM",
                                    "confidence_score": 60})
    assert result["auto_booking_safe"] is False
    assert result["manual_fields_required"] is True
    assert result["interview_date"] == _upcoming()          # still shown as read


def test_a_written_past_year_is_never_moved_and_filled(monkeypatch):
    # The Marriott invite as received: Thu, 20 Nov 2025. Moving it to 2026
    # would fill a date the invite never gave.
    past = date.today().replace(year=date.today().year - 1) + timedelta(days=32)
    result = _ai_only(monkeypatch, {"interview_date": past.isoformat(), "start_time": "10:00 PM",
                                    "confidence_score": 100})
    assert result["auto_booking_safe"] is False
    assert result["manual_fields_required"] is True
    assert result.get("year_adjusted") is True
    assert any(past.isoformat() in w for w in result["warnings"])


def test_validation_marks_a_moved_year_for_checking():
    past = date.today().replace(year=date.today().year - 1) + timedelta(days=32)
    result = invite_extract.validate_invite_extraction(
        {"interview_date": past.isoformat(), "start_time": "10:00 PM", "confidence_score": 95,
         "auto_booking_safe": True})
    assert result["year_adjusted"] is True
    assert result["manual_fields_required"] is True
    assert result["auto_booking_safe"] is False
