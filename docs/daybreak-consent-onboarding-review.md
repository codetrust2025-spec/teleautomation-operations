# Operations → Daybreak account mirror

Operations service-account IDs are the stable source identities. The existing five-minute credential loop can additionally reconcile the account list when `DAYBREAK_ACCOUNT_MIRROR_ENABLED=1`; it remains OFF by default. No Naukri login, queue submission or save is performed by this loop.

- New uniquely identified Naukri accounts require an explicit administrator consent attestation bound to the current normalized email and an available password. Never infer consent from an import, notes or possession of credentials. Both recipient encryption keys must be approved.
- Existing identities retain IDs, facts, sessions, save history and scheduling. Names and enabled status reconcile from Operations. Changed passwords use the existing signed prepare/commit and RSA-sealed per-worker envelopes.
- Removed, reclassified, disabled or consent-revoked source accounts are deactivated, not deleted. Active queued/running work delays reconciliation. Unknown source identities, duplicates and changed emails are reported rather than guessed or rebound. A changed identity needs renewed consent and review of the old identity link.
- Missing/corrupt source data fails closed. Each pass is serialized and bounded. Durable pending delivery intents survive ambiguous responses/crashes; they are not blindly repeated. Receipts are required for delivery confirmation; actual account-matched Naukri evidence is separately required for Connected.
- The Daybreak receiver must advertise `accountMirrorV1`. Its signed metadata action and creation guard reject active work and uncertain saves by identity, including historical email evidence. There are no person-specific restrictions.

## Counts

The API and Data Room show source count, Daybreak physical record count, matched/missing counts, exact blocked source IDs/reasons, retained deleted links and pending delivery outcomes. With 28 valid consented unique source accounts and no blocked identities, the integration test converges from 24 to 28 Daybreak records. After deletion, active account counts mirror the source while deactivated historical records remain; history must never be deleted to manufacture equal raw totals.

## Approval and rollout gate

Do not activate this feature until the owner approves the exact Operations and companion Daybreak receiver commits. Use normal CI and code-only release gates, a fresh verified private D1 backup, idle-work checks, unchanged worker routing/keys/09:30 IST schedule, and confirmed Naukri writes OFF. Deploy the additive Daybreak receiver guard first; verify its capability and signed refusal cases. Deploy Operations through the normal pinned immutable-image process with the mirror flag still OFF. Verify read-only planned changes and source consent. Enable the mirror flag only under the same reviewed approval, then observe two automatic cycles, compare IDs/counts/receipts, and verify no jobs or saves were created. Never force 28 by fabricating missing consent.

## Rollback

Disable only `DAYBREAK_ACCOUNT_MIRROR_ENABLED` and recreate Operations API using its existing image; ordinary credential delivery can remain enabled. Keep the previous immutable image and Daybreak Worker version. Code rollback must preserve new source-links, delivery intents, consent evidence, all profiles and history. Do not reactivate a deactivated account automatically during rollback, clear uncertainty, replay pending operations, delete new candidates or restore an old database snapshot. If any reconciliation outcome is ambiguous, hold that identity for read-only review.
