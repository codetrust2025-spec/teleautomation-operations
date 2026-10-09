# Removing the legacy Interview Data records (runbook)

Prepared 9 Oct 2026, revised 10 Oct 2026 (the application is now **stopped** for the purge). **Nothing here has
been run against production except read-only checks.** The deletion is run by the owner.

## What is being removed

| File (in `data_room/` on the operations data volume) | Records | Records digest |
|---|---|---|
| `credentials.json` (live) | 99 (81 WhatsApp import + 17 srujan import + 1 Thrilok dataset) | `d292eec0cf32e5d6` |
| `credentials.json.pre-srujan-import-20261005T093705Z` (older app-made copy) | 81 (all 81 are also in the live file) | `0b07fefcd1841e85` |

Only the top-level key `interview_data` is removed, from only those two files. No other file under the data
directory mentions the key (checked). The code and schema were removed on 6 Oct 2026 (`da09f6e`, `6770713`).

## Why the application is stopped first

The application is the only writer of `credentials.json` (one uvicorn worker; only the `operations-api` container
mounts that volume). Its store does **load the whole file, change something, save the whole dict back**: Data Room
edits, handler password changes and `sync_admin_login_copy` all do this. A request that loaded the file *before* the
purge and saves it *after* writes `interview_data` straight back. The purge tool's hash checks cannot prevent that:
the application takes no lock the tool could respect.

This was reproduced in rehearsal on the production host (stand-in application, fixture data): an in-flight request
that loaded the file before the purge restored all 99 records afterwards, and the tool's watch caught it. So:

1. **Stop the application** (the only writer) before the purge, and keep it stopped until the purge is verified.
2. The tool **refuses to apply** unless the writer is demonstrably down: it must be told (`--writers-stopped`), nothing
   may be listening on the application's port (`--require-port-closed`; checked, not trusted), no process may have the
   files open, and the files must have been quiet for 120 s (`--min-idle-seconds`). It re-reads the files before
   replacing them, and **watches them for `--settle-seconds` after writing**.
3. Verify with the application **stopped**, **restart** it, and verify **again** with it running (`--after-restart
   --watch 60`), then once more later.

Proven against production today: with the application running, both `plan` and `apply` refuse (exit 2, nothing written),
even when given `--writers-stopped`, because port 8210 is open.

## What has been verified (9-10 Oct 2026)

- **The tool** (`scripts/purge_interview_data.py`, stdlib only, pure ASCII): 91 tests against a fixture shaped like
  production (88 run on Windows; the 3 POSIX-only ones, for file modes and the open-file check, were skipped there and
  their behaviour was exercised in the Linux rehearsals below), each safety property mutation-checked. They include
  the **delayed concurrent write**: a stale writer that saves after the purge restores the records and `verify` catches
  it; the post-write watch catches a writer that wakes up late (records back, or other data rewritten); a stopped and
  restarted application cannot bring them back; `verify --watch` catches a late writer.
- **Rehearsals on the production host with real processes, ports and restic, on throwaway fixtures under `/tmp`**
  (all removed afterwards): (1) backup, byte-identity check, plan, refusals, apply, verify, restore, rollback, with
  modes/owners kept; (2) a stand-in application with a stale read-modify-write loop: unpaused apply refused; the
  hazard reproduced and caught by the watch; the full stop, purge, restart, verify cycle clean, including 14 s after the
  moment the dead process would have saved its old copy; a process holding a file open blocks apply.
- **The real backup**: nightly restic job healthy (last run 8 Oct 20:34 UTC; restore test and `check --read-data`
  passed; 0 consecutive failures). Snapshot `da4a7033` holds **byte-identical** copies of both target files.
- **The production dry run**: all 12 data gates pass, exit status 0, nothing written.

## Stop conditions (STOP and tell Claude; do not force anything)

The tool refuses (exit status 2, **nothing written**) unless all hold:

1. the live file holds exactly **99** records with digest `d292eec0cf32e5d6`, and the older copy exactly **81** with `0b07fefcd1841e85`;
2. everything else in each file digests as expected (`33622d20f6231a7e` / `aeba532e0c43b6f1`). Any application write changes `updated_at`, so this also trips if the application wrote since the dry run;
3. every record in the older copy is also in the live file;
4. no other file under the data directory mentions `interview_data`;
5. each file's sha256 equals the value verified against the backup;
6. `--confirm-count 99` matches;
7. the writer is down: `--writers-stopped` given, nothing listening on `127.0.0.1:8210`, no process has the files open, both files idle for 120 s.

