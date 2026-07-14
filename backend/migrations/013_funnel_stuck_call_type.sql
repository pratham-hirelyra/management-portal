-- Migration 013: distinguish AI voice call types on client_ai_calls
--
-- Adds call_type so the same table can track both:
--   'reachout'     — initial outreach call to new leads (existing agent)
--   'funnel_stuck' — follow-up call to clients stuck at 'interested' or
--                    'agreement_sent' (new "Funnel Stuck" agent)
--
-- Run once: psql $DATABASE_URL -f migrations/013_funnel_stuck_call_type.sql

ALTER TABLE client_ai_calls ADD COLUMN IF NOT EXISTS call_type text NOT NULL DEFAULT 'reachout';

CREATE INDEX IF NOT EXISTS idx_client_ai_calls_call_type ON client_ai_calls(call_type);
