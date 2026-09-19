#!/usr/bin/env bash
# Decide which CI lane a change takes. Reads changed paths on stdin, one per
# line, and prints `frontend` or `full`.
#
# This is the safety-critical half of the change-aware pipeline, so it lives in
# a file that tests/test_ci_lane_selection.py can drive directly rather than
# inside workflow YAML where nothing could reach it.
#
# The rule is an allowlist, so it is fail-safe by construction: a path only
# takes the fast lane if it matches ALLOW and does not match DENY. Anything
# unrecognised -- a new extension, a renamed directory, an empty list -- is
# `full`. Adding a backend directory later needs no edit here; it is already
# covered by not being on the list.
set -uo pipefail

# Dashboard source only, in the three extensions the suite and the build know.
# Everything else forces the full lane: package.json and package-lock.json
# (an `npm ci` in the image would resolve differently), vite.config.js,
# Dockerfile, requirements.txt, core/, api/, features/, services/, workers/,
# tests/, migrations, compose files and .github/.
ALLOW='^dashboard/src/.*[.](js|jsx|css)$'

# Frontend code that nevertheless belongs to a risk domain the full pipeline is
# required to cover. These are UI files, but realtime mail delivery and
# attendance are exactly the areas where a silent regression is expensive and
# not visible in a screenshot.
DENY='^dashboard/src/(notifications|attendance)/'

# Frontend logic in the domains every release must cross-check, whatever layer
# the change sits in: booking and payments, Gmail automation, and sign-in and
# credentials. Matched by folder and by name, so a new file in one of these
# domains is caught without an edit here. Only .js/.jsx: a stylesheet cannot
# change what these screens do, and the dashboard job still tests and builds
# every change on either lane.
RISK_DIRS='^dashboard/src/(pages|dailyOps|candidates)/'
RISK_NAMES='(booking|slot|payment|payout|paid|earning|expenditure|referrer|upi|mail|reconnect|ocr|ollama|ainode|aianalysis|expiry|candidate|interview|roster|auth|login|password|credential|dataroom|vault|offerletter|ownership|config)'

lane=frontend
seen=0

# `|| [ -n "$path" ]` keeps the last line when the input has no trailing
# newline. Without it `read` returns non-zero on that line and drops it, so a
# list whose final entry is a backend file would classify as frontend -- the
# fast lane granted by losing the one path that disqualifies it.
while IFS= read -r path || [ -n "$path" ]; do
  # Trim whitespace/CR so a list pasted from anywhere still classifies.
  path="$(printf '%s' "$path" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  [ -z "$path" ] && continue
  seen=1
  if ! printf '%s\n' "$path" | grep -Eq "$ALLOW"; then
    echo "full lane: $path is outside the dashboard-source allowlist" >&2
    lane=full
    break
  fi
  if printf '%s\n' "$path" | grep -Eq "$DENY"; then
    echo "full lane: $path is realtime/attendance code" >&2
    lane=full
    break
  fi
  if printf '%s\n' "$path" | grep -Eq '[.]jsx?$' \
     && { printf '%s\n' "$path" | grep -Eq "$RISK_DIRS" \
          || printf '%s\n' "${path##*/}" | grep -Eiq "$RISK_NAMES"; }; then
    echo "full lane: $path is booking, payment, mail or sign-in logic" >&2
    lane=full
    break
  fi
done

# An empty list means the caller could not determine what changed. That is not
# evidence of a small change, so it takes the full lane.
if [ "$seen" = 0 ]; then
  echo "full lane: no changed paths were supplied" >&2
  lane=full
fi

printf '%s\n' "$lane"
