"""
Customer Executive calling system.

Deliberately separate from the RM (Relationship Manager) system — RM
assignment is permanent and post-onboarding (see rm_service.py); CE
assignment is a daily call queue with shift/reschedule semantics, scoped to
pre-onboarding leads.

Pool (top_end only — bottom_end queue logic not yet defined):
  (clients.segment = 'employer_lead' AND clients.stage = 'interested')
  OR clients.segment = 'active_job_post'
  excluding stage IN ('onboarded', 'disqualified', 'churned')

Assignment is automatic (see ce_service.sweep_assign), load-balanced under
each CE's max_clients_per_day, mirroring rm_service.assign_rm's style.
"""
import uuid
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncpg
from database import get_conn

router = APIRouter(tags=["customer-executives"])


# ── Request models ───────────────────────────────────────────────────────────

class CECreate(BaseModel):
    name: str
    phone: Optional[str] = None
    pin: Optional[str] = None
    tier: str = "top_end"
    max_clients_per_day: int = 20
    is_active: bool = True


class CEUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    pin: Optional[str] = None
    tier: Optional[str] = None
    max_clients_per_day: Optional[int] = None
    is_active: Optional[bool] = None


class CEVerifyPin(BaseModel):
    pin: str


class MarkCalledBody(BaseModel):
    sentiment: str  # 'positive' | 'negative' | 'wrong_poc'
    comment: Optional[str] = None


class ShiftBody(BaseModel):
    call_date: date  # ISO date, e.g. "2026-08-01"


# ── Admin CRUD ────────────────────────────────────────────────────────────────

