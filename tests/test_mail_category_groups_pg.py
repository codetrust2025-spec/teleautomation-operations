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
