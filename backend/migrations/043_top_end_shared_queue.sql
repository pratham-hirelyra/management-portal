-- Top-end CE queue moves from auto-assigned (least-loaded CE picked at
-- insert time) to a shared, claim-based pool — mirrors the bottom_end
-- ticket desk's model instead of rm_service.assign_rm's auto-pick style.
-- See services/ce_service.py::sweep_assign (now just tops up the pool,
-- doesn't pick a CE) and routers/ce.py's new claim endpoint.

ALTER TABLE ce_assignments ALTER COLUMN ce_id DROP NOT NULL;
ALTER TABLE ce_assignments ADD COLUMN IF NOT EXISTS claimed_at timestamptz;

-- max_clients_per_day is no longer an assignment ceiling (claiming isn't
-- capped) — it's now a daily minimum target, tracked (claimed-today vs.
-- target) rather than enforced.
ALTER TABLE customer_executives RENAME COLUMN max_clients_per_day TO daily_target;

CREATE INDEX IF NOT EXISTS idx_ce_assignments_unclaimed
    ON ce_assignments (call_date) WHERE ce_id IS NULL AND status = 'pending';

-- Confirmed OK to clear existing assignments — clean slate for the new
-- shared-pool model rather than backfilling claimed_at for old rows.
TRUNCATE ce_assignments;
