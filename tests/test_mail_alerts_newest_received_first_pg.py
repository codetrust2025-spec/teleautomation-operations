"""Mail Alerts lists mail in the order it was received, newest first.

The list followed `created_at`, the moment the tool detected the mail. A mail
recovered or re-read today then sat above one received an hour ago, and the
Selection view grouped by candidate, so a candidate's older mails sat above
another candidate's newer one. The order is now the Mail received column,
whatever the candidate, across pages and under every filter.

Run as the real query on the real schema (see test_booking_result_filter_pg).
"""
from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from tests.test_booking_result_filter_pg import alerts  # noqa: F401  (fixture)


def add(connect, alert_id, *, received_hours_ago, detected_hours_ago, candidate="c1",
        classification="interview_confirmed", read=False):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mail_monitoring_notifications(
                   id, candidate_id, gmail_message_id, classification, email_subject,
                   is_read, email_received_at, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,
                       CASE WHEN %s IS NULL THEN NULL ELSE now() - make_interval(hours => %s) END,
                       now() - make_interval(hours => %s))""",
            (alert_id, candidate, "gm-" + alert_id, classification, "Mail " + alert_id, read,
             received_hours_ago, received_hours_ago or 0, detected_hours_ago),
        )


@pytest.fixture
def mails(alerts):  # noqa: F811
    # Detection order is the reverse of receipt order on purpose: a recovered
    # mail ("recovered") was detected last but received long ago.
    add(alerts, "newest", candidate="c2", classification="offer_received",
        received_hours_ago=1, detected_hours_ago=30)
    add(alerts, "second", candidate="c1", received_hours_ago=2, detected_hours_ago=20)
    add(alerts, "third", candidate="c2", classification="joining_confirmed",
        received_hours_ago=3, detected_hours_ago=10, read=True)
    add(alerts, "fourth", candidate="c1", classification="hr_confirmation",
        received_hours_ago=4, detected_hours_ago=5)
    add(alerts, "recovered", candidate="c3", classification="offer_received",
        received_hours_ago=48, detected_hours_ago=0)
    return alerts


def page(limit=50, offset=0, **filters):
    rows, total = store.list_notifications(filters=filters, limit=limit, offset=offset)
    return [row["id"] for row in rows], total


def test_the_latest_received_mail_is_first_whatever_the_candidate(mails):
    ids, total = page()
    assert ids == ["newest", "second", "third", "fourth", "recovered"]
    assert total == 5


def test_the_order_holds_across_pages(mails):
    walked = []
    for offset in range(0, 5, 2):
        walked += page(limit=2, offset=offset)[0]
    assert walked == ["newest", "second", "third", "fourth", "recovered"]


def test_the_order_holds_under_filters(mails):
    assert page(classification_group="selection")[0] == ["newest", "third", "fourth", "recovered"]
    assert page(is_read=False)[0] == ["newest", "second", "fourth", "recovered"]
    assert page(candidate_id="c2")[0] == ["newest", "third"]
    assert page(search="Mail")[0] == ["newest", "second", "third", "fourth", "recovered"]


def test_oldest_first_is_the_exact_reverse(mails):
    assert page(sort="oldest")[0] == ["recovered", "fourth", "third", "second", "newest"]


def test_the_download_follows_the_same_order(mails):
    exported = [row["notification_id"] for row in store.export_notifications({"sort": "newest"})]
    assert exported == ["newest", "second", "third", "fourth", "recovered"]


def test_a_mail_without_a_received_time_falls_back_to_detection(alerts):  # noqa: F811
    add(alerts, "received", received_hours_ago=2, detected_hours_ago=9)
    add(alerts, "unknown", received_hours_ago=None, detected_hours_ago=1)
    assert page()[0] == ["unknown", "received"]
