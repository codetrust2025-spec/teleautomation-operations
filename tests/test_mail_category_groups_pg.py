"""Selection / Interview Related: one card per Candidate + Company.

A group is listed when any of its mails matches the page (category, tab,
filters); its counts and its history are the whole visible history of that
candidate with that company, Selection and Interview alike. Company names
that differ only in case, punctuation or a legal suffix are one company.
Read-only. Run as the real query on the real schema (see
test_booking_result_filter_pg).
"""
from __future__ import annotations

import re

import pytest

from core import recruitment_mail_store as store
from tests.test_booking_result_filter_pg import MIGRATIONS, _create_table
from tests.test_booking_result_filter_pg import alerts  # noqa: F401  (fixture)


def mailbox_messages_ddl() -> list[str]:
    """The mail table the Joining Confirmed check reads text from, as the
    migrations define it, without its foreign key."""
    create = _create_table("001_recruitment_mail_tracking.sql", "mailbox_messages").replace(" ON DELETE CASCADE", "")
    alters = []
    for migration in ("005_recruitment_mail_historical_rescan.sql", "010_recruitment_mail_lifecycle_truth.sql"):
        alters += re.findall(r"ALTER TABLE mailbox_messages[^;]*;", (MIGRATIONS / migration).read_text())
    return [create, *alters]


@pytest.fixture
def schema(alerts):  # noqa: F811
    with alerts() as conn, conn.cursor() as cur:
        for statement in mailbox_messages_ddl():
            cur.execute(statement)
    return alerts


