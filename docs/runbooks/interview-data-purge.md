# Removing the legacy Interview Data records (runbook)

Prepared 9 Oct 2026, revised 10 Oct 2026.

> **Current state: production is RUNNING and untouched.** Nothing has been stopped, and nothing here has been run
> against production except read-only checks. The procedure below stops the application at Phase 1 and starts it
> again at Phase 3. The deletion is run by the owner, never by Claude.

## What is being removed

| File (in `data_room/` on the operations data volume) | Records | Records digest |
|---|---|---|
| `credentials.json` (live) | 99 (81 WhatsApp import + 17 srujan import + 1 Thrilok dataset) | `d292eec0cf32e5d6` |
| `credentials.json.pre-srujan-import-20261005T093705Z` (older app-made copy) | 81 (all 81 are also in the live file) | `0b07fefcd1841e85` |

Only the top-level key `interview_data` is removed, from only those two files. No other file under the data
directory mentions the key (checked). The code and schema were removed on 6 Oct 2026 (`da09f6e`, `6770713`).

## Why the application must be stopped (Phase 1)

The application is the only writer of `credentials.json` (one uvicorn worker; only the `operations-api` container
mounts that volume). Its store does **load the whole file, change something, save the whole dict back**: Data Room
edits, handler password changes and `sync_admin_login_copy` all do this. A request that loaded the file *before* the
purge and saves it *after* writes `interview_data` straight back. The purge tool's hash checks cannot prevent that:
the application takes no lock the tool could respect.

This was reproduced in rehearsal on the production host (stand-in application, fixture data): an in-flight request
that loaded the file before the purge restored all 99 records afterwards, and the tool's watch caught it. So:

1. **Stop the application** (the only writer) before the purge, and keep it stopped until the purge is verified.
2. The tool **refuses to apply** unless the writer is demonstrably down, using safeguards that are **pinned in the
   tool and cannot be weakened** (next section), and it **watches the files for 10 s after writing**.
3. Verify with the application **stopped**, **restart** it, and verify **again** with it running (`--after-restart
   --watch 60`), then once more later.

Proven against production today (read-only): with the application running, `apply` refuses (exit 2, nothing written)
even when given `--writers-stopped`: the container is running and port 8210 is open.

## Safeguards that cannot be weakened

An earlier version accepted `--min-idle-seconds 0` and any port named with `--require-port-closed`, so a mistaken
or hurried command could switch the writer checks off. Those options no longer exist (the tool answers
`unrecognized arguments`). The checks are pinned in the tool and run on every `apply`:

| Check | Pinned value | How it is checked |
|---|---|---|
| The application container is not running | `teleautomation-production-operations-api-1` | `docker inspect`; "cannot confirm" (no Docker, no such container) is a refusal, not a pass |
| The port that is checked is the application's | `127.0.0.1:8210` | must be a port Docker's own record says the container publishes, so naming the wrong port fails |
| Nothing is listening on that port | `127.0.0.1:8210` | a real connection attempt must be refused |
| No process has the files open | both target files | `/proc` scan |
| The files have been quiet | 120 s | the file's last-written time |
| The files are watched after writing | 10 s | the key must not come back and the files must not be rewritten |
| The writer is re-checked before each replacement | container, port and open files | repeated immediately before each of the two files is replaced, so a writer that appears after the gates is caught before that file is touched |
| `--writers-stopped` | required | your confirmation only; it is checked by the lines above, not trusted |

`--expectations` (a file that replaces the expected values and these settings) exists only so fixtures can be tested;
**on the production data directory the tool refuses it**, so the pinned values are the ones in force.
`plan --check-writer` shows these checks without applying anything.

## What has been verified (9-10 Oct 2026)

