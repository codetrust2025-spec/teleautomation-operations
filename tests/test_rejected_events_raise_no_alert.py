"""An alert whose own event the validator rejected is not shown, nor counted.

On 2 Sep 2026 fifteen alerts were filed from events whose validation was
REJECTED: webinar invitations, job-alert digests, Indeed newsletters, an
application receipt and a OneDrive welcome mail -- three of them counted as
job selections with an invented "John Doe" summary. Routing now fails such an
event closed before an alert is written; the shared visibility rule keeps the
ones written before that off the screen and out of every card, without
rewriting the rows.

Checked read-only against production before shipping: visible 169 -> 154,
selections 9 -> 6, shortlists 26 -> 14, alerts with no event unchanged.
"""
from core import recruitment_mail_store as store


def test_the_shared_visibility_rule_excludes_rejected_events():
    clause, params = store.visible_rows_sql()
    assert "NOT EXISTS (SELECT 1 FROM ai_recruitment_events rejected_event" in clause
    assert "rejected_event.id = ai_recruitment_event_id" in clause
    assert "rejected_event.validation_status = 'REJECTED'" in clause
    assert params == []


def test_it_still_hides_dismissed_and_historical_rows():
    clause, _ = store.visible_rows_sql()
    assert clause.startswith("dismissed_at IS NULL AND COALESCE(booking_status,'') <> 'Historical Skipped'")


def test_the_cards_and_the_table_share_it():
    combined, _ = store.notification_visibility_sql()
    clause, _ = store.visible_rows_sql()
    assert clause in combined
