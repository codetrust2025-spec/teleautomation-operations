# Interview Data purge: execution plan (for approval)

> **Status: DRAFT, NOT APPROVED. Nothing has been executed.** Production is running and unchanged by this procedure (release `0ff815e` since 10 Oct 08:12 UTC; `4a7c61c` when this plan was drafted),
> `operations-api` healthy; the three files this depends on match the recorded hashes). The application is not stopped and the
> purge is not run until the owner approves this plan, in writing, in the block at the end.
>
> The commands, the expected outputs and the tool are in [`interview-data-purge.md`](interview-data-purge.md). This page is
> the decision: what is being done, to what, when, how it is checked, and how it is undone.

## 1. What you are approving

| | |
|---|---|
| **Scope** | Remove the top-level key `interview_data` from exactly two files: the live `data_room/credentials.json` (99 records) and the older app-made copy `credentials.json.pre-srujan-import-20261005T093705Z` (81 records, all of which are also in the live file). Nothing else. |
| **Recovery point** | Nightly restic snapshot `da4a7033` (8 Oct 20:33 UTC): byte-identical copies of both files, verified read-only. The files have not been written since 5 Oct, so no data is lost by restoring it. Re-verified at execution time (Phase 0.2). |
| **Downtime** | The application is stopped for the purge: **about 3-5 minutes** (stop up to 30 s, purge a minute or two of human steps, start about 1-2 min; the 9 Oct restart reached healthy in 42 s). Window to be chosen: see section 4. |
| **Verification** | Before: backup byte-identity, dry run (12 data gates), pinned writer checks, no deploy running. After: verify while stopped, health and release after restart, verify with a 60 s watch, checksum diff of every data file against a baseline taken after the stop (exactly two lines differ), a soak check later. Section 7. |
| **Rollback** | Restore the two files from snapshot `da4a7033` into a root-only scratch directory, put each back with the tool (refuses if anything else changed), remove the scratch copy. Section 8. |
| **Who runs what** | The owner runs every command that stops the application or deletes data. Claude reads each output with you and says go or stop; Claude does not run Phase 1 or the deletion. |

## 2. Scope in detail

**In scope**

- `interview_data` in `credentials.json`: 99 records, digest `d292eec0cf32e5d6` (81 WhatsApp import, 17 srujan import,
  1 Thrilok dataset). The tool identifies them by count, digest and salted-hash tags, never by id or content.
- `interview_data` in `credentials.json.pre-srujan-import-20261005T093705Z`: 81 records, digest `0b07fefcd1841e85`. Included
  so no copy of the records remains in the data directory ("remove completely"). The tool refuses to purge the live file alone
  while this copy still holds the key.

**Out of scope (left exactly as it is)**

- The expense store, the rest of the Data Room, salaries, proofs, every other file: checksummed before and after.
- The other `credentials.json.*` copies (they do not hold the key).
- **Backups.** Every nightly snapshot since 5 Oct holds the records, and retention (7 daily, 4 weekly, 6 monthly) keeps them up
  to about 6 months. Neither snapshots nor retention are touched (standing rule). Removing the records from backups is a
  separate decision for the owner (section 10).
- No release, no deploy, no code change, no database change. The application image stays whatever is running when the window starts (`0ff815e` at the time of writing).

## 3. Recovery point

- **Snapshot `da4a7033`**, taken 8 Oct 20:33 UTC by the nightly job (status `ok`, restore test and `restic check --read-data`
  passed, 0 consecutive failures). Both target files in it are byte-identical to the live files (sha256 `8173a1ad...` and
  `78424ddf...`).
- Why it is a valid recovery point: the live file's last write was 5 Oct 09:37 UTC. Phase 0.2 re-checks at execution time; a
  newer nightly snapshot is equally good, and "DIFFERENT" means stop.
- The tool will not overwrite a file that changed after the purge, so a rollback cannot silently discard later Data Room edits.
  The application is stopped while the purge and any rollback run, so no such edit can happen in the window.
- Optional extra recovery point (not required): a manual snapshot immediately before the window, which forgets nothing,
  `TA_SKIP_RETENTION=1 /usr/local/sbin/teleautomation-backup` (about 2.5 minutes, runs with the application up). It adds
  nothing for these two files, which have not changed since 5 Oct.

## 4. Downtime window

**What the pause does.** While `operations-api` is stopped, the dashboard and the dynamic public pages (slot booking on both
domains) return 502 and submissions fail; the static landing page stays up. The Gmail, reminder and outbox loops pause and
resume. The host monitor runs every 5 minutes and only alerts after a check has failed for 5 minutes, so a short pause
normally raises nothing (a longer one may open a GitHub alert issue that closes itself on recovery).

