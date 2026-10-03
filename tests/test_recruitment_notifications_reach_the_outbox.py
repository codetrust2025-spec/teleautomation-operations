"""Recruitment and mailbox-failure notifications actually leave the building.

`notify_system` and `notify_detection` imported `features.web_push`, which does
not exist, inside `except Exception: pass`. Nothing was ever sent: a mailbox that
failed three syncs in a row, or a selection/offer detection, told nobody. They
now enqueue to the durable outbox like every other notification.

Names and ids are invented.
"""
import pytest

from services import cross_project_outbox as outbox
from services import recruitment_notifications as notes


@pytest.fixture(autouse=True)
def _outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(outbox, "_PATH", str(tmp_path / "outbox.json"))


def queued():
    return outbox._load()


def test_the_module_no_longer_depends_on_a_web_push_module_that_does_not_exist():
    import importlib.util

    assert importlib.util.find_spec("features.web_push") is None, "premise: it never existed here"
    import inspect

    assert "import web_push" not in inspect.getsource(notes) and "from features.web_push" not in inspect.getsource(notes)


def test_a_system_notification_is_queued():
    notes.notify_system("Mailbox synchronization failed", "Mailbox mb-1 requires administrator attention.", "mailbox-failure:mb-1")
    rows = queued()
    assert len(rows) == 1 and rows[0]["event_type"] == "marketing.notification.v1" and rows[0]["status"] == "pending"
    assert rows[0]["payload"]["title"] == "Mailbox synchronization failed"
    assert rows[0]["payload"]["tag"].startswith("mailbox-failure:mb-1:")


def test_the_same_failure_on_the_same_day_is_one_notification():
    for _ in range(3):
        notes.notify_system("Mailbox synchronization failed", "Mailbox mb-1 requires administrator attention.", "mailbox-failure:mb-1")
    assert len(queued()) == 1


def test_the_same_failure_on_a_later_day_notifies_again(monkeypatch):
    from datetime import datetime

    from core.ist_time import IST

    notes.notify_system("t", "b", "mailbox-failure:mb-1")
    monkeypatch.setattr(notes, "ist_now", lambda: datetime(2030, 1, 1, 10, 0, tzinfo=IST))
    notes.notify_system("t", "b", "mailbox-failure:mb-1")
    assert len(queued()) == 2


def test_two_mailboxes_are_two_notifications():
    notes.notify_system("t", "b", "mailbox-failure:mb-1")
    notes.notify_system("t", "b", "mailbox-failure:mb-2")
    assert len(queued()) == 2


def test_a_selection_detection_is_queued_with_its_company_and_confidence(monkeypatch):
    monkeypatch.setattr("core.recruitment_offer_visibility.should_show_in_selection_offer_review", lambda e: True)
    notes.notify_detection({"id": "ev-1", "primary_status": "offer_received", "company_name": "Example Co",
                            "job_title": "Engineer", "confidence": 0.93})
    row = queued()[0]
    assert row["payload"]["title"] == "Offer Received"
    assert row["payload"]["body"] == "Example Co · Engineer · 93% confidence"
    assert row["payload"]["tag"] == "recruitment:ev-1"


def test_a_detection_that_is_not_for_review_queues_nothing(monkeypatch):
    monkeypatch.setattr("core.recruitment_offer_visibility.should_show_in_selection_offer_review", lambda e: False)
    notes.notify_detection({"id": "ev-2", "primary_status": "rejected"})
    assert queued() == []


def test_a_malformed_event_never_breaks_mail_processing(monkeypatch):
    monkeypatch.setattr("core.recruitment_offer_visibility.should_show_in_selection_offer_review", lambda e: True)
    notes.notify_detection({"id": "ev-3"})  # no primary_status
    assert queued() == []


def test_an_unwritable_outbox_is_logged_not_raised(monkeypatch, caplog):
    def boom(**_kw):
        raise OSError("disk full")

    monkeypatch.setattr(outbox, "enqueue", boom)
    with caplog.at_level("WARNING", logger=notes.logger.name):
        notes.notify_system("t", "b", "tag")  # must not raise
    assert any("could not be queued" in r.getMessage() for r in caplog.records)


def test_the_idempotency_key_matches_the_async_sender():
    """One notification raised by either path is one outbox row."""
    import asyncio

    from services import messaging_client

    async def go():
        async def no_dispatch():
            return 0

        messaging_client.dispatch_notifications_once, original = no_dispatch, messaging_client.dispatch_notifications_once
        try:
            await messaging_client.send_notification(title="T", body="B", tag="x")
        finally:
            messaging_client.dispatch_notifications_once = original

    asyncio.run(go())
    notes._enqueue("T", "B", "x")
    assert len(queued()) == 1
