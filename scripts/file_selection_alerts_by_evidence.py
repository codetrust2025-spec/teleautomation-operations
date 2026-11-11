"""File existing Selection alerts by their mail's own evidence. Dry run unless --apply.

Run inside the operations-api container:

    docker exec -i -w /app -e PYTHONPATH=/app teleautomation-production-operations-api-1 \\
        python - < scripts/file_selection_alerts_by_evidence.py                    # dry run
    ... python - --apply --confirm N < scripts/file_selection_alerts_by_evidence.py  # apply

New alerts are filed by `selection_alert_filing` as they are written. This
applies the same rule, with the same company context, to visible alerts
written before it: a Selection alert whose mail states interview progress or
a shortlist, or an unproven "Selected" / "HR confirmation" claim, moves to the
Interview classification the rule names.

It changes the alert's classification and status label only: no alert is
created, dismissed, read or unread, and no analysis, event, booking, mail or
candidate row is touched. --apply writes only when --confirm equals the
dry-run count. Prints dates, companies and subjects; no record ids beyond a
short prefix.
"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", type=int)
    args = parser.parse_args(argv)

    from core.db.connection import get_connection
    from core import recruitment_mail_store as store
    from services.recruitment_mail_agent import clean_email
    from services.recruitment_semantics import SELECTION_ALERT_CLASSIFICATIONS, selection_alert_filing

    visible, visible_params = store.visible_rows_sql(prefix="n")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT n.id, n.classification, n.created_at::date AS created, n.company_name, n.email_subject,
                       coalesce(nullif(m.body_text,''), m.html_body_text, '') AS body,
                       EXISTS(SELECT 1 FROM mail_monitoring_notifications o
                               WHERE o.candidate_id = n.candidate_id AND o.id <> n.id
                                 AND split_part(lower(btrim(COALESCE(o.company_name, ''))),' ',1)
                                   = split_part(lower(btrim(COALESCE(n.company_name, ''))),' ',1)
                                 AND btrim(COALESCE(n.company_name, '')) <> ''
                                 AND o.classification = ANY(%s)) AS context
                  FROM mail_monitoring_notifications n
                  LEFT JOIN mailbox_messages m
                    ON m.provider_message_id = n.gmail_message_id AND m.candidate_id = n.candidate_id
                 WHERE {visible} AND n.classification = ANY(%s)
                 ORDER BY n.created_at""",
            [list(store._SELECTION_CONTEXT_CLASSIFICATIONS), *visible_params, sorted(SELECTION_ALERT_CLASSIFICATIONS)],
        )
        names = [d.name for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        moves = []
        for row in rows:
            texts = [row["email_subject"] or "", clean_email(row["body"] or "")]
            filed = selection_alert_filing(row["classification"], texts, selection_context=bool(row["context"]))
            if filed != row["classification"]:
                moves.append((row, filed))
                print(f"{row['id'][:8]} {row['created']} {row['classification']} -> {filed} | "
                      f"{row['company_name'] or '-'} | {(row['email_subject'] or '')[:70]}")
        print(f"{len(moves)} of {len(rows)} visible Selection alerts would move to Interview")
        if not args.apply:
            conn.rollback()
            print("dry run: nothing written")
            return 0
        if args.confirm != len(moves):
            conn.rollback()
            print(f"refused: --confirm {args.confirm} != {len(moves)}")
            return 2
        moved = 0
        for row, filed in moves:
            cur.execute(
                """UPDATE mail_monitoring_notifications SET classification=%s, candidate_status=%s, updated_at=now()
                    WHERE id=%s AND classification=%s
                      AND NOT EXISTS (SELECT 1 FROM mail_monitoring_notifications d
                                       WHERE d.gmail_message_id = mail_monitoring_notifications.gmail_message_id
                                         AND d.classification = %s)""",
                (filed, store._CLASSIFICATION_STATUS[filed], row["id"], row["classification"], filed),
            )
            moved += cur.rowcount
        if moved != len(moves):
            conn.rollback()
            print(f"refused: {moved} rows matched, expected {len(moves)}; nothing written")
            return 2
        conn.commit()
        print(f"applied: {moved} alerts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
