-- Test records that reached production, kept but left out of money figures.
--
-- A row named here stays in candidates_store exactly as it is: nothing is
-- deleted, merged or rewritten, and its audit history is untouched. Payment
-- totals, handler earnings, pending collections and the payment mismatch
-- report skip it. Setting revoked_at puts it back in every figure.
--
-- Additive only.
CREATE TABLE IF NOT EXISTS candidate_test_record_exclusions (
    candidate_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    marked_by TEXT NOT NULL,
    marked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    revoked_by TEXT
);

-- The four rows the owner asked to be audited on 13 Nov 2026, each confirmed as test data.
INSERT INTO candidate_test_record_exclusions (candidate_id, reason, evidence, marked_by)
VALUES
    ('40c31f4597',
     'Test fixture: "Ram Charan M S" with placeholder phone 9000000009, not the real candidate',
     '{"created_at": "2026-10-31T08:54:20Z",
       "batch": "created 31 Oct 08:54:17-29 UTC with Channel Person 414, Legacy Proof and Sakthivek Test",
       "payment_reference": "T2606111736078866007255 is Uday Kumar Rapolu''s real payment; UTR 678487078430 also on test row Legacy Proof",
       "amount_correction": "factor-of-ten correction applied 1 second after creation",
       "real_candidate_rows": ["dfea98052b", "9ee52550b8"]}'::jsonb,
     'owner-approved audit 13 Nov 2026'),
    ('lead',
     'Test fixture: "Round Person", reference "Test Owner", placeholder phone 9000000601',
     '{"fixture_id": "lead", "payment_reference": "none"}'::jsonb,
     'owner-approved audit 13 Nov 2026'),
    ('booked',
     'Test fixture: "Round Person", reference "Test Owner", placeholder phone 9000000601',
     '{"fixture_id": "booked", "payment_reference": "fake proof pf1, UTR U-pf1 / T-pf1, also on test rows earlier and plain-ok"}'::jsonb,
     'owner-approved audit 13 Nov 2026'),
    ('earlier',
     'Test fixture: "Round Person", reference "Test Owner", placeholder phone 9000000601',
     '{"fixture_id": "earlier", "payment_reference": "fake proof pf1, UTR U-pf1 / T-pf1, also on test rows booked and plain-ok"}'::jsonb,
     'owner-approved audit 13 Nov 2026')
ON CONFLICT (candidate_id) DO NOTHING;
