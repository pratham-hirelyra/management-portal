-- Migration 017: add district column to candidates
--
-- Same as migration 016 for clients: the candidate apply form's Google Places
-- autocomplete now extracts the district from the selected place's
-- address_components and shows it back to the candidate as a read-only
-- confirmation field, helping them catch a wrong same-named location pick
-- within the same state before submitting.
--
-- Run once: psql $DATABASE_URL -f migrations/017_candidate_district.sql

ALTER TABLE candidates ADD COLUMN IF NOT EXISTS district text;
