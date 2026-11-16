"""Selection Alerts: one mail is one alert, and an alert sits on its employer's card.

16 Nov 2026, from production data:

- every digiverifier BGV reminder arrived twice (two SES messages, identical
  sender, subject, content and sent time, two Gmail ids), the reader could not
  read them, and each copy became its own "Needs reading" alert: one card of 8
  mails held 3 mails twice;
- one Onni mail filed as HR Confirmation in September came back as Joining
  Confirmed when re-read in November: two alerts, one Gmail message;
- a VHS offer, its RE: reply and a BGV mail from vhspro.net sat on a "Company
  not identified" card beside the VHS card, because Selection grouped by the
  stored company only, while the resolver (thread, sender domain) already named
  VHS for Interview Related.

Genuine mails are kept: a resend minutes later, or the same subject on another
day, is its own alert. Run as the real queries on the real schema (CI).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core import recruitment_mail_store as store
from tests.test_mail_category_groups_pg import _fresh_company_resolution, schema  # noqa: F401  (fixtures)
from tests.test_booking_result_filter_pg import alerts  # noqa: F401  (fixture)

SENT = datetime(2026, 11, 14, 13, 30, 15, tzinfo=timezone.utc)
BGV = "Reminder: Invitation - Digital Employment BGV_RH29885831"


def mail(provider_id, *, sent=SENT, subject=BGV, sender="noreply@digiverifier.example", candidate="sakthi"):
    return {"provider_message_id": provider_id, "provider_thread_id": "th-" + provider_id, "candidate_id": candidate,
            "subject": subject, "sender_name": "Digiverifier", "sender_email": sender, "sent_at": sent}


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(store, "_candidate_snapshot", lambda cid, structured: ("Sakthi", "sakthi@example.com"))
    monkeypatch.setattr(store, "canonical_candidate_id", lambda cid: cid)


def alerts_of(connect, candidate="sakthi"):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT gmail_message_id, classification, dismissed_at IS NOT NULL FROM mail_monitoring_notifications"
                    " WHERE candidate_id = %s ORDER BY gmail_message_id, classification", (candidate,))
        return cur.fetchall()


def test_an_identical_copy_of_an_unread_selection_mail_is_not_a_second_alert(schema, quiet):  # noqa: F811
    assert store.record_unread_selection_alert(mail("gm-a"), evidence="a background check")
    # The same reminder, delivered again under another Gmail id, same second.
    assert store.record_unread_selection_alert(mail("gm-b"), evidence="a background check") is None
    # Retrying the first is still a no-op.
    assert store.record_unread_selection_alert(mail("gm-a"), evidence="a background check") is None
    assert alerts_of(schema) == [("gm-a", "selection_needs_reading", False)]


def test_genuine_repeats_are_kept(schema, quiet):  # noqa: F811
    store.record_unread_selection_alert(mail("gm-1"), evidence="a background check")
    # The next reminder ten hours later, and a re-sent invitation 75 minutes later.
    assert store.record_unread_selection_alert(mail("gm-2", sent=SENT + timedelta(hours=10)), evidence="a background check")
    assert store.record_unread_selection_alert(mail("gm-3", sent=SENT + timedelta(minutes=75), subject="Invitation - BGV"),
                                               evidence="a background check")
    # Same subject and time, another sender: another mail.
    assert store.record_unread_selection_alert(mail("gm-4", sender="hr@vhspro.example"), evidence="an offer")
    # Same mail text for another candidate: their own alert.
    assert store.record_unread_selection_alert(mail("gm-5", candidate="gopal"), evidence="a background check")
    assert [r[0] for r in alerts_of(schema)] == ["gm-1", "gm-2", "gm-3", "gm-4"]
    assert [r[0] for r in alerts_of(schema, "gopal")] == ["gm-5"]


def test_a_copy_of_a_dismissed_alert_stays_away(schema, quiet):  # noqa: F811
    store.record_unread_selection_alert(mail("gm-x"), evidence="a background check")
    with schema() as conn, conn.cursor() as cur:
        cur.execute("UPDATE mail_monitoring_notifications SET dismissed_at = now()")
    assert store.record_unread_selection_alert(mail("gm-y"), evidence="a background check") is None
    assert alerts_of(schema) == [("gm-x", "selection_needs_reading", True)]


def test_unread_interview_copies_are_one_alert_too(schema, quiet):  # noqa: F811
    invite = dict(subject="Interview for Senior Developer", sender="jobs@acme.example")
    assert store.record_unread_interview_alert(mail("gi-1", **invite), evidence="a time")
    assert store.record_unread_interview_alert(mail("gi-2", **invite), evidence="a time") is None
    assert store.record_unread_interview_alert(mail("gi-3", sent=SENT + timedelta(seconds=25), **invite), evidence="a time")
    assert [r[0] for r in alerts_of(schema)] == ["gi-1", "gi-3"]


def _stored_mail(connect, mm_id, provider_id, *, sent=SENT, subject="Thirdparty verification on behalf of Onni"):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mailbox_messages(id, mailbox_id, candidate_id, provider_message_id, provider_thread_id,
                   sender_name, sender_email, recipient_email, subject, sent_at)
               VALUES (%s, 'mb-y', 'yamini', %s, %s, 'Onboarding', 'onboarding@onni.example', 'y@example.com', %s, %s)""",
            (mm_id, provider_id, "th-" + provider_id, subject, sent))


