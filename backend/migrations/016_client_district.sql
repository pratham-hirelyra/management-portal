-- Migration 016: add district column to clients
--
-- The onboarding form lets clients pick job_location via Google Places
-- autocomplete, but they sometimes pick the wrong result among several
-- same-named locations within the same state. Capturing the district from
-- the selected place's address_components and showing it back to the client
-- as a read-only confirmation field helps them catch this before submitting.
--
-- Run once: psql $DATABASE_URL -f migrations/016_client_district.sql

ALTER TABLE clients ADD COLUMN IF NOT EXISTS district text;