- **The tool** (`scripts/purge_interview_data.py`, stdlib only, pure ASCII): 129 tests against a fixture shaped like
  production (126 run on Windows; the 3 POSIX-only ones, for file modes and the open-file check, were skipped there and
  their behaviour was exercised in the Linux rehearsals below), each safety property mutation-checked, including every
  pinned value, the refusal of `--expectations` on the production directory, and the exit-status guarantee (a failure on
  the second file after the first was purged is exit 1 with the state spelled out; a fault-injection matrix over every stage
  of the write asserts that exit 2 only ever means nothing changed, and a central backstop enforces it). They include
  the **delayed concurrent write**: a stale writer that saves after the purge restores the records and `verify` catches
  it; the post-write watch catches a writer that wakes up late (records back, or other data rewritten); a stopped and
  restarted application cannot bring them back; `verify --watch` catches a late writer.
- **Rehearsals on the production host with real processes, ports and restic, on throwaway fixtures under `/tmp`**
  (all removed afterwards): (1) backup, byte-identity check, plan, refusals, apply, verify, restore, rollback, with
  modes/owners kept; (2) with the **final, pinned tool**: the weakening options are rejected; an unpaused apply is
  refused; a request that loaded the file before the purge is caught by the watch (and the hazard is reproduced); the
  full stop, purge, restart, verify cycle is clean, including 14 s after the moment the dead process would have saved
  its old copy; a process holding a file open blocks apply; the **wrong port** is caught against Docker's real record
  of the real production container (read-only `docker inspect`); and on the production data path an expectations
  override is refused before any data is read; (3) the **partial state** on real files with the real restic binary: the older
  copy purged and the live file changed by an application write, or failing with a disk error, gives exit status 1 with the
  state of each file spelled out (it used to exit 2 and say "refused"); finishing the purge on the live file alone works;
  undoing the older copy from a restic restore returns both files to their byte-identical starting state; a clean run is
  still exit 0 and a pure refusal still exit 2 with nothing changed.
- **The real backup**: nightly restic job healthy (last run 8 Oct 20:34 UTC; restore test and `check --read-data`
  passed; 0 consecutive failures). Snapshot `da4a7033` holds **byte-identical** copies of both target files.
- **The production dry run**: all 12 data gates pass, exit status 0, nothing written.

## Stop conditions (STOP and tell Claude; do not force anything)

Before it writes anything, the tool refuses (exit status 2: nothing was changed) unless all hold:

1. the live file holds exactly **99** records with digest `d292eec0cf32e5d6`, and the older copy exactly **81** with `0b07fefcd1841e85`;
2. everything else in each file digests as expected (`33622d20f6231a7e` / `aeba532e0c43b6f1`). Any application write changes `updated_at`, so this also trips if the application wrote since the dry run;
3. every record in the older copy is also in the live file;
4. no other file under the data directory mentions `interview_data`;
5. each file's sha256 equals the value verified against the backup;
6. `--confirm-count 99` matches;
7. the writer is down, by the pinned checks above: `--writers-stopped` given, the container stopped, port 8210 confirmed as its port and refusing connections, no process has the files open, both files idle for 120 s. A line saying the tool "could not confirm" something (for example Docker unavailable) is a refusal: stop.

It also refuses to replace a file that changed while it ran. **If that happens to the live file after the older copy was
already purged, the result is a PARTIAL STATE (exit status 1), not a refusal.** After writing it re-reads each file, then
watches for 10 s.
Stop yourself if: the backup check does not say `IDENTICAL` twice; the dry run does not end `all gates pass`; the dry
run differs from "Expected output"; the time is between 20:25 and 20:45 UTC (the nightly backup runs at 20:30).

## Reading the result (exit status and the RESULT line)

The tool's exit status is a promise, and it is enforced in one place: **exit status 2 means nothing was changed, and only
that.** The tool returns 2 only while it has replaced no file; if any other code path ever returned 2 after a file had been
replaced, the tool turns it into 1. Whatever else goes wrong after a change is exit status 1.

Add `; echo "exit status: $?"` to the end of the deletion command (2.2 already does) and read both the last `RESULT:` line
and the status:

| Last line of the output | Exit | What it means | What to do |
|---|---|---|---|
| `RESULT: done. credentials.json.pre-srujan-import-..., credentials.json` (apply), `RESULT: all gates pass.` (plan), `RESULT: clean.` (verify), `RESULT: restored.` (rollback) | 0 | Complete, or all good | Continue |
| `RESULT: STOP. N gate(s) failed. Nothing was written.` or `RESULT: refused. Nothing was written.` | 2 | **Nothing was changed.** The data is exactly as it was | Start the application again if it was stopped (3.1), then report the output |
| `RESULT: error. No file had been replaced, so nothing was written.` | 1 | An error (disk, permission) before any file was replaced | The data is as it was, but do not retry until Claude has read the output |
| `PARTIAL STATE: the purge did NOT complete, and the data has CHANGED.` ... `RESULT: PARTIAL (exit status 1).` | 1 | **At least one file was changed and the purge is incomplete** | **Do NOT start the application.** Follow "If the exit status is 1" below |
| `RESULT: error AFTER a file was replaced ...` or `ERROR (internal): a file was replaced, so the exit status is 1, not 2` | 1 | The same: something failed after a change | The same |

After any exit status 1, do not start the application until the state is understood. The application is only started
again after a clean `verify` or a completed rollback.

## If the exit status is 1: the partial state

The tool writes the older copy first and the live file second, so the most likely partial state is: the older copy purged,
the live file not (a disk error, or the application or someone writing to the live file in between). The output spells out the
state of every file, for example (real output from the rehearsal on the host, fixture data):

```
PARTIAL STATE: the purge did NOT complete, and the data has CHANGED.
  purged and verified (the key is gone):         credentials.json.pre-srujan-import-20261005T093705Z
  replaced but NOT verified:                     (none)
  not touched (still hold their records):        credentials.json
  changed by something else, not by this tool:   (none)
  why it stopped: OSError: [Errno 28] No space left on device
Do NOT start the application. The tool has changed nothing further. See the runbook, 'If the exit status is 1'.
RESULT: PARTIAL (exit status 1). At least one file was changed and the purge is not complete.
```

The four lines mean: *purged and verified* is finished and correct; *replaced but NOT verified* was written but is not
what was planned (roll it back); *not touched* still holds its records, byte for byte; *changed by something else* was
changed by the application or a person, not by the tool, and is shown with whether it still holds the records.

Keep the application stopped and send the whole output to Claude. Then, depending on the state:

**A. The older copy is purged and the live file is "not touched"** (the case above). Two ways out, both rehearsed:

- *Finish the purge on the live file alone.* The older copy no longer holds the key, so it is simply left out (the live file's
  digests and backup hash are unchanged). First the dry run, which must end `all gates pass`:

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - plan --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --expect-file-sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91 --check-writer' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

  then the live-only deletion (expect `purged credentials.json: 99 records removed`, `RESULT: done. credentials.json`, exit status 0):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - apply --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --confirm-count 99 --expect-file-sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91 --writers-stopped' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"; echo "exit status: $?"
```

  then verify as in 2.3 (expect `RESULT: clean.`) and carry on from Phase 3.
- *Undo the older copy, back to the starting state.* Run R1, then R3 (older copy only), then R4 below. The dry run (0.3) must
  then end `all gates pass` again, exactly as at the start.

**B. Anything else** (the live file shows "replaced but NOT verified", or "changed by something else", or "the key came
back"): do not try to finish. Keep the application stopped, run `verify` (2.3) and send Claude both outputs. The way back is
the rollback section: R1, then R2 for the live file (and R3 if the older copy should be restored too), then R4.

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

0.1 Pre-flight snapshot of everything else (writes `purge-preflight.txt` locally). It leaves out the three files the running
application rewrites on its own all day: `job_heartbeats.json`, `gmail_reconnect_notices.json` and `cross_project_outbox.json` (the
last one changed at 04:07 UTC on 10 Oct with nobody working, which is what broke an earlier whole-list comparison). Expect about 382
lines (not fewer than 380):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 "cd /var/lib/docker/volumes/teleautomation-production_operations_data/_data && find . -type f ! -name job_heartbeats.json ! -name gmail_reconnect_notices.json ! -name cross_project_outbox.json ! -name '*.tmp' -print0 | sort -z | xargs -0 sha256sum" > purge-preflight.txt
```