It also refuses if a file changes while it runs. After writing it re-reads each file, then watches for 10 s.
Stop yourself if: the backup check does not say `IDENTICAL` twice; the dry run does not end `all gates pass`; the dry
run differs from "Expected output"; the time is between 20:25 and 20:45 UTC (the nightly backup runs at 20:30).

**Exit status 1 (`ERROR`) is different from 2.** 2 means refused before writing: start the application again
(`docker start ...`), nothing changed. 1 means something went wrong *after* writing (for example the key came back during
the watch): **do not start the application**, send the output to Claude, and use the rollback section.

## What the deletion does NOT remove (owner's decision)

Backups. Every existing nightly restic snapshot (5 Oct onward) holds these records. Retention is 7 daily / 4 weekly /
6 monthly, so they age out of the backups over up to ~6 months. This procedure touches neither snapshots nor retention
(standing rule). If the records must also leave the backups, that is a separate, explicit decision.

## What the pause costs

While `operations-api` is stopped (about 3-5 minutes in total): the dashboard and the dynamic public pages (slot
booking on both domains) return 502, so slot submissions fail; the static landing page stays up. The background loops
(Gmail, reminders, outbox) pause and resume. The host monitor runs every 5 minutes and only raises an alert after a
check has failed for 5 minutes, so a short pause normally raises nothing; a longer one may open a GitHub alert issue
(emailed to the owner) that closes itself on recovery, so keep the pause short. Pick a quiet time. Marketing, both
databases and the backups are unaffected. The restart policy is `unless-stopped`, so the stopped container stays stopped until started,
and nothing on the host restarts it (the monitor never does).

## Commands (run from Git Bash on the laptop; each is one command)

### Phase 0. Before stopping anything (all read-only)

0.1 Baseline checksum of everything else (writes `purge-before.txt` locally; expect 383 lines):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 "cd /var/lib/docker/volumes/teleautomation-production_operations_data/_data && find . -type f ! -name job_heartbeats.json ! -name gmail_reconnect_notices.json ! -name '*.tmp' -print0 | sort -z | xargs -0 sha256sum" > purge-before.txt
```

0.2 Backup check (expect `IDENTICAL` for **both** files, `backup lock held right now: no`, `last_result: ok`):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'bash -s' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/interview_purge_backup_check.sh"
```

Live sha256 values must be `8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91` and
`78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d`. A newer snapshot is fine; `DIFFERENT` means stop.

0.3 Dry run (expect `RESULT: all gates pass. Nothing was written.`, see "Expected output: dry run"):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - plan --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --also credentials.json.pre-srujan-import-20261005T093705Z --expect-file-sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91 --also-expect-file-sha256 credentials.json.pre-srujan-import-20261005T093705Z=78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

### Phase 1. PAUSE the writer (this takes the dashboard and public booking pages down)

1.1 Stop the application (graceful, up to 30 s) and confirm it is down:

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'docker stop --time 30 teleautomation-production-operations-api-1 && echo "running operations-api containers: $(docker ps -q --filter name=teleautomation-production-operations-api-1 | wc -l)"; echo "listeners on 8210: $(ss -tln | grep -c ":8210 ")"'
```

Expected: `teleautomation-production-operations-api-1`, then `running operations-api containers: 0` and `listeners on 8210: 0`.
If either is not `0`, do not continue.

### Phase 2. Purge while it is stopped

2.1 Dry run again, now also checking that the writer is down (expect all gates pass, including the three writer lines):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - plan --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --also credentials.json.pre-srujan-import-20261005T093705Z --expect-file-sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91 --also-expect-file-sha256 credentials.json.pre-srujan-import-20261005T093705Z=78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d --require-port-closed 127.0.0.1:8210 --min-idle-seconds 120' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

If it says a file was "last written ... ago" under 120 s, someone wrote to the Data Room just before the stop: wait and repeat.

2.2 **THE DELETION** (only when 0.2, 0.3 and 2.1 are exactly as expected and the application is stopped):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - apply --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --also credentials.json.pre-srujan-import-20261005T093705Z --confirm-count 99 --expect-file-sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91 --also-expect-file-sha256 credentials.json.pre-srujan-import-20261005T093705Z=78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d --writers-stopped --require-port-closed 127.0.0.1:8210 --min-idle-seconds 120 --settle-seconds 10' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

2.3 Verify while still stopped (expect `RESULT: clean.`):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - verify --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --also credentials.json.pre-srujan-import-20261005T093705Z' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

2.4 Checksum everything else again, still stopped, and compare:

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 "cd /var/lib/docker/volumes/teleautomation-production_operations_data/_data && find . -type f ! -name job_heartbeats.json ! -name gmail_reconnect_notices.json ! -name '*.tmp' -print0 | sort -z | xargs -0 sha256sum" > purge-after.txt
```

