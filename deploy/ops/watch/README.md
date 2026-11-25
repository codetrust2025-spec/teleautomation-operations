# Off-server watchdog

Source of truth for the private `teleautomation-ops-watch` repository (copy
`watchdog.py` and `.github/workflows/watchdog.yml` there unchanged).

Every 30 minutes it probes the public `/health`, reads the production monitor's
`status.json` over a command-restricted SSH key (`ta_offhost.py watch`: it can
run `status` and nothing else), and keeps one `[ops-alert]` issue per alert.
The issues are opened by `github-actions[bot]` and @-mention the owner, so
GitHub emails the owner on open, at most once a day while failing, and on close.

## Where it runs (since 25 Nov 2026)

GitHub-hosted minutes for private repositories are refused for billing, so the
repository was deleted on 23 Nov 2026 and re-created on 25 Nov 2026 with:

- self-hosted runner `rtx4060-ops-watch` (label `ops-watch`) on the RTX 4060
  laptop, `C:\Users\codet\actions-runner-ops-watch`, started at logon by the
  scheduled task `TeleAutomation Ops Watch Runner (rtx4060)`;
- `tick.sh`, run every 30 minutes by the scheduled task
  `TeleAutomation Ops Watch Clock`, dispatches the workflow (log: `tick.log`
  beside the runner). The workflow's own 3-hourly schedule is the fallback.
- secrets `WATCH_SSH_KEY`, `WATCH_KNOWN_HOSTS`, `WATCH_HOST`. The key was
  replaced on 25 Nov 2026 (authorized_keys backup on the host:
  `/root/.ssh/authorized_keys.bak-20261125-opswatch`).

It only runs while the laptop is on and online. The old GitHub-hosted clock
(`.github/workflows/watchdog-clock.yml` here) was removed in PR #552.
