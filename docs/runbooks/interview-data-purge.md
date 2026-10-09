# Removing the legacy Interview Data records (runbook)

Prepared 9 Oct 2026. **Nothing here has been run against production except read-only checks.** The deletion is
run by the owner.

## What is being removed

| File (in `data_room/` on the operations data volume) | Records | Records digest |
|---|---|---|
| `credentials.json` (live) | 99 (81 WhatsApp import + 17 srujan import + 1 Thrilok dataset) | `d292eec0cf32e5d6` |
| `credentials.json.pre-srujan-import-20261005T093705Z` (older app-made copy) | 81 (all 81 are also in the live file) | `0b07fefcd1841e85` |

Only the top-level key `interview_data` is removed, from only those two files. No other file under the data
directory mentions the key (checked). The code and schema were removed on 6 Oct 2026 (`da09f6e`, `6770713`).

The application re-reads `credentials.json` from disk on every call, so no restart is needed.

## What has been verified (read-only, 9 Oct 2026)

- **The tool** (`scripts/purge_interview_data.py`, stdlib only, ASCII): 71 tests against a fixture shaped like
  production (69 run on Windows; the 2 POSIX mode/owner tests ran on the host), each safety property
  mutation-checked.
- **Full rehearsal on the production host with the real restic binary, on a throwaway fixture and a throwaway
  repository under `/tmp`** (removed afterwards): backup, byte-identity check, plan, refusals, apply, verify,
  restore, rollback. Modes and owners (`644 1000:1000`, `640 1001:1001`) survived apply and rollback; every file
  was content-identical after rollback; no temp files were left.