```bash
diff purge-before.txt purge-after.txt
```

Expected: exactly the two-hunk diff shown under "Expected output: checksum diff", and nothing else.

### Phase 3. Restart and wait until healthy

3.1 Start the application (same container, same image and settings; this is not a redeploy):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'docker start teleautomation-production-operations-api-1 && for i in $(seq 1 40); do s=$(docker inspect -f "{{.State.Health.Status}}" teleautomation-production-operations-api-1); echo "check $i: $s"; [ "$s" = healthy ] && break; sleep 5; done'
```

Expected: a few `starting` lines, then `healthy` (usually within 1-2 minutes; if still not healthy after 40 checks, send the output to Claude).

3.2 Confirm the site is back and on the expected release:

```bash
curl -s https://operations.teleautomation.online/health && echo && curl -s https://operations.teleautomation.online/version
```

Expected: `{"status":"ok","service":"teleautomation-operations"}` and the release SHA that was serving before (`4a7c61c...` at the time of writing).

### Phase 4. Verify again, with the application running

4.1 Watch for a minute (expect `RESULT: clean.`, `watched for 60s: the key did not come back`; an `[INFO] ... other data differs` line is normal only if the application has written other data since):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - verify --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --also credentials.json.pre-srujan-import-20261005T093705Z --after-restart --watch 60' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

4.2 Checksums once more with the application running; the diff against `purge-before.txt` must still be only the two files:

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 "cd /var/lib/docker/volumes/teleautomation-production_operations_data/_data && find . -type f ! -name job_heartbeats.json ! -name gmail_reconnect_notices.json ! -name '*.tmp' -print0 | sort -z | xargs -0 sha256sum" > purge-after-restart.txt
```

```bash
diff purge-before.txt purge-after-restart.txt
```

### Phase 5. Soak (same 4.1 command without `--watch`)

Run 4.1 again about 10 minutes later, and once more tomorrow after the 20:30 UTC backup. The first backup after the
purge holds the purged files; to confirm that (read-only; the command was tested today against the current snapshot and
printed `1`, the one `interview_data` line; after the purge and the next nightly backup it must print `0`):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'export RESTIC_REPOSITORY=/var/backups/teleautomation/restic RESTIC_PASSWORD_FILE=/etc/teleautomation/restic.password; S=$(restic snapshots --tag nightly --host teleautomation-production --json --latest 1 | python3 -c "import json,sys; print(json.load(sys.stdin)[-1][\"short_id\"])"); echo "latest snapshot $S"; restic dump $S /var/backups/teleautomation/stage/operations-data/data_room/credentials.json | grep -c interview_data'
```

## Rollback

Use it only if something is wrong after 2.2 (exit status 1, or a failed verify). The tool will not overwrite a file that
changed since the purge, and it needs the exact pre-purge bytes, which snapshot `da4a7033` holds. **Roll back with the
application stopped** (if it was already started, stop it first with command 1.1).

R1. Restore both files from the snapshot into a root-only scratch directory (writes a copy of the records there):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'umask 077 && mkdir -p /root/purge-rollback && RESTIC_REPOSITORY=/var/backups/teleautomation/restic RESTIC_PASSWORD_FILE=/etc/teleautomation/restic.password restic restore da4a7033 --target /root/purge-rollback --include /var/backups/teleautomation/stage/operations-data/data_room/credentials.json --include /var/backups/teleautomation/stage/operations-data/data_room/credentials.json.pre-srujan-import-20261005T093705Z && sha256sum /root/purge-rollback/var/backups/teleautomation/stage/operations-data/data_room/credentials.json*'
```

Expected: the two sha256 values equal the pre-purge ones (`8173a1ad...` and `78424ddf...`).

R2. Put the live file back:

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - rollback --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --file credentials.json --from /root/purge-rollback/var/backups/teleautomation/stage/operations-data/data_room/credentials.json --expect-source-sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

