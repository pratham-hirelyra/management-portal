-- Migration 014: freeze classified_category on candidate_funnel_status
--
-- Previously, update_candidate_funnel_status() (fired AFTER UPDATE OF
-- form_submitted, evaluation_status ON candidates) recomputed BOTH status
-- and classified_category on the candidate's latest cohort row. That meant
-- a candidate who entered a batch as 'without_ai_score' (form filled, no AI
-- test yet) would get silently reclassified into 'with_ai_score' the moment
-- their AI evaluation completed — moving them into a different analytics
-- bucket than the one they actually entered with.
--
-- classified_category now reflects the cohort the candidate ENTERED with
-- (set once, at row-creation time by record_candidate_jd_sent) and never
-- changes again. status continues to update live to reflect the candidate's
-- progress (form_filled -> passed/failed) within that original bucket.
--
-- Run once: psql $DATABASE_URL -f migrations/014_freeze_funnel_classified_category.sql

CREATE OR REPLACE FUNCTION update_candidate_funnel_status()
RETURNS trigger AS $$
DECLARE
    v_status text;
BEGIN
    v_status := CASE
        WHEN NEW.evaluation_status ILIKE 'pass' THEN 'passed'
        WHEN NEW.evaluation_status ILIKE 'fail' THEN 'failed'
        WHEN NEW.form_submitted = true THEN 'form_filled'
        ELSE 'none'
    END;

    UPDATE candidate_funnel_status
    SET status = v_status, updated_at = now()
    WHERE id = (
        SELECT id FROM candidate_funnel_status
        WHERE candidate_id = NEW.id
        ORDER BY batch_date DESC, created_at DESC
        LIMIT 1
    )
    AND status != v_status;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── Backfill: restore classified_category for rows already mis-reclassified
-- by the old trigger.
--
-- A row is suspect if updated_at > created_at + 1s — the only thing that
-- moves updated_at on candidate_funnel_status is the old trigger reacting to
-- a later form_submitted/evaluation_status change on candidates. For each
-- such row, reconstruct form_submitted/evaluation_status AS THEY WERE at
-- cfs.created_at using candidate_events (which logs every change to both
-- fields with a timestamp), then recompute the entry category from that
-- historical state.
WITH state_at_creation AS (
    SELECT
        cfs.id,
        -- form_submitted value at cfs.created_at (NULL old/new values are
        -- legitimate "not yet" states — use EXISTS, not COALESCE, so a real
        -- NULL doesn't fall through to the candidate's current value)
        CASE
            WHEN EXISTS (
                SELECT 1 FROM candidate_events ce
                WHERE ce.candidate_id = cfs.candidate_id AND ce.field = 'form_submitted'
                  AND ce.created_at <= cfs.created_at
            ) THEN (
                SELECT (ce.new_value)::boolean FROM candidate_events ce
                WHERE ce.candidate_id = cfs.candidate_id AND ce.field = 'form_submitted'
                  AND ce.created_at <= cfs.created_at
                ORDER BY ce.created_at DESC LIMIT 1
            )
            WHEN EXISTS (
                SELECT 1 FROM candidate_events ce
                WHERE ce.candidate_id = cfs.candidate_id AND ce.field = 'form_submitted'
                  AND ce.created_at > cfs.created_at
            ) THEN (
                SELECT (ce.old_value)::boolean FROM candidate_events ce
                WHERE ce.candidate_id = cfs.candidate_id AND ce.field = 'form_submitted'
                  AND ce.created_at > cfs.created_at
                ORDER BY ce.created_at ASC LIMIT 1
            )
            ELSE c.form_submitted
        END AS form_submitted_then,
        -- evaluation_status value at cfs.created_at (same NULL-safe approach)
        CASE
            WHEN EXISTS (
                SELECT 1 FROM candidate_events ce
                WHERE ce.candidate_id = cfs.candidate_id AND ce.field = 'evaluation_status'
                  AND ce.created_at <= cfs.created_at
            ) THEN (
                SELECT ce.new_value FROM candidate_events ce
                WHERE ce.candidate_id = cfs.candidate_id AND ce.field = 'evaluation_status'
                  AND ce.created_at <= cfs.created_at
                ORDER BY ce.created_at DESC LIMIT 1
            )
            WHEN EXISTS (
                SELECT 1 FROM candidate_events ce
                WHERE ce.candidate_id = cfs.candidate_id AND ce.field = 'evaluation_status'
                  AND ce.created_at > cfs.created_at
            ) THEN (
                SELECT ce.old_value FROM candidate_events ce
                WHERE ce.candidate_id = cfs.candidate_id AND ce.field = 'evaluation_status'
                  AND ce.created_at > cfs.created_at
                ORDER BY ce.created_at ASC LIMIT 1
            )
            ELSE c.evaluation_status
        END AS evaluation_status_then,
        EXISTS (
            SELECT 1 FROM candidate_funnel_status cfs2
            WHERE cfs2.candidate_id = cfs.candidate_id AND cfs2.created_at < cfs.created_at
        ) AS has_prior
    FROM candidate_funnel_status cfs
    JOIN candidates c ON c.id = cfs.candidate_id
    WHERE cfs.classified_category = 'with_ai_score'
      AND cfs.updated_at > cfs.created_at + interval '1 second'
)
UPDATE candidate_funnel_status cfs
SET classified_category = CASE
        WHEN sac.evaluation_status_then ILIKE 'pass' OR sac.evaluation_status_then ILIKE 'fail' THEN 'with_ai_score'
        WHEN sac.form_submitted_then = true                                                      THEN 'without_ai_score'
        WHEN NOT sac.has_prior                                                                    THEN 'sourced'
        ELSE 'from_database'
    END
FROM state_at_creation sac
WHERE cfs.id = sac.id;