**Evidence** (nginx access logs, 25 Sep to 9 Oct 2026: **15 days**, external requests, counts only; IST = UTC + 5:30; "writes" are
POST/PATCH/DELETE, the requests that fail if the application is down and have to be retried):

| Over the 15 days | Window A: 21:15-23:30 UTC (02:45-05:00 IST) | Window B: 12:00-14:00 UTC (17:30-19:30 IST) |
|---|---|---|
| Requests in the window | 32,097 (1,300-4,000 on most nights; 42 on 8 Oct) | 35,382 (1,400-4,800 most days) |
| Distinct clients | 55 | 118 |
| Bot-like user agents / 4xx-5xx | 1% / 0% | 1% / 0% |
| **Writes in the window** | **8** (busiest single day: 2) | **64** (busiest single day: 30) |
| What the writes were | logins and one scanner probe among them; no booking, expense or edit in the top kinds | logins, 10 booking confirmations, 10 slot-invite parses, candidate edits and deletes, handler-expense saves |

How to read it: in **both** windows most requests are reads from dashboards that stay open and poll in the background (a
tab left open overnight just shows a 502 for the pause and recovers). What differs is writes. A write that lands during the
pause fails and has to be retried: about one every two days in window A, about four a day in window B, with a worst day of 30.
Window A is therefore the lower-impact choice **because of writes, not because traffic is low** (an earlier draft of this page,
`d5999dd`, said "about 20 requests per hour" for window A; that came from the last 1.7 days only and the 15-day data
corrects it).

**Honest limit.** Fifteen days is still a short history. It covers both weekdays and weekends and window A never exceeded 2 writes
in a day, but a change in how the business works (an interview batch, month-end payouts) could shift it. So the window is a
starting point and **the live quiet check decides** (section 6, item "quiet check"): the application is only stopped if nobody
changed any data in the previous 30 minutes, whichever window is chosen.

**Never** between **20:25 and 20:45 UTC** (01:55-02:15 IST): the nightly backup runs at 20:30. Also never while a deploy or
merge to `main` is in progress (a deploy replaces the container).

**Options (decision 1 in section 10)**

- **A. Fewest writes: 21:15-23:30 UTC (02:45-05:00 IST).** 8 writes in 15 days (busiest day 2: logins and a scanner probe among
  them), though background read traffic from open dashboards continues (a 502 for a few minutes, then it recovers). Starts after
  the backup has finished. An inconvenient hour for the person running it.
- **B. Daytime: 12:00-14:00 UTC (17:30-19:30 IST).** 64 writes in 15 days (busiest day 30; bookings, expense saves and candidate
  edits among them), so a write failing during the pause is likely on a busy day. Easier to run and more visible: people would need
  to be told in advance (decision 4), and the quiet check may well say NO GO.

Recommendation: **A**, unless people can be told in advance and the quiet check passes, in which case B is acceptable. One small check either way:
no interview scheduled to be reminded in the window (Daily Ops), and the live quiet check passes.

## 5. Sequence and time budget (T = the moment the application is stopped)

| When | Phase | Planned time | Who | Go / stop rule |
|---|---|---|---|---|
| T minus 30 min | **Phase 0** read-only checks (0.1 to 0.5) and the go/no-go list in section 6 | 5 min | owner, Claude reads | Any item not green: do not start |
| T | **1.1** `docker stop` the application | up to 30 s | owner | Must show 0 containers and 0 listeners; otherwise stop here, nothing changed |
| T + 30 s | **1.2** comparison baseline of all data files, with the application stopped | seconds | Claude (read-only) | About 382 lines, and the three key hashes present |
| T + 1 min | **2.1** dry run with `--check-writer` | seconds (measured 0.4 s on production) | owner | Must end `all gates pass`; a "last written ... ago" under 120 s means wait |
| T + 2 min | **2.2** the deletion | about 15 s (a 10 s watch after writing) | owner | `RESULT: done.` and exit 0 only; anything else: section 8 |
| T + 3 min | **2.3, 2.4** verify and checksum diff while stopped | 1 min | owner | `clean`, and exactly two lines differ |
| T + 4 min | **3.1, 3.2** start, wait healthy, `/health` and `/version` | 1-2 min | owner | Healthy within 40 checks (about 3 min) |
| T + 6 min | **4.1, 4.2** verify with a 60 s watch, checksum diff again | 2 min | owner | `clean`, key did not come back |
| T + 10 min and next day | **5** soak verify; backup content check after the next nightly backup | 1 min each | owner | `clean`; the check prints `0` |

