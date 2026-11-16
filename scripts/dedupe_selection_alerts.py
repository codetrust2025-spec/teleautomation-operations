"""Remove the duplicate Selection alerts already filed: one mail, one alert.

The creation paths now refuse a second alert for one mail (16 Nov 2026,
`_same_mail_alert` in core.recruitment_mail_store). Alerts written before that
are found here, read-only by default:

    python -m scripts.dedupe_selection_alerts                          # dry run: the plan
    python -m scripts.dedupe_selection_alerts --apply --backup FILE    # dismiss, backup first
    python -m scripts.dedupe_selection_alerts --restore FILE           # undo a run

Two kinds, both proven from the stored rows alone:

- copies: visible Selection alerts of one candidate whose mails have the same
  sender, subject and sent time to the second (the sender delivered the mail
  twice; Gmail gave each copy an id). The first filed is kept.
- one Gmail message with more than one visible Selection alert (a re-read
  filed it again under another classification). The newest reading is kept.

A duplicate is dismissed the way the screen's "Dismiss alert" does (soft:
`dismissed_at` set, nothing deleted, Gmail untouched). If the kept alert is
unread while a duplicate was opened, the kept one is marked read too, so the
person's reading is not lost. --restore clears exactly what the run set.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from core import recruitment_mail_store as store


def plan(cur) -> list[dict[str, object]]:
    """[{drop, keep, why, subject, candidate}] for every visible duplicate Selection alert. Read-only."""
    vis, vis_params = store.notification_visibility_sql()
    selection = sorted(store.SELECTION_RELATED_CLASSIFICATIONS)
    cur.execute(
        f"""SELECT id::text, candidate_id, candidate_name, gmail_message_id, lower(COALESCE(sender_email, '')),
                   COALESCE(email_subject, ''), email_received_at, classification, is_read, created_at
              FROM mail_monitoring_notifications
             WHERE {vis} AND classification = ANY(%s)
             ORDER BY created_at, id""",
        list(vis_params) + [selection],
    )
    rows = [dict(zip(("id", "candidate_id", "candidate", "message", "sender", "subject", "sent", "classification",
                      "is_read", "created_at"), r)) for r in cur.fetchall()]
    changes: list[dict[str, object]] = []
    dropped: set[str] = set()
    # One Gmail message, several readings: keep the newest.
    by_message: dict[str, list[dict]] = {}
    for row in rows:
        by_message.setdefault(row["message"], []).append(row)
    for message, same in by_message.items():
        if message and len(same) > 1:
            keep = max(same, key=lambda r: (r["created_at"], r["id"]))
            for row in same:
                if row is not keep:
                    changes.append(_change(row, keep, "same Gmail message, older reading"))
                    dropped.add(row["id"])
    # Identical copies under different Gmail ids: keep the first filed.
    by_copy: dict[tuple, list[dict]] = {}
    for row in rows:
        if row["id"] in dropped or row["sent"] is None or not row["subject"].strip():
            continue
        by_copy.setdefault((row["candidate_id"], row["sender"], row["subject"], row["sent"]), []).append(row)
    for same in by_copy.values():
        if len(same) > 1:
            keep = same[0]
            for row in same[1:]:
                changes.append(_change(row, keep, "identical copy: same sender, subject and sent time"))
    return changes


def _change(row: dict, keep: dict, why: str) -> dict[str, object]:
    return {"drop": row["id"], "keep": keep["id"], "why": why, "candidate": row["candidate"],
            "subject": row["subject"], "sent": row["sent"].isoformat() if row["sent"] else None,
            "classification": row["classification"], "kept_classification": keep["classification"],
            "drop_read": bool(row["is_read"]), "keep_read": bool(keep["is_read"])}


def apply(cur, changes: list[dict[str, object]]) -> dict[str, int]:
    dismissed = marked_read = 0
    for change in changes:
        cur.execute("""UPDATE mail_monitoring_notifications SET dismissed_at = now(), updated_at = now()
                        WHERE id = %s AND dismissed_at IS NULL""", (change["drop"],))
        dismissed += cur.rowcount
        if change["drop_read"] and not change["keep_read"]:
            cur.execute("""UPDATE mail_monitoring_notifications SET is_read = true, read_at = COALESCE(read_at, now()),
                             updated_at = now() WHERE id = %s AND NOT is_read""", (change["keep"],))
            marked_read += cur.rowcount
            change["kept_marked_read"] = bool(cur.rowcount)
    return {"dismissed": dismissed, "marked_read": marked_read}


def restore(cur, changes: list[dict[str, object]]) -> dict[str, int]:
    undismissed = unread = 0
    for change in changes:
        cur.execute("UPDATE mail_monitoring_notifications SET dismissed_at = NULL, updated_at = now()"
                    " WHERE id = %s AND dismissed_at IS NOT NULL", (change["drop"],))
        undismissed += cur.rowcount
        if change.get("kept_marked_read"):
            cur.execute("UPDATE mail_monitoring_notifications SET is_read = false, read_at = NULL, updated_at = now()"
                        " WHERE id = %s", (change["keep"],))
            unread += cur.rowcount
    return {"undismissed": undismissed, "marked_unread": unread}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="dismiss the duplicates (needs --backup)")
    parser.add_argument("--backup", help="write the plan and what changed here before/after applying")
    parser.add_argument("--restore", help="undo a run from its backup file")
    args = parser.parse_args(argv)
    with store.get_connection() as conn, conn.cursor() as cur:
        if args.restore:
            changes = json.loads(Path(args.restore).read_text(encoding="utf-8"))["changes"]
            print(json.dumps(restore(cur, changes)))
            return 0
        changes = plan(cur)
        for c in changes:
            print(f"{c['candidate']} | {c['subject'][:60]} | {c['sent']} | {c['classification']} -> dismiss {c['drop']}"
                  f" (keep {c['keep']}, {c['kept_classification']}) | {c['why']}")
        print(f"{len(changes)} duplicate alert(s)")
        if not args.apply:
            conn.rollback()
            return 0
        if not args.backup:
            print("--apply needs --backup FILE", file=sys.stderr)
            conn.rollback()
            return 2
        backup = Path(args.backup)
        backup.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "changes": changes}, indent=1),
                          encoding="utf-8")
        result = apply(cur, changes)
        backup.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "changes": changes,
                                      "result": result}, indent=1), encoding="utf-8")
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
