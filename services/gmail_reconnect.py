"""Gmail reconnect, from detection to resolution.

Google ends a mailbox's authorisation (seven days after consent while the OAuth
app is in Testing mode, or when the account holder revokes it), and from then on
only the Gmail account holder can grant it again. Nothing here replaces that
consent and nothing tries to: the link this module builds opens Google's own
consent screen, for one specific address, and the existing callback refuses any
other account.

What used to be manual was everything around that one click. A mailbox moved to
ERROR and stayed there until somebody happened to open the Mail tab -- twenty-two
were found broken at once -- and the worker's "requires administrator attention"
notice went through a module that does not exist in this service, so nobody was
told. This module:

* detects mailboxes that need a reconnect, by the same rule the Mail tab uses;
* gives each a signed, time-limited link that goes straight to Google consent;
* tells the team through the Marketing notification channel, as one digest per
  cycle rather than one message per mailbox, inside daytime hours, at a
  decreasing cadence, never more often than a minimum gap;
* stops the moment a mailbox is connected again (the callback resolves it
  immediately; the next cycle resolves anything else);
* records each episode so the Operations screen can say when it was detected,
  how often the team was told and when the next reminder is due.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable

from core.config import DATA_DIR
from core.ist_time import IST

logger = logging.getLogger(__name__)

_FILE = os.path.join(DATA_DIR, "gmail_reconnect_notices.json")
_lock = RLock()
_task: asyncio.Task | None = None

#: A link is minted fresh on every reminder, so it never needs to live long.
LINK_TTL_SECONDS = 72 * 3600
_TOKEN_PURPOSE = "gmail-reconnect-v1"

#: Reminders go out between these IST hours only. A mailbox that breaks at night
#: is reported at the start of the next window rather than at 3 a.m.
SEND_WINDOW_IST = (9, 21)

#: Never two digests closer than this, however many mailboxes break in between.
MIN_DIGEST_GAP = timedelta(hours=2)

#: First notice on detection, then twice at 12 hours, then daily until resolved.
EARLY_GAP = timedelta(hours=12)
LATE_GAP = timedelta(hours=24)
EARLY_NOTICES = 2

#: Accounts per message. A longer list is split into several messages in the same
#: cycle rather than cut off: a reminder without a link is not actionable.
MAX_LINES_IN_TEXT = 12
MAX_MESSAGES_PER_CYCLE = 3
MAX_NAMES_IN_PUSH = 6

CYCLE_INTERVAL_SEC = 600
STARTUP_DELAY_SEC = 90.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str:
    return value.astimezone(timezone.utc).isoformat() if value else ""


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- detection ------------------------------------------------------------------

def needs_reconnect(mailbox: dict[str, Any]) -> bool:
    """The Mail tab's rule (`needsReconnect` in mailboxStatus.js), unchanged.

    A closed, rejected or dropped candidate's Gmail is history: reconnecting it
    achieves nothing, so it is not reconnect work and is never reminded about.
    """
    if not isinstance(mailbox, dict) or mailbox.get("monitoring_excluded"):
        return False
    if str(mailbox.get("connection_status") or "").upper() == "ERROR":
        return True
    error = str(mailbox.get("last_error_message") or "").lower()
    return "expired" in error or "revoked" in error


# --- signed links ---------------------------------------------------------------

def _secret() -> bytes:
    return (
        os.environ.get("DASHBOARD_AUTH_SECRET") or os.environ.get("DASHBOARD_PASSWORD") or ""
    ).encode()


def make_token(mailbox_id: str, email: str, *, ttl: int = LINK_TTL_SECONDS, now: float | None = None) -> str:
    key = _secret()
    if not key:
        raise RuntimeError("Reconnect links are not configured: no signing secret")
    issued = int(now if now is not None else time.time())
    body = json.dumps(
        {"p": _TOKEN_PURPOSE, "m": str(mailbox_id), "e": str(email).strip().lower(), "x": issued + ttl},
        separators=(",", ":"),
    )
    raw = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return raw + "." + hmac.new(key, (_TOKEN_PURPOSE + raw).encode(), hashlib.sha256).hexdigest()


def read_token(token: str, *, now: float | None = None) -> tuple[str, str]:
    """(mailbox_id, email) of a valid, unexpired link, else ValueError."""
    key = _secret()
    if not key or "." not in str(token or ""):
        raise ValueError("invalid link")
    raw, sig = str(token).rsplit(".", 1)
    expected = hmac.new(key, (_TOKEN_PURPOSE + raw).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("invalid link")
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + "==="))
    except (ValueError, TypeError):
        raise ValueError("invalid link") from None
    if data.get("p") != _TOKEN_PURPOSE:
        raise ValueError("invalid link")
    if float(data.get("x") or 0) < (now if now is not None else time.time()):
        raise ValueError("link expired")
    return str(data.get("m") or ""), str(data.get("e") or "")


def public_base() -> str:
    redirect = (os.environ.get("GOOGLE_OAUTH_REDIRECT_URI") or "").strip()
    parts = urllib.parse.urlsplit(redirect)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def link_for(mailbox: dict[str, Any], *, now: float | None = None) -> str:
    base = public_base()
    if not base or not _secret():
        return ""
    token = make_token(str(mailbox.get("id")), str(mailbox.get("email_address") or ""), now=now)
    return f"{base}/api/candidate-mailboxes/reconnect/{token}"


# --- state ----------------------------------------------------------------------

def _empty() -> dict[str, Any]:
    return {"notices": {}, "last_digest_at": "", "updated_at": ""}


def load_state() -> dict[str, Any]:
    with _lock:
        try:
            with open(_FILE, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("notices"), dict):
        return _empty()
    data.setdefault("last_digest_at", "")
    return data


def save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = _iso(_now())
    with _lock:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, _FILE)


# --- planning (pure: no I/O, so every branch is testable) -----------------------

def in_send_window(now: datetime) -> bool:
    hour = now.astimezone(IST).hour
    return SEND_WINDOW_IST[0] <= hour < SEND_WINDOW_IST[1]


def _gap_after(count: int) -> timedelta:
    return EARLY_GAP if count <= EARLY_NOTICES else LATE_GAP


def plan_cycle(
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    now: datetime,
) -> list[str]:
    """Update `state` for this moment and return the mailbox ids due a reminder.

    Opens an episode for each mailbox that newly needs a reconnect, resolves the
    episode of each that no longer does, and selects what is due. Selection
    respects the send window and the minimum gap between digests; it never
    decides *what* to say.
    """
    notices = state.setdefault("notices", {})
    by_id = {str(row.get("id")): row for row in rows}
    broken = {mid: row for mid, row in by_id.items() if needs_reconnect(row)}

    for mailbox_id, row in broken.items():
        notice = notices.get(mailbox_id)
        if notice and notice.get("status") == "OPEN":
            notice["email"] = row.get("email_address") or notice.get("email") or ""
            notice["last_error_code"] = row.get("last_error_code") or notice.get("last_error_code") or ""
            continue
        episodes = int((notice or {}).get("episodes") or 0) + 1
        notices[mailbox_id] = {
            "status": "OPEN",
            "episodes": episodes,
            "email": row.get("email_address") or "",
            "candidate_id": str(row.get("candidate_id") or ""),
            "first_detected_at": _iso(now),
            "last_notified_at": "",
            "notice_count": 0,
            "next_notice_at": _iso(now),
            "last_error_code": row.get("last_error_code") or "",
            "resolved_at": "",
            "resolution": "",
        }

    for mailbox_id, notice in notices.items():
        if notice.get("status") != "OPEN" or mailbox_id in broken:
            continue
        row = by_id.get(mailbox_id)
        connected = bool(row) and str(row.get("connection_status") or "").upper() == "CONNECTED"
        notice["status"] = "RESOLVED"
        notice["resolved_at"] = _iso(now)
        notice["resolution"] = "reconnected" if connected else "no longer required"

    if not in_send_window(now):
        return []
    last = _parse(state.get("last_digest_at"))
    if last and now - last < MIN_DIGEST_GAP:
        return []
    due = [
        mailbox_id
        for mailbox_id, notice in notices.items()
        if notice.get("status") == "OPEN"
        and (_parse(notice.get("next_notice_at")) or now) <= now
    ]
    return sorted(due, key=lambda mid: notices[mid].get("first_detected_at") or "")


def record_sent(state: dict[str, Any], mailbox_ids: list[str], *, now: datetime) -> None:
    for mailbox_id in mailbox_ids:
        notice = state["notices"].get(mailbox_id)
        if not notice or notice.get("status") != "OPEN":
            continue
        notice["notice_count"] = int(notice.get("notice_count") or 0) + 1
        notice["last_notified_at"] = _iso(now)
        notice["next_notice_at"] = _iso(now + _gap_after(notice["notice_count"]))
    state["last_digest_at"] = _iso(now)


def resolve(state: dict[str, Any], mailbox_id: str, *, now: datetime, resolution: str = "reconnected") -> bool:
    notice = state.get("notices", {}).get(str(mailbox_id))
    if not notice or notice.get("status") != "OPEN":
        return False
    notice["status"] = "RESOLVED"
    notice["resolved_at"] = _iso(now)
    notice["resolution"] = resolution
    return True


def mark_resolved(mailbox_id: str, *, resolution: str = "reconnected") -> bool:
    """Stop reminding about a mailbox now (called by the OAuth callback)."""
    with _lock:
        state = load_state()
        changed = resolve(state, mailbox_id, now=_now(), resolution=resolution)
        if changed:
            save_state(state)
    return changed


# --- wording --------------------------------------------------------------------

def build_digest(
    items: list[dict[str, Any]],
    *,
    still_open_elsewhere: int,
    now: datetime,
    part: tuple[int, int] = (1, 1),
) -> dict[str, str]:
    """One notification for everything due.

    Each item: name, owner, link, reminder (how many times already told).
    The text varies with the hour so the receiving service's idempotency key
    never collides with an earlier, legitimately different reminder.
    """
    count = len(items)
    noun = "account" if count == 1 else "accounts"
    names = [item["name"] or item["email"] for item in items]
    shown = ", ".join(names[:MAX_NAMES_IN_PUSH])
    if count > MAX_NAMES_IN_PUSH:
        shown += f" and {count - MAX_NAMES_IN_PUSH} more"
    repeat = max((int(item.get("reminder") or 0) for item in items), default=0)
    title = f"Gmail reconnect needed — {count} {noun}"
    if part[1] > 1:
        title += f" ({part[0]} of {part[1]})"
    if repeat:
        title += f" (reminder {repeat + 1})"
    body = f"{shown}. Monitoring has stopped until each Gmail account is reconnected."
    if still_open_elsewhere:
        body += f" {still_open_elsewhere} more are also waiting."

    lines = ["Gmail reconnect needed — the account holder must approve Google's screen:"]
    for item in items[:MAX_LINES_IN_TEXT]:
        owner = f" ({item['owner']})" if item.get("owner") else ""
        label = item["name"] or item["email"]
        lines.append(f"• {label}{owner}: {item['link']}" if item.get("link") else f"• {label}{owner}")
    if count > MAX_LINES_IN_TEXT:
        lines.append(f"…and {count - MAX_LINES_IN_TEXT} more — open Operations → Mail → Reconnect.")
    lines.append("Links work for 3 days. Nothing is needed once an account shows Connected.")
    return {
        "title": title,
        "body": body,
        "whatsapp_text": "\n".join(lines),
        "tag": f"gmail-reconnect:{now.astimezone(IST):%Y%m%d%H}:{part[0]}",
    }


# --- orchestration ----------------------------------------------------------------

def _owner_and_name(candidate_id: str, lookup: Callable[[str], dict | None]) -> tuple[str, str]:
    try:
        row = lookup(candidate_id) or {}
    except Exception:  # noqa: BLE001 - a lookup failure must not block a reminder
        row = {}
    return str(row.get("name") or ""), str(row.get("reference") or "")


async def run_cycle(
    *,
    now: datetime | None = None,
    rows: list[dict[str, Any]] | None = None,
    lookup: Callable[[str], dict | None] | None = None,
    recheck: Callable[[str], dict | None] | None = None,
    send: Callable[..., Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One detection-and-reminder pass. Returns what it did, for logs and tests.

    The state lock is never held across an await: a coroutine on the same thread
    would walk straight through a thread lock, so holding it would protect
    nothing and only look safe. Instead the plan is saved before anything is
    sent, every mailbox due is re-read immediately before sending (a reconnect
    that landed since the snapshot must not be reminded about), and afterwards
    only the notices that are still open are advanced -- a mailbox resolved by
    the OAuth callback in between stays resolved.
    """
    now = now or _now()
    if rows is None:
        from core import recruitment_mail_store as store

        rows = await asyncio.to_thread(store.mailbox_health_rows)
    if lookup is None:
        from features import candidate_store

        lookup = candidate_store.get_candidate
    if recheck is None:
        from core import recruitment_mail_store as store

        recheck = store.mailbox_by_id
    by_id = {str(row.get("id")): row for row in rows}

    with _lock:
        state = load_state()
        due = plan_cycle(state, rows, now=now)
        if not dry_run:
            save_state(state)
    open_total = sum(1 for n in state["notices"].values() if n.get("status") == "OPEN")

    confirmed: list[str] = []
    for mailbox_id in due:
        fresh = await asyncio.to_thread(recheck, mailbox_id)
        still_broken = bool(fresh) and (
            str(fresh.get("connection_status") or "").upper() == "ERROR"
            or needs_reconnect({**by_id.get(mailbox_id, {}), **fresh})
        )
        if still_broken:
            confirmed.append(mailbox_id)
        elif not dry_run:
            mark_resolved(mailbox_id)
            open_total -= 1

    items: list[dict[str, Any]] = []
    for mailbox_id in confirmed:
        row = by_id.get(mailbox_id) or {}
        name, owner = await asyncio.to_thread(_owner_and_name, str(row.get("candidate_id") or ""), lookup)
        items.append({
            "mailbox_id": mailbox_id, "name": name, "owner": owner,
            "email": row.get("email_address") or "", "link": link_for(row),
            "reminder": int(state["notices"][mailbox_id].get("notice_count") or 0),
        })
    result: dict[str, Any] = {"open": open_total, "due": len(items), "sent": False, "dry_run": dry_run}
    if not items:
        return result
    size = MAX_LINES_IN_TEXT
    chunks = [items[i:i + size] for i in range(0, len(items), size)][:MAX_MESSAGES_PER_CYCLE]
    digests = [
        build_digest(
            chunk,
            still_open_elsewhere=max(0, open_total - len(items)) if index == 1 else 0,
            now=now,
            part=(index, len(chunks)),
        )
        for index, chunk in enumerate(chunks, 1)
    ]
    result["digest"] = digests[0]
    result["messages"] = len(digests)
    if dry_run:
        return result
    if send is None:
        from services.messaging_client import send_notification as send
    delivered: list[str] = []
    for chunk, digest in zip(chunks, digests):
        try:
            await send(title=digest["title"], body=digest["body"], tag=digest["tag"],
                       whatsapp_text=digest["whatsapp_text"])
        except Exception:  # noqa: BLE001 - unsent accounts stay due, so the next cycle retries
            logger.exception("Gmail reconnect digest could not be queued; will retry")
            break
        delivered.extend(item["mailbox_id"] for item in chunk)
    if delivered:
        with _lock:
            latest = load_state()
            record_sent(latest, delivered, now=now)
            save_state(latest)
    result["sent"] = bool(delivered)
    result["accounts_notified"] = len(delivered)
    return result


