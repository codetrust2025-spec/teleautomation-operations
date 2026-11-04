# Continuous Operations → Daybreak credential delivery

This opt-in background job reconciles Data Room Naukri credentials with existing Daybreak candidates every five minutes. It uses the already-reviewed signed Operations intake and encrypted worker delivery protocol; it never exposes passwords to the browser, logs or GitHub.

## Enable after review and authorized deployment

Set `DAYBREAK_CREDENTIAL_AUTO_SYNC_ENABLED=1` in the Operations API deployment and restart that service. Default is OFF. Keep the Daybreak Operations intake configured and verify both RTX 4060 and Jagadeesh encryption-key fingerprints in Data Room → Daybreak. The loop will only deliver to approved enrolled worker keys.

Every pass handles up to ten uniquely matched, consented, enabled accounts with no existing delivery, or accounts with changed or expired deliveries. Pending deliveries wait for worker receipts; completed deliveries are not repeated. Login failures and identity conflicts require a human. This loop never starts Naukri sessions or queues headline saves.

## Source vs destination counts

Operations currently lists 28 Naukri credential records and Daybreak lists 24 accounts. Exact normalized email comparison identifies four unmatched Data Room records: Kalyan Goud, Keerthi Nannapaneni Frontend Developer, Purra Gouthami and Surekha Gaddagolla. **The background loop never creates missing candidates or interprets the four-account mismatch as permission to add them.** The existing Data Room → Daybreak UI has a manual **Add** flow which requires confirmation of each candidate's consent. Surekha has a protected quarantine and must not be automatically added or reactivated.

To verify: inspect Data Room → Daybreak reconciliation, worker-key approvals, receipts, and Daybreak connections independently. A delivered credential is not automatically a verified Naukri login. Disable by setting the env flag to `0` and restarting. Disabling the loop does not affect existing Daybreak sessions, automation settings, or saved headlines.

## Scope of the automatic loop

- Administrative worker-key approvals remain manual and are revalidated by Daybreak for each delivery.
- Candidate creation, consent, account enabling and Naukri saves are **never** performed automatically by this bridge.
- A local nonblocking file lock prevents duplicate passes across Operations API processes on the same data volume. It also bounds each pass to ten deliveries to reduce D1 traffic.
- Delivery uses the existing signed `prepare` and `commit` actions with RSA-encrypted worker envelopes; source passwords do not appear in logs or audit payloads.
- On a temporary error the next interval retries. Worker disconnects and unapproved key rotations fail closed.
