-- Migration 009: add is_freshly_sourced flag to candidates
-- All existing candidates default to false (they're from the imported DB or old sourcing runs).
-- The sourcing automation should set this to true on every new INSERT going forward.
-- Run once: psql $DATABASE_URL -f migrations/009_add_is_freshly_sourced.sql

ALTER TABLE candidates
    ADD COLUMN IF NOT EXISTS is_freshly_sourced boolean NOT NULL DEFAULT false;
