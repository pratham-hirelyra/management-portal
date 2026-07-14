-- Migration 004: RM city, all_time_clients; candidate locks; MMS scores;
--                interview slot reminders + feedback; handover tokens; outreach batches

-- ── rms ───────────────────────────────────────────────────────────────────────
ALTER TABLE rms
    ADD COLUMN IF NOT EXISTS city            text,
    ADD COLUMN IF NOT EXISTS all_time_clients int NOT NULL DEFAULT 0;

-- ── clients ───────────────────────────────────────────────────────────────────
ALTER TABLE clients ADD COLUMN IF NOT EXISTS city text;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS sourcing_needed boolean NOT NULL DEFAULT false;

-- ── candidate_overrides: DND support + notice period ─────────────────────────
ALTER TABLE candidate_overrides ADD COLUMN IF NOT EXISTS dnd_until timestamptz;
ALTER TABLE candidate_overrides ADD COLUMN IF NOT EXISTS notice_period text;

-- ── client_candidate_mappings: batch, reminders, feedback ─────────────────────
ALTER TABLE client_candidate_mappings
    ADD COLUMN IF NOT EXISTS batch_number              int     NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS reminder_t2_sent          boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS feedback_client           text,    -- 'liked' | 'disliked'
    ADD COLUMN IF NOT EXISTS feedback_candidate        text,    -- 'liked' | 'disliked'
    ADD COLUMN IF NOT EXISTS feedback_token_client     text,
    ADD COLUMN IF NOT EXISTS feedback_token_candidate  text,
    ADD COLUMN IF NOT EXISTS low_trust_flagged         boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS feedback_requested_at     timestamptz;

-- unique indexes for feedback tokens
CREATE UNIQUE INDEX IF NOT EXISTS idx_mappings_token_client
    ON client_candidate_mappings(feedback_token_client) WHERE feedback_token_client IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mappings_token_candidate
    ON client_candidate_mappings(feedback_token_candidate) WHERE feedback_token_candidate IS NOT NULL;

-- ── candidate_locks ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS candidate_locks (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id uuid NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    client_id    uuid NOT NULL REFERENCES clients(id)    ON DELETE CASCADE,
    locked_at    timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz,         -- 24h after interview_slot; NULL = unlock manually
    unlocked_at  timestamptz,
    unlock_reason text,               -- 'not_interested'|'interview_done'|'expired'
    UNIQUE(candidate_id, client_id)
);
CREATE INDEX IF NOT EXISTS idx_locks_active
    ON candidate_locks(candidate_id) WHERE unlocked_at IS NULL;

-- ── mms_scores ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mms_scores (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id            uuid NOT NULL REFERENCES clients(id)     ON DELETE CASCADE,
    candidate_id         uuid NOT NULL REFERENCES candidates(id)  ON DELETE CASCADE,
    mms_score            numeric,
    technical_component  numeric,
    proximity_component  numeric,
    salary_component     numeric,
    skills_component     numeric,
    scoring_path         text,        -- 'accountant' | 'deo'
    filter_passed        boolean NOT NULL DEFAULT false,
    filter_exclusion_reason text,
    travel_time_minutes  numeric,
    bts                  numeric,     -- Benchmark Technical Score (accountant path)
    classified_category  text,        -- post-score: 'Accountant'|'Data Entry Operator'|'rejected'
    dnd_flagged          boolean NOT NULL DEFAULT false,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE(client_id, candidate_id)
);

-- ── handover_tokens ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS handover_tokens (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id  uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    token      text NOT NULL UNIQUE DEFAULT gen_random_uuid()::text,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz
);

-- ── outreach_batches ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS outreach_batches (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id         uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    batch_number      int  NOT NULL DEFAULT 1,
    candidate_ids     uuid[] NOT NULL DEFAULT '{}',
    sent_at           timestamptz,
    interested_count  int NOT NULL DEFAULT 0,
    total_sent        int NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now()
);
