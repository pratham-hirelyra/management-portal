-- Migration 002: Add missing columns and new mapping_stage enum values
-- Run once against the Cloud Run / production database.

-- ── mapping_stage enum: add new values used by the app ───────────────────────
-- (ADD VALUE IF NOT EXISTS is safe to re-run; cannot be done inside a transaction)
ALTER TYPE mapping_stage ADD VALUE IF NOT EXISTS 'intent_ask';
ALTER TYPE mapping_stage ADD VALUE IF NOT EXISTS 'interested';
ALTER TYPE mapping_stage ADD VALUE IF NOT EXISTS 'not_interested';
ALTER TYPE mapping_stage ADD VALUE IF NOT EXISTS 'slot_booked';
ALTER TYPE mapping_stage ADD VALUE IF NOT EXISTS 'rejected';

-- ── client_stage enum: add agreement_sent if missing ─────────────────────────
ALTER TYPE client_stage ADD VALUE IF NOT EXISTS 'agreement_sent';

-- ── clients table: add all columns the app writes but schema lacks ────────────
ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS phone_numbers       jsonb    DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS agreed_phone        text,
  ADD COLUMN IF NOT EXISTS company_website     text,
  ADD COLUMN IF NOT EXISTS company_description text,
  ADD COLUMN IF NOT EXISTS source_job_id       text,
  ADD COLUMN IF NOT EXISTS female_employee_pct numeric,
  ADD COLUMN IF NOT EXISTS diwali_bonus        text,
  ADD COLUMN IF NOT EXISTS business_type       text,
  ADD COLUMN IF NOT EXISTS available_slots     jsonb    DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS recruiter_slots     jsonb    DEFAULT '[]'::jsonb;

-- ── client_candidate_mappings: add available_slots ───────────────────────────
ALTER TABLE client_candidate_mappings
  ADD COLUMN IF NOT EXISTS available_slots jsonb DEFAULT '[]'::jsonb;

-- ── schema.sql sync: update the base schema to match ─────────────────────────
-- (the schema.sql file in the repo should also be updated manually)