This snapshot is a record and a sanity check. **It is not the baseline for the later comparison**: while the application runs, its
other stores change with normal work (the expense store, referrers, reminders, payment ledger, uploaded proofs), so two snapshots
taken hours apart are not expected to be identical, and requiring that would give a false failure. What it must show is that the three
files this procedure depends on are as recorded (the two credentials files and the expense store):

```bash
grep -c -E "^(8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91  \./data_room/credentials\.json|78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d  \./data_room/credentials\.json\.pre-srujan-import-20261005T093705Z|c7ef95600bfe85c3f0126f1930c5a07e2eeaa76101e7423477518047679c6ca6  \./handler_expenses\.json)$" purge-preflight.txt
```

Expected: `3`. Anything else: do not start (a file was changed; find out why).

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

0.4 Confirm where things stand right now (read-only). Expect the application **running**, because nothing has been stopped yet:

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'echo "operations-api: $(docker inspect -f "{{.State.Status}} {{.State.Health.Status}}" teleautomation-production-operations-api-1)"; echo "listeners on 8210: $(ss -tln | grep -c ":8210 ")"'
```

Expected now: `operations-api: running healthy` and `listeners on 8210: 1`.

0.5 Quiet check (read-only): is anyone changing data right now? It reads the nginx logs **and** the application's own request log. Expect `VERDICT: GO`. Anything else: wait and run it again, do not stop the application.
It is advice about people; the safeguard is the stop and the tool's checks.

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'bash -s' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/interview_purge_quiet_check.sh"
```

Expected: `last 30 minutes: N external requests in nginx; M requests in the application's own log` (and at most login/logout lines) and
`VERDICT: GO. No data-changing request in the last 30 minutes.` A `NO GO` lists what was written (for example a booking confirmation,
an expense save, or a password change or reset, which write the credentials file): someone is working. Login, logout and the admin
re-check do not block.

**Phase 0 is only valid for 30 minutes.** If more than 30 minutes pass between 0.1 to 0.5 and the Phase 1 command, run Phase 0 again.
The rollback commands (R1 to R4) must be open and ready before you stop the application.

### Phase 1. PAUSE the writer (this takes the dashboard and public booking pages down)

1.1 Stop the application (graceful, up to 30 s) and confirm it is down:

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'docker stop --time 30 teleautomation-production-operations-api-1 && echo "running operations-api containers: $(docker ps -q --filter name=teleautomation-production-operations-api-1 | wc -l)"; echo "listeners on 8210: $(ss -tln | grep -c ":8210 ")"'
```

Expected: `teleautomation-production-operations-api-1`, then `running operations-api containers: 0` and `listeners on 8210: 0`.
If either is not `0`, do not continue.

1.2 Take the **comparison baseline now, with the application stopped** (read-only; writes `purge-before.txt` locally). With the only writer
down nothing can change between this and the end of Phase 2, so the diff in 2.4 is exact. Expect about the same 382 lines as 0.1 (a
few more if work was saved between 0.1 and the stop) and `3` from the same `grep -c` check as in 0.1, run on this file:

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 "cd /var/lib/docker/volumes/teleautomation-production_operations_data/_data && find . -type f ! -name job_heartbeats.json ! -name gmail_reconnect_notices.json ! -name cross_project_outbox.json ! -name '*.tmp' -print0 | sort -z | xargs -0 sha256sum" > purge-before.txt
```

