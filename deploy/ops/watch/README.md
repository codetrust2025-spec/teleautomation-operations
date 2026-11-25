# Off-server watchdog (retired)

`watchdog.py` and `.github/workflows/watchdog.yml` ran in the private
`teleautomation-ops-watch` repository, started every 30 minutes by a clock
workflow in this repository (`.github/workflows/watchdog-clock.yml`).

On 23 Nov 2026 `teleautomation-ops-watch` was deleted: GitHub-hosted minutes
for private repositories are refused for billing. The clock then failed on
every run ("Repository not found"), so it was removed on 25 Nov 2026.

What still runs: the on-server monitor (`/var/lib/teleautomation-monitor/status.json`).
What no longer runs: alerts from outside the server (host unreachable, public
site down, monitor stale) and the `[ops-alert]` issue emails.

These files are kept as the source to restore it from. The watchdog needs a
runner that is not billed (a public repository or a self-hosted runner), a
command-restricted SSH key on the server, and somewhere private for its issues.
