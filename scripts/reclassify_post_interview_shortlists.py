"""Move existing post-interview shortlist alerts to Selection Related. Dry run unless --apply.

Run inside the operations-api container:

    docker exec -i -w /app -e PYTHONPATH=/app teleautomation-production-operations-api-1 \\
        python - < scripts/reclassify_post_interview_shortlists.py                    # dry run
    ... python - --apply --confirm N < scripts/reclassify_post_interview_shortlists.py  # apply

Mail Alerts filed every shortlist as `interview_shortlisted` (Interview
Related). New mail is now filed as `post_interview_shortlisted` when the mail
places the candidate past the interviews (`is_post_interview_shortlist`). This
applies the same rule, from the mail's own subject and body, to alerts written
before it.

It changes the alert's classification and status label, and the stored
analysis's, nothing else: no alert is created, dismissed, read or unread, and
no booking, event or mail row is touched. --apply writes only when --confirm
equals the dry-run count, so a set that moved since it was reviewed refuses to
run. Prints dates, companies and subjects; no record ids beyond a short prefix.
"""
from __future__ import annotations

import argparse
import sys

NEW = "post_interview_shortlisted"
OLD = "interview_shortlisted"


def candidates(rows, *, clean, is_post):
    """The alerts to move: an interview shortlist whose own mail is post-interview."""
    chosen = []
    for row in rows:
        if row["classification"] != OLD:
            continue
        texts = [row.get("email_subject") or "", clean(row.get("body") or "")]
        if any(is_post(text) for text in texts if text):
            chosen.append(row)
    return chosen


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", type=int)
    args = parser.parse_args(argv)

    from core.db.connection import get_connection
    from core import recruitment_mail_store as store
    from services.recruitment_mail_agent import clean_email
    from services.recruitment_semantics import is_post_interview_shortlist

    label = store._CLASSIFICATION_STATUS[NEW]
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT n.id, n.classification, n.created_at::date AS created, n.company_name,
                      n.email_subject, n.dismissed_at IS NULL AS visible, m.id AS message_id,
                      coalesce(nullif(m.body_text,''), m.html_body_text, '') AS body
                 FROM mail_monitoring_notifications n
                 LEFT JOIN mailbox_messages m
                   ON m.provider_message_id = n.gmail_message_id AND m.candidate_id = n.candidate_id
                WHERE n.classification = %s
                ORDER BY n.created_at""",
            (OLD,),
        )
        names = [d.name for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        chosen = candidates(rows, clean=clean_email, is_post=is_post_interview_shortlist)
        for row in chosen:
            state = "visible" if row["visible"] else "dismissed"
            print(f"{row['id'][:8]} {row['created']} {state:9} {row['company_name'] or '-'} | {(row['email_subject'] or '')[:80]}")
        print(f"{len(chosen)} of {len(rows)} {OLD} alerts would move to {NEW} ({label})")
        if not args.apply:
            conn.rollback()
            print("dry run: nothing written")
            return 0
        if args.confirm != len(chosen):
            conn.rollback()
            print(f"refused: --confirm {args.confirm} != {len(chosen)}")
            return 2
        ids = [row["id"] for row in chosen]
        messages = [row["message_id"] for row in chosen if row["message_id"]]
        cur.execute(
            """UPDATE mail_monitoring_notifications SET classification=%s, candidate_status=%s, updated_at=now()
                WHERE id = ANY(%s) AND classification = %s""",
            (NEW, label, ids, OLD),
        )
        moved = cur.rowcount
        if moved != len(chosen):
            conn.rollback()
            print(f"refused: {moved} rows matched, expected {len(chosen)}; nothing written")
            return 2
        cur.execute(
            """UPDATE mail_ai_analyses SET classification=%s, candidate_status=%s, updated_at=now()
                WHERE mailbox_message_id = ANY(%s) AND classification = %s""",
            (NEW, label, messages, OLD),
        )
        analyses = cur.rowcount
        conn.commit()
        print(f"applied: {moved} alerts, {analyses} analyses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