def _classify(provider_mm, classification, event_id):
    return store.create_monitoring_notification(
        {"id": event_id, "mailbox_message_id": provider_mm, "candidate_id": "yamini", "structured_result": {},
         "confidence": 0.95, "requires_manual_review": False},
        {"id": "an-" + event_id, "classification": classification, "confidence": 0.95},
        silent=True,
    )


@pytest.fixture
def classify(schema, quiet, monkeypatch):  # noqa: F811
    monkeypatch.setattr(store, "_selection_alert_filing", lambda cur, ev, st, classification, mid, subject: classification)
    monkeypatch.setattr(store, "should_route_to_mail_alert", lambda *a, **k: True)
    return schema


def test_a_reread_mail_moves_its_alert_instead_of_adding_one(classify):
    _stored_mail(classify, "mm-1", "gm-onni")
    first = _classify("mm-1", "hr_confirmation", "ev-1")
    with classify() as conn, conn.cursor() as cur:
        cur.execute("UPDATE mail_monitoring_notifications SET is_read = true")
    again = _classify("mm-1", "joining_confirmed", "ev-2")
    assert again["id"] == first["id"]
    assert alerts_of(classify, "yamini") == [("gm-onni", "joining_confirmed", False)]
    with classify() as conn, conn.cursor() as cur:
        cur.execute("SELECT is_read, ai_recruitment_event_id, candidate_status FROM mail_monitoring_notifications")
        assert cur.fetchone() == (True, "ev-2", store._CLASSIFICATION_STATUS["joining_confirmed"])


def test_a_needs_reading_mail_the_reader_later_reads_keeps_one_alert(classify):
    _stored_mail(classify, "mm-2", "gm-late", subject=BGV)
    store.record_unread_selection_alert(
        {**mail("gm-late"), "candidate_id": "yamini", "sender_email": "onboarding@onni.example"}, evidence="a background check")
    _classify("mm-2", "background_verification", "ev-3")
    assert alerts_of(classify, "yamini") == [("gm-late", "background_verification", False)]


def test_an_identical_copy_read_by_the_ai_is_not_a_second_alert(classify):
    _stored_mail(classify, "mm-3", "gm-c1")
    _stored_mail(classify, "mm-4", "gm-c2")  # same sender, subject and second
    _stored_mail(classify, "mm-5", "gm-c3", sent=SENT + timedelta(minutes=3))  # a genuine resend
    first = _classify("mm-3", "offer_received", "ev-4")
    assert _classify("mm-4", "offer_received", "ev-5")["id"] == first["id"]
    _classify("mm-5", "offer_received", "ev-6")
    assert [r[0] for r in alerts_of(classify, "yamini")] == ["gm-c1", "gm-c3"]


