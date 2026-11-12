"""Store the company an Interview Related alert's own evidence proves, with its source.

Mail Alerts already shows such a company on screen (`resolved_alert_companies`
in core.recruitment_mail_store, the resolver in core.mail_alert_company). This
stores exactly what that resolver decides, so the alert keeps it and records
where it came from in `company_source` (migration 040):

    python -m scripts.backfill_alert_companies                         # dry run: what would change
    python -m scripts.backfill_alert_companies --apply --backup FILE   # store it, backup first
    python -m scripts.backfill_alert_companies --restore FILE          # undo a run

Safe to repeat. A run touches only visible alerts whose stored company is
empty, so a second run finds nothing more to do. Trashed, dismissed and hidden
alerts are not visible and are never touched. Nothing is deleted.
The backup holds every changed row's previous company and source. --restore
puts those back, but only on rows that still hold what the run wrote, so a
company somebody set by hand after the run is kept.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from core import recruitment_mail_store as store


def plan(cur) -> list[dict[str, str]]:
    """What a run would store: one entry per visible alert its evidence proves a company for."""
    store.clear_resolved_companies()
    resolved = store.resolved_alert_companies(cur, refresh=True)
    ids = sorted(resolved)
    if not ids:
        return []
    cur.execute(
        """SELECT id, company_name, company_source FROM mail_monitoring_notifications
            WHERE id = ANY(%s) AND btrim(COALESCE(company_name, '')) = ''""",
        (ids,),
    )
    return [
        {"id": str(alert_id), "old_company": old_company, "old_source": old_source,
         "company": resolved[str(alert_id)][0], "source": resolved[str(alert_id)][1]}
        for alert_id, old_company, old_source in cur.fetchall()
    ]


def apply(cur, changes: list[dict[str, str]]) -> int:
    written = 0
    for change in changes:
        cur.execute(
            """UPDATE mail_monitoring_notifications SET company_name = %s, company_source = %s
                WHERE id = %s AND btrim(COALESCE(company_name, '')) = ''""",
            (change["company"], change["source"], change["id"]),
        )
        written += cur.rowcount
    return written


def restore(cur, changes: list[dict[str, str]]) -> int:
    restored = 0
    for change in changes:
        cur.execute(
            """UPDATE mail_monitoring_notifications SET company_name = %s, company_source = %s
                WHERE id = %s AND company_name = %s AND company_source = %s""",
            (change["old_company"], change["old_source"], change["id"], change["company"], change["source"]),
        )
        restored += cur.rowcount
    return restored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="store the companies (default: dry run)")
    parser.add_argument("--backup", type=Path, help="where --apply writes the backup (required with --apply)")
    parser.add_argument("--restore", type=Path, help="undo the run this backup file describes")
    args = parser.parse_args(argv)
    with store.get_connection() as conn, conn.cursor() as cur:
        if args.restore:
            changes = json.loads(args.restore.read_text(encoding="utf-8"))["changes"]
            count = restore(cur, changes)
            conn.commit()
            store.clear_resolved_companies()
            print(f"restored {count} of {len(changes)} alerts")
            return 0
        changes = plan(cur)
        by_source: dict[str, int] = {}
        for change in changes:
            by_source[change["source"]] = by_source.get(change["source"], 0) + 1
            print(f"{change['id']}\t{change['source']}\t{change['company']}")
        print(f"{len(changes)} alerts to store; by source: {json.dumps(by_source, sort_keys=True)}")
        if not args.apply:
            print("dry run: nothing written (use --apply --backup FILE)")
            return 0
        if not args.backup:
            parser.error("--apply needs --backup FILE")
        if args.backup.exists():
            parser.error(f"{args.backup} exists; choose a new file so no earlier backup is overwritten")
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        args.backup.write_text(json.dumps({"written_at": datetime.now(timezone.utc).isoformat(), "changes": changes},
                                          indent=1, ensure_ascii=False), encoding="utf-8")
        count = apply(cur, changes)
        conn.commit()
        store.clear_resolved_companies()
        print(f"stored {count} companies; backup {args.backup}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
