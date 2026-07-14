import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.encoders import jsonable_encoder
import asyncpg
from database import get_conn, get_pool
from services.scraper_service import call_scraper
import os

router = APIRouter(prefix="/clients", tags=["scraping"])

VALID_SCRAPER_TYPES = {"job_portal", "apollo", "google_maps", "custom"}

SCRAPER_ENV_KEYS = {
    "job_portal": "SCRAPER_JOB_PORTAL_URL",
    "apollo":     "SCRAPER_APOLLO_URL",
    "google_maps":"SCRAPER_GOOGLE_MAPS_URL",
    "custom":     "SCRAPER_CUSTOM_URL",
}


_ALLOWED_CITIES = ("ahmedabad", "gandhinagar")

def _is_allowed_location(client_data: dict) -> bool:
    for field in ("city", "location", "job_location"):
        val = (client_data.get(field) or "").lower()
        if any(city in val for city in _ALLOWED_CITIES):
            return True
    return False


async def _run_scraper(job_id: str, client_id: str, scraper_type: str, client_data: dict):
    scraper_url = os.environ.get(SCRAPER_ENV_KEYS[scraper_type], "")
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            result = await call_scraper(scraper_url, client_data)

            await conn.execute(
                """
                UPDATE scraping_jobs
                SET status = 'completed', result = $1, completed_at = now()
                WHERE id = $2
                """,
                result,
                uuid.UUID(job_id),
            )

            if not _is_allowed_location(client_data):
                await conn.execute(
                    "UPDATE clients SET stage = 'disqualified'::client_stage, updated_at = now() WHERE id = $1",
                    uuid.UUID(client_id),
                )
                return

            if result.get("poc_phone"):
                await conn.execute(
                    """
                    UPDATE clients
                    SET stage = 'scraped'::client_stage, poc_phone = $1, updated_at = now()
                    WHERE id = $2
                    """,
                    result["poc_phone"],
                    uuid.UUID(client_id),
                )
            else:
                await conn.execute(
                    "UPDATE clients SET stage = 'scraped'::client_stage, updated_at = now() WHERE id = $1",
                    uuid.UUID(client_id),
                )

        except Exception as e:
            await conn.execute(
                """
                UPDATE scraping_jobs
                SET status = 'failed', error_msg = $1, completed_at = now()
                WHERE id = $2
                """,
                str(e),
                uuid.UUID(job_id),
            )


@router.post("/{client_id}/scrape/{scraper_type}")
async def trigger_scrape(
    client_id: uuid.UUID,
    scraper_type: str,
    background_tasks: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_conn),
):
    if scraper_type not in VALID_SCRAPER_TYPES:
        raise HTTPException(400, f"Invalid scraper type. Must be one of: {', '.join(sorted(VALID_SCRAPER_TYPES))}")

    job = await conn.fetchrow(
        """
        INSERT INTO scraping_jobs (client_id, scraper_type, status, triggered_at)
        VALUES ($1, $2, 'running', now())
        RETURNING *
        """,
        client_id,
        scraper_type,
    )

    await conn.execute(
        "UPDATE clients SET stage = 'scraping'::client_stage, updated_at = now() WHERE id = $1",
        client_id,
    )

    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    background_tasks.add_task(
        _run_scraper,
        str(job["id"]),
        str(client_id),
        scraper_type,
        jsonable_encoder(dict(client)),
    )

    return {"data": {"job_id": str(job["id"]), "status": "running"}, "ok": True}


@router.get("/{client_id}/scrape")
async def get_scraping_jobs(
    client_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (scraper_type) *
        FROM scraping_jobs
        WHERE client_id = $1
        ORDER BY scraper_type, triggered_at DESC
        """,
        client_id,
    )
    return {"data": [dict(r) for r in rows], "ok": True}