### Phase 2. Purge while it is stopped

2.1 Dry run again, now also checking the pinned writer safeguards (expect all gates pass, including the six writer lines):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - plan --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --also credentials.json.pre-srujan-import-20261005T093705Z --expect-file-sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91 --also-expect-file-sha256 credentials.json.pre-srujan-import-20261005T093705Z=78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d --check-writer' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

If it says a file was "last written ... ago" under 120 s, someone wrote to the Data Room just before the stop: wait and repeat.

2.2 **THE DELETION** (only when 0.2, 0.3 and 2.1 are exactly as expected and the application is stopped):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - apply --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --also credentials.json.pre-srujan-import-20261005T093705Z --confirm-count 99 --expect-file-sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91 --also-expect-file-sha256 credentials.json.pre-srujan-import-20261005T093705Z=78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d --writers-stopped' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"; echo "exit status: $?"
```

Read the result with the table in "Reading the result": `RESULT: done.` and exit status 0 are the only success.

2.3 Verify while still stopped (expect `RESULT: clean.`):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - verify --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --also credentials.json.pre-srujan-import-20261005T093705Z' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

2.4 Checksum everything else again, still stopped, and compare with the baseline from 1.2 (same exclusions, so the same files are compared):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 "cd /var/lib/docker/volumes/teleautomation-production_operations_data/_data && find . -type f ! -name job_heartbeats.json ! -name gmail_reconnect_notices.json ! -name cross_project_outbox.json ! -name '*.tmp' -print0 | sort -z | xargs -0 sha256sum" > purge-after.txt
```

```bash
diff purge-before.txt purge-after.txt
```

Expected: exactly the two-hunk diff shown under "Expected output: checksum diff", and nothing else. This one is exact, because the
application was stopped from 1.2 until now.

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

4.2 Checksums once more with the application running, compared with the same baseline from 1.2. The two credentials files must show
their purged hashes. Because the application is running again, it may also have written its own stores since the restart (someone
saved an expense, uploaded a proof, a reminder was sent); any other line that differs must be explained by that, and none of it may be
a Data Room file. It is stability evidence, not the proof of untouched data (that is 2.4, which is exact):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 "cd /var/lib/docker/volumes/teleautomation-production_operations_data/_data && find . -type f ! -name job_heartbeats.json ! -name gmail_reconnect_notices.json ! -name cross_project_outbox.json ! -name '*.tmp' -print0 | sort -z | xargs -0 sha256sum" > purge-after-restart.txt
```

```bash
diff purge-before.txt purge-after-restart.txt
```

Expected: the same two hunks as in 2.4, plus at most lines for files the application wrote after the restart. Check the expense store
separately (it should be unchanged unless someone used it since the restart):

```bash
grep handler_expenses.json purge-before.txt purge-after-restart.txt
```

### Phase 5. Soak (same 4.1 command without `--watch`)

Run 4.1 again about 10 minutes later, and once more tomorrow after the 20:30 UTC backup. The first backup after the
purge holds the purged files; to confirm that (read-only; the command was tested today against the current snapshot and
printed `1`, the one `interview_data` line; after the purge and the next nightly backup it must print `0`):

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'export RESTIC_REPOSITORY=/var/backups/teleautomation/restic RESTIC_PASSWORD_FILE=/etc/teleautomation/restic.password; S=$(restic snapshots --tag nightly --host teleautomation-production --json --latest 1 | python3 -c "import json,sys; print(json.load(sys.stdin)[-1][\"short_id\"])"); echo "latest snapshot $S"; restic dump $S /var/backups/teleautomation/stage/operations-data/data_room/credentials.json | grep -c interview_data'
```

## Rollback

Use it only if something is wrong after 2.2 (exit status 1 with a PARTIAL STATE that you decided to undo, or a failed verify). The tool will not overwrite a file that
changed since the purge, and it needs the exact pre-purge bytes, which snapshot `da4a7033` holds. **Roll back with the
application stopped** (if it was already started, stop it first with command 1.1).

