"""One candidate's history, newest first, read from the records that already exist.

Nothing here is stored or written. Each entry comes from the record that owns
it -- the booking rows, the Mail Alerts, the recruitment events read from the
candidate's Gmail -- and one happening is shown once even when two records
describe it: an alert and the recruitment event it was raised for are one
entry, and a payment screenshot copied onto every slot of a profile is one.

Rows are the person's by identity link (phone, email, explicit link), never by
a shared name, the same rule the booking gates use.

What no record keeps is not invented: a manual edit in Daily Ops leaves no
trace beyond the fields it changed, so edits other than bookings, moves and
attendance marks cannot appear here.
"""

from __future__ import annotations

from typing import Any, Callable

from features import candidate_store

# How a booking reached the roster, in the words the team uses.
BOOKED_FROM_GMAIL = "Gmail"
BOOKED_FROM_FORM = "Booking form"
BOOKED_FROM_DAILY_OPS = "Daily Ops"

ATTENDANCE_TITLES = {
    "attended": "Marked attended",
    "not_attended": "Marked not attended",
    "cancelled": "Interview cancelled",
    "rescheduled": "Marked rescheduled",
    candidate_store.RELEASED_FOR_RESCHEDULE_STATUS: "Released to wait for a new slot",
    "re_service": "Re-Service granted",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _words(value: Any) -> str:
    """INTERVIEW_SCHEDULED -> Interview scheduled."""
    text = _text(value).replace("_", " ").lower()
    return text[:1].upper() + text[1:]


def _entry(at: Any, kind: str, title: str, detail: str, source: str, **refs: Any) -> dict | None:
    when = _text(at)
    if not when:
        return None
    return {
        "at": when, "kind": kind, "title": title, "detail": detail, "source": source,
        "booking_id": _text(refs.get("booking_id")) or None,
        "mail_id": _text(refs.get("mail_id")) or None,
        "event_id": _text(refs.get("event_id")) or None,
    }


def _schedule(slot: dict) -> str:
    day = _text(slot.get("date"))[:10]
    start = candidate_store.normalise_interview_clock(_text(slot.get("time")))
    end = candidate_store.normalise_interview_clock(_text(slot.get("time_end")))
    when = f"{day} {start}" + (f"-{end}" if end else "")
    extras = [_text(slot.get(key)) for key in ("interview_company", "interview_round") if _text(slot.get(key))]
    return " · ".join([when.strip(), *extras])


def booked_from(row: dict) -> str:
    if candidate_store.interview_booking_source(row) == "ai_auto_booked":
        return BOOKED_FROM_GMAIL
    screenshots = row.get("slot_screenshot_proofs") or []
    if _text(row.get("booking_idempotency_key")) or any(
        "submit-slot form" in _text(proof.get("note")) for proof in screenshots if isinstance(proof, dict)
    ):
        return BOOKED_FROM_FORM
    return BOOKED_FROM_DAILY_OPS


def _booking_entries(rows: list[dict]) -> list[dict]:
    entries: list[dict | None] = []
    payments_seen: set[str] = set()
    closures_seen: set[str] = set()
    first_seen = min((_text(row.get("created_at")) for row in rows if _text(row.get("created_at"))), default="")
    if first_seen:
        entries.append(_entry(first_seen, "created", "Added to Operations", "", BOOKED_FROM_DAILY_OPS))
    for row in rows:
        booking_id = _text(row.get("id"))
        if candidate_store.candidate_has_confirmed_slot(row):
            assessment = (_text(row.get("booking_type")) or "Interview") == "Assessment"
            entries.append(_entry(
                row.get("slot_confirmed_at") or row.get("created_at"),
                "assessment" if assessment else "booking",
                "Assessment booked" if assessment else "Interview booked",
                _schedule(row), booked_from(row), booking_id=booking_id,
                mail_id=row.get("interview_source_message_id"),
            ))
        for sitting in row.get("interview_previous_sittings") or []:
            if isinstance(sitting, dict):
                entries.append(_entry(
                    sitting.get("moved_at"), "rescheduled", "Interview moved",
                    f"From {_schedule(sitting)} to {_schedule(row)}", "Booking", booking_id=booking_id,
                ))
        if _text(row.get("superseded_at")):
            replacement = _text(row.get("superseded_by_booking_id"))
            entries.append(_entry(
                row.get("superseded_at"), "superseded", "Replaced by a newer booking",
                _schedule(row) + (f" -> booking {replacement}" if replacement else ""),
                BOOKED_FROM_GMAIL, booking_id=booking_id,
            ))
        status = candidate_store.row_interview_attendance_status(row)
        if status in ATTENDANCE_TITLES and _text(row.get("interview_attended_at")):
            by = _text(row.get("interview_attended_by"))
            remark = _text(row.get("interview_attendance_remark"))
            detail = " · ".join(part for part in (_schedule(row), remark, f"by {by}" if by else "") if part)
            kind = "cancelled" if status == "cancelled" else (
                "released" if status == candidate_store.RELEASED_FOR_RESCHEDULE_STATUS else "attendance")
            entries.append(_entry(row.get("interview_attended_at"), kind, ATTENDANCE_TITLES[status],
                                  detail, BOOKED_FROM_DAILY_OPS, booking_id=booking_id))
        for proof in row.get("payment_proofs") or []:
            proof_id = _text(proof.get("id")) if isinstance(proof, dict) else ""
            if not proof_id or proof_id in payments_seen:
                continue
            payments_seen.add(proof_id)
            entries.append(_entry(proof.get("uploaded_at"), "payment", "Payment screenshot added", "", "Payments"))
        closed = _text(row.get("closure_date"))
        if _text(row.get("closure_recorded_at")) and closed not in closures_seen:
            closures_seen.add(closed)
            entries.append(_entry(row.get("closure_recorded_at"), "closure", "Profile closed",
                                  closed, BOOKED_FROM_DAILY_OPS))
    return [entry for entry in entries if entry]


def _alert_title(alert: dict) -> str:
    block = alert.get("booking_block") or {}
    if isinstance(block, dict) and _text(block.get("title")):
        return _text(block.get("title"))
    return _text(alert.get("candidate_status")) or _words(alert.get("classification")) or "Mail alert"


def _mail_entries(alerts: list[dict], events: list[dict]) -> list[dict]:
    entries: list[dict | None] = []
    alerted_events = {_text(alert.get("ai_recruitment_event_id")) for alert in alerts}
    events_by_id = {_text(event.get("id")): event for event in events}
    for alert in alerts:
        block = alert.get("booking_block") or {}
        reason = _text(block.get("reason")) if isinstance(block, dict) else ""
        subject = _text(alert.get("email_subject"))
        company = _text(alert.get("company_name"))
        entries.append(_entry(
            alert.get("email_received_at") or alert.get("created_at"), "alert", _alert_title(alert),
            " · ".join(part for part in (reason, company, subject) if part), BOOKED_FROM_GMAIL,
            booking_id=alert.get("booking_id"), mail_id=alert.get("gmail_message_id"),
            event_id=_text(alert.get("ai_recruitment_event_id")) if _text(alert.get("ai_recruitment_event_id")) in events_by_id else None,
        ))
    for event in events:
        if _text(event.get("id")) in alerted_events:
            continue
        where = " · ".join(part for part in (_text(event.get("company_name")), _text(event.get("job_title"))) if part)
        entries.append(_entry(
            event.get("created_at") or event.get("email_sent_at"), "mail", _words(event.get("primary_status")),
            " · ".join(part for part in (where, _text(event.get("subject"))) if part), "Candidate Gmail",
            booking_id=event.get("booking_id"), mail_id=event.get("mailbox_message_id"), event_id=event.get("id"),
        ))
    return [entry for entry in entries if entry]


def _unique(rows: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for row in rows:
        key = _text(row.get("id"))
        if key and key not in seen:
            seen[key] = row
    return list(seen.values())


def candidate_timeline(
    candidate_id: str,
    *,
    alerts_for: Callable[[str], list[dict]],
    events_for: Callable[[str], list[dict]],
    limit: int = 300,
) -> list[dict]:
    """Every entry for the person `candidate_id` belongs to, newest first."""
    cid = _text(candidate_id)
    family = set(candidate_store.candidate_identity_ids(cid, include_name_matches=False)) | {cid}
    rows = [row for row in candidate_store.all_booking_rows() if _text(row.get("id")) in family]
    alerts = _unique([alert for member in sorted(family) for alert in alerts_for(member)])
    events = _unique([event for member in sorted(family) for event in events_for(member)])
    entries = _booking_entries(rows) + _mail_entries(alerts, events)
    entries.sort(key=lambda entry: entry["at"], reverse=True)
    return entries[:limit]
