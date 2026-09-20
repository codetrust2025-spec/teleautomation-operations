"""Read-only discovery, not permission to replay historical mail.

An invite with no booking behind it is two different things, and they were
filed together. A cancelled or superseded revision is history working
correctly: nothing should hold that hour. An invite whose interview has gone by
with no booking at all is the opposite -- it may be an interview that happened
and was never recorded, and calling it stale is how two of them stayed
invisible for weeks. They are separate states now, so the second kind can be
looked at.

Nothing here books, cancels, reschedules or edits anything.
"""
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from services.calendar_invite_parser import parse_calendar
from services.recruitment_identity import resolve

CANCELLED_OR_SUPERSEDED = "CANCELLED_OR_SUPERSEDED"
PAST_NEVER_BOOKED = "PAST_NEVER_BOOKED"
ALREADY_REPRESENTED = "ALREADY_REPRESENTED"
RECOVERY_CANDIDATE = "RECOVERY_CANDIDATE"
UNRESOLVED = "UNRESOLVED"

#: What each state is called where a person reads it.
DISCOVERY_LABELS = {
    CANCELLED_OR_SUPERSEDED: "Cancelled / superseded",
    PAST_NEVER_BOOKED: "Past interview — never booked",
    ALREADY_REPRESENTED: "Already booked",
    RECOVERY_CANDIDATE: "Recovery candidate",
    UNRESOLVED: "Unresolved",
}


def classify_records(records, *, calendars, slots, audits, links, now=None):
    now = now or datetime.now(timezone.utc)
    owners = {a['booking_id']: resolve(a['candidate_id'], links) for a in audits if a.get('booking_id')}
    parsed = []
    for source in calendars:
        calendar = parse_calendar(source.get('extracted_text') or '')
        if calendar:
            parsed.append((source, calendar))
    names = {str(row.get('id')): row.get('name') for row in slots if row.get('name')}
    output = []
    for source in records:
        row = dict(source)
        person = resolve(row.get('canonical_candidate_id') or '', links)
        row['canonical_candidate_id'] = person
        matches = [cal for m, cal in parsed if m['mailbox_message_id'] == row['mailbox_message_id']]
        row['discovery_state'] = UNRESOLVED
        row['reason'] = 'Calendar evidence missing or ambiguous; no automatic recovery authorized.'
        # Who and where, so a past invite reads on its own without another query.
        row['candidate_name'] = (row.get('candidate_name') or names.get(person)
                                 or names.get(row.get('canonical_candidate_id') or '') or '')
        row['company'] = row.get('company_name') or ''
        if len(matches) == 1:
            cal = matches[0]
            uid = str(cal.get('uid') or '').casefold()
            start, end = cal.get('start'), cal.get('end')
            row.update(calendar_uid=cal.get('uid'), calendar_sequence=cal.get('sequence'),
                       start_ist=start.astimezone(ZoneInfo('Asia/Kolkata')).isoformat() if start else None,
                       end_ist=end.astimezone(ZoneInfo('Asia/Kolkata')).isoformat() if end else None)
            cancelled = cal.get('method') == 'CANCEL' or cal.get('status') == 'CANCELLED'
            superseded = any(
                uid and str(other.get('uid') or '').casefold() == uid
                and m['mailbox_id'] == row['mailbox_id']
                and (other['sequence'] > cal['sequence'] or
                     other['sequence'] == cal['sequence'] and
                     (other.get('method') == 'CANCEL' or other.get('status') == 'CANCELLED'))
                for m, other in parsed
            )
            same_interview = [s for s in slots if s.get('slot_confirmed')
                         and owners.get(s['id'], resolve(s.get('canonical_candidate_id') or s['id'], links)) == person
                         and uid and str(s.get('interview_calendar_uid') or '').casefold() == uid]
            def version(slot):
                try:
                    return int(slot.get('interview_calendar_sequence') or 0)
                except (TypeError, ValueError):
                    return -1  # ambiguous metadata is never proof of persistence
            superseded = superseded or any(version(s) > cal['sequence'] for s in same_interview)
            local_start = start.astimezone(ZoneInfo('Asia/Kolkata')) if start else None
            local_end = end.astimezone(ZoneInfo('Asia/Kolkata')) if end else None
            def holds_exactly(slot):
                return (local_start and local_end
                        and str(slot.get('date') or '')[:10] == local_start.date().isoformat()
                        and str(slot.get('time') or '')[:5] == local_start.strftime('%H:%M')
                        and str(slot.get('time_end') or '')[:5] == local_end.strftime('%H:%M'))

            persisted = [s for s in same_interview if version(s) == cal['sequence'] and holds_exactly(s)]
            matched_by = 'calendar event'
            if not persisted:
                # A slot booked by hand carries no calendar id, so the UID can
                # never match and the invite reads as unbooked: Keerthi's 21 Sep
                # interview was reported as having no booking the day before it,
                # while the booking sat in Daily Ops. The schedule can still be
                # matched, and strictly -- the same person, the same day, the
                # same start and the same end, on a row that claims no calendar
                # event of its own, so this can never shadow a different
                # meeting that happens to share the hour.
                persisted = [s for s in slots
                             if s.get('slot_confirmed')
                             and not str(s.get('interview_calendar_uid') or '').strip()
                             and owners.get(s['id'], resolve(s.get('canonical_candidate_id') or s['id'], links)) == person
                             and holds_exactly(s)]
                if persisted:
                    matched_by = 'exact schedule'
            if cancelled or superseded:
                row.update(discovery_state=CANCELLED_OR_SUPERSEDED,
                           reason='Cancelled or superseded calendar revision.')
            elif persisted:
                row.update(discovery_state=ALREADY_REPRESENTED, booking_ids=[s['id'] for s in persisted],
                           matched_by=matched_by,
                           reason=('Confirmed persisted slot matches person, UID, revision and exact schedule.'
                                   if matched_by == 'calendar event' else
                                   'A confirmed slot with no calendar id of its own holds exactly this '
                                   'person, day, start and end.'))
            elif start and start <= now:
                # Why no booking exists, as far as this report can see: the mail
                # was set aside, and the interview has gone by since.
                ignored = str(row.get('ignore_reason') or '').strip()
                row.update(discovery_state=PAST_NEVER_BOOKED,
                           reason='Interview start is in the past and no confirmed slot holds it'
                                  + (f'; the mail was set aside as {ignored}.' if ignored else '.'))
            elif start and end and uid:
                row.update(discovery_state=RECOVERY_CANDIDATE,
                           reason='Future calendar not represented by a confirmed slot; AI, payment and lifecycle checks still required.')
        row['label'] = DISCOVERY_LABELS.get(row['discovery_state'], row['discovery_state'])
        output.append(row)
    counts = Counter(r['discovery_state'] for r in output)
    return {'summary': {'total': len(output), 'recovery_candidates': counts[RECOVERY_CANDIDATE],
                       'already_represented': counts[ALREADY_REPRESENTED],
                       'cancelled_or_superseded': counts[CANCELLED_OR_SUPERSEDED],
                       'past_never_booked': counts[PAST_NEVER_BOOKED],
                       'already_assessed': 0, 'unresolved': counts[UNRESOLVED]}, 'records': output}


