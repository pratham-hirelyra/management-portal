-- Candidate's stated reason for declining an offer_sent mapping
-- (routers/candidate_portal.respond_to_offer). Kept separate from the
-- existing decline_reason column, which is a fixed code used at the earlier
-- JD-intent stage — this one is the candidate's free-text explanation.
ALTER TABLE client_candidate_mappings
  ADD COLUMN IF NOT EXISTS offer_decline_reason text;
