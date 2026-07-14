CREATE TABLE IF NOT EXISTS candidate_overrides (
    candidate_id     uuid PRIMARY KEY REFERENCES candidates(id) ON DELETE CASCADE,
    is_active        boolean NOT NULL DEFAULT true,
    expected_salary  numeric,
    current_location text,
    working_radius   int,
    work_preference  text,
    job_type         text,
    notes            text,
    updated_at       timestamptz DEFAULT now()
);