def add(connect, alert_id, *, candidate, company, hours_ago, classification, read=False, status=None):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mail_monitoring_notifications(
                   id, candidate_id, gmail_message_id, classification, candidate_status, company_name,
                   email_subject, is_read, email_received_at, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now() - make_interval(hours => %s), now())""",
            (alert_id, candidate, "gm-" + alert_id, classification, status, company,
             "Mail " + alert_id, read, hours_ago),
        )


@pytest.fixture
def mails(schema):
    add(schema, "h-join", candidate="ashok", company="HCLTech", hours_ago=1,
        classification="joining_confirmed", status="Joining Confirmed")
    add(schema, "h-eval", candidate="ashok", company="hcltech", hours_ago=5,
        classification="post_interview_shortlisted", read=True)
    add(schema, "h-int", candidate="ashok", company="HCLTech.", hours_ago=30,
        classification="interview_confirmed", read=True)
    add(schema, "v-offer", candidate="gopal", company="Northwind Professional Services Pvt. Ltd", hours_ago=10,
        classification="offer_received", read=True, status="Offer Received")
    add(schema, "v-join", candidate="gopal", company="Northwind Professional Services Pvt Ltd", hours_ago=40,
        classification="joining_confirmed", read=True)
    add(schema, "c-int", candidate="ravi", company="Capgemini", hours_ago=2,
        classification="interview_shortlisted")
    return schema


def test_a_selection_group_counts_its_whole_history(mails):
    result = store.list_notification_groups(filters={"classification_group": "selection"})
    by = {(g["candidate_id"], g["company_key"]): g for g in result["groups"]}
    assert result["total"] == 2 and len(by) == 2  # Capgemini has no selection mail
    ashok = by[("ashok", "hcltech")]
    assert (ashok["total"], ashok["unread"], ashok["read"]) == (3, 1, 2)
    # The card's own counts: Selection mail only, as the page's counters count.
    assert (ashok["category_total"], ashok["category_unread"], ashok["category_read"]) == (1, 1, 0)
    # Its newest selection mail is Joining Confirmed, but no mail text states anything.
    assert (ashok["latest_status"], ashok["latest_status_evidence"]) == ("Needs Review", "not stated")
    vhs = by[("gopal", "northwind professional services")]
    assert (vhs["total"], vhs["unread"]) == (2, 0)
    assert (vhs["latest_classification"], vhs["latest_status"]) == ("offer_received", "Needs Review")  # no mail text
    assert [g["candidate_id"] for g in result["groups"]] == ["ashok", "gopal"]


def test_the_history_is_both_categories_newest_first(mails):
    rows = store.list_group_emails("ashok", "hcltech")
    assert [r["id"] for r in rows] == ["h-join", "h-eval", "h-int"]


def test_tabs_and_filters_choose_groups_not_their_counts(mails):
    unread = store.list_notification_groups(filters={"classification_group": "selection", "is_read": False})
    assert [(g["candidate_id"], g["total"]) for g in unread["groups"]] == [("ashok", 3)]
    company = store.list_notification_groups(filters={"classification_group": "selection", "company_key": "northwind professional services"})
    assert [g["candidate_id"] for g in company["groups"]] == ["gopal"]
    interview = store.list_notification_groups(filters={"classification_group": "interview"})
    assert {g["candidate_id"] for g in interview["groups"]} == {"ashok", "ravi"}
    assert {c["key"] for c in interview["companies"]} == {"hcltech", "capgemini"}


def test_reading_writes_nothing(mails):
    before = store.list_group_emails("ashok", "hcltech")
    store.list_notification_groups(filters={"classification_group": "selection"})
    assert store.list_group_emails("ashok", "hcltech") == before


def test_name_variants_of_one_employer_are_one_card(schema):
    # "Acme" via an applicant-tracking system, "Acme Analytics" from the
    # employer: one person's one hiring process, so one card of three mails.
    add(schema, "a-1", candidate="meera", company="Acme", hours_ago=1, classification="joining_confirmed")
    add(schema, "a-2", candidate="meera", company="Acme Analytics", hours_ago=2, classification="offer_received", read=True)
    add(schema, "a-3", candidate="meera", company="Acme Analytics Private Limited", hours_ago=3,
        classification="interview_confirmed", read=True)
    add(schema, "z-1", candidate="meera", company="Zenith", hours_ago=4, classification="offer_received")
    result = store.list_notification_groups(filters={"classification_group": "selection"})
    cards = {g["company_key"]: g for g in result["groups"] if g["candidate_id"] == "meera"}
    assert set(cards) == {"acme analytics", "zenith"}
    acme = cards["acme analytics"]
    assert (acme["total"], acme["unread"], acme["read"]) == (3, 1, 2)
    assert [r["id"] for r in store.list_group_emails("meera", "acme analytics")] == ["a-1", "a-2", "a-3"]


def test_an_alerts_mail_is_read_in_the_app_with_gmail_optional(schema):
    add(schema, "r-1", candidate="ravi", company="Acme", hours_ago=1, classification="interview_needs_reading")
    with schema() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mailbox_messages(id, mailbox_id, candidate_id, provider_message_id, provider_thread_id,
                   sender_name, sender_email, recipient_email, subject, body_text)
               VALUES ('mm-1', 'mb-1', 'ravi', 'gm-r-1', 'th-1', 'Acme HR', 'hr@acme.example',
                       'ravi@example.com', 'Interview on Monday', 'Please join at 10 am.')""")
        cur.execute("UPDATE mail_monitoring_notifications SET gmail_thread_id = 'th-1' WHERE id = 'r-1'")
    result = store.notification_email("r-1")
    assert result["email"]["subject"] == "Interview on Monday"
    assert result["email"]["body"] == "Please join at 10 am."
    assert result["gmail"] == {"message_id": "gm-r-1", "thread_id": "th-1", "account": "ravi@example.com"}
    # Reading is not marking read.
    assert store.list_group_emails("ravi", "acme")[0]["is_read"] is False
    assert store.notification_email("no-such-alert") is None


