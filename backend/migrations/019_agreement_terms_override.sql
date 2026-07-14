-- Per-client overrides for agreement clauses that are otherwise hardcoded
-- (payment due period and backdoor hiring protection period).
ALTER TABLE clients ADD COLUMN IF NOT EXISTS payment_due_days integer;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS backdoor_period text;