The application is **started only** when one of three things is true: the purge verified clean (2.3 and 2.4), the tool
refused with exit 2 (nothing changed), or a rollback completed. After any exit 1 it stays stopped until the state is understood.
That can extend the downtime beyond 5 minutes, deliberately: a running application can overwrite the data being repaired.

## 6. Go / no-go checklist (before stopping anything)

**Revalidation rule.** Nothing measured earlier counts at execution time: not the timings, not the backup state, not the traffic
pattern in section 4. Only Phase 0 outputs from the **last 30 minutes** are valid. If more than 30 minutes pass between Phase 0 and
Phase 1.1 (or anything unexpected happens), repeat Phase 0 from the top. Every box below is ticked from fresh output.

**Rollback must be ready before the service is stopped.** The recovery point is re-verified in 0.2 (`IDENTICAL` for both files,
within the 30 minutes), the rollback commands R1 to R4 are open and ready in a second window, `/root/purge-rollback` does not
already exist, and the host has free disk (the 0.2 output ends with the free space of the backup volume).

- [ ] Phase 0.1: about 382 lines (not fewer than 380), and the `grep -c` check prints `3` (both credentials files and the expense store
      as recorded). It does **not** have to equal an earlier snapshot: the running application changes its own stores with normal work
      (and `cross_project_outbox.json`, `job_heartbeats.json`, `gmail_reconnect_notices.json` on their own), so the comparison
      baseline is taken after the stop (1.2).
- [ ] Phase 0.2: `IDENTICAL` for both files; `last_result: ok`; backup lock not held; free disk shown.
- [ ] Phase 0.3: `RESULT: all gates pass. Nothing was written.`, exit 0, numbers as in the runbook.
- [ ] Phase 0.4: `operations-api: running healthy`, one listener on 8210 (confirms nothing was stopped early).
- [ ] Phase 0.5, **quiet check**: `VERDICT: GO` (no data-changing request in the last 30 minutes in either log; login, logout and the
      admin re-check do not block; password changes and resets do, because they write the credentials file). It is advice about
      people. The safeguard is the stop and the tool's checks (section 8A).
