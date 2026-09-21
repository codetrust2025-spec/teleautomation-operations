# Working in this repository

## The default end goal is live production

Every task here — bug fix, feature, UI change, configuration change — is
finished when it is **running and verified in production**, not when the code
is written, not when CI is green, and not when a PR is merged.

Carry on through the whole pipeline without being asked and without pausing for
approval between stages:

```
change → tests → Operations PR + pin PR → both CIs → merge → pin merge → sync → build → deploy → verify
```

Do not stop at code, tests, PR, CI, merge, pin, or build. Those are steps, not
destinations. A reply that ends at "the PR is open" or "CI is running" is an
unfinished task, and so is one that ends at a green deploy without checking the
requested behaviour on the live site.

**Only give a final answer when all of these hold:**

- the change is live in production
- `/version` reports the expected commit
- every container in the compose project is healthy
- the behaviour that was actually requested is verified on the live site

### When not to deploy

Stop before deploying only if the request says **"do not deploy"**, **"local
only"**, or **"PR only"**.

Otherwise pause mid-pipeline only for:

- **credentials or manual login** — never enter passwords, MFA codes, or
  secrets; ask the person to do it
- **a destructive or genuinely high-risk action** — deleting production data,
  rewriting history, anything not recoverable by redeploying
- **an unrecoverable failure** — report what broke, with the evidence

Being unsure whether a change is worth deploying is not one of these. Ship it.

## How this repository reaches production

Operations is deployed **from the Marketing repository**, which owns
`docker-compose.production.yml` and the release anchor pinning the Operations
commit. Check both out side by side, push your branch, then:

```bash
cd ../teleautomation-messaging
# from a branch: opens the Operations PR, merges it, then ships it
OPERATIONS_BRANCH=fix/thing KVM1_SSH=user@host bash scripts/fix_and_deploy.sh

# or from a commit already on Operations main
OPERATIONS_SHA=<40-hex> KVM1_SSH=user@host bash scripts/fix_and_deploy.sh
```

Stages, each idempotent and recorded so an interrupted run resumes rather than
repeating work or double-merging:

```
ops_pr pin ops_ci pin_ci ops_merge preflight pin_merge sync build deploy verify
```

`--dry-run` prints the plan and changes nothing. `--restart` discards recorded
progress.

Given a branch, the script opens the Operations PR itself, with a description
assembled from that branch's commits, and opens the pin PR straight away, so
both repositories' CI run at the same time. Nothing merges until both are
green; then Operations main is fast-forwarded to exactly the pinned commit --
the PR head CI tested -- and the deploy follows. There is no manual gap. A
branch pushed to after it was pinned stops the run, and if main moved in the
meantime the PR is merged normally and the pin follows the merge commit.

Each stage prints how long it took, and the run its total.

`deploy` releases the image CI built from exactly that commit, by digest,
through the Operations `deploy` workflow and the host's `teleautomation-deploy`,
which records the release, verifies it, and restores the previous one if it
does not verify. `DEPLOY_VIA=host` is the break-glass path: build on the host
and hand the image to `teleautomation-deploy deploy-local`. Never start the
container any other way -- a raw `docker compose up` leaves the host's release
record naming an older release, and rollback trusts that record.

It stops on a **merge conflict** rather than guessing: resolving one means
choosing which side of the change survives, and that is not a decision to
automate.

Re-running is safe. Every mutating stage asks the remote whether its effect is
already there, so a restart never opens a second PR, repeats a merge, or
redeploys work already done.

Neither repository holds environment specifics: hostnames and paths come from
the environment (`KVM1_SSH`, `KVM1_SSH_KEY`, `PROD_ENV_FILE`), never from
committed files. Keep it that way.

## Things that have gone wrong here

- **A test can pass while the path it describes never runs.** Payment tests
  called the regex helpers directly and stayed green through a production
  outage in the gated path above them. Drive the real entry point, and if a
  test environment cannot reach the real behaviour, say so rather than letting
  a green run imply more than it demonstrated.