Expected: `restored credentials.json: 17948 -> 104979 bytes; sha256 8173a1ad...; 99 records back` and `RESULT: restored.`
R3 (older copy) is the same with `--file credentials.json.pre-srujan-import-20261005T093705Z`, its path under
`/root/purge-rollback/...` and `--expect-source-sha256 78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d`.
Then start the application (3.1) and verify the records are back through the Data Room.

R4. Remove the scratch copy when finished, so the records do not stay on disk in a second place:

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'rm -rf /root/purge-rollback && ls -d /root/purge-rollback 2>&1'
```

Expected: `ls: cannot access '/root/purge-rollback': No such file or directory`. If the rollback refuses because the file
changed since the purge, do not force it: merge by hand from the restored copy.

## Expected output: dry run (0.3; application still running)

```
MODE: PLAN (read-only; nothing is written)
file: credentials.json
  size 104979 bytes, mode 0o644, owner 0:0, sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91
  top-level keys: site_url, vps_host, admin, handlers, service_accounts, prompts, resources, offer_letters, updated_at, offer_letters_rows_migrated_v1, interview_data
  interview_data: 99 records, digest d292eec0cf32e5d6
  by import batch: srujan-import-2026-10-05 x17, thrilok-dataset-2026-10-05 x1, whatsapp-import-2026-10-05 x81
  by review status: conflict x14, needs_review x64, recorded x21
  identity list digest 45fd5b6391f7431c (record tag:content tag, 99 entries; ids and contents are never printed)
  everything else in the file: 10 keys, digest 33622d20f6231a7e
  if applied: the file would become 17948 bytes, sha256 d00ee10b7ecda4e0d9b4a49255b58116e2f9b00c5b9b2830998f7c238e20d01b; formatting kept: yes
file: credentials.json.pre-srujan-import-20261005T093705Z
  size 82698 bytes, mode 0o644, owner 0:0, sha256 78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d
  top-level keys: site_url, vps_host, admin, handlers, service_accounts, prompts, resources, offer_letters, updated_at, offer_letters_rows_migrated_v1, interview_data
  interview_data: 81 records, digest 0b07fefcd1841e85
  by import batch: whatsapp-import-2026-10-05 x81
  by review status: conflict x13, needs_review x50, recorded x18
  identity list digest 367f9397e6a3b575 (record tag:content tag, 81 entries; ids and contents are never printed)
  everything else in the file: 10 keys, digest aeba532e0c43b6f1
  if applied: the file would become 16137 bytes, sha256 1b12a5fff9df565c7df01b53829cdc0aeca6cf34ba7c5d6327e67934ff96e7c0; formatting kept: yes
files under the data directory that mention the key: data_room/credentials.json, data_room/credentials.json.pre-srujan-import-20261005T093705Z
GATES:
  [PASS] credentials.json: holds the interview_data key
  [PASS] credentials.json: 99 records (expected 99)
  [PASS] credentials.json: records digest d292eec0cf32e5d6 (expected d292eec0cf32e5d6)
  [PASS] credentials.json: everything else in the file digests to 33622d20f6231a7e (expected 33622d20f6231a7e)
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: holds the interview_data key
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: 81 records (expected 81)
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: records digest 0b07fefcd1841e85 (expected 0b07fefcd1841e85)
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: everything else in the file digests to aeba532e0c43b6f1 (expected aeba532e0c43b6f1)
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: every record in it is also in credentials.json (81 of 81)
  [PASS] no other file under the data directory mentions the key
  [PASS] credentials.json: file sha256 is the one verified against the backup (8173a1ad1cf9... vs 8173a1ad1cf9...)
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: file sha256 is the one verified against the backup (78424ddfa75f... vs 78424ddfa75f...)
RESULT: all gates pass. Nothing was written.
```

Any `[FAIL]` line means stop.

## Expected output: writer-down dry run (2.1, application stopped)

The same as above, with these three extra gate lines after the backup-binding lines (the "ago" numbers keep growing;
anything over 120 is right):

```
  [PASS] nothing is listening on 127.0.0.1:8210
  [PASS] credentials.json: last written 385000s ago (needs at least 120s of quiet)
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: last written 387500s ago (needs at least 120s of quiet)
  [PASS] no process has these files open
