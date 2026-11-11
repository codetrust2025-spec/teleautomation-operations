"""Selection / Interview Related: one card per Candidate + Company.

A group is listed when any of its mails matches the page (category, tab,
filters); its counts and its history are the whole visible history of that
candidate with that company, Selection and Interview alike. Company names
that differ only in case, punctuation or a legal suffix are one company.
Read-only. Run as the real query on the real schema (see
test_booking_result_filter_pg).
"""
from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from tests.test_booking_result_filter_pg import alerts  # noqa: F401  (fixture)


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
def mails(alerts):  # noqa: F811
    add(alerts, "h-join", candidate="ashok", company="HCLTech", hours_ago=1,
        classification="joining_confirmed", status="Joining Confirmed")
    add(alerts, "h-eval", candidate="ashok", company="hcltech", hours_ago=5,
        classification="post_interview_shortlisted", read=True)
    add(alerts, "h-int", candidate="ashok", company="HCLTech.", hours_ago=30,
        classification="interview_confirmed", read=True)
    add(alerts, "v-offer", candidate="gopal", company="Northwind Professional Services Pvt. Ltd", hours_ago=10,
        classification="offer_received", read=True, status="Offer Received")
    add(alerts, "v-join", candidate="gopal", company="Northwind Professional Services Pvt Ltd", hours_ago=40,
        classification="joining_confirmed", read=True)
    add(alerts, "c-int", candidate="ravi", company="Capgemini", hours_ago=2,
        classification="interview_shortlisted")
    return alerts


def test_a_selection_group_counts_its_whole_history(mails):
    result = store.list_notification_groups(filters={"classification_group": "selection"})
    by = {(g["candidate_id"], g["company_key"]): g for g in result["groups"]}
    assert result["total"] == 2 and len(by) == 2  # Capgemini has no selection mail
    ashok = by[("ashok", "hcltech")]
    assert (ashok["total"], ashok["unread"], ashok["read"]) == (3, 1, 2)
    assert ashok["latest_status"] == "Joining Confirmed"
    vhs = by[("gopal", "northwind professional services")]
    assert (vhs["total"], vhs["unread"]) == (2, 0)
    assert vhs["latest_status"] == "Offer Received"
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
