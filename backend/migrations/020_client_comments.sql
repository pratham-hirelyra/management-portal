-- RM comments / sourcing notes per client. Shown in a dedicated "Comments"
-- tab on the client detail page so RMs can record what to look for while
-- sourcing candidates (e.g. experience requirements beyond the standard fields).
CREATE TABLE IF NOT EXISTS client_comments (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    comment_text        text,
    experience_criteria text,
    created_by          text NOT NULL DEFAULT 'RM',
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS client_comments_client_id_idx
    ON client_comments (client_id, created_at DESC);