- **The env file decides, not the routing default.** `model_for(...)` defaults
  are only defaults; a variable set in production overrides them for every
  workload sharing it. Read the resolved value inside the running container.
- **Verify against the code production actually calls.** A probe once proved a
  payment verified using a function nothing outside tests calls, while
  production refused it for a different reason entirely.

## Verifying

A green pipeline proves the release is running. It does not prove the change
does what was asked — check that in the browser, and say plainly which of the
two you actually confirmed.

## How much verification a change needs

Match the checks to the risk. The tier is set by the riskiest file touched.

| Tier | What it covers | Locally | Production check |
|---|---|---|---|
| UI only | stylesheets, markup, copy | targeted UI tests; dashboard suite once; layout harness only if sizing, grid or breakpoints change | `/version`, health, the live bundle carries the change |
| Frontend logic | behaviour in `dashboard/src` | targeted component tests, with a fails-on-old-code proof where it adds something; dashboard suite once | read-only check if the change reads new API fields |
| Backend | `core/`, `services/`, `features/`, `workers/` outside the high-risk domains | tests for the changed modules and what imports them; CI runs the full Python and Postgres suites | read-only validation where needed: new SQL against production data, the deployed code in the container |
| High risk | booking, payments, Gmail automation, database and migrations, security, deployment logic | the full workflow: replay real stored data read-only, one full Python run, Postgres in CI, a regression test proven to fail on the old code | through the deployed code, read-only |

Rules for every tier:

- Never run an unchanged full suite twice. After a targeted fix, re-run only the
  files that failed; CI runs everything again anyway.
- Never run the full dashboard and Python suites at the same time on one
  machine: contention stretched a 25s dashboard run to 3 minutes. Put a long
  suite in the background and do non-CPU work meanwhile.
- Batch reads and independent tool calls. Reuse the map below, the layout
  harness and the deploy script instead of rediscovering them.
- Stop a background wait or monitor as soon as the event it waited for arrives.
- Keep final reports short: what changed, what was verified and how.

CI applies the same idea on its own. `scripts/ci_lane.sh` sends a change down the
frontend lane only when every file is `dashboard/src` JS, JSX or CSS outside
notifications and attendance, and no JS or JSX file belongs to booking,
payments, mail or sign-in. Marketing skips its `dual-service` check for a pin
only when both the rule in production and the rule being pinned agree, and
never when the range edits the rule.

## Where things are

- Backend: `core/` (API routes, stores), `services/` (booking, mail agent,
  calendar parsing), `features/` (candidate store), `workers/`.
- Dashboard: `dashboard/src`. Global stylesheets, in the order `main.jsx` loads
  them: `index.css`, `businessShell.css` (the shell, with its 232px sidebar),
  `dailyOps.css`, `recruitmentMail.css` (Mail Alerts, Candidate Gmail). The
  public booking form is `pages/SubmitSlotPage.jsx`.
- Tests: `tests/test_*.py` with pytest. `tests/test_*_pg.py` need Postgres and
  run only in CI; `tests/test_booking_result_filter_pg.py` shows building tables
  from `core/migrations` in a throwaway schema. Dashboard tests sit beside their
  components (`*.test.jsx`, vitest); jsdom performs no layout.
- Layout: capture what a test renders with `captureLayout()` from
  `dashboard/src/test/captureLayout.js`, then `python scripts/layout_harness.py
  <capture>` shows it in the real shell at every width, and one
  `await window.layoutReport()` measures overflow and clipped text at all of them.
- Migrations: `core/migrations/NNN_*.sql`; each table's columns can be spread
  over several (`mail_monitoring_notifications`: 007, 008, 018).
- Production: the Operations container serves the code at `/app`. Read-only
  checks may run the deployed code there; nothing run there may write.
- On Windows: a fresh venv needs `tzdata`, and
  `tests/test_handler_login_persistence.py::test_the_store_is_not_readable_by_anything_else_on_the_volume`
  fails because Windows has no POSIX file modes. It passes on Linux CI.
