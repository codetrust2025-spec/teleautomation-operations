#!/usr/bin/env bash
# Install or update the backup and monitoring automation on the production host.
# Idempotent: safe to run again after every change to deploy/ops.
#
#   sudo bash deploy/ops/install.sh [--watch-key FILE.pub] [--rtx-key FILE.pub]
#
# * restic from Ubuntu's own repository
# * /etc/teleautomation/restic.password: generated once, never overwritten
#   (copy it into a password manager: without it no backup can be read)
# * the restic repository at /var/backups/teleautomation/restic
# * scripts to /usr/local/sbin and /usr/local/lib/teleautomation
# * systemd timers: backup nightly 02:00 IST, monitor every 5 minutes
# * off-server public keys, each pinned to ta_offhost.py with `restrict`
#   (no shell, no forwarding, no file transfer): --watch-key for the GitHub
#   watchdog, --rtx-key for the RTX 4060 laptop
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LIB=/usr/local/lib/teleautomation
ROOT=/var/backups/teleautomation
PASS=/etc/teleautomation/restic.password
AUTH=/root/.ssh/authorized_keys
WATCH_KEY=""; RTX_KEY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --watch-key) WATCH_KEY="$2"; shift 2 ;;
    --rtx-key) RTX_KEY="$2"; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

command -v restic >/dev/null || { apt-get update -qq && apt-get install -y -qq restic >/dev/null; }

install -d -m 0700 /etc/teleautomation "$ROOT" /var/lib/teleautomation-monitor
install -d -m 0755 "$LIB"
if [ ! -s "$PASS" ]; then
  ( umask 077; openssl rand -base64 48 | tr -d '\n' > "$PASS" )
  echo "generated $PASS (copy it into the password manager)"
fi
chmod 0600 "$PASS"

install -m 0755 "$HERE/teleautomation-backup" /usr/local/sbin/teleautomation-backup
install -m 0755 "$HERE/teleautomation-monitor" /usr/local/sbin/teleautomation-monitor
install -m 0755 "$HERE/ta_backup_verify.py" "$LIB/ta_backup_verify.py"
install -m 0755 "$HERE/ta_offhost.py" "$LIB/ta_offhost.py"
for unit in teleautomation-backup.service teleautomation-backup.timer teleautomation-monitor.service teleautomation-monitor.timer; do
  install -m 0644 "$HERE/systemd/$unit" "/etc/systemd/system/$unit"
done

if [ ! -f "$ROOT/restic/config" ]; then
  RESTIC_PASSWORD_FILE="$PASS" restic init --repo "$ROOT/restic" >/dev/null
  echo "initialised restic repository $ROOT/restic"
fi

pin_key() {  # pin_key ROLE PUBKEY_FILE
  local role="$1" key tag="ta-offhost-$1"
  key="$(awk '{print $1" "$2}' "$2")"
  case "$key" in ssh-ed25519\ *) ;; *) echo "not an ed25519 public key: $2" >&2; exit 2 ;; esac
  install -d -m 0700 /root/.ssh; touch "$AUTH"; chmod 0600 "$AUTH"
  grep -v " $tag\$" "$AUTH" > "$AUTH.tmp" || true
  printf 'restrict,command="%s %s" %s %s\n' "$LIB/ta_offhost.py" "$role" "$key" "$tag" >> "$AUTH.tmp"
  mv "$AUTH.tmp" "$AUTH"
  echo "pinned $role key to ta_offhost.py"
}
[ -n "$WATCH_KEY" ] && pin_key watch "$WATCH_KEY"
[ -n "$RTX_KEY" ] && pin_key rtx "$RTX_KEY"

systemctl daemon-reload
systemctl enable --now teleautomation-monitor.timer teleautomation-backup.timer >/dev/null
echo "timers:"; systemctl list-timers --no-pager 'teleautomation-*' | sed -n '1,4p'