@router.get("/admin/ces")
async def list_ces(conn: asyncpg.Connection = Depends(get_conn)):
    rows = await conn.fetch(
        """
        SELECT
            ce.*,
            COUNT(a.id) FILTER (WHERE a.status = 'pending' AND a.call_date <= CURRENT_DATE) AS today_pending,
            COUNT(a.id) FILTER (WHERE a.status = 'called') AS total_called,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'positive') AS positive,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'negative') AS negative,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'wrong_poc') AS wrong_poc,
            COUNT(a.id) FILTER (WHERE cl.stage::text = 'onboarded') AS onboarded
        FROM customer_executives ce
        LEFT JOIN ce_assignments a ON a.ce_id = ce.id
        LEFT JOIN clients cl ON cl.id = a.client_id
        GROUP BY ce.id
        ORDER BY ce.name
        """
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.post("/admin/ces", status_code=201)
async def create_ce(body: CECreate, conn: asyncpg.Connection = Depends(get_conn)):
    if body.tier not in ("top_end", "bottom_end"):
        raise HTTPException(400, "tier must be 'top_end' or 'bottom_end'")
    row = await conn.fetchrow(
        """
        INSERT INTO customer_executives (name, phone, pin, tier, max_clients_per_day, is_active)
        VALUES ($1, $2, $3, $4, $5, $6) RETURNING *
        """,
        body.name, body.phone, body.pin, body.tier, body.max_clients_per_day, body.is_active,
    )
    return {"data": dict(row), "ok": True}


@router.patch("/admin/ces/{ce_id}")
async def update_ce(ce_id: uuid.UUID, body: CEUpdate, conn: asyncpg.Connection = Depends(get_conn)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    if "tier" in updates and updates["tier"] not in ("top_end", "bottom_end"):
        raise HTTPException(400, "tier must be 'top_end' or 'bottom_end'")
    keys = list(updates.keys())
    vals = list(updates.values())
    set_clause = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(keys))
    vals.append(ce_id)
    row = await conn.fetchrow(
        f"UPDATE customer_executives SET {set_clause}, updated_at = now() WHERE id = ${len(vals)} RETURNING *",
        *vals,
    )
    if not row:
        raise HTTPException(404, "Customer executive not found")
    return {"data": dict(row), "ok": True}


@router.delete("/admin/ces/{ce_id}")
async def delete_ce(ce_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    try:
        result = await conn.execute("DELETE FROM customer_executives WHERE id = $1", ce_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            409,
            "This executive has call history or an active queue — reassign their pending "
            "clients first (see /reassign), then deactivate instead of deleting to keep "
            "their call history intact.",
        )
    if result == "DELETE 0":
        raise HTTPException(404, "Customer executive not found")
    return {"ok": True}


@router.post("/admin/ces/{ce_id}/reassign")
async def reassign_pending(
    ce_id: uuid.UUID,
    to_ce_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Move all of this CE's still-uncalled assignments to another CE — e.g. when
    someone leaves, so their unworked queue doesn't just sit orphaned. Called
    assignments (history) are never moved — they stay permanently attributed to
    whoever actually made the call."""
    if ce_id == to_ce_id:
        raise HTTPException(400, "Can't reassign to the same executive")
    target = await conn.fetchrow("SELECT id, is_active FROM customer_executives WHERE id = $1", to_ce_id)
    if not target:
        raise HTTPException(404, "Target executive not found")
    rows = await conn.fetch(
        """
        UPDATE ce_assignments
        SET ce_id = $1, updated_at = now()
        WHERE ce_id = $2 AND status = 'pending'
        RETURNING id
        """,
        to_ce_id, ce_id,
    )
    return {"data": {"reassigned": len(rows)}, "ok": True}


@router.post("/admin/ces/{ce_id}/verify-pin")
async def verify_ce_pin(ce_id: uuid.UUID, body: CEVerifyPin, conn: asyncpg.Connection = Depends(get_conn)):
    row = await conn.fetchrow("SELECT pin FROM customer_executives WHERE id = $1", ce_id)
    if not row:
        raise HTTPException(404, "Customer executive not found")
    if not row["pin"]:
        raise HTTPException(403, "No PIN set for this executive")
    if row["pin"] != body.pin:
        raise HTTPException(401, "Incorrect PIN")
    return {"ok": True}


# ── Executive-facing ──────────────────────────────────────────────────────────

_ASSIGNMENT_SELECT = """
    SELECT
        a.id AS assignment_id, a.client_id, a.call_date, a.status, a.sentiment,
        a.comment, a.called_at, a.assigned_at, a.no_pickup_count, a.last_attempt_at,
        cl.company_name, cl.poc_name, cl.poc_phone, cl.job_title, cl.stage::text AS client_stage,
        cl.segment, cl.industry, COALESCE(cl.job_location, cl.location) AS location,
        GREATEST(
            cl.last_outreach_at,
            (SELECT MAX(wm.created_at) FROM whatsapp_messages wm
             WHERE cl.poc_phone IS NOT NULL AND RIGHT(wm.phone, 10) = RIGHT(cl.poc_phone, 10))
        ) AS last_engagement_at
    FROM ce_assignments a
    JOIN clients cl ON cl.id = a.client_id
"""


@router.get("/ce/{ce_id}/me")
async def get_ce_info(ce_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    row = await conn.fetchrow(
        "SELECT id, name, phone, tier, max_clients_per_day, is_active FROM customer_executives WHERE id = $1",
        ce_id,
    )
    if not row:
        raise HTTPException(404, "Customer executive not found")
    return {"data": dict(row), "ok": True}


@router.get("/ce/{ce_id}/queue")
async def get_ce_queue(ce_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    """Today's list: pending assignments whose call_date has arrived (today or overdue —
    overdue ones are exactly the carry-over from days they weren't called). Ordered so a
    no-pickup retry sinks behind anyone not yet attempted today (see /no-pickup)."""
    rows = await conn.fetch(
        _ASSIGNMENT_SELECT + """
        WHERE a.ce_id = $1 AND a.status = 'pending' AND a.call_date <= CURRENT_DATE
        ORDER BY a.call_date ASC, COALESCE(a.last_attempt_at, a.assigned_at) ASC
        """,
        ce_id,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.get("/ce/{ce_id}/search")
async def search_ce_assignments(ce_id: uuid.UUID, q: str, conn: asyncpg.Connection = Depends(get_conn)):
    """Search across ALL of this executive's assignments (any status/date) — e.g. a
    client calling back after being marked called, or scheduled for a future date."""
    if len(q.strip()) < 2:
        return {"data": [], "count": 0, "ok": True}
    pattern = f"%{q.strip()}%"
    rows = await conn.fetch(
        _ASSIGNMENT_SELECT + """
        WHERE a.ce_id = $1 AND (cl.company_name ILIKE $2 OR cl.poc_name ILIKE $2 OR cl.poc_phone ILIKE $2)
        ORDER BY a.call_date DESC
        LIMIT 30
        """,
        ce_id, pattern,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.get("/ce/{ce_id}/history")
async def get_ce_history(
    ce_id: uuid.UUID,
    limit: int = 100,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """All calls this executive has already made, most recent first."""
    rows = await conn.fetch(
        _ASSIGNMENT_SELECT + """
        WHERE a.ce_id = $1 AND a.status = 'called'
        ORDER BY a.called_at DESC
        LIMIT $2
        """,
        ce_id, limit,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


_TERMINAL_CLIENT_STAGES = {"onboarded", "disqualified", "churned"}


async def _check_not_terminal(conn: asyncpg.Connection, assignment_id: uuid.UUID) -> None:
    """A client can leave the eligible pool independently of the CE (e.g. they
    onboard through another channel entirely). Block further call actions on
    that assignment once that's happened — nothing left to do."""
    stage = await conn.fetchval(
        """
        SELECT cl.stage::text FROM ce_assignments a
        JOIN clients cl ON cl.id = a.client_id
        WHERE a.id = $1
        """,
        assignment_id,
    )
    if stage in _TERMINAL_CLIENT_STAGES:
        raise HTTPException(409, f"Client is already {stage} — no further action needed")


_NO_PICKUP_LIMIT = 2  # after this many same-day no-pickups, give up for today and push to tomorrow


@router.post("/ce/{ce_id}/assignments/{assignment_id}/no-pickup")
async def mark_no_pickup(
    ce_id: uuid.UUID,
    assignment_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Client didn't answer. 1st time today: sink to the back of today's queue
    (via last_attempt_at, read by the queue's ORDER BY) so other not-yet-tried
    clients get called first, then this one gets retried once those are done.
    2nd time today (_NO_PICKUP_LIMIT): give up for today, push call_date to
    tomorrow, and reset the counter for a fresh attempt count that day.
    """
    await _check_not_terminal(conn, assignment_id)
    current = await conn.fetchrow(
        "SELECT no_pickup_count FROM ce_assignments WHERE id = $1 AND ce_id = $2 AND status = 'pending'",
        assignment_id, ce_id,
    )
    if not current:
        raise HTTPException(404, "Assignment not found")

    next_count = current["no_pickup_count"] + 1
    if next_count >= _NO_PICKUP_LIMIT:
        row = await conn.fetchrow(
            """
            UPDATE ce_assignments
            SET no_pickup_count = 0, last_attempt_at = now(),
                call_date = CURRENT_DATE + 1, updated_at = now()
            WHERE id = $1
            RETURNING *
            """,
            assignment_id,
        )
        return {"data": dict(row), "moved_to_tomorrow": True, "ok": True}

    row = await conn.fetchrow(
        """
        UPDATE ce_assignments
        SET no_pickup_count = $1, last_attempt_at = now(), updated_at = now()
        WHERE id = $2
        RETURNING *
        """,
        next_count, assignment_id,
    )
    return {"data": dict(row), "moved_to_tomorrow": False, "ok": True}


@router.post("/ce/{ce_id}/assignments/{assignment_id}/call")
async def mark_called(
    ce_id: uuid.UUID,
    assignment_id: uuid.UUID,
    body: MarkCalledBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    if body.sentiment not in ("positive", "negative", "wrong_poc"):
        raise HTTPException(400, "sentiment must be 'positive', 'negative', or 'wrong_poc'")
    await _check_not_terminal(conn, assignment_id)
    row = await conn.fetchrow(
        """
        UPDATE ce_assignments
        SET status = 'called', sentiment = $1, comment = $2, called_at = now(), updated_at = now()
        WHERE id = $3 AND ce_id = $4
        RETURNING *
        """,
        body.sentiment, body.comment, assignment_id, ce_id,
    )
    if not row:
        raise HTTPException(404, "Assignment not found")

    # Org-wide visibility into scraping accuracy — surface it on the client
    # record itself (existing labels mechanism already used for e.g. 'Opted
    # Out'), not just buried in this one CE's call log.
    if body.sentiment == "wrong_poc":
        await conn.execute(
            """
            UPDATE clients
            SET labels = array_append(array_remove(COALESCE(labels, '{}'), 'Wrong POC'), 'Wrong POC'),
                updated_at = now()
            WHERE id = $1
            """,
            row["client_id"],
        )

    return {"data": dict(row), "ok": True}


@router.post("/ce/{ce_id}/assignments/{assignment_id}/shift")
async def shift_call_date(
    ce_id: uuid.UUID,
    assignment_id: uuid.UUID,
    body: ShiftBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    await _check_not_terminal(conn, assignment_id)
    row = await conn.fetchrow(
        """
        UPDATE ce_assignments
        SET call_date = $1, no_pickup_count = 0, updated_at = now()
        WHERE id = $2 AND ce_id = $3 AND status = 'pending'
        RETURNING *
        """,
        body.call_date, assignment_id, ce_id,
    )
    if not row:
        raise HTTPException(404, "Assignment not found (or already called)")
    return {"data": dict(row), "ok": True}


@router.get("/ce/{ce_id}/dashboard")
async def get_ce_dashboard(ce_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    row = await conn.fetchrow(
        """
        SELECT
            COUNT(a.id) FILTER (WHERE a.status = 'called') AS total_called,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'positive') AS positive,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'negative') AS negative,
            COUNT(a.id) FILTER (WHERE a.sentiment = 'wrong_poc') AS wrong_poc,
            COUNT(a.id) FILTER (WHERE cl.stage::text = 'onboarded') AS onboarded,
            COUNT(a.id) FILTER (WHERE a.status = 'pending' AND a.call_date <= CURRENT_DATE) AS today_pending
        FROM ce_assignments a
        JOIN clients cl ON cl.id = a.client_id
        WHERE a.ce_id = $1
        """,
        ce_id,
    )
    return {"data": dict(row), "ok": True}


@router.post("/ce/{ce_id}/sweep-assign")
async def trigger_sweep_assign(ce_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    """
    Manual on-demand trigger for ce_service.sweep_assign — the same
    auto-assignment sweep that otherwise only runs once a day inside the
    bundled cron job (routers/cron.py). Assignment is global/load-balanced
    across all active top_end CEs, not scoped to this ce_id — the id in the
    path is just so the CE's own page can trigger "get me more clients to
    call" without an admin having to wait for the next scheduled run.
    """
    ce = await conn.fetchval("SELECT 1 FROM customer_executives WHERE id = $1", ce_id)
    if not ce:
        raise HTTPException(404, "Customer executive not found")

    from services.ce_service import sweep_assign
    result = await sweep_assign(conn)
    return {"data": result, "ok": True}