def load_report(*, limit=500):
    from core.db.connection import get_connection
    from core.recruitment_mail_store import _CALENDAR_FALSE_IGNORE_REASONS, _rows
    from features import candidate_store
    slots = list(candidate_store._load(force=True).get('candidates') or [])
    cap = max(1, min(limit, 2000))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cur.execute('SELECT alias_candidate_id,canonical_candidate_id FROM candidate_identity_links')
        links = dict(cur.fetchall())
        cur.execute("""SELECT m.id AS mailbox_message_id,m.mailbox_id,m.provider_message_id,
                 m.subject,m.sent_at,m.ignore_reason,m.processing_status,
                 b.candidate_id AS canonical_candidate_id,
                 n.candidate_name,n.company_name
            FROM mailbox_messages m JOIN candidate_mailboxes b ON b.id=m.mailbox_id
            LEFT JOIN LATERAL (
                SELECT candidate_name,company_name FROM mail_monitoring_notifications x
                 WHERE x.gmail_message_id=m.provider_message_id ORDER BY x.created_at LIMIT 1
            ) n ON true
            WHERE m.processing_status IN ('AUTO_IGNORE','IGNORED_NOT_OFFER_RELATED','IGNORED_LOW_CONFIDENCE')
              AND m.ignore_reason=ANY(%s)
              AND EXISTS(SELECT 1 FROM mailbox_attachments a WHERE a.mailbox_message_id=m.id
                         AND lower(COALESCE(a.filename,'')) LIKE '%%.ics')
            ORDER BY m.sent_at,m.id LIMIT %s""", (list(_CALENDAR_FALSE_IGNORE_REASONS), cap + 1))
        records = _rows(cur)
        complete = len(records) <= cap
        records = records[:cap]
        # Include source cancellations/reschedules, even if AI ignored those too.
        cur.execute("""SELECT m.id AS mailbox_message_id,m.mailbox_id,c.extracted_text
            FROM mailbox_messages m JOIN mailbox_attachments a ON a.mailbox_message_id=m.id
            JOIN mailbox_attachment_cache c ON c.checksum=a.checksum
            WHERE m.mailbox_id=ANY(%s) AND lower(COALESCE(a.filename,'')) LIKE '%%.ics'""",
            (list({r['mailbox_id'] for r in records}),))
        calendars = _rows(cur)
        cur.execute('SELECT booking_id,candidate_id FROM interview_auto_booking_audit WHERE booking_id=ANY(%s)',
                    ([s['id'] for s in slots if s.get('slot_confirmed')],))
        audits = _rows(cur)
    report = classify_records(records, calendars=calendars, slots=slots, audits=audits, links=links)
    report['coverage'] = {'complete': complete}
    return report