RESULT: all gates pass. Nothing was written.
```

With the application still running, the real production output of this command today was exactly the above with
`[FAIL] something IS listening on 127.0.0.1:8210: the application is still running` in place of the first line, and
`RESULT: STOP. 1 gate(s) failed. Nothing was written.`

## Expected output: the deletion (2.2)

The file descriptions and the same gate list as the dry run, then these extra lines, then the result:

```
  [PASS] --writers-stopped: the application has been stopped before this run
  [PASS] --require-port-closed names the application's port, so a running application is detected
  [PASS] nothing is listening on 127.0.0.1:8210
  [PASS] credentials.json: last written 385000s ago (needs at least 120s of quiet)
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: last written 387500s ago (needs at least 120s of quiet)
  [PASS] no process has these files open
  [PASS] --confirm-count 99 matches the 99 records to be deleted
purged credentials.json.pre-srujan-import-20261005T093705Z: 81 records removed; 82698 -> 16137 bytes; sha256 1b12a5fff9df565c7df01b53829cdc0aeca6cf34ba7c5d6327e67934ff96e7c0; the rest of the file is unchanged (aeba532e0c43b6f1); mode and owner kept
purged credentials.json: 99 records removed; 104979 -> 17948 bytes; sha256 d00ee10b7ecda4e0d9b4a49255b58116e2f9b00c5b9b2830998f7c238e20d01b; the rest of the file is unchanged (33622d20f6231a7e); mode and owner kept
watched the files for 10s after writing: unchanged
files under the data directory that still mention the key: none
RESULT: done. credentials.json.pre-srujan-import-20261005T093705Z, credentials.json
```

Exit status 0. Anything else (`STOP`, `ERROR`, exit status 1 or 2) means stop and report it (see "Exit status 1 is
different from 2" above). The older copy is written first, so a refusal on the live file leaves the live data as it was.

## Expected output: verify while stopped (2.3)

```
MODE: VERIFY (read-only)
file: credentials.json size 17948 sha256 d00ee10b7ecda4e0d9b4a49255b58116e2f9b00c5b9b2830998f7c238e20d01b
file: credentials.json.pre-srujan-import-20261005T093705Z size 16137 sha256 1b12a5fff9df565c7df01b53829cdc0aeca6cf34ba7c5d6327e67934ff96e7c0
GATES:
  [PASS] credentials.json: the interview_data key is gone
  [PASS] credentials.json: everything else is as it was (33622d20f6231a7e, expected 33622d20f6231a7e)
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: the interview_data key is gone
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: everything else is as it was (aeba532e0c43b6f1, expected aeba532e0c43b6f1)
  [PASS] no file under the data directory mentions the key
RESULT: clean.
```

## Expected output: verify after the restart (4.1)

```
MODE: VERIFY (read-only), after the application was restarted
file: credentials.json size ... sha256 ...
file: credentials.json.pre-srujan-import-20261005T093705Z size 16137 sha256 1b12a5fff9df565c7df01b53829cdc0aeca6cf34ba7c5d6327e67934ff96e7c0
GATES:
  [PASS] credentials.json: the interview_data key is gone
  [PASS] credentials.json: everything else is as it was (33622d20f6231a7e, expected 33622d20f6231a7e)
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: the interview_data key is gone
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: everything else is as it was (aeba532e0c43b6f1, expected aeba532e0c43b6f1)
  [PASS] watched for 60s: the key did not come back
  [PASS] no file under the data directory mentions the key
RESULT: clean.
```

If the application has written other Data Room data since the restart, the second line becomes
`[INFO] credentials.json: other data differs from the purge-time digest (...); expected once the application has written`
and the file's sha256 differs; that is normal. A `[FAIL]` on "the key is gone" or "the key CAME BACK" is not: stop
the application (1.1) and report.

## Expected output: checksum diff (2.4 and 4.2)

Exactly two lines differ (one hunk each) and nothing else, which means the expense store, the other Data Room files,
salaries, proofs and every other store are identical. Line numbers can shift by a line or two if files were added
meanwhile; the content must be this:

```
250c250
< 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91  ./data_room/credentials.json
---
> d00ee10b7ecda4e0d9b4a49255b58116e2f9b00c5b9b2830998f7c238e20d01b  ./data_room/credentials.json
254c254
< 78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d  ./data_room/credentials.json.pre-srujan-import-20261005T093705Z
---
> 1b12a5fff9df565c7df01b53829cdc0aeca6cf34ba7c5d6327e67934ff96e7c0  ./data_room/credentials.json.pre-srujan-import-20261005T093705Z
```

(Generated from the real before-checksums with only those two hashes substituted. After the restart the live file's
hash can differ again if the application has legitimately written to it; the older copy's cannot.)
