-- "No pickup" tracking: 1st no-pickup pushes the client to the back of
-- today's queue (via last_attempt_at) for a same-day retry; 2nd no-pickup
-- on the same call_date auto-shifts to tomorrow and resets the counter.
ALTER TABLE ce_assignments ADD COLUMN IF NOT EXISTS no_pickup_count int NOT NULL DEFAULT 0;
ALTER TABLE ce_assignments ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz;