def status_payload(rows: list[dict[str, Any]], lookup: Callable[[str], dict | None]) -> dict[str, Any]:
    """What the Operations screen shows: each open mailbox's reminder history."""
    state = load_state()
    by_id = {str(row.get("id")): row for row in rows}
    now = _now()
    notices = []
    for mailbox_id, notice in state["notices"].items():
        row = by_id.get(mailbox_id)
        if notice.get("status") != "OPEN" or not row or not needs_reconnect(row):
            continue
        name, owner = _owner_and_name(str(row.get("candidate_id") or ""), lookup)
        notices.append({
            "mailbox_id": mailbox_id,
            "email": row.get("email_address") or "",
            "candidate_name": name,
            "owner": owner,
            "detected_at": notice.get("first_detected_at") or "",
            "last_notified_at": notice.get("last_notified_at") or "",
            "notice_count": int(notice.get("notice_count") or 0),
            "next_notice_at": notice.get("next_notice_at") or "",
            "link": link_for(row),
        })
    notices.sort(key=lambda n: n["detected_at"])
    week = now - timedelta(days=7)
    resolved = [
        n for n in state["notices"].values()
        if n.get("status") == "RESOLVED" and (_parse(n.get("resolved_at")) or week) >= week
    ]
    return {
        "enabled": not disabled(),
        "open_count": len(notices),
        "resolved_7d": len(resolved),
        "last_digest_at": state.get("last_digest_at") or "",
        "send_window_ist": list(SEND_WINDOW_IST),
        "notices": notices,
    }


# --- the loop ---------------------------------------------------------------------

def disabled() -> bool:
    return os.environ.get("GMAIL_RECONNECT_REMINDERS_ENABLED", "1").strip().lower() in ("0", "false", "no")


async def gmail_reconnect_loop() -> None:
    await asyncio.sleep(STARTUP_DELAY_SEC)
    while True:
        try:
            result = await run_cycle()
            if result.get("sent"):
                logger.info("Gmail reconnect digest queued: due=%d open=%d", result["due"], result["open"])
        except Exception:  # noqa: BLE001 - never let one bad pass end the loop
            logger.exception("Gmail reconnect cycle failed")
        await asyncio.sleep(CYCLE_INTERVAL_SEC)


def start_gmail_reconnect_loop() -> None:
    global _task
    if disabled():
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(gmail_reconnect_loop(), name="gmail-reconnect")


async def stop_gmail_reconnect_loop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
