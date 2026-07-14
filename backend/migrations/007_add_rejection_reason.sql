-- Migration 007: track WHY a mapping landed in 'rejected' so RMs can tell
-- a genuine client dislike apart from system-driven outcomes (failed hard
-- filters / overqualified, slot not picked in time, position filled) —
-- previously all four paths collapsed into one "Rejected by Client" label.
-- Run once: psql $DATABASE_URL -f migrations/007_add_rejection_reason.sql

ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS rejection_reason text;
