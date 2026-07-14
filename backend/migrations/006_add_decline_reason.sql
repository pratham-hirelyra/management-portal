-- Migration 006: track WHY a candidate mapping landed in not_interested/rejected
-- so RMs can filter "Not Interested in Role" vs "Not Looking for Job" vs generic decline
-- under each client profile (previously these all collapsed into one bucket).
-- Run once: psql $DATABASE_URL -f migrations/006_add_decline_reason.sql

ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS decline_reason text;
