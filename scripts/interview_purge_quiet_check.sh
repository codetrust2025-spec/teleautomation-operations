#!/bin/bash
# READ-ONLY. Is anyone changing data right now? Counts the data-changing requests (POST/PATCH/DELETE) served in the last 30
# minutes, from TWO sources: the nginx access logs (every request a person makes) and the application's own request log (which
# also records anything that reached it without going through nginx). Prints counts and path classes only: no addresses,
# user agents or ids.
#
#   ssh ... 'bash -s' < interview_purge_quiet_check.sh
#
# What it covers (checked against the deployed code): every route that can write the Data Room credentials files needs a
# logged-in session and so comes through nginx; service tokens only reach /internal/, /ai/smart-reply/ and POST /inbox/*/sync/*;
# no background loop or worker touches the store. Password changes and resets (/auth/change-password, /auth/reset-password) DO
# write the file, so they block. Only login, logout and the admin re-check do not.
#
# Verdict: GO when no successful data-changing request other than login/logout/verify-admin happened in the window.
# It is advice about people, not the safeguard: the safeguard is stopping the application and the tool's own checks.
#
# Settings for tests only (defaults are production's): QUIET_NGINX_LOGS (colon-separated), QUIET_APP_LOG_CMD, QUIET_NOW.
python3 - <<'PY'
import collections, datetime, os, re, subprocess, sys

WINDOW_MINUTES = 30
NON_BLOCKING_PATHS = {"/auth/login", "/auth/logout", "/auth/verify-admin"}   # change-password / reset-password are NOT here: they write
now_text = os.environ.get("QUIET_NOW")
now = datetime.datetime.fromisoformat(now_text) if now_text else datetime.datetime.now(datetime.timezone.utc)
since = now - datetime.timedelta(minutes=WINDOW_MINUTES)
logs = (os.environ.get("QUIET_NGINX_LOGS") or "/var/log/nginx/access.log:/var/log/nginx/access.log.1").split(":")
app_cmd = os.environ.get("QUIET_APP_LOG_CMD") or f"docker logs --since {WINDOW_MINUTES}m teleautomation-production-operations-api-1"

nginx_line = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3}) ')
app_line = re.compile(r'"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (\S+) HTTP/[\d.]+" (\d{3})')
internal = re.compile(r'^(127\.|::1|172\.(1[6-9]|2\d|3[01])\.)')


def classify(source, method, target, status):
    """None for a request that does not matter, else a label."""
    if method not in ("POST", "PATCH", "DELETE", "PUT"):
        return None
    path = re.sub(r"[0-9a-f]{8,}.*", "<id>", target.split("?")[0])
    if status >= 400:
        return ("refused (does not block)", False)
    if path in NON_BLOCKING_PATHS:
        return ("login/logout (does not block)", False)
    return (f"WRITE {method} {path} [{source}]", True)


kinds = collections.Counter()
blocking = collections.Counter()
total = 0
newest = None
for path in logs:
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        continue
    with handle:
        handle.seek(0, 2)
        offset = max(0, handle.tell() - 12_000_000)
        handle.seek(offset)
        if offset:
            handle.readline()   # skip the partial line we landed in the middle of
        for raw in handle:
            m = nginx_line.match(raw)
            if not m or internal.match(m.group(1)):
                continue
            try:
                when = datetime.datetime.strptime(m.group(2), "%d/%b/%Y:%H:%M:%S %z")
            except ValueError:
                continue
            newest = max(newest, when) if newest else when
            if when < since:
                continue
            total += 1
            label = classify("nginx", m.group(3), m.group(4), int(m.group(5)))
            if label:
                kinds[label[0]] += 1
                if label[1]:
                    blocking[label[0]] += 1

# The application's own log: records requests that never touched nginx. Read-only.
app_total = 0
app_note = ""
try:
    done = subprocess.run(app_cmd, shell=True, capture_output=True, text=True, timeout=60)
    for raw in (done.stdout + done.stderr).splitlines():
        m = app_line.search(raw)
        if not m:
            continue
        app_total += 1
        label = classify("application log", m.group(1), m.group(2), int(m.group(3)))
        if label:
            kinds[label[0]] += 1
            if label[1]:
                blocking[label[0]] += 1
    if done.returncode != 0:
        app_note = "the application log could not be read (is the container running?); only nginx was checked"
except (OSError, subprocess.SubprocessError):
    app_note = "the application log could not be read; only nginx was checked"

print(f"now (UTC): {now:%Y-%m-%d %H:%M:%S}; newest nginx line: {newest:%H:%M:%S} UTC" if newest else "no nginx log lines found")
print(f"last {WINDOW_MINUTES} minutes: {total} external requests in nginx; {app_total} requests in the application's own log")
if app_note:
    print(f"  note: {app_note}")
for kind, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
    print(f"  {count:4d}  {kind}")
if newest is None or (now - newest).total_seconds() > 15 * 60:
    print("VERDICT: NO GO. The logs are not current, so quiet cannot be judged.")
    sys.exit(1)
if blocking:
    print(f"VERDICT: NO GO. {sum(blocking.values())} data-changing request(s) in the last {WINDOW_MINUTES} minutes: someone is working. Wait and run again.")
    sys.exit(1)
print(f"VERDICT: GO. No data-changing request in the last {WINDOW_MINUTES} minutes.")
PY