- [ ] No deploy run in progress and no merge to `main` planned during the window (`gh run list --workflow deploy.yml`).
- [ ] The time is outside 20:25-20:45 UTC and inside the approved window.
- [ ] The runbook and tool in use are the approved commit (draft PR #322, CI green), not a different one.
- [ ] Rollback is ready, as described above, and Claude is available to read outputs.

## 7. Verification (what proves it worked)

1. Tool, while stopped: `verify` prints `RESULT: clean.`; the key is gone from both files and everything else in them is as it was.
2. Data volume: the checksum diff against the baseline taken after the stop (1.2) shows exactly two changed lines (the two
   credentials files) out of about 382, and nothing else: the expense store, the other Data Room files, salaries, proofs and every
   other store are byte-identical. Exact, because the application was stopped for the whole interval.
3. After the restart: `operations-api` healthy, `/health` ok, `/version` shows the same release as before the stop (read it in Phase 0; `0ff815e` at the time of writing).
4. With the application running: `verify --after-restart --watch 60` is clean (nothing brought the key back), and the
   checksum diff is still those two files, plus at most files the running application wrote after the restart (each explained by
   activity; none a Data Room file). This is stability evidence; the proof that unrelated data is untouched is item 2.
5. Soak: the same verify about 10 minutes later and the next day; the backup content check after the next nightly backup
   prints `0`.
6. What this does not prove, stated plainly: it proves the records are gone from the data directory and the new backups. It does
   not remove them from existing snapshots (section 2).

## 8. Rollback and recovery matrix

The tool will not overwrite a file that changed since the purge. All rollback runs with the application stopped.

| What happens | Meaning | Action |
|---|---|---|
| 1.1 does not stop it (container still running, listener present) | Nothing changed | Do not continue; investigate; the application stays as it is |
| Any command in Phase 2 ends `RESULT: STOP ... Nothing was written.` (exit 2) | **Nothing was changed** (guaranteed by the tool) | Start the application (3.1); read the failed gate; reschedule |
| `PARTIAL STATE` (exit 1), older copy purged and live file "not touched" | At least one file changed; the purge is incomplete | Stay stopped. Either finish on the live file alone (runbook, partial state A) or undo the older copy (R1, R3, R4); then continue from 2.3 |
| `PARTIAL STATE` with the live file "replaced but NOT verified", "changed by something else", or the key came back | The live file is not in a known-good state | Do not try to finish. R1, then R2 (and R3 if you want everything back), then R4; start the application only after |
| 2.3 or 2.4 shows the key still present, or any file other than the two changed | The purge is wrong | Rollback R1 to R4 for the affected files; start the application after |
| Application does not become healthy after 3.1 | Not caused by the purge: the application never reads this key, and it was rehearsed against the real store. Look at the container log | Check `docker logs`; do not touch the data; escalate. If a rollback of the data is wanted anyway: R1 to R4, then 3.1 |
| 4.1 reports the key came back | Something wrote a stale copy after the restart (should not happen) | Stop the application (1.1), rerun the purge from Phase 2 with it stopped, or roll back; report |
| The restore (R1) cannot read the backup | The recovery point is unusable | Stop and escalate before anything is purged; this is why Phase 0.2 comes first |

Rollback time: the commands themselves take seconds (reading both files out of the real repository took 1 s, measured
read-only); allow a few minutes for the human steps. Rollback was rehearsed end to end on the host (real restic, fixture data):
the files return byte-identical, with modes and owners kept.

## 8A. Can a write still happen after the quiet check? What prevents it

No: the quiet check is **not** what prevents writes, and it cannot be: it only says nobody is working right now. What prevents them is
that **the only writer is stopped, and the tool refuses to proceed unless that is demonstrably true**, repeatedly.

**Who can write these two files** (audited against the code deployed on production, `4a7c61c`, read-only; the one release since, `0ff815e`, changes only the invite reader, the booking form and booking validation, and touches no Data Room store, auth route or worker):

- Only the Data Room routes (POST/PATCH/DELETE under `/data-room/credentials/...`, service-account images, offer-letter uploads) and the
  password routes (`/auth/change-password`, `/auth/reset-password`, which write the credentials file). Every one needs a logged-in
  session, so it comes through nginx and shows in the log the quiet check reads.
- A one-time plaintext-password migration, and a one-time offer-letter migration on a read; both are already done in production and
  would only ever change the file (so they trip the tool's "everything else is as expected" gate).
- **No background loop or worker touches the store** (none of `services/` or `workers/` imports it).
- **Other containers cannot reach a writer.** The marketing container does hold an internal URL and token for the operations API, but
  the service token only authorizes `/internal/...`, `/ai/smart-reply/...` and `POST /inbox/*/sync/*`; everything else needs a user
  session. Only the operations container mounts the data volume.
- Host-level jobs: the nightly backup only reads the volume; nothing else on the host writes it (cron and timers checked).

**The layers, in the order they act**

| Layer | What it does | Catches |
|---|---|---|
| Quiet check (0.5) | Reads nginx **and** the application's own request log for the last 30 minutes | A person working: do not interrupt them |
| `docker stop` (1.1), graceful up to 30 s | Removes the only writer; in-flight requests finish first | The stale in-flight write that this whole procedure exists to prevent |
| Gates in the tool (2.1/2.2) | Container stopped, port 8210 confirmed as its port and refusing connections, no process holding the files open, files quiet for 120 s, everything else in the files as expected | A forgotten stop, a restart, a just-finished write |
| **Re-check before each replacement** | The container, port and open-file checks are repeated immediately before each of the two files is replaced | A writer that appears after the gates and before a write (the file is not touched; if it is the second file, exit 1 and a partial-state report) |
| Replace guard | The file is re-read and compared (hash, size, mtime) immediately before it is replaced; temp file + atomic rename | A write between the last read and the replace |
| Watch after writing (10 s) | The files must stay exactly as written | A late or stale writer that gets in anyway |
| Verify with `--watch` after the restart (4.1) | 60 s of looking for the key coming back | A writer that wakes up after the restart |

**What this does not do:** it does not physically lock the files (the application takes no lock the tool could respect), and it cannot
stop someone who deliberately starts the container or runs a deploy during the window. The go/no-go list has "no deploy or merge in
progress"; the re-check before each write catches a container started in the middle of the run; and the restart policy
(`unless-stopped`) means a stopped container stays stopped until someone starts it (nothing on the host does).

## 8B. The recovery point, verified independently (10 Oct 2026)

Not taken from a report: done against the real backup, then cleaned up.

- **A real restore** of both files from snapshot `da4a7033` (taken 8 Oct 20:33 UTC), using the same restic command as R1 with the target in
  RAM (`/dev/shm`, root-only), so no second copy of the records touched disk. It restored **exactly the two target files** (nothing
  extra), in 0 s.
- **Hashes identical to the recorded pre-purge values**: live `8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91`,
  older copy `78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d`; modes `644 root:root`, as in production.
- **The tool's own rollback** was run against production with those restored files. Its gates for the right file, the right hash, the
  right record count and digest (99 / `d292eec0cf32e5d6`, 81 / `0b07fefcd1841e85`) and "nothing else has changed" all **passed**; it
  refused only on "the purge is still in effect" (correct: nothing has been purged), exit status 2, nothing written.
- **Cleaned up**: the RAM copy was removed and nothing under `/dev/shm` mentions the key.
- Not exercised against production data, by design: the final write of a restored file over a purged one. That step was rehearsed
  end to end on the host with the real restic binary on a fixture (byte-identical result, modes and owners kept), and the tool's
  refusal-before-write behaviour was just shown on the real files.

### The pre-purge record (taken 10 Oct, read-only; re-take at execution time)

| Item | Value |
|---|---|
| Live file `credentials.json` | size 104,979; mtime 2026-10-05 09:37:06 UTC; sha256 `8173a1ad1cf9b5bf9d44f94d841df1a6ccaad12e6c28c29c5775054343b68e91`; 99 records, digest `d292eec0cf32e5d6`; rest-of-file digest `33622d20f6231a7e` |
| Older copy | size 82,698; mtime 2026-10-05 08:54:15 UTC; sha256 `78424ddfa75f365ed5300246b7ad65128679e0c4d17f4fe2eb9545b6f235967d`; 81 records, digest `0b07fefcd1841e85`; rest-of-file digest `aeba532e0c43b6f1` |
| Recovery point | restic snapshot `da4a7033`, 2026-10-08 20:33:10 UTC, tag `nightly` |
| Expected after the purge | live 17,948 bytes, sha256 `d00ee10b7ecda4e0d9b4a49255b58116e2f9b00c5b9b2830998f7c238e20d01b`; older 16,137 bytes, sha256 `1b12a5fff9df565c7df01b53829cdc0aeca6cf34ba7c5d6327e67934ff96e7c0` |
| Data volume pre-flight (10 Oct) | 382 files, leaving out the three self-updating application files; expense store `handler_expenses.json` sha256 `c7ef95600bfe85c3f0126f1930c5a07e2eeaa76101e7423477518047679c6ca6`. The earlier 383-file record (sorted-list sha256 `81a954b0bb3513c1a90c3de0b08ede295f07bec1c2687260a75ac91d5ce69acd`) included `cross_project_outbox.json`, which the application rewrites by itself; it changed at 04:07 UTC on 10 Oct and was the only file that differed (checked: the two credentials files and the expense store were identical). That is why the comparison baseline is now taken after the stop |
| Application | release `0ff815e` (since 10 Oct 08:12 UTC; was `4a7c61c`), `operations-api` running and healthy |

At execution time the operator keeps the **1.2** file (`purge-before.txt`, taken with the application stopped) with the approval
record and writes down `sha256sum purge-before.txt` next to the table above. That is the working baseline for the diff in 2.4 and
4.2. The Phase 0.1 file (`purge-preflight.txt`) is kept as the pre-flight record only.

## 9. Residual risks, honestly

- **Backups keep the records** until retention expires them (up to about 6 months). By design; section 2.
- **Downtime is real**: 3-5 minutes of 502 on the dashboard and dynamic public pages. If a booking or a Daily Ops action
  happens in that minute, it fails and has to be retried.
- **An in-flight request at the moment of the stop** is the only thing that could rewrite the file. The graceful stop
  (up to 30 s) lets it finish before the purge; the tool then refuses unless the container is stopped, the port is closed and
  no process holds the files, repeats those checks immediately before each file is replaced, and watches the files for 10 s
  after writing (section 8A).
- **The tool is new.** It is stdlib-only, was rehearsed on the production host on fixtures, mutation-tested, and its guarantees
  (exit 2 means nothing changed) are enforced in code and tested. It has not run against production data, only dry runs.
- **Another person or agent deploying during the window** would restart the application. Mitigation: the go/no-go list.

## 10. Decisions needed from the owner, and approval

1. **Window.** Option A (21:15-23:30 UTC) or B (12:00-14:00 UTC), and the date: ______________________
2. **Scope.** Both files (recommended), or the live file only with the older copy deliberately left in place: ______________
3. **Backups.** Leave existing snapshots to age out (recommended; no action), or request a separate plan to remove them: ______
4. **Notice.** Tell the handlers about the 3-5 minute interruption beforehand (recommended for option B): yes / no
5. **Operator.** Who runs the commands: ______________________  (Claude reads outputs live; does not run Phase 1 or the deletion)

**Approval.** I have read this plan and the runbook, and approve executing it as written, in the window above.

Approved by: ______________________   Date/time: ______________________

(Until this block is signed, nothing is stopped and nothing is deleted.)
