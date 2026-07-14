-- Migration 011: candidate_funnel_status — anchor cohort to JD-send, not mapping creation
--
-- Previously, a candidate_funnel_status row was created the moment a candidate
-- was MAPPED to a client (trg_insert_candidate_funnel_status on
-- client_candidate_mappings INSERT). That meant candidates who were mapped but
-- never actually contacted (no JD sent yet) showed up in the analytics
-- breakdown.
--
-- Going forward, the row is created when the initial JD-share message is
-- actually sent (record_candidate_jd_sent, called explicitly from the JD-send
-- endpoints), with batch_date = the date the JD was sent.
--
-- The update_candidate_funnel_status trigger (on candidates form/eval changes)
-- is unchanged — it still updates whichever row is the candidate's latest.
--
-- Run once: psql $DATABASE_URL -f migrations/011_funnel_status_on_jd_sent.sql

-- ── Remove the mapping-time trigger + function ───────────────────────────────
DROP TRIGGER IF EXISTS trg_insert_candidate_funnel_status ON client_candidate_mappings;
DROP FUNCTION IF EXISTS insert_candidate_funnel_status();

-- ── New explicit function: call when the initial JD-share message is sent ──
CREATE OR REPLACE FUNCTION record_candidate_jd_sent(p_candidate_id uuid, p_mapping_id uuid)
RETURNS void AS $$
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
    FROM candidates c WHERE c.id = p_candidate_id;

    SELECT EXISTS (
        SELECT 1 FROM candidate_funnel_status WHERE candidate_id = p_candidate_id
    ) INTO v_has_prior;

    v_category := CASE
        WHEN v_status IN ('passed', 'failed') THEN 'with_ai_score'
        WHEN v_status = 'form_filled'         THEN 'without_ai_score'
        WHEN NOT v_has_prior                  THEN 'sourced'
        ELSE 'from_database'
    END;

    INSERT INTO candidate_funnel_status (candidate_id, mapping_id, batch_date, status, classified_category)
    VALUES (p_candidate_id, p_mapping_id, (now() AT TIME ZONE 'Asia/Kolkata')::date, v_status, v_category)
    ON CONFLICT (mapping_id) DO NOTHING;
END;
$$ LANGUAGE plpgsql;
