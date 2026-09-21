"""Which of a person's ids hold mail, asked of the real schema in one query.

The Candidate history timeline used to ask every booking row's id for alerts
and recruitment events -- two queries per row, twenty-five rows for one
candidate. This is the query that finds the one or two ids worth asking.
"""
from __future__ import annotations

import re

from core import recruitment_mail_store as store
from test_booking_result_filter_pg import MIGRATIONS, add, alerts  # noqa: F401  (fixture)


def events_table(connect):
    ddl = (MIGRATIONS / "001_recruitment_mail_tracking.sql").read_text()
    start = ddl.index("CREATE TABLE IF NOT EXISTS ai_recruitment_events")
    depth = 0
    for index in range(start, len(ddl)):
        depth += (ddl[index] == "(") - (ddl[index] == ")")
        if depth == 0 and ddl[index] == ")":
            create = re.sub(r"\s+REFERENCES\s+\w+\(\w+\)(\s+ON DELETE \w+( \w+)?)?", "", ddl[start:index + 1])
            break
    with connect() as conn, conn.cursor() as cur:
        cur.execute(create + ";")


def add_event(connect, event_id, candidate):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ai_recruitment_events(id, candidate_id, primary_status, confidence, structured_result)
               VALUES (%s, %s, 'SHORTLISTED', 0.9, '{}'::jsonb)""",
            (event_id, candidate),
        )


def test_only_the_ids_holding_alerts_or_events_come_back(alerts):  # noqa: F811
    events_table(alerts)
    add(alerts, "a1", candidate="profile")
    add(alerts, "a2", candidate="profile")
    add_event(alerts, "e1", "older-profile")
    add(alerts, "a3", candidate="someone-else")

    found = store.candidate_ids_with_mail(["profile", "older-profile", "slot-1", "slot-2"])

    assert found == {"profile", "older-profile"}
    assert store.candidate_ids_with_mail([]) == set()
