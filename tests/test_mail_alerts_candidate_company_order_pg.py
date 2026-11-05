"""Mail Alerts is grouped Candidate -> Company -> Mail, each by Mail received.

Candidates follow their newest mail; within a candidate, companies follow
that company's newest mail; within a company, mails are newest first. Two
companies of one candidate (HCLTech and Infosys) are two sections, never
interleaved. The page unit is the candidate, so the order must hold across
pages and under every filter.

Run as the real query on the real schema (see test_booking_result_filter_pg).
"""
from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from tests.test_booking_result_filter_pg import alerts  # noqa: F401  (fixture)


def add(connect, alert_id, *, candidate, company, received_hours_ago,
        classification="interview_confirmed", detected_hours_ago=0):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mail_monitoring_notifications(
                   id, candidate_id, gmail_message_id, classification, company_name,
                   email_subject, email_received_at, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,
                       now() - make_interval(hours => %s), now() - make_interval(hours => %s))""",
            (alert_id, candidate, "gm-" + alert_id, classification, company, "Mail " + alert_id,
             received_hours_ago, detected_hours_ago),
        )


@pytest.fixture
def mails(alerts):  # noqa: F811
    # Ashok: HCLTech and Infosys mails interleaved in time; HCLTech holds his
    # newest mail, so it comes first. Detection order is scrambled on purpose.
    add(alerts, "hcl-1", candidate="ashok", company="HCLTech", received_hours_ago=1, detected_hours_ago=9)
    add(alerts, "inf-1", candidate="ashok", company="Infosys", received_hours_ago=2, detected_hours_ago=1)
    add(alerts, "hcl-2", candidate="ashok", company=" hcltech ", received_hours_ago=3)
    add(alerts, "inf-2", candidate="ashok", company="Infosys", received_hours_ago=4)
    add(alerts, "hcl-3", candidate="ashok", company="HCLTech", received_hours_ago=50,
        classification="offer_received")
    # Puja: newest mail older than Ashok's, so second.
    add(alerts, "fra-1", candidate="puja", company="Fractal", received_hours_ago=5,
        classification="joining_confirmed")
    add(alerts, "none-1", candidate="puja", company=None, received_hours_ago=6)
    # Ravi: oldest candidate.
    add(alerts, "tcs-1", candidate="ravi", company="TCS", received_hours_ago=20,
        classification="offer_received")
    return alerts


def grouped(limit=50, offset=0, **filters):
    rows, total = store.list_notifications(
        filters=filters, limit=limit, offset=offset, group_by_candidate=True)
    return [row["id"] for row in rows], total


def test_candidate_then_company_then_mail_newest_first(mails):
    ids, total = grouped()
    assert ids == [
        "hcl-1", "hcl-2", "hcl-3",  # Ashok / HCLTech (latest 1h; " hcltech " is the same company)
        "inf-1", "inf-2",           # Ashok / Infosys (latest 2h)
        "fra-1",                    # Puja / Fractal (latest 5h)
        "none-1",                   # Puja / no company
        "tcs-1",                    # Ravi
    ]
    assert total == 3  # candidates


def test_the_order_holds_across_candidate_pages(mails):
    walked = []
    for offset in range(3):
        walked += grouped(limit=1, offset=offset)[0]
    assert walked == grouped()[0]


def test_the_order_holds_under_filters(mails):
    # Candidates follow their newest mail among those the filter keeps: Ashok's
    # only selection mail is two days old, so he moves to the end.
    assert grouped(classification_group="selection")[0] == ["fra-1", "tcs-1", "hcl-3"]
    assert grouped(candidate_id="ashok")[0] == ["hcl-1", "hcl-2", "hcl-3", "inf-1", "inf-2"]
    # A search narrows the sections to the mails it matches.
    assert grouped(search="Mail inf")[0] == ["inf-1", "inf-2"]


def test_oldest_first_reverses_every_level(mails):
    assert grouped(sort="oldest")[0] == [
        "tcs-1", "none-1", "fra-1", "inf-2", "inf-1", "hcl-3", "hcl-2", "hcl-1",
    ]
