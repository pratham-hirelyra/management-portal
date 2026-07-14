from fastapi import APIRouter, Depends, Query
import asyncpg
from database import get_conn

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

_STAGE_ORDER = {
    "lead": 1, "scraping": 2, "scraped": 3, "reachout_sent": 4,
    "interested": 5, "agreement_sent": 6, "onboarded": 7, "disqualified": 8,
    "churned": 9,
}


@router.get("/funnel")
async def get_funnel(conn: asyncpg.Connection = Depends(get_conn)):
    rows = await conn.fetch("SELECT stage, COUNT(*) AS count FROM clients GROUP BY stage")
    total = sum(r["count"] for r in rows)
    data = sorted(
        [
            {
                "stage": r["stage"],
                "count": r["count"],
                "percentage": round(r["count"] / total * 100, 1) if total else 0,
            }
            for r in rows
        ],
        key=lambda x: _STAGE_ORDER.get(x["stage"], 99),
    )
    return {"data": data, "total": total, "ok": True}


@router.get("/stuck")
async def get_stuck_clients(
    days: int = Query(7),
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        """
        SELECT c.* FROM clients c
        WHERE c.stage NOT IN ('onboarded', 'disqualified', 'churned')
        AND COALESCE(
            (SELECT MAX(pe.created_at) FROM pipeline_events pe WHERE pe.client_id = c.id),
            c.created_at
        ) < now() - make_interval(days => $1)
        ORDER BY c.updated_at ASC
        """,
        days,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}
