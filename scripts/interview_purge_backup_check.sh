#!/bin/bash
# READ-ONLY. Compares the live Data Room files with their copies in the latest real nightly snapshot.
# restic streams each file straight into sha256sum on the host; no file is written and no contents are printed.
set -u
export RESTIC_REPOSITORY=/var/backups/teleautomation/restic
export RESTIC_PASSWORD_FILE=/etc/teleautomation/restic.password
VOL=/var/lib/docker/volumes/teleautomation-production_operations_data/_data
OLDER=credentials.json.pre-srujan-import-20261005T093705Z
echo "now (UTC): $(date -u +%FT%TZ)  | backup lock held right now: $(flock -n /var/lib/teleautomation-monitor/backup.lock true 2>/dev/null && echo no || echo YES)"

echo; echo "== recent nightly snapshots (newest last)"
restic snapshots --tag nightly --host teleautomation-production --compact 2>&1 | tail -6

SNAP=$(restic snapshots --tag nightly --host teleautomation-production --json --latest 1 2>/dev/null | python3 -c 'import json,sys; s=json.load(sys.stdin); print(s[-1]["short_id"] if s else "")')
TIME=$(restic snapshots --tag nightly --host teleautomation-production --json --latest 1 2>/dev/null | python3 -c 'import json,sys; s=json.load(sys.stdin); print(s[-1]["time"][:19] if s else "")')
echo; echo "latest nightly snapshot: $SNAP taken $TIME UTC"
[ -n "$SNAP" ] || { echo "NO SNAPSHOT FOUND"; exit 1; }

echo; echo "== live file vs the same file inside that snapshot"
for f in credentials.json "$OLDER"; do
  LIVE="$VOL/data_room/$f"
  IN=$(restic find --snapshot "$SNAP" "$f" 2>/dev/null | grep -m1 "/operations-data/data_room/$f\$")
  LIVE_SHA=$(sha256sum "$LIVE" | cut -d' ' -f1)
  SNAP_SHA=$(restic dump "$SNAP" "$IN" 2>/dev/null | sha256sum | cut -d' ' -f1)
  echo "  $f"
  echo "    live mtime : $(stat -c %y "$LIVE" | cut -c1-19) UTC | size $(stat -c %s "$LIVE")"
  echo "    live sha256: $LIVE_SHA"
  echo "    snap sha256: $SNAP_SHA"
  [ "$LIVE_SHA" = "$SNAP_SHA" ] && echo "    -> IDENTICAL: the snapshot holds this exact file" || echo "    -> DIFFERENT: the snapshot is NOT a valid rollback for this file"
done

echo; echo "== backup job health (from its own status file)"
python3 - <<'PY'
import json
s = json.load(open("/var/lib/teleautomation-monitor/backup-status.json"))
print({k: s[k] for k in ("last_result", "last_success_at", "last_snapshot", "consecutive_failures", "last_restore_test_at")})
PY
echo; echo "== retention in force (read from the script; not changed)"
grep -nE "keep-daily|keep-weekly|keep-monthly" /usr/local/sbin/teleautomation-backup | cut -c1-140
echo; echo "== disk for a rollback working copy"; df -h /var/backups 2>/dev/null | tail -1
