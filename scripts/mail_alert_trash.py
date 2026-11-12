"""Move Interview Related Mail Alerts to Trash, purge them, or restore them.

Dry run unless --apply. Run inside the operations-api container:
    docker cp ids.json teleautomation-production-operations-api-1:/tmp/ids.json
    docker exec -i -w /app -e PYTHONPATH=/app teleautomation-production-operations-api-1 \\
        python - trash --ids-file /tmp/ids.json --reason "..." --actor owner \\
        --backup-ref "..." < scripts/mail_alert_trash.py                      # dry run
    ... python - trash ... --apply --confirm 130 < scripts/mail_alert_trash.py   # apply

  trash    copy each alert row into mail_alert_trash and hide it from the screen
  purge    delete the alert rows of trashed alerts (the trash copy stays)
  restore  put alerts back from their trash copy and lift the reimport block

--apply writes only when --confirm equals the number of records the command
would change, so a list that moved since it was approved refuses to run. Every
command runs in one transaction: one refused record refuses the whole batch.

Trash refuses any alert that is not Interview Related, has a booking (on the
alert, in the booking audit or in the interview lifecycle), or has review
evaluations. Only alert rows are ever removed: emails, analyses, bookings,
payments and audit history stay, and nothing is deleted from Gmail. While a
trash row exists, migration 038's trigger stops that email from raising an
Interview Related alert again; Selection Related alerts are never blocked.
"""
from __future__ import annotations

import argparse
import json
import sys

from core.recruitment_mail_store import INTERVIEW_RELATED_CLASSIFICATIONS, get_connection


def _ids(path: str) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        ids = json.load(handle)
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        raise SystemExit("--ids-file must hold a non-empty JSON list of alert ids")
    if len(set(ids)) != len(ids):
        raise SystemExit("--ids-file lists an alert twice")
    return ids


def _refusals(cur, ids: list[str]) -> list[str]:
    cur.execute("""
        SELECT p.id,
          n.id IS NULL AS missing,
          n.classification,
          COALESCE(n.booking_id,'') <> '' OR COALESCE(n.booking_status,'') <> '' AS booked,
          EXISTS(SELECT 1 FROM interview_auto_booking_audit a
                 WHERE a.gmail_message_id=n.gmail_message_id AND a.booking_id IS NOT NULL) AS audit_booking,
          EXISTS(SELECT 1 FROM interview_lifecycle_states l WHERE l.source_message_id=n.gmail_message_id) AS lifecycle,
          EXISTS(SELECT 1 FROM mail_review_evaluations r WHERE r.notification_id=n.id) AS reviewed,
          EXISTS(SELECT 1 FROM mail_alert_trash t WHERE t.notification_id=p.id) AS already
        FROM unnest(%s::text[]) AS p(id)
        LEFT JOIN mail_monitoring_notifications n ON n.id=p.id""", (ids,))
    refused = []
    for alert_id, missing, classification, booked, audit, lifecycle, reviewed, already in cur.fetchall():
        reasons = [name for name, hit in (
            ("not found", missing),
            (f"not Interview Related ({classification})",
             not missing and classification not in INTERVIEW_RELATED_CLASSIFICATIONS),
            ("has a booking", booked), ("booking audit", audit), ("interview lifecycle", lifecycle),
            ("review evaluations", reviewed), ("already in Trash", already),
        ) if hit]
        if reasons:
            refused.append(f"{alert_id}: {', '.join(reasons)}")
    return refused


def trash(cur, ids, args) -> int:
    refused = _refusals(cur, ids)
    if refused:
        raise SystemExit("refused, nothing written:\n  " + "\n  ".join(refused))
    cur.execute("""
        INSERT INTO mail_alert_trash(notification_id,gmail_message_id,candidate_id,classification,
          row_data,reason,trashed_by,backup_reference)
        SELECT n.id,n.gmail_message_id,n.candidate_id,n.classification,to_jsonb(n),%s,%s,%s
        FROM mail_monitoring_notifications n WHERE n.id = ANY(%s)""",
        (args.reason, args.actor, args.backup_ref, ids))
    return cur.rowcount


def purge(cur, ids, _args) -> int:
    cur.execute("""
        SELECT p.id FROM unnest(%s::text[]) AS p(id)
        LEFT JOIN mail_alert_trash t ON t.notification_id=p.id AND t.purged_at IS NULL
        LEFT JOIN mail_monitoring_notifications n ON n.id=p.id
        WHERE t.notification_id IS NULL OR n.id IS NULL OR to_jsonb(n) <> t.row_data""", (ids,))
    refused = [row[0] for row in cur.fetchall()]
    if refused:
        raise SystemExit("refused, nothing deleted -- not in Trash, already purged, or changed since "
                         "it was trashed:\n  " + "\n  ".join(refused))
    cur.execute("DELETE FROM mail_monitoring_notifications WHERE id = ANY(%s)", (ids,))
    deleted = cur.rowcount
    cur.execute("UPDATE mail_alert_trash SET purged_at=now() WHERE notification_id = ANY(%s)", (ids,))
    if cur.rowcount != deleted:
        raise SystemExit("purge count mismatch; rolled back")
    return deleted


def restore(cur, ids, _args) -> int:
    cur.execute("""SELECT p.id FROM unnest(%s::text[]) AS p(id)
        LEFT JOIN mail_alert_trash t ON t.notification_id=p.id WHERE t.notification_id IS NULL""", (ids,))
    refused = [row[0] for row in cur.fetchall()]
    if refused:
        raise SystemExit("refused, not in Trash:\n  " + "\n  ".join(refused))
    cur.execute("""DELETE FROM mail_alert_trash WHERE notification_id = ANY(%s)
        RETURNING notification_id, row_data, purged_at IS NOT NULL""", (ids,))
    rows = cur.fetchall()
    for _id, row_data, purged in rows:
        if purged:
            cur.execute("""INSERT INTO mail_monitoring_notifications
                SELECT * FROM jsonb_populate_record(NULL::mail_monitoring_notifications, %s::jsonb)""",
                (json.dumps(row_data),))
    return len(rows)


COMMANDS = {"trash": trash, "purge": purge, "restore": restore}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--ids-file", required=True)
    parser.add_argument("--reason", default="")
    parser.add_argument("--actor", default="")
    parser.add_argument("--backup-ref", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", type=int)
    args = parser.parse_args(argv)
    if args.command == "trash" and not (args.reason and args.actor and args.backup_ref):
        parser.error("trash needs --reason, --actor and --backup-ref")
    ids = _ids(args.ids_file)

    with get_connection() as conn, conn.cursor() as cur:
        changed = COMMANDS[args.command](cur, ids, args)
        if changed != len(ids):
            conn.rollback()
            raise SystemExit(f"{args.command}: {changed} of {len(ids)} records would change; rolled back")
        if not args.apply:
            conn.rollback()
            print(f"DRY RUN {args.command}: {changed} records would change. Nothing written.")
            return 0
        if args.confirm != changed:
            conn.rollback()
            raise SystemExit(f"--confirm {args.confirm} does not match {changed}; rolled back")
        conn.commit()
    print(f"APPLIED {args.command}: {changed} records.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
