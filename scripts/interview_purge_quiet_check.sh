#!/bin/bash
# READ-ONLY. Is anyone changing data right now? Counts the write requests (POST/PATCH/DELETE) the application served in the
# last 30 minutes, from the nginx access logs. Prints counts and path classes only: no addresses, user agents or ids.
#
#   ssh ... 'bash -s' < interview_purge_quiet_check.sh
#
# Verdict: GO when no successful write other than a login/logout happened in the last 30 minutes. A login does not block (it
# is retried by the browser and changes nothing). Anything else means a person is working: wait and run it again.
python3 - <<'PY'
import collections, datetime, glob, re, sys

WINDOW_MINUTES = 30
now = datetime.datetime.now(datetime.timezone.utc)
since = now - datetime.timedelta(minutes=WINDOW_MINUTES)
line = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3}) ')
internal = re.compile(r'^(127\.|::1|172\.(1[6-9]|2\d|3[01])\.)')

files = ["/var/log/nginx/access.log", "/var/log/nginx/access.log.1"]
total = writes = 0
kinds = collections.Counter()
newest = None
for path in files:
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        continue
    with handle:
        # the window is recent: read only the tail of each file
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 12_000_000))
        handle.readline()
        for raw in handle:
            m = line.match(raw)
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
            method, target, status = m.group(3), m.group(4), int(m.group(5))
            if method in ("POST", "PATCH", "DELETE"):
                writes += 1
                target = re.sub(r"[0-9a-f]{8,}.*", "<id>", target.split("?")[0])
                if target.startswith("/auth/") or status >= 400:
                    kinds["login/logout or refused (does not block)"] += 1
                else:
                    kinds["WRITE " + method + " " + target] += 1

blocking = {k: v for k, v in kinds.items() if k.startswith("WRITE ")}
print(f"now (UTC): {now:%Y-%m-%d %H:%M:%S}; newest log line: {newest:%H:%M:%S} UTC" if newest else "no log lines found")
print(f"last {WINDOW_MINUTES} minutes: {total} external requests, {writes} write requests")
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
