"""Three failures that changed what an operator would see were logged at DEBUG.

A slot-booked notification that could not be queued, an invite-parser crash that
let an interview message continue as "no interview", and a failed evidence-audit
write all left nothing in the production log (INFO and above only). The Sourcebae
invite that "went missing" was exactly this kind of silent path.
"""
import asyncio
import logging

from services import recruitment_mail_agent, slot_booking_notify


def test_a_slot_notification_that_cannot_be_queued_is_logged_as_a_warning(monkeypatch, caplog):
    async def broken(**_kw):
        raise RuntimeError("outbox unavailable")

    from services import messaging_client

    monkeypatch.setattr(messaging_client, "send_notification", broken)
    row = {"id": "c1", "name": "Probe Person", "date": "2026-10-03", "time": "11:00", "technology": "Java"}
    with caplog.at_level(logging.INFO, logger=slot_booking_notify.logger.name):
        asyncio.run(slot_booking_notify.notify_slot_booked(row))
    assert any(r.levelno >= logging.WARNING and "outbox unavailable" in r.getMessage() for r in caplog.records)


def test_a_crashing_invite_parser_is_logged_as_a_warning(monkeypatch, caplog):
    import services.calendar_invite_parser as parser

    def boom(*_a, **_k):
        raise ValueError("parser exploded")

    monkeypatch.setattr(parser, "trusted_interview_result", boom)
    with caplog.at_level(logging.INFO, logger=recruitment_mail_agent.logger.name):
        result = recruitment_mail_agent._publish_ignored_interview({}, {}, [], "ignored", "duplicate")
    assert result is None, "a parser crash must not break ingestion"
    levels = [r.levelno for r in caplog.records if "Interview signal check failed" in r.getMessage()]
    assert levels and min(levels) >= logging.WARNING
