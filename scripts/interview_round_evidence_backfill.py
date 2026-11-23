"""Give bookings with no round the round their evidence states, or mark them Needs Review.

Dry run unless --apply. Run inside the operations-api container:
    docker exec -i -w /app -e PYTHONPATH=/app teleautomation-production-operations-api-1 \\
        python - --out /tmp/round-backfill < scripts/interview_round_evidence_backfill.py            # dry run
    ... python - --out /tmp/round-backfill --apply --confirm 109 < scripts/...                    # apply

For every booking Daily Ops would show without a round (a dated booking that
is not an assessment and not a marked test record) it reads:

  * the booking's own invite title, notes and Telegram slot text;
  * the email the booking was made from (`interview_source_message_id`);
  * every mail alert, AI analysis and booking-audit entry that names the
    booking, and the emails those point at.

Only words are evidence: a round the model wrote into an alert or analysis
is not used unless the email's own text says it. `features.interview_round_evidence`
decides; two different rounds, or none, leave the booking "Needs Review".

Writes `<out>/dry_run.csv` and `<out>/dry_run.json` (booking id, extracted
round, evidence source, phrase, confidence, reason). With --apply it first
writes `<out>/backup_rows.json` -- the full stored row of every booking it
will touch, with a sha256 -- and refuses to run unless --confirm equals the
number of bookings it would change, so a list that moved since it was
reviewed does not run. Each booking is then updated through
`candidate_store.record_interview_round_evidence`, which keeps the round
history and evidence on the row.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from core.db.connection import get_connection
from features import candidate_store as cs
from features import interview_round_evidence as round_evidence
from features import test_record_exclusions

BY = "round-evidence-backfill"


def _q(cur, sql: str, args: tuple) -> list[dict]:
    cur.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, values)) for values in cur.fetchall()]


def affected_rows() -> list[dict]:
    excluded = test_record_exclusions.excluded_ids()
    return [
        row for row in cs.all_booking_rows()
        if cs.interview_round_needs_review(row) and str(row.get("id") or "") not in excluded
    ]


def evidence_sources(cur, row: dict) -> list[tuple[str, str]]:
    """Every stored text that belongs to this booking, named by where it came from."""
    bid = str(row["id"])
    source_id = str(row.get("interview_source_message_id") or "")
    alerts = _q(cur, "SELECT id, gmail_message_id, email_subject FROM mail_monitoring_notifications WHERE booking_id=%s", (bid,))
    analyses = _q(cur, "SELECT id, mailbox_message_id, gmail_message_id FROM interview_mail_analyses WHERE reconciled_booking_id=%s", (bid,))
    audits = _q(cur, "SELECT id, gmail_message_id FROM interview_auto_booking_audit WHERE booking_id=%s", (bid,))
    message_ids = sorted({m for m in [source_id] + [a["gmail_message_id"] for a in alerts + analyses + audits] if m})
    mailbox_ids = sorted({a["mailbox_message_id"] for a in analyses if a.get("mailbox_message_id")})
    messages = _q(
        cur,
        """SELECT id, provider_message_id, rfc_message_id, subject, body_text
             FROM mailbox_messages
            WHERE provider_message_id = ANY(%s) OR rfc_message_id = ANY(%s) OR id = ANY(%s)
            ORDER BY sent_at""",
        (message_ids, message_ids, mailbox_ids),
    )
    sources: list[tuple[str, str]] = []
    for message in messages:
        own = source_id and source_id in (message["provider_message_id"], message["rfc_message_id"])
        tag = f"{'source_email' if own else 'linked_email'}[{message['provider_message_id']}]"
        sources.append((f"{tag}.subject", message.get("subject") or ""))
        sources.append((f"{tag}.body", message.get("body_text") or ""))
    for alert in alerts:
        sources.append((f"mail_alert[{alert['id']}].subject", alert.get("email_subject") or ""))
    return sources


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="directory for dry_run.* and backup_rows.json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", type=int, default=-1)
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    rows = affected_rows()
    plan = []
    with get_connection() as conn:
        cur = conn.cursor()
        for row in rows:
            sources = cs.round_evidence_sources(row) + evidence_sources(cur, row)
            result = round_evidence.resolve_round(sources)
            plan.append({"row": row, "result": result})

    report = [{
        "booking_id": item["row"]["id"],
        "name": item["row"].get("name") or "",
        "date": str(item["row"].get("date") or "")[:10],
        "booking_source": item["row"].get("interview_booking_source") or "",
        "extracted_round": item["result"]["round"] or round_evidence.NEEDS_REVIEW_LABEL,
        "evidence_source": item["result"]["evidence_source"],
        "phrase": item["result"]["phrase"],
        "confidence": item["result"]["confidence"] or "none",
        "reason": item["result"]["reason"],
    } for item in plan]
    with open(os.path.join(args.out, "dry_run.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, ensure_ascii=False)
    with open(os.path.join(args.out, "dry_run.csv"), "w", newline="", encoding="utf-8") as handle:
        if report:
            writer = csv.DictWriter(handle, fieldnames=list(report[0]))
            writer.writeheader()
            writer.writerows(report)
    evidenced = sum(1 for r in report if r["extracted_round"] != round_evidence.NEEDS_REVIEW_LABEL)
    print(json.dumps({"affected": len(report), "evidenced": evidenced,
                      "needs_review": len(report) - evidenced, "out": args.out}))

    if not args.apply:
        print("dry run: nothing written to bookings")
        return 0
    if args.confirm != len(plan):
        print(f"refused: --confirm {args.confirm} but {len(plan)} bookings would change", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = {"taken_at": stamp, "rows": [item["row"] for item in plan]}
    blob = json.dumps(backup, indent=1, ensure_ascii=False, default=str, sort_keys=True).encode("utf-8")
    backup_path = os.path.join(args.out, "backup_rows.json")
    with open(backup_path, "wb") as handle:
        handle.write(blob)
    print(json.dumps({"backup": backup_path, "sha256": hashlib.sha256(blob).hexdigest(), "rows": len(plan)}))

    applied = {"evidenced": 0, "needs_review": 0, "unchanged": 0}
    for item in plan:
        saved = cs.record_interview_round_evidence(
            str(item["row"]["id"]), by=f"{BY} {stamp}", resolution=item["result"],
        )
        if saved is None:
            applied["unchanged"] += 1
        elif saved.get("interview_round_needs_review"):
            applied["needs_review"] += 1
        else:
            applied["evidenced"] += 1
    print(json.dumps({"applied": applied}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
