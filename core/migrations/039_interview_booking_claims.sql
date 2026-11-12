-- One automatic booking per interview, enforced by the database.
--
-- Before an AI-mail booking writes a slot it claims the interview's keys here:
-- the source mail ("mail:<candidate>:<gmail id>") and every exact meeting room
-- the mail carries at that start ("room:<candidate>:<date>:<time>:<room>").
-- The primary key makes a second claim of the same key impossible, so a retry,
-- a replay or a reminder of an interview already booked can never create a
-- second slot, whatever the code above it decides.
--
-- Additive only: no existing booking, alert or audit row is read or changed.
CREATE TABLE IF NOT EXISTS interview_booking_claims (
    claim_key TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    booking_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_interview_booking_claims_booking
    ON interview_booking_claims (booking_id) WHERE booking_id IS NOT NULL;