- **The real backup**: nightly restic job healthy (last run 8 Oct 20:34 UTC, restore test and `check --read-data`
  passed, 0 consecutive failures). Snapshot `da4a7033` holds **byte-identical** copies of both target files
  (live sha256 `8173a1ad…` and `78424ddf…` equal the snapshot's).
- **The production dry run**: all 12 gates pass, exit status 0, nothing written.

## Stop conditions (the tool enforces these; if you see one, STOP and tell Claude)

The tool refuses (exit status 2, **nothing written**) unless all hold:

1. the live file holds exactly **99** records with digest `d292eec0cf32e5d6`, and the older copy exactly **81** with `0b07fefcd1841e85`;
2. everything else in each file digests to the expected value (`33622d20f6231a7e` / `aeba532e0c43b6f1`), so nothing else in those files has moved since the dry run;
3. every record in the older copy is also in the live file;
4. no other file under the data directory mentions `interview_data`;
5. each file's sha256 equals the value verified against the backup (`--expect-file-sha256`);
6. `--confirm-count 99` matches.

It also refuses if a file changes while it runs, and it re-reads each file after writing it. You should also stop if:
the backup check below does not say `IDENTICAL` twice; the dry run does not end `all gates pass`; the dry run's
numbers differ from "Expected output"; the system is between 20:25 and 20:45 UTC (the nightly backup runs at 20:30).

## What the deletion does NOT remove (decision for the owner)

Backups. Every existing nightly restic snapshot (5 Oct onward) holds these records. The retention in force is
7 daily / 4 weekly / 6 monthly, so the records age out of the backups over as long as ~6 months. Neither
retention nor snapshots are touched by this procedure (standing rule: no manual snapshot deletion, no retention
changes). If the records must also leave the backups, that is a separate, explicit decision.

## Commands (run from Git Bash on the laptop; each is one command)

`SSH` is `ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90`. The tool is piped over stdin, so nothing is
left on the host.

### 0. Baseline of everything else (read-only; writes a local file)

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 "cd /var/lib/docker/volumes/teleautomation-production_operations_data/_data && find . -type f ! -name job_heartbeats.json ! -name gmail_reconnect_notices.json ! -name '*.tmp' -print0 | sort -z | xargs -0 sha256sum" > purge-before.txt
```

Expected: 383 lines (`wc -l purge-before.txt`). The two excluded files are background-job state that changes on its own.

### 1. Backup check (read-only)

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'bash -s' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/interview_purge_backup_check.sh"
```

Expected (times differ): `backup lock held right now: no`; latest nightly snapshot `da4a7033` (or a newer one);
for **both** files `-> IDENTICAL: the snapshot holds this exact file`; live sha256 values
`8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91` and
`78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d`; `last_result: ok`.
If a newer snapshot exists and a file shows `DIFFERENT`, stop: the file changed after that backup.

### 2. Dry run (read-only)

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - plan --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --also credentials.json.pre-srujan-import-20261005T093705Z --expect-file-sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91 --also-expect-file-sha256 credentials.json.pre-srujan-import-20261005T093705Z=78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

Add `--manifest` to also list every record as an opaque `tag:tag` pair (no ids, no contents).

Expected output: see "Expected output: dry run" below (it is the real output of the 9 Oct dry run).

### 3. THE DELETION (run only when 1 and 2 are exactly as expected)

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - apply --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --also credentials.json.pre-srujan-import-20261005T093705Z --confirm-count 99 --expect-file-sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91 --also-expect-file-sha256 credentials.json.pre-srujan-import-20261005T093705Z=78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

### 4. Verify (read-only)

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - verify --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --also credentials.json.pre-srujan-import-20261005T093705Z' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 "cd /var/lib/docker/volumes/teleautomation-production_operations_data/_data && find . -type f ! -name job_heartbeats.json ! -name gmail_reconnect_notices.json ! -name '*.tmp' -print0 | sort -z | xargs -0 sha256sum" > purge-after.txt
```

```bash
diff purge-before.txt purge-after.txt
```

Expected: exactly **two** lines differ (one hunk each) and nothing else, which means the expense store, the other
Data Room files, salaries, proofs and every other store are identical. The line numbers can differ by a line or two
if files were added meanwhile; the content must be this:

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

(Generated from the real before-checksums with only those two hashes substituted.) Then check the
site: `curl -s https://operations.teleautomation.online/health` returns `{"status":"ok",...}`, and Data Room loads.

## Rollback

Use it only if something is wrong after step 3. The tool will not overwrite a file that changed since the purge.
It needs the exact pre-purge bytes, which the verified snapshot `da4a7033` holds.

R1. Restore both files from the snapshot into a root-only scratch directory (writes a copy of the records there):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'umask 077 && mkdir -p /root/purge-rollback && RESTIC_REPOSITORY=/var/backups/teleautomation/restic RESTIC_PASSWORD_FILE=/etc/teleautomation/restic.password restic restore da4a7033 --target /root/purge-rollback --include /var/backups/teleautomation/stage/operations-data/data_room/credentials.json --include /var/backups/teleautomation/stage/operations-data/data_room/credentials.json.pre-srujan-import-20261005T093705Z && sha256sum /root/purge-rollback/var/backups/teleautomation/stage/operations-data/data_room/credentials.json*'
```

Expected: the two sha256 values equal the pre-purge ones (`8173a1ad…` and `78424ddf…`).

R2. Put the live file back:

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - rollback --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --file credentials.json --from /root/purge-rollback/var/backups/teleautomation/stage/operations-data/data_room/credentials.json --expect-source-sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

Expected: `restored credentials.json: 17948 -> 104979 bytes; sha256 8173a1ad…; 99 records back`, `RESULT: restored.`
R3 (only if the older copy is also wanted back) is the same with `--file credentials.json.pre-srujan-import-20261005T093705Z`,
its path under `/root/purge-rollback/...`, and `--expect-source-sha256 78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d`.

R4. Remove the scratch copy when finished (so the records do not stay on disk in a second place):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'rm -rf /root/purge-rollback && ls -d /root/purge-rollback 2>&1'
```

Expected: `ls: cannot access '/root/purge-rollback': No such file or directory`.

If the rollback refuses because the live file changed since the purge (any other key moved), do not force it:
the records can be merged back by hand from the restored copy.

## Expected output: dry run (step 2)

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

Any `[FAIL]` line, or `RESULT: STOP`, means do not proceed.

## Expected output: the deletion (step 3)

The same file descriptions and gate list as above, plus one more gate
`[PASS] --confirm-count 99 matches the 99 records to be deleted`, then:

```
purged credentials.json.pre-srujan-import-20261005T093705Z: 81 records removed; 82698 -> 16137 bytes; sha256 1b12a5fff9df565c7df01b53829cdc0aeca6cf34ba7c5d6327e67934ff96e7c0; the rest of the file is unchanged (aeba532e0c43b6f1); mode and owner kept
purged credentials.json: 99 records removed; 104979 -> 17948 bytes; sha256 d00ee10b7ecda4e0d9b4a49255b58116e2f9b00c5b9b2830998f7c238e20d01b; the rest of the file is unchanged (33622d20f6231a7e); mode and owner kept
files under the data directory that still mention the key: none
RESULT: done. credentials.json.pre-srujan-import-20261005T093705Z, credentials.json
```

Exit status 0. Anything else (`STOP`, `ERROR`, exit status 1 or 2) means stop and report it. The older copy is
written first, so a refusal on the live file leaves the live data as it was.

## Expected output: verify (step 4)

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
