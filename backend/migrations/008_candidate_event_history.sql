-- Migration 008: track candidate-side state transitions so analytics can
-- answer "what did the funnel look like as of date X" — mirrors the existing
-- pipeline_events / log_client_stage_change pattern used for clients.stage.
-- Run once: psql $DATABASE_URL -f migrations/008_candidate_event_history.sql

CREATE TABLE IF NOT EXISTS candidate_events (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id uuid NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    mapping_id   uuid REFERENCES client_candidate_mappings(id) ON DELETE CASCADE,
    field        text NOT NULL,
    old_value    text,
    new_value    text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS candidate_events_lookup_idx
    ON candidate_events (candidate_id, field, created_at);

-- Tracks the candidate-table fields the analytics funnel derives
-- form / ai_call / passed / junior / senior / not_looking from.
CREATE OR REPLACE FUNCTION log_candidate_field_change()
RETURNS trigger AS $$
BEGIN
  IF OLD.form_submitted IS DISTINCT FROM NEW.form_submitted THEN
    INSERT INTO candidate_events (candidate_id, field, old_value, new_value)
    VALUES (NEW.id, 'form_submitted', OLD.form_submitted::text, NEW.form_submitted::text);
  END IF;
  IF OLD.evaluation_status IS DISTINCT FROM NEW.evaluation_status THEN
    INSERT INTO candidate_events (candidate_id, field, old_value, new_value)
    VALUES (NEW.id, 'evaluation_status', OLD.evaluation_status, NEW.evaluation_status);
  END IF;
  IF OLD.category IS DISTINCT FROM NEW.category THEN
    INSERT INTO candidate_events (candidate_id, field, old_value, new_value)
    VALUES (NEW.id, 'category', OLD.category, NEW.category);
  END IF;
  IF OLD.work_preference IS DISTINCT FROM NEW.work_preference THEN
    INSERT INTO candidate_events (candidate_id, field, old_value, new_value)
    VALUES (NEW.id, 'work_preference', OLD.work_preference, NEW.work_preference);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_candidate_field_change ON candidates;
CREATE TRIGGER trg_candidate_field_change
AFTER UPDATE ON candidates
FOR EACH ROW EXECUTE FUNCTION log_candidate_field_change();

-- Tracks mapping stage transitions (drives not_interested / positive).
CREATE OR REPLACE FUNCTION log_mapping_stage_change()
RETURNS trigger AS $$
BEGIN
  IF OLD.stage IS DISTINCT FROM NEW.stage THEN
    INSERT INTO candidate_events (candidate_id, mapping_id, field, old_value, new_value)
    VALUES (NEW.candidate_id, NEW.id, 'mapping_stage', OLD.stage::text, NEW.stage::text);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_mapping_stage_change ON client_candidate_mappings;
CREATE TRIGGER trg_mapping_stage_change
AFTER UPDATE ON client_candidate_mappings
FOR EACH ROW EXECUTE FUNCTION log_mapping_stage_change();

-- Reconstructs "what was this field's value as of timestamp p_as_of":
--   1. latest event at/before p_as_of -> its new_value (already changed by then)
--   2. else earliest event after p_as_of -> its old_value (hadn't changed yet,
--      but we know what it changed FROM)
--   3. else no events ever -> field never changed -> current value applies
CREATE OR REPLACE FUNCTION candidate_field_as_of(
    p_candidate_id uuid,
    p_field        text,
    p_mapping_id   uuid,
    p_as_of        timestamptz,
    p_current      text
) RETURNS text AS $$
    SELECT COALESCE(
        (SELECT new_value FROM candidate_events
         WHERE candidate_id = p_candidate_id AND field = p_field
           AND (p_mapping_id IS NULL OR mapping_id = p_mapping_id)
           AND created_at <= p_as_of
         ORDER BY created_at DESC LIMIT 1),
        (SELECT old_value FROM candidate_events
         WHERE candidate_id = p_candidate_id AND field = p_field
           AND (p_mapping_id IS NULL OR mapping_id = p_mapping_id)
           AND created_at > p_as_of
         ORDER BY created_at ASC LIMIT 1),
        p_current
    );
$$ LANGUAGE sql STABLE;