def test_interview_emails_are_one_row_each_newest_first(mails):
    add(mails, "c-int2", candidate="ravi", company="Capgemini Ltd", hours_ago=3,
        classification="interview_confirmed", read=True)
    result = store.list_category_emails(filters={"classification_group": "interview"})
    # A shortlist after an interview is Interview progress, so h-eval is listed.
    assert [r["id"] for r in result["emails"]] == ["c-int", "c-int2", "h-eval", "h-int"]
    assert result["total"] == 4
    # Name variants share one employer key, as the company filter uses it.
    assert {r["company_key"] for r in result["emails"]} == {"capgemini", "hcltech"}
    assert all("verified_status" in r and "status_evidence" in r for r in result["emails"])


def test_interview_emails_follow_the_tab_company_and_paging(mails):
    add(mails, "c-int2", candidate="ravi", company="Capgemini Ltd", hours_ago=3,
        classification="interview_confirmed", read=True)
    unread = store.list_category_emails(filters={"classification_group": "interview", "is_read": False})
    assert [r["id"] for r in unread["emails"]] == ["c-int"] and unread["total"] == 1
    cap = store.list_category_emails(filters={"classification_group": "interview", "company_key": "capgemini"})
    assert [r["id"] for r in cap["emails"]] == ["c-int", "c-int2"]
    second = store.list_category_emails(filters={"classification_group": "interview"}, limit=1, offset=1)
    assert [r["id"] for r in second["emails"]] == ["c-int2"] and second["total"] == 4


@pytest.fixture(autouse=True)
def _fresh_company_resolution():
    store.clear_resolved_companies()
    yield
    store.clear_resolved_companies()