R1. Restore both files from the snapshot into a root-only scratch directory (writes a copy of the records there). This exact
command was verified on 10 Oct against the real snapshot (restored into RAM instead of `/root`): it restores exactly the two files, with
hashes identical to the pre-purge values:

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'umask 077 && mkdir -p /root/purge-rollback && RESTIC_REPOSITORY=/var/backups/teleautomation/restic RESTIC_PASSWORD_FILE=/etc/teleautomation/restic.password restic restore da4a7033 --target /root/purge-rollback --include /var/backups/teleautomation/stage/operations-data/data_room/credentials.json --include /var/backups/teleautomation/stage/operations-data/data_room/credentials.json.pre-srujan-import-20261005T093705Z && sha256sum /root/purge-rollback/var/backups/teleautomation/stage/operations-data/data_room/credentials.json*'
```

Expected: the two sha256 values equal the pre-purge ones (`8173a1ad...` and `78424ddf...`).

R2. Put the live file back:

```bash
ssh -i ~/.ssh/teleautomation_vps_ed25519 root@187.127.164.90 'python3 - rollback --data-dir /var/lib/docker/volumes/teleautomation-production_operations_data/_data --file credentials.json --from /root/purge-rollback/var/backups/teleautomation/stage/operations-data/data_room/credentials.json --expect-source-sha256 8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91' < "C:/Project Opus/tele-ops/.worktrees/interview-purge/scripts/purge_interview_data.py"
```

Expected: `restored credentials.json: 17948 -> 104979 bytes; sha256 8173a1ad...; 99 records back` and `RESULT: restored.`
(Rollback's own statuses follow the same rule: 2 = nothing written; 1 = an error after the file was replaced, e.g. the restored
file could not be read back, in which case do not start the application.)
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

The same as the dry run above, with these six extra gate lines after the backup-binding lines (the "ago" numbers keep
growing; anything over 120 is right):

```
  [PASS] the application container teleautomation-production-operations-api-1 is stopped
  [PASS] 127.0.0.1:8210 is the application's published port (Docker's own record)
  [PASS] nothing is listening on 127.0.0.1:8210
  [PASS] credentials.json: last written 385000s ago (needs at least 120s of quiet)
  [PASS] credentials.json.pre-srujan-import-20261005T093705Z: last written 387500s ago (needs at least 120s of quiet)
  [PASS] no process has these files open
RESULT: all gates pass. Nothing was written.
```

**With the application running, which is today's real state**, the same command prints these instead of the first
and third lines (real output from production, read-only, 10 Oct 2026), and ends `RESULT: STOP. 2 gate(s) failed. Nothing was written.`:

```
  [FAIL] the application container teleautomation-production-operations-api-1 is still RUNNING
  [PASS] 127.0.0.1:8210 is the application's published port (Docker's own record)
  [FAIL] something IS listening on 127.0.0.1:8210: the application is still running
```

## Expected output: the deletion (2.2)

The file descriptions and the same gate list as the dry run, then these extra lines, then the result:

```
  [PASS] --writers-stopped: you confirm the application is stopped (checked below, not trusted)
  [PASS] the application container teleautomation-production-operations-api-1 is stopped
  [PASS] 127.0.0.1:8210 is the application's published port (Docker's own record)
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

Exit status 0 and `RESULT: done.` are the only success. Anything else: stop and read "Reading the result". Note the order:
the older copy is written first, so if the live file then fails, the older copy **has** been purged and the result is a
PARTIAL STATE (exit status 1), never exit status 2.

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

**In 2.4** exactly two lines differ (one hunk each) and nothing else, which means the expense store, the other Data Room files,
salaries, proofs and every other store are identical. **In 4.2** the same two hunks appear, plus at most lines for files the running
application wrote after the restart. Line numbers can shift by a line or two if files were added; the content must be this:

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
