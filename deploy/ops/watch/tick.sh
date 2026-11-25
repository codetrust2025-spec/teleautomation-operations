#!/usr/bin/env bash
# Dispatches the off-server watchdog in codetrust2025-spec/teleautomation-ops-watch.
# Run every 30 minutes by the scheduled task "TeleAutomation Ops Watch Clock".
set -u
export PATH="$PATH:/c/Users/codet/AppData/Local/Microsoft/WinGet/Packages/GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe/bin"
export GH_TOKEN=$(printf "protocol=https\nhost=github.com\n\n" | git credential fill 2>/dev/null | sed -n 's/^password=//p')
log=/c/Users/codet/actions-runner-ops-watch/tick.log
if gh workflow run watchdog.yml -R codetrust2025-spec/teleautomation-ops-watch --ref main >/dev/null 2>&1; then r=ok; else r=FAILED; fi
echo "$(date -u +%FT%TZ) dispatch $r" >> "$log"
tail -n 500 "$log" > "$log.tmp" && mv "$log.tmp" "$log"
