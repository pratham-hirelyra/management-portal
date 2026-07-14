"""
Shared fixtures and seed helpers for all tests.
"""

import json
import os
import uuid

import asyncpg
import pytest
import pytest_asyncio
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

DATABASE_URL = os.environ["DATABASE_URL"]

# ── One-time DDL (committed for real, run once per session) ───────────────────

@pytest.fixture(scope="session")
def _ensure_columns():
    """
    Synchronous driver: spin up a temp loop to run the DDL once.
    Avoids the session-vs-function loop-scope mismatch entirely.
    """
    import asyncio

    async def _run():
        conn = await asyncpg.connect(dsn=DATABASE_URL)
        try:
            for ddl in [
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS matching_state text",
                "ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS slot_booking_sent  boolean DEFAULT false",
                "ALTER TABLE client_candidate_mappings ADD COLUMN IF NOT EXISTS waiting_message_sent boolean DEFAULT false",
            ]:
                await conn.execute(ddl)
        finally:
            await conn.close()

    asyncio.run(_run())


# ── Per-test connection in a rolled-back transaction ──────────────────────────

@pytest_asyncio.fixture
async def conn(_ensure_columns):
    """Each test gets a fresh direct connection wrapped in a rolled-back transaction."""
    c = await asyncpg.connect(dsn=DATABASE_URL)
    tr = c.transaction()
    await tr.start()
    yield c
    await tr.rollback()
    await c.close()


# ── Seed helpers ──────────────────────────────────────────────────────────────

async def seed_client(conn: asyncpg.Connection, **overrides) -> dict:
    defaults = {
        "company_name": "Test Co",
        "job_title": "Senior Accountant",
        "max_salary": 30_000.0,
        "job_type": "Full Time",
        "office_type": "Corporate",
        "tools_requirements": "Tally",
        "gst_tds": json.dumps({"gst": "Filing", "tds": "Filing"}),
        "poc_phone": "+919999900001",
        "poc_name": "Test POC",
        "location_lat": 19.076,
        "location_lng": 72.877,
        "stage": "onboarded",
        "recruiter_slots": None,
    }
    d = {**defaults, **overrides}
    if isinstance(d.get("gst_tds"), dict):
        d["gst_tds"] = json.dumps(d["gst_tds"])
    if isinstance(d.get("recruiter_slots"), list):
        d["recruiter_slots"] = json.dumps(d["recruiter_slots"])

    row = await conn.fetchrow(
        """
        INSERT INTO clients (
            company_name, job_title, max_salary, job_type, office_type,
            tools_requirements, gst_tds, poc_phone, poc_name,
            location_lat, location_lng, stage, recruiter_slots
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12::client_stage,$13::jsonb)
        RETURNING *
        """,
        d["company_name"], d["job_title"], d["max_salary"], d["job_type"],
        d["office_type"], d["tools_requirements"], d["gst_tds"],
        d["poc_phone"], d["poc_name"], d["location_lat"], d["location_lng"],
        d["stage"], d["recruiter_slots"],
    )
    return dict(row)


async def seed_candidate(conn: asyncpg.Connection, phone_suffix: str, **overrides) -> dict:
    defaults = {
        "name": f"Test Cand {phone_suffix}",
        "phone": f"+9999{phone_suffix}",
        "category": "Senior Accountant",
        "technical_evaluation_score": 60.0,
        "evaluation_status": "Pass",
        "cv_url": None,
        "current_salary": 15_000.0,
        "experience": "3 years",
        "job_type": "Full Time",
        "work_preference": "Corporate, Industrial",
        "location_lat": 19.076,
        "location_lng": 72.877,
        "working_radius": 20,
        "is_active": True,
        "other_details": json.dumps({"tools": "Tally, Excel", "gst": "GST Filing", "tds": "TDS Filing"}),
    }
    d = {**defaults, **overrides}
    if isinstance(d.get("other_details"), dict):
        d["other_details"] = json.dumps(d["other_details"])

    row = await conn.fetchrow(
        """
        INSERT INTO candidates (
            name, phone, category, technical_evaluation_score,
            evaluation_status, cv_url, current_salary, experience,
            job_type, work_preference, location_lat, location_lng,
            working_radius, is_active, other_details
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb)
        RETURNING *
        """,
        d["name"], d["phone"], d["category"], d["technical_evaluation_score"],
        d["evaluation_status"], d["cv_url"], d["current_salary"], d["experience"],
        d["job_type"], d["work_preference"], d["location_lat"], d["location_lng"],
        d["working_radius"], d["is_active"], d["other_details"],
    )
    return dict(row)


async def seed_mapping(
    conn: asyncpg.Connection,
    client_id: uuid.UUID,
    candidate_id: uuid.UUID,
    stage: str = "interested",
    **overrides,
) -> dict:
    defaults = {
        "match_score": 75,
        "slot_booking_sent": False,
        "waiting_message_sent": False,
    }
    d = {**defaults, **overrides}
    row = await conn.fetchrow(
        """
        INSERT INTO client_candidate_mappings (
            client_id, candidate_id, stage, match_score,
            slot_booking_sent, waiting_message_sent
        )
        VALUES ($1,$2,$3::mapping_stage,$4,$5,$6)
        RETURNING *
        """,
        client_id, candidate_id, stage,
        d["match_score"], d["slot_booking_sent"], d["waiting_message_sent"],
    )
    return dict(row)
