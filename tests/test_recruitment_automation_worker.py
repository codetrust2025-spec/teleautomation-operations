from workers.recruitment_mail_worker import RecruitmentMailWorker
from workers import recruitment_mail_worker as worker_module


def test_legacy_review_promotion_runs_even_when_ollama_is_down(monkeypatch):
    calls = []
    monkeypatch.setattr(worker_module.store, "promote_legacy_review_states", lambda: calls.append("retry") or 2)
    monkeypatch.setattr(worker_module.store, "promote_ignored_messages", lambda: calls.append("ignore") or 3)
    monkeypatch.setattr(worker_module, "_publish", lambda *_args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr("core.ai_gateway.health", lambda **_kwargs: {"endpoint_reachable": False, "model_available": False})

    RecruitmentMailWorker().process_ai_recovery()

    assert calls[:2] == ["retry", "ignore"]
    assert calls[2]["promoted_retry_count"] == 2
    assert calls[2]["auto_ignore_count"] == 3


def test_calendar_recovery_reprocesses_only_explicit_message_ids(monkeypatch):
    monkeypatch.setenv("AI_CALENDAR_RECOVERY_MESSAGE_IDS", "cgi-message,thaga-message")
    calls = []
    row = {
        "id": "mail-row", "mailbox_id": "mb1", "mailbox_candidate_id": "legacy-candidate",
        "email_address": "nitin@example.invalid", "provider_message_id": "cgi-message",
        "provider_thread_id": "thread1", "body_text": "L1 Discussion", "attachments": [],
    }
    monkeypatch.setattr(worker_module.store, "promote_legacy_review_states", lambda: 0)
    monkeypatch.setattr(worker_module.store, "promote_ignored_messages", lambda: 0)
    monkeypatch.setattr(
        worker_module.store, "claim_calendar_invite_recovery_messages",
        lambda **kwargs: calls.append(kwargs) or [row],
    )
    monkeypatch.setattr(
        worker_module, "process_message",
        lambda *_args, **_kwargs: {"auto_booking": {"automation_state": "AUTO_BOOKED"}},
    )
    monkeypatch.setattr(worker_module.store, "stored_message", lambda *_args: {"processing_status": "AUTO_BOOKED"})
    monkeypatch.setattr(worker_module.store, "complete_calendar_invite_recovery", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr("core.ai_gateway.health", lambda **_kwargs: {"endpoint_reachable": True, "model_available": True})
    monkeypatch.setattr(worker_module.store, "claim_ai_messages", lambda **_kwargs: [])

    RecruitmentMailWorker().process_ai_recovery()

    assert calls[0]["provider_message_ids"] == ["cgi-message", "thaga-message"]
    assert calls[1] == (("mail-row",), {"state": "AUTO_BOOKED", "reason": "CALENDAR_RECOVERY"})
