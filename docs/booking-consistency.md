# Booking identity and read-only reconciliation

Booking, event canonical identity, new interview alerts and lifecycle locks now
resolve the persisted `candidate_identity_links` graph. They never select a
person ID from the current UI display row. Missing links retain the original ID;
cycles fail closed. Original event/mailbox IDs remain provenance.

## Backward compatibility and replay safety

Existing lifecycle rows may have been written under a historical display/slot
alias. The claim reads and locks every persisted alias's interview key, choosing
the authoritative UID/SEQUENCE (cancellation wins equal sequence), then source
time. It checks stale/conflicting precedence **before** consulting historical
transition success. An equivalent retry reuses the existing booking ID and
transition key. The booking executor must still re-read that slot; an audit or
transition alone never proves persistence. No backfill is required to preserve
old cancellation tombstones.

## Reconciliation is evidence, not an automatic repair

The inventory uses raw candidate rows (including slot clones), a repeatable-read,
read-only SQL snapshot, alias-aware candidate filtering, and audit-reference
closure. It distinguishes:

- Retained, deactivated rows explained by a later successful cancellation.
- Absent booking rows versus unexplained deactivated rows.
- Persisted alert/event projections that disagree with actual slot state.
- Historical audit references that differ from the persisted canonical person.
- Exact interview identity duplicates, not coincident interview times.

Stored alert discrepancies are not claims that the live UI is displaying them:
the notification read path also releases stale booked claims. Confirmed Slots
and Daily Ops derive their bookings from the candidate slot store.

The report includes reference IDs and repair recommendations. It does not edit
audits, source events, slots, identity links, notifications, or queues. Historical
IDs remain untouched. A data normalization or slot repair requires separate
approval with an exact target list and preservation of the original references.
Truncated inventories explicitly report incomplete coverage and do not infer
missing references from an incomplete page.

## Historical calendar discovery

Discovery includes the legacy ignored statuses as well as `AUTO_IGNORE`. It
compares source UID/SEQUENCE and source cancellations, timezone-normalized time,
canonical person and actual confirmed slots. An audit row is not enough to mark
an invite represented. The endpoint never calls AI, leases mail, or reprocesses
it. `RECOVERY_CANDIDATE` means source evidence needs the normal AI, payment and
lifecycle checks, not that booking has been authorized. An invite with no
booking is reported as one of two states, never one: `CANCELLED_OR_SUPERSEDED`
for a revision that was called off or replaced, and `PAST_NEVER_BOOKED` for an
interview that has gone by with nothing holding its hour -- which may be one
that happened and was never recorded. Worker allowlists and
Claude's classifier/relevance rules are unchanged.

## Release verification

After the normal CI/pin/deploy pipeline, verify `/version`, all four containers,
the Confirmed Slots UI, and both read-only inventory endpoints. Confirm that
protected slots and cancelled interviews have not changed. Do not bulk-replay
historical candidates or convert the report into an automatic repair queue.

No schema migration is included in this release.
