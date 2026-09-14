"""Read-only, lifecycle-aware comparison of booking surfaces."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

from services.recruitment_identity import resolve


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stamp(row: Mapping[str, Any]) -> str:
    return _text(row.get("updated_at") or row.get("created_at"))


def _cancelled(row: Mapping[str, Any]) -> bool:
    return _text(row.get("booking_status")).casefold() in {"cancelled", "auto_cancelled"} or row.get("lifecycle_state") == "CANCELLED"


def build_reconciliation_report(
    *, candidates: Iterable[Mapping[str, Any]], analyses: Iterable[Mapping[str, Any]],
    audits: Iterable[Mapping[str, Any]], notifications: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]] = (), lifecycles: Iterable[Mapping[str, Any]] = (),
    identity_links: Mapping[str, str] | None = None, complete: bool = True,
) -> dict[str, Any]:
    """Compare evidence without rewriting historical success or cancellation."""
    candidate_rows, analysis_rows, audit_rows, notification_rows, event_rows, lifecycle_rows = (
        [dict(row) for row in rows] for rows in (candidates, analyses, audits, notifications, events, lifecycles)
    )
    links = dict(identity_links or {})
    slots = {_text(r.get("id")): r for r in candidate_rows}
    confirmed = {key: row for key, row in slots.items() if row.get("slot_confirmed")}
    by_booking: dict[str, list[dict]] = defaultdict(list)
    for row in audit_rows:
        if row.get("booking_id"):
            by_booking[_text(row["booking_id"])].append(row)
    events_by_message = {_text(e.get("provider_message_id")): e for e in event_rows}
    authoritative_lifecycles = {}
    for row in lifecycle_rows:
        identity = (resolve(_text(row.get('candidate_id')), links),
                    _text(row.get('calendar_uid')).casefold() or _text(row.get('booking_id') or row.get('interview_key')))
        version = lambda r: (int(r.get('calendar_sequence') or 0),
                            bool(r.get('calendar_uid') and _cancelled(r)), _text(r.get('source_sent_at')))
        prior = authoritative_lifecycles.get(identity)
        if prior is None or version(row) > version(prior):
            authoritative_lifecycles[identity] = row
    current_lifecycles = list(authoritative_lifecycles.values())
    lifecycle_by_booking = {_text(r.get("booking_id")): r for r in current_lifecycles if r.get("booking_id")}
    findings: list[dict[str, Any]] = []
    explained: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(code, severity, row, *, detail, expected, repair, sources, booking_id=None):
        bid = _text(booking_id if booking_id is not None else row.get("booking_id"))
        mid = _text(row.get("gmail_message_id") or row.get("provider_message_id") or row.get("source_message_id"))
        event = events_by_message.get(mid, {})
        # One missing booking is one problem, however many historical audits exist.
        key = (code, bid, "" if bid else mid or _text(row.get("id")))
        if key in seen:
            return
        seen.add(key)
        findings.append({
            "code": code, "problem_type": code, "severity": severity,
            "candidate_id": resolve(_text(row.get("candidate_id")), links),
            "booking_id": bid, "message_id": mid,
            "event_id": _text(row.get("ai_recruitment_event_id") or event.get("id")),
            "detail": detail, "current_state": _text(row.get("booking_status") or row.get("lifecycle_state") or row.get("primary_status")),
            "expected_state": expected, "recommended_repair": repair, "sources": list(sources),
        })

    cancellation = {}
    for bid, history in by_booking.items():
        latest = max(history, key=_stamp)
        lifecycle = lifecycle_by_booking.get(bid, {})
        cancellation[bid] = _cancelled(latest) or (_cancelled(lifecycle) and lifecycle.get("transition_status") == "APPLIED")
        if bid not in confirmed and bid in slots and cancellation[bid]:
            explained.append({"booking_id": bid, "reason": "PERSISTED_CANCELLED_SLOT", "audit_ids": [_text(a.get("id")) for a in history]})
        for audit in history:
            if not audit.get("auto_booked") or bid in confirmed or cancellation[bid] and bid in slots:
                continue
            if _cancelled(audit):
                continue  # auto_booked historically means the automatic action succeeded.
            inactive = bid in slots
            add("BOOKING_DEACTIVATED_WITHOUT_CANCELLATION_EVIDENCE" if inactive else "AUTO_BOOKED_WITHOUT_PERSISTED_SLOT",
                "high" if inactive else "critical", audit,
                detail="The slot remains stored but is no longer confirmed; no successful cancellation explains it." if inactive else "A successful audit points to an absent booking row.",
                expected="A persisted confirmed slot or a documented subsequent cancellation/operator edit",
                repair="Inspect operator/history evidence. Do not recreate or reprocess automatically.", sources=("booking_audit", "candidate_store"))
        if bid in confirmed and cancellation[bid]:
            add("CANCELLED_LIFECYCLE_HAS_CONFIRMED_SLOT", "critical", latest,
                detail="Cancellation history conflicts with a currently confirmed slot.", expected="Cancelled slot, subject to source UID/SEQUENCE verification",
                repair="Verify cancellation identity and any later reinstatement before a targeted repair.", sources=("booking_audit", "candidate_store"))

    for bid, slot in confirmed.items():
        if slot.get("interview_booking_source") == "ai_auto_booked" and not by_booking.get(bid) and complete:
            add("PERSISTED_AI_SLOT_WITHOUT_AUDIT", "high", slot, booking_id=bid,
                detail="Confirmed AI slot has no booking audit.", expected="Audit for persisted slot", repair="Recover audit from source evidence without booking again.", sources=("candidate_store", "booking_audit"))

    audit_ids = {_text(a.get("id")) for a in audit_rows}
    for audit in audit_rows:
        if audit.get('auto_booked') and not audit.get('booking_id') and not _cancelled(audit):
            add('AUTO_BOOKED_WITHOUT_PERSISTED_SLOT', 'critical', audit,
                detail='Successful booking audit has no booking ID.', expected='Verified persisted slot reference',
                repair='Inspect source history; never rebook based on an audit alone.', sources=('booking_audit', 'candidate_store'))
    for note in notification_rows:
        bid = _text(note.get("booking_id"))
        if _text(note.get("booking_status")).casefold() in {"auto booked", "approved & booked", "rescheduled", "auto_booked", "auto_rescheduled"} and bid not in confirmed:
            if not (bid in slots and cancellation.get(bid)):
                add("NOTIFICATION_CLAIMS_BOOKED_WITHOUT_SLOT", "critical", note,
                    detail="Current alert claims a confirmed slot that is absent or inactive.", expected="Alert reflects verified current lifecycle", repair="Reconcile the alert after verifying slot history; do not create a slot.", sources=("notification", "candidate_store"))
        if note.get("booking_audit_id") and note["booking_audit_id"] not in audit_ids and complete:
            add("NOTIFICATION_AUDIT_MISSING", "high", note, detail="Alert references a missing audit.", expected="Existing audit reference", repair="Inspect reference before updating the alert.", sources=("notification", "booking_audit"))

    analysis_ids = {_text(a.get("id")) for a in analysis_rows}
    for audit in audit_rows:
        if audit.get("email_analysis_id") and audit["email_analysis_id"] not in analysis_ids and complete:
            add("BOOKING_AUDIT_ANALYSIS_MISSING", "medium", audit, detail="Audit references a missing analysis.", expected="Existing interview analysis", repair="Inspect source analysis; preserve audit history.", sources=("booking_audit", "interview_analysis"))
        cid = _text(audit.get("candidate_id"))
        event = events_by_message.get(_text(audit.get("gmail_message_id")), {})
        expected = resolve(_text(event.get("candidate_id")) or cid, links)
        # Historical source IDs are immutable facts, not stale projections.
        # Only persisted identity links prove equivalence; never infer a link
        # from names, contact details, booking times, or the event itself.
        audit_identity = resolve(cid, links)
        event_identity = resolve(_text(event.get("canonical_candidate_id")), links)
        if expected and (audit_identity != expected or event and event_identity != expected):
            add("CANONICAL_CANDIDATE_REFERENCE_DRIFT", "high", audit,
                detail=f"Audit candidate {cid} resolves to {audit_identity}; event canonical {event.get('canonical_candidate_id')} resolves to {event_identity}; persisted identity {expected}.",
                expected=expected, repair="Verify identity evidence before proposing a separately approved append-only correction or projection update. Preserve original audit references. Do not link identities or rebook automatically.", sources=("booking_audit", "ai_recruitment_event", "candidate_identity_links"))

    message_audits = {}
    # Attempts are append-only. A later failed/skipped replay cannot hide a
    # successful historical transition; its slot still needs re-verification.
    for audit in sorted(audit_rows, key=lambda a: (bool(a.get('auto_booked')), _stamp(a))):
        message_audits[_text(audit.get('gmail_message_id'))] = audit
    for event in event_rows:
        if event.get('automation_state') not in {'AUTO_BOOKED', 'AUTO_RESCHEDULED'}:
            continue
        audit = message_audits.get(_text(event.get('provider_message_id')), {})
        bid = _text(audit.get('booking_id'))
        if bid not in confirmed and not (bid in slots and cancellation.get(bid)) and (audit or complete):
            add('EVENT_CLAIMS_BOOKED_WITHOUT_SLOT', 'critical', event, booking_id=bid,
                detail='Event automation state is not backed by a confirmed persisted slot or later cancellation.',
                expected='Current lifecycle backed by slot evidence', repair='Reconcile event projection after source verification; do not book.',
                sources=('ai_recruitment_event', 'booking_audit', 'candidate_store'))
    for lifecycle in current_lifecycles:
        bid = _text(lifecycle.get('booking_id'))
        if lifecycle.get('transition_status') == 'APPLIED' and lifecycle.get('lifecycle_state') in {'BOOKED', 'RESCHEDULED'} and bid not in confirmed:
            add('APPLIED_LIFECYCLE_WITHOUT_CONFIRMED_SLOT', 'high', lifecycle,
                detail='Applied lifecycle state does not have a confirmed persisted slot.',
                expected='Verified slot or durable later cancellation/operator edit',
                repair='Inspect alias-linked newer lifecycle and operator history before repair.',
                sources=('interview_lifecycle_states', 'candidate_store'))

    # Time overlap alone is permitted. A duplicate needs source identity and person.
    identities: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for bid, slot in confirmed.items():
        owner = (by_booking.get(bid) or [{}])[0].get("candidate_id") or slot.get("canonical_candidate_id") or bid
        uid = _text(slot.get("interview_calendar_uid")).casefold()
        message = _text(slot.get("interview_source_message_id"))
        if uid or message:
            identities[(resolve(_text(owner), links), "uid" if uid else "message", uid or message)].append(bid)
    for (owner, kind, value), ids in identities.items():
        if len(ids) > 1:
            add("DUPLICATE_CONFIRMED_INTERVIEW_IDENTITY", "high", {"candidate_id": owner}, booking_id=",".join(sorted(ids)),
                detail=f"{len(ids)} confirmed slots share the same {kind} and canonical person.", expected="One active slot per logical interview",
                repair="Inspect UID/SEQUENCE history before a targeted duplicate repair.", sources=("candidate_store", "booking_audit"))
    return {"mode": "report_only", "coverage": {"complete": complete},
            "summary": {"candidates": len(candidate_rows), "analyses": len(analysis_rows), "audits": len(audit_rows),
                        "notifications": len(notification_rows), "events": len(event_rows), "findings": len(findings),
                        "explained_cancellations": len(explained), **dict(Counter(f["severity"] for f in findings))},
            "findings": findings, "explained": explained}


def load_current_report(*, candidate_id: str | None = None, limit: int = 500) -> dict[str, Any]:
    """Fetch referential closure, including historical/cancelled rows, read-only."""
    from core.db.connection import get_connection
    from features import candidate_store
    from services.recruitment_identity import aliases
    candidates = list(candidate_store._load(force=True).get("candidates") or [])
    cap = max(1, min(limit, 2000))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cur.execute("SELECT alias_candidate_id,canonical_candidate_id FROM candidate_identity_links")
        links = dict(cur.fetchall())
        scope = sorted(aliases(candidate_id, links)) if candidate_id else None
        def query(sql, params=()):
            cur.execute(sql, params)
            names = [c.name for c in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]
        audits = query("SELECT * FROM interview_auto_booking_audit WHERE (%s::text[] IS NULL OR candidate_id=ANY(%s)) ORDER BY updated_at DESC LIMIT %s", (scope, scope, cap + 1))
        complete = len(audits) <= cap
        audits = audits[:cap]
        bids = list({a['booking_id'] for a in audits if a.get('booking_id')})
        # A recent audit's older cancellation/confirmation and referenced analysis
        # must remain in the snapshot even when the seed list is paginated.
        closure = query("SELECT * FROM interview_auto_booking_audit WHERE booking_id=ANY(%s)", (bids,))
        audits = list({a['id']: a for a in audits + closure}.values())
        notes = query("SELECT * FROM mail_monitoring_notifications WHERE (%s::text[] IS NULL OR candidate_id=ANY(%s) OR booking_id=ANY(%s)) ORDER BY updated_at DESC LIMIT %s", (scope, scope, bids, cap + 1))
        complete = complete and len(notes) <= cap
        notes = notes[:cap]
        referenced = query("SELECT * FROM interview_auto_booking_audit WHERE id=ANY(%s)", ([n['booking_audit_id'] for n in notes if n.get('booking_audit_id')],))
        audits = list({a['id']: a for a in audits + referenced}.values())
        analyses = query("SELECT * FROM interview_mail_analyses WHERE id=ANY(%s)", ([a['email_analysis_id'] for a in audits if a.get('email_analysis_id')],))
        mids = sorted({r['gmail_message_id'] for r in audits + notes if r.get('gmail_message_id')})
        events = query("SELECT e.*,m.provider_message_id FROM ai_recruitment_events e JOIN mailbox_messages m ON m.id=e.mailbox_message_id WHERE m.provider_message_id=ANY(%s) ORDER BY e.updated_at,e.id", (mids,))
        lifecycles = query("SELECT * FROM interview_lifecycle_states WHERE (%s::text[] IS NULL OR candidate_id=ANY(%s) OR booking_id=ANY(%s))", (scope, scope, bids))
    if scope:
        linked_bids = {a.get('booking_id') for a in audits} | {n.get('booking_id') for n in notes}
        candidates = [c for c in candidates if c.get('id') in scope or c.get('id') in linked_bids]
    return build_reconciliation_report(candidates=candidates, analyses=analyses, audits=audits,
        notifications=notes, events=events, lifecycles=lifecycles, identity_links=links, complete=complete)
