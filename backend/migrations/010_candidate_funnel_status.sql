-- Migration 010: candidate_funnel_status — cohort-based candidate analytics
--
-- One row per (candidate, client_candidate_mapping). batch_date = the date
-- that mapping was created and never changes again. status/classified_category
-- reflect the candidate's funnel progress and only update on the candidate's
-- SINGLE MOST RECENT row (highest batch_date) — older rows freeze permanently
-- once a newer mapping exists, preserving a true historical record of how far
-- each cohort got before the candidate moved into a new batch.
--
-- classified_category:
--   sourced          -> first-ever mapping for this candidate, no form/AI yet
--   from_database     -> not first mapping, still no form/AI yet
--   without_ai_score  -> form submitted, AI evaluation not completed
--   with_ai_score     -> AI evaluation completed (passed or failed)
--
-- Run once: psql $DATABASE_URL -f migrations/010_candidate_funnel_status.sql

CREATE TABLE IF NOT EXISTS candidate_funnel_status (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id         uuid NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    mapping_id           uuid NOT NULL UNIQUE REFERENCES client_candidate_mappings(id) ON DELETE CASCADE,
    batch_date           date NOT NULL,
    status               text NOT NULL,
    classified_category  text NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS candidate_funnel_status_candidate_idx
    ON candidate_funnel_status (candidate_id, batch_date);

CREATE INDEX IF NOT EXISTS candidate_funnel_status_breakdown_idx
    ON candidate_funnel_status (batch_date, classified_category);

-- ── New mapping created -> insert a new cohort row for the candidate ─────────
CREATE OR REPLACE FUNCTION insert_candidate_funnel_status()
RETURNS trigger AS $$
DECLARE
    v_status    text;
    v_category  text;
    v_has_prior boolean;
BEGIN
    SELECT
        CASE
            WHEN c.evaluation_status ILIKE 'pass' THEN 'passed'
            WHEN c.evaluation_status ILIKE 'fail' THEN 'failed'
            WHEN c.form_submitted = true THEN 'form_filled'
            ELSE 'none'
        END
    INTO v_status
    FROM candidates c WHERE c.id = NEW.candidate_id;

    SELECT EXISTS (
        SELECT 1 FROM candidate_funnel_status WHERE candidate_id = NEW.candidate_id
    ) INTO v_has_prior;

    v_category := CASE
        WHEN v_status IN ('passed', 'failed') THEN 'with_ai_score'
        WHEN v_status = 'form_filled'         THEN 'without_ai_score'
        WHEN NOT v_has_prior                  THEN 'sourced'
        ELSE 'from_database'
    END;

    INSERT INTO candidate_funnel_status (candidate_id, mapping_id, batch_date, status, classified_category)
    VALUES (NEW.candidate_id, NEW.id, DATE(NEW.created_at AT TIME ZONE 'Asia/Kolkata'), v_status, v_category);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_insert_candidate_funnel_status ON client_candidate_mappings;
CREATE TRIGGER trg_insert_candidate_funnel_status
AFTER INSERT ON client_candidate_mappings
FOR EACH ROW EXECUTE FUNCTION insert_candidate_funnel_status();

-- ── Candidate progresses -> update only their latest (live) cohort row ──────
CREATE OR REPLACE FUNCTION update_candidate_funnel_status()
RETURNS trigger AS $$
DECLARE
    v_status   text;
    v_category text;
BEGIN
    v_status := CASE
        WHEN NEW.evaluation_status ILIKE 'pass' THEN 'passed'
        WHEN NEW.evaluation_status ILIKE 'fail' THEN 'failed'
        WHEN NEW.form_submitted = true THEN 'form_filled'
        ELSE 'none'
    END;

    v_category := CASE
        WHEN v_status IN ('passed', 'failed') THEN 'with_ai_score'
        WHEN v_status = 'form_filled'         THEN 'without_ai_score'
        ELSE 'from_database'
    END;

    UPDATE candidate_funnel_status
    SET status = v_status, classified_category = v_category, updated_at = now()
    WHERE id = (
        SELECT id FROM candidate_funnel_status
        WHERE candidate_id = NEW.id
        ORDER BY batch_date DESC, created_at DESC
        LIMIT 1
    )
    AND (status != v_status OR classified_category != v_category);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_candidate_funnel_status ON candidates;
CREATE TRIGGER trg_update_candidate_funnel_status
AFTER UPDATE OF form_submitted, evaluation_status ON candidates
FOR EACH ROW EXECUTE FUNCTION update_candidate_funnel_status();

-- ── Backfill: one row per existing candidate, anchored to their most recent
-- mapping (real batch_date), seeded with their CURRENT status/category. This
-- becomes their live row going forward. No candidate qualifies as 'sourced'
-- in the backfill since 'sourced' only applies to a brand-new candidate's
-- first-ever mapping going forward.
INSERT INTO candidate_funnel_status (candidate_id, mapping_id, batch_date, status, classified_category)
SELECT DISTINCT ON (ccm.candidate_id)
    ccm.candidate_id,
    ccm.id,
    DATE(ccm.created_at AT TIME ZONE 'Asia/Kolkata'),
    CASE
        WHEN c.evaluation_status ILIKE 'pass' THEN 'passed'
        WHEN c.evaluation_status ILIKE 'fail' THEN 'failed'
        WHEN c.form_submitted = true THEN 'form_filled'
        ELSE 'none'
    END,
    CASE
        WHEN c.evaluation_status ILIKE 'pass' OR c.evaluation_status ILIKE 'fail' THEN 'with_ai_score'
        WHEN c.form_submitted = true THEN 'without_ai_score'
        ELSE 'from_database'
    END
FROM client_candidate_mappings ccm
JOIN candidates c ON c.id = ccm.candidate_id
ORDER BY ccm.candidate_id, ccm.created_at DESC
ON CONFLICT (mapping_id) DO NOTHING;
