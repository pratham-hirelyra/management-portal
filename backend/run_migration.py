"""
One-time migration script. Run with: python run_migration.py
"""
import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

SQL = """
ALTER TABLE candidate_overrides ADD COLUMN IF NOT EXISTS labels text[] DEFAULT '{}';
ALTER TABLE candidate_overrides ADD COLUMN IF NOT EXISTS photo_url text;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS labels text[] DEFAULT '{}';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS feedback_token uuid DEFAULT gen_random_uuid();
ALTER TABLE clients ADD COLUMN IF NOT EXISTS sourcing_count_needed int;
ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS feedback_client_reason text;

UPDATE clients SET feedback_token = gen_random_uuid() WHERE feedback_token IS NULL;

CREATE TABLE IF NOT EXISTS outreach_batches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    batch_number int NOT NULL,
    candidate_ids text[],
    sent_at timestamptz DEFAULT now(),
    total_sent int DEFAULT 0,
    interested_count int DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jd_send_queue (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id    uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    batch_number int  NOT NULL,
    mapping_rows jsonb NOT NULL,
    components   jsonb NOT NULL,
    scheduled_at timestamptz NOT NULL,
    sent_at      timestamptz,
    status       text NOT NULL DEFAULT 'pending',
    error_msg    text,
    created_at   timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS jd_send_queue_due_idx
    ON jd_send_queue (scheduled_at)
    WHERE status = 'pending';
"""

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await conn.execute(SQL)
        print("Migration complete.")
    finally:
        await conn.close()

asyncio.run(main())