def test_a_mail_without_a_company_sits_on_its_threads_employer_card(schema):  # noqa: F811
    with schema() as conn, conn.cursor() as cur:
        for alert_id, company, thread, sender, classification in (
            ("v-offer", "VHS Professional Services Pvt. Ltd", "th-vhs", "hr@vhspro.example", "offer_received"),
            ("v-copy", None, "th-vhs", "hr@vhspro.example", "offer_received"),
            ("v-reply", None, "th-vhs", "hr@vhspro.example", "joining_date_updated"),
        ):
            cur.execute(
                """INSERT INTO mail_monitoring_notifications(id, candidate_id, gmail_message_id, gmail_thread_id,
                       classification, company_name, sender_email, email_subject, is_read, email_received_at, created_at)
                   VALUES (%s, 'gopal', %s, %s, %s, %s, %s, 'Offer of Employment', true, now(), now())""",
                (alert_id, "gm-" + alert_id, thread, classification, company, sender))
    result = store.list_notification_groups(filters={"classification_group": "selection"})
    cards = [(g["company_key"], g["category_total"]) for g in result["groups"] if g["candidate_id"] == "gopal"]
    assert cards == [("vhs professional services", 3)]
    assert {r["id"] for r in store.list_group_emails("gopal", "vhs professional services")} == {"v-offer", "v-copy", "v-reply"}
    # Display only: the stored rows keep no company.
    with schema() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mail_monitoring_notifications WHERE company_name IS NULL")
        assert cur.fetchone()[0] == 2


def test_the_cleanup_dismisses_only_duplicates_and_can_be_undone(schema):  # noqa: F811
    from scripts import dedupe_selection_alerts as dedupe
    with schema() as conn, conn.cursor() as cur:
        rows = [
            # Two copies of one reminder; the later copy is the one somebody opened.
            ("s-1", "sakthi", "gm-1", BGV, SENT, "selection_needs_reading", False, 1),
            ("s-2", "sakthi", "gm-2", BGV, SENT, "selection_needs_reading", True, 2),
            # The next reminder: genuine.
            ("s-3", "sakthi", "gm-3", BGV, SENT + timedelta(hours=10), "selection_needs_reading", False, 3),
            # One Gmail message read twice: keep the newest reading.
            ("y-1", "yamini", "gm-y", "Thirdparty verification", SENT, "hr_confirmation", True, 4),
            ("y-2", "yamini", "gm-y", "Thirdparty verification", SENT, "joining_confirmed", True, 5),
            # An interview alert is never touched here.
            ("i-1", "sakthi", "gm-i1", "Interview", SENT, "interview_confirmed", False, 6),
            ("i-2", "sakthi", "gm-i2", "Interview", SENT, "interview_confirmed", False, 7),
        ]
        for alert_id, cand, gid, subject, sent, classification, read, order in rows:
            cur.execute(
                """INSERT INTO mail_monitoring_notifications(id, candidate_id, gmail_message_id, classification,
                       sender_email, email_subject, email_received_at, is_read, created_at)
                   VALUES (%s,%s,%s,%s,'noreply@digiverifier.example',%s,%s,%s, now() + make_interval(secs => %s))""",
                (alert_id, cand, gid, classification, subject, sent, read, order))
        changes = dedupe.plan(cur)
    assert sorted((c["drop"], c["keep"]) for c in changes) == [("s-2", "s-1"), ("y-1", "y-2")]
    with schema() as conn, conn.cursor() as cur:
        assert dedupe.apply(cur, changes) == {"dismissed": 2, "marked_read": 1}
    with schema() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, dismissed_at IS NOT NULL, is_read FROM mail_monitoring_notifications ORDER BY id")
        state = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    assert state["s-1"] == (False, True)  # kept, and carries the reading the copy had
    assert state["s-2"][0] and state["y-1"][0]
    assert not any(state[i][0] for i in ("s-3", "y-2", "i-1", "i-2"))
    with schema() as conn, conn.cursor() as cur:
        assert dedupe.plan(cur) == []  # a second run finds nothing
        assert dedupe.restore(cur, changes) == {"undismissed": 2, "marked_unread": 1}
    with schema() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mail_monitoring_notifications WHERE dismissed_at IS NOT NULL")
        assert cur.fetchone()[0] == 0
