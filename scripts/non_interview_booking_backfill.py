"""Type the bookings whose invite title says they are not interviews.

Dry run unless --apply. Run inside the operations-api container:
    docker exec -i -w /app -e PYTHONPATH=/app teleautomation-production-operations-api-1 \\
        python - --out /tmp/non-interview < scripts/non_interview_booking_backfill.py                 # dry run
    ... python - --out /tmp/non-interview --apply --confirm 3 < scripts/...                          # apply

A booking Daily Ops shows as "Needs Review" whose own invite title names a
career consultation or a BGV verification -- and does not call it an
interview -- is not an interview at all (owner, 23 Nov 2026). It is kept as
it is (slot, status, attendee, history); only `booking_type` changes, with
the title phrase recorded as `booking_type_evidence`. No round is set.

Writes `<out>/dry_run.csv`. With --apply it first writes
`<out>/backup_rows.json` (full stored rows + sha256) and refuses unless
--confirm equals the number of bookings it would change.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from features import candidate_store as cs
from features import interview_round_evidence as round_evidence

BY = "non-interview-backfill"


def planned() -> list[tuple[dict, str, str]]:
    out = []
    for row in cs.all_booking_rows():
        if not cs.interview_round_needs_review(row):
            continue
        kind, phrase = round_evidence.non_interview_type_from_title(row.get("interview_role"))
        if kind:
            out.append((row, kind, phrase))
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", type=int)
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    plan = planned()
    with open(os.path.join(args.out, "dry_run.csv"), "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "name", "date", "time", "booking_type_now", "booking_type_new", "evidence_source", "phrase", "invite_title"])
        for row, kind, phrase in plan:
            writer.writerow([row.get("id"), row.get("name"), row.get("date"), row.get("time"),
                             cs.booking_type_of(row), kind, "booking.invite_title", phrase, row.get("interview_role")])
    print(f"{len(plan)} bookings to type")
    for row, kind, phrase in plan:
        print(f"  {row.get('id')}  {row.get('date')} {row.get('time')}  {kind:<20} <- {phrase!r}")
    if not args.apply:
        return 0
    if args.confirm != len(plan):
        print(f"refusing: --confirm {args.confirm} but {len(plan)} would change", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stored = {r["id"]: r for r in cs._load(force=True).get("candidates") or []}
    backup = [stored[row["id"]] for row, _, _ in plan]
    blob = json.dumps(backup, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    with open(os.path.join(args.out, "backup_rows.json"), "wb") as handle:
        handle.write(blob)
    print("backup sha256", hashlib.sha256(blob).hexdigest())

    for row, kind, _ in plan:
        saved = cs.record_interview_round_evidence(row["id"], by=f"{BY} {stamp}")
        got = (saved or {}).get("booking_type")
        print(f"  {row['id']}: {got}")
        if got != kind:
            print(f"unexpected result for {row['id']}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
