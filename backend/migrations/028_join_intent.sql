-- Adds a "willing to join?" gate between Hire and document collection.
-- Client clicks Hire -> candidate is asked to confirm on WhatsApp/portal ->
-- only on ACCEPT do we move to documents_requested (see company_portal.hire_candidate,
-- candidate_portal.respond_to_join_intent).
-- (ADD VALUE IF NOT EXISTS is safe to re-run; cannot be done inside a transaction)
ALTER TYPE mapping_stage ADD VALUE IF NOT EXISTS 'join_intent_requested';

ALTER TABLE client_candidate_mappings
  ADD COLUMN IF NOT EXISTS join_intent_requested_at timestamptz,
  ADD COLUMN IF NOT EXISTS join_intent_responded_at timestamptz,
  ADD COLUMN IF NOT EXISTS join_decline_reason text,
  ADD COLUMN IF NOT EXISTS documents_reminder_count int DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_documents_reminder_at timestamptz;
