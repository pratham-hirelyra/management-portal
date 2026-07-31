-- Debounce buffer for candidates who send a question in several rapid WhatsApp
-- messages instead of one. One row per phone; each new fragment appends and
-- bumps updated_at. The agent only actually runs once updated_at has gone
-- quiet for AI_RM_DEBOUNCE_SECONDS — see services/ai_rm_service.py.
CREATE TABLE IF NOT EXISTS candidate_ai_pending (
    phone         text PRIMARY KEY,
    buffered_text text NOT NULL DEFAULT '',
    updated_at    timestamptz NOT NULL DEFAULT now()
);