def add_sent(connect, alert_id, *, candidate, company, sender, subject, hours_ago, classification="interview_confirmed", booking_id=None):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mail_monitoring_notifications(
                   id, candidate_id, candidate_name, gmail_message_id, classification, company_name, sender_email,
                   email_subject, is_read, booking_id, email_received_at, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,false,%s, now() - make_interval(hours => %s), now())""",
            (alert_id, candidate, candidate.title(), "gm-" + alert_id, classification, company, sender,
             subject, booking_id, hours_ago),
        )


def test_an_interview_email_without_a_company_shows_the_one_its_evidence_proves(schema, monkeypatch):
    monkeypatch.setattr(store, "_booking_companies", lambda: {"bk-1": "DXC Technology"})
    add_sent(schema, "named", candidate="ravi", company="HCLTech", sender="hr@hcltech.com", subject="HCLTech | Interview", hours_ago=9)
    add_sent(schema, "by-domain", candidate="ravi", company=None, sender="hr-team@hcltech.com", subject="Ravi", hours_ago=1)
    add_sent(schema, "by-booking", candidate="meera", company="", sender="a@dxc.com", subject="Tech 1 Interview || Meera", hours_ago=2, booking_id="bk-1")
    add_sent(schema, "unproven", candidate="meera", company=None, sender="x@inbound.workablemail.com", subject="Interview for Developer job", hours_ago=3)
    result = store.list_category_emails(filters={"classification_group": "interview"})
    by = {r["id"]: r for r in result["emails"]}
    assert (by["by-domain"]["company_name"], by["by-domain"]["company_source"]) == ("HCLTech", "sender")
    assert (by["by-booking"]["company_name"], by["by-booking"]["company_source"]) == ("DXC Technology", "booking")
    assert not by["unproven"]["company_name"] and by["unproven"]["company_source"] is None
    assert by["named"]["company_source"] is None  # a stored company is shown as stored
    # The company filter, search and options see the resolved company too.
    hcl = store.list_category_emails(filters={"classification_group": "interview", "company_key": "hcltech"})
    assert sorted(r["id"] for r in hcl["emails"]) == ["by-domain", "named"]
    found = store.list_category_emails(filters={"classification_group": "interview", "search": "DXC"})
    assert [r["id"] for r in found["emails"]] == ["by-booking"]
    assert {c["key"] for c in result["companies"]} >= {"hcltech", "dxc technology"}
    # Nothing is written: the stored rows still hold no company.
    with schema() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, company_name FROM mail_monitoring_notifications WHERE id IN ('by-domain','by-booking','unproven') ORDER BY id")
        assert cur.fetchall() == [("by-booking", ""), ("by-domain", None), ("unproven", None)]


def test_the_backfill_stores_what_the_resolver_proves_and_can_be_undone(schema, monkeypatch, tmp_path):
    from scripts import backfill_alert_companies as backfill

    monkeypatch.setattr(store, "_booking_companies", lambda: {"bk-1": "DXC Technology"})
    add_sent(schema, "named", candidate="ravi", company="HCLTech", sender="hr@hcltech.com", subject="HCLTech | Interview", hours_ago=9)
    add_sent(schema, "by-domain", candidate="ravi", company=None, sender="hr-team@hcltech.com", subject="Ravi", hours_ago=1)
    add_sent(schema, "by-booking", candidate="meera", company="", sender="a@dxc.com", subject="Tech 1 Interview || Meera", hours_ago=2, booking_id="bk-1")
    add_sent(schema, "unproven", candidate="meera", company=None, sender="x@inbound.workablemail.com", subject="Interview for Developer job", hours_ago=3)
    with schema() as conn, conn.cursor() as cur:
        cur.execute("SELECT company_name, company_source FROM mail_monitoring_notifications WHERE id = 'named'")
        assert cur.fetchone() == ("HCLTech", None)

    assert backfill.main([]) == 0  # a dry run writes nothing
    with schema() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mail_monitoring_notifications WHERE company_source IS NOT NULL")
        assert cur.fetchone()[0] == 0

    backup = tmp_path / "backup.json"
    assert backfill.main(["--apply", "--backup", str(backup)]) == 0
    with schema() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, company_name, company_source FROM mail_monitoring_notifications ORDER BY id")
        assert cur.fetchall() == [("by-booking", "DXC Technology", "booking"), ("by-domain", "HCLTech", "sender"),
                                  ("named", "HCLTech", None), ("unproven", None, None)]
    # The screen shows the stored company with the source it recorded.
    by = {r["id"]: r for r in store.list_category_emails(filters={"classification_group": "interview"})["emails"]}
    assert (by["by-domain"]["company_name"], by["by-domain"]["company_source"]) == ("HCLTech", "sender")
    assert by["named"]["company_source"] is None

    # Idempotent: a second run finds nothing left to store.
    with schema() as conn, conn.cursor() as cur:
        assert backfill.plan(cur) == []

    # A company set by hand after the run is kept by --restore; the rest go back.
    with schema() as conn, conn.cursor() as cur:
        cur.execute("UPDATE mail_monitoring_notifications SET company_name = 'DXC', company_source = NULL WHERE id = 'by-booking'")
    assert backfill.main(["--restore", str(backup)]) == 0
    with schema() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, company_name, company_source FROM mail_monitoring_notifications ORDER BY id")
        assert cur.fetchall() == [("by-booking", "DXC", None), ("by-domain", None, None),
                                  ("named", "HCLTech", None), ("unproven", None, None)]


def test_a_stored_resolution_is_not_evidence_for_another_alert(schema, monkeypatch):
    monkeypatch.setattr(store, "_booking_companies", lambda: {})
    # Only a resolved row names a company for this domain: it proves nothing.
    add_sent(schema, "resolved", candidate="ravi", company="Northwind", sender="a@northwind.com", subject="Interview", hours_ago=2)
    add_sent(schema, "empty", candidate="ravi", company=None, sender="b@northwind.com", subject="Round 2", hours_ago=1)
    with schema() as conn, conn.cursor() as cur:
        cur.execute("UPDATE mail_monitoring_notifications SET company_source = 'subject' WHERE id = 'resolved'")
        assert store.resolved_alert_companies(cur, refresh=True) == {}
