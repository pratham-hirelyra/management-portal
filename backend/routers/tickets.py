"""
Ticketing system for the bottom_end CE tier. Thin router — all business logic
lives in services/ticket_service.py (mirrors routers/ce.py + services/ce_service.py's
split). See services/ticket_service.py's module docstring for the bigger picture.
"""
import uuid
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_conn
import services.ticket_service as ticket_service

router = APIRouter(tags=["tickets"])


# ── Request models ───────────────────────────────────────────────────────────

class TicketCreateBody(BaseModel):
    phone: str
    channel: str  # 'client' | 'candidate'
    queue_code: str
    category_code: str
    subject: str | None = None
    priority: str | None = None
    reason: str | None = None
    client_id: uuid.UUID | None = None
    candidate_id: uuid.UUID | None = None
    mapping_id: uuid.UUID | None = None
    created_by_ce_id: uuid.UUID | None = None


class ClaimBody(BaseModel):
    ce_id: uuid.UUID


class ReplyBody(BaseModel):
    ce_id: uuid.UUID
    text: str


class NoteBody(BaseModel):
    ce_id: uuid.UUID
    text: str


class ReassignBody(BaseModel):
    to_ce_id: uuid.UUID


class ResolveBody(BaseModel):
    ce_id: uuid.UUID
    resolution_note: str | None = None


class ReopenBody(BaseModel):
    ce_id: uuid.UUID
    reason: str | None = None


class TicketPatchBody(BaseModel):
    priority: str | None = None
    category_id: uuid.UUID | None = None
    subject: str | None = None


class QueueCreate(BaseModel):
    name: str
    code: str
    description: str | None = None
    is_active: bool = True


class QueueUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    is_active: bool | None = None


class CategoryCreate(BaseModel):
    queue_id: uuid.UUID
    name: str
    code: str
    is_active: bool = True


class CategoryUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    is_active: bool | None = None


class QueueAccessBody(BaseModel):
    queue_ids: list[uuid.UUID]


# ── Shared select ─────────────────────────────────────────────────────────────

_LIST_SELECT = """
    SELECT
        t.*, tq.name AS queue_name, tq.code AS queue_code,
        tc.name AS category_name, tc.code AS category_code,
        ce.name AS claimed_by_name,
        cl.company_name, cl.job_title AS client_job_title, cl.stage::text AS client_stage,
        cd.name AS candidate_name
    FROM tickets t
    JOIN ticket_queues tq ON tq.id = t.queue_id
    JOIN ticket_categories tc ON tc.id = t.category_id
    LEFT JOIN customer_executives ce ON ce.id = t.claimed_by
    LEFT JOIN clients cl ON cl.id = t.client_id
    LEFT JOIN candidates cd ON cd.id = t.candidate_id
"""

_FROM_JOINS = """
    FROM tickets t
    JOIN ticket_queues tq ON tq.id = t.queue_id
    JOIN ticket_categories tc ON tc.id = t.category_id
    LEFT JOIN customer_executives ce ON ce.id = t.claimed_by
    LEFT JOIN clients cl ON cl.id = t.client_id
    LEFT JOIN candidates cd ON cd.id = t.candidate_id
"""


# ── Executive-facing: list / detail / timeline ────────────────────────────────

@router.get("/tickets")
async def list_tickets(
    ce_id: uuid.UUID | None = None,
    queue_id: uuid.UUID | None = None,
    status: str | None = None,
    priority: str | None = None,
    category_id: uuid.UUID | None = None,
    assigned_to: uuid.UUID | None = None,
    source: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    tab: str | None = None,   # 'unclaimed' | 'mine' | 'all' | 'resolved'
    page: int = 1,
    page_size: int = 50,
    conn: asyncpg.Connection = Depends(get_conn),
):
    conditions = []
    params: list = []

    def _add(value) -> int:
        params.append(value)
        return len(params)

    if ce_id:
        idx = _add(ce_id)
        conditions.append(f"t.queue_id IN (SELECT queue_id FROM executive_queue_access WHERE ce_id = ${idx})")

    if queue_id:
        conditions.append(f"t.queue_id = ${_add(queue_id)}")
    if status:
        conditions.append(f"t.status = ${_add(status)}")
    if priority:
        conditions.append(f"t.priority = ${_add(priority)}")
    if category_id:
        conditions.append(f"t.category_id = ${_add(category_id)}")
    if assigned_to:
        conditions.append(f"t.claimed_by = ${_add(assigned_to)}")
    if source:
        conditions.append(f"t.source = ${_add(source)}")
    if date_from:
        conditions.append(f"t.created_at >= ${_add(date_from)}")
    if date_to:
        conditions.append(f"t.created_at < (${_add(date_to)}::date + 1)")

    if search:
        idx = _add(f"%{search}%")
        conditions.append(
            f"(t.id::text ILIKE ${idx} OR t.phone ILIKE ${idx} OR cl.company_name ILIKE ${idx} "
            f"OR cd.name ILIKE ${idx} OR ce.name ILIKE ${idx} OR tc.name ILIKE ${idx} OR t.status ILIKE ${idx})"
        )

    if tab == "unclaimed":
        conditions.append("t.claimed_by IS NULL AND t.status <> 'RESOLVED'")
    elif tab == "mine":
        if not ce_id:
            raise HTTPException(400, "tab=mine requires ce_id")
        conditions.append(f"t.claimed_by = ${_add(ce_id)}")
    elif tab == "resolved":
        conditions.append("t.status = 'RESOLVED'")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total = await conn.fetchval(f"SELECT COUNT(*) {_FROM_JOINS} {where}", *params)

    order = (
        "ORDER BY (t.claimed_by IS NULL) DESC, "
        "CASE t.priority WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END, "
        "t.created_at DESC"
    )
    offset = (page - 1) * page_size
    limit_idx = _add(page_size)
    offset_idx = _add(offset)
    rows = await conn.fetch(
        f"{_LIST_SELECT} {where} {order} LIMIT ${limit_idx} OFFSET ${offset_idx}", *params,
    )
    return {"data": [dict(r) for r in rows], "count": total, "ok": True}


@router.get("/tickets/analytics")
async def ticket_analytics(
    queue_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    conn: asyncpg.Connection = Depends(get_conn),
):
    base_conditions = []
    base_params: list = []
    if queue_id:
        base_params.append(queue_id)
        base_conditions.append(f"t.queue_id = ${len(base_params)}")
    if date_from:
        base_params.append(date_from)
        base_conditions.append(f"t.created_at >= ${len(base_params)}")
    if date_to:
        base_params.append(date_to)
        base_conditions.append(f"t.created_at < (${len(base_params)}::date + 1)")

    def _where(extra: str | None = None) -> str:
        conds = list(base_conditions)
        if extra:
            conds.append(extra)
        return f"WHERE {' AND '.join(conds)}" if conds else ""

    open_clause = "t.status <> 'RESOLVED'"
    resolved_today_clause = "t.status = 'RESOLVED' AND t.resolved_at::date = now()::date"
    open_count = await conn.fetchval(
        f"SELECT COUNT(*) FROM tickets t {_where(open_clause)}", *base_params,
    )
    resolved_today = await conn.fetchval(
        f"SELECT COUNT(*) FROM tickets t {_where(resolved_today_clause)}", *base_params,
    )
    by_category = await conn.fetch(
        f"""
        SELECT tc.name, COUNT(*) AS count FROM tickets t
        JOIN ticket_categories tc ON tc.id = t.category_id
        {_where()} GROUP BY tc.name ORDER BY count DESC
        """,
        *base_params,
    )
    by_queue = await conn.fetch(
        f"""
        SELECT tq.name, COUNT(*) AS count FROM tickets t
        JOIN ticket_queues tq ON tq.id = t.queue_id
        {_where()} GROUP BY tq.name ORDER BY count DESC
        """,
        *base_params,
    )
    avg_first_response = await conn.fetchval(
        f"""
        SELECT AVG(EXTRACT(EPOCH FROM (t.first_response_at - t.created_at)) / 60)
        FROM tickets t {_where("t.first_response_at IS NOT NULL")}
        """,
        *base_params,
    )
    avg_resolution = await conn.fetchval(
        f"""
        SELECT AVG(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 60)
        FROM tickets t {_where("t.resolved_at IS NOT NULL")}
        """,
        *base_params,
    )
    return {
        "data": {
            "open_count": open_count,
            "resolved_today": resolved_today,
            "by_category": [dict(r) for r in by_category],
            "by_queue": [dict(r) for r in by_queue],
            "avg_first_response_minutes": round(avg_first_response, 1) if avg_first_response is not None else None,
            "avg_resolution_minutes": round(avg_resolution, 1) if avg_resolution is not None else None,
        },
        "ok": True,
    }


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    row = await conn.fetchrow(f"{_LIST_SELECT} WHERE t.id = $1", ticket_id)
    if not row:
        raise HTTPException(404, "Ticket not found")
    data = dict(row)

    related = await conn.fetch(
        """
        SELECT id, status, priority, source, subject, created_at, resolved_at
        FROM tickets
        WHERE phone = $1 AND channel = $2 AND id <> $3
        ORDER BY created_at DESC LIMIT 5
        """,
        row["phone"], row["channel"], ticket_id,
    )
    data["related_tickets"] = [dict(r) for r in related]
    return {"data": data, "ok": True}


@router.get("/tickets/{ticket_id}/timeline")
async def get_ticket_timeline(ticket_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    exists = await conn.fetchval("SELECT 1 FROM tickets WHERE id = $1", ticket_id)
    if not exists:
        raise HTTPException(404, "Ticket not found")

    rows = await conn.fetch(
        """
        SELECT id, 'whatsapp'::text AS kind, direction, message_text AS body, NULL::text AS type,
               NULL::uuid AS actor_ce_id, NULL::text AS actor_label, NULL::jsonb AS meta, created_at
        FROM whatsapp_messages WHERE ticket_id = $1
        UNION ALL
        SELECT id, 'activity'::text AS kind, NULL::text AS direction, body, type,
               actor_ce_id, actor_label, meta, created_at
        FROM ticket_activities WHERE ticket_id = $1
        ORDER BY created_at ASC
        """,
        ticket_id,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


# ── Executive-facing: mutations ───────────────────────────────────────────────

@router.post("/tickets", status_code=201)
async def create_ticket(body: TicketCreateBody, conn: asyncpg.Connection = Depends(get_conn)):
    if body.channel not in ("client", "candidate"):
        raise HTTPException(400, "channel must be 'client' or 'candidate'")
    try:
        row = await ticket_service.create_or_reopen_ticket(
            conn, phone=body.phone, channel=body.channel, queue_code=body.queue_code,
            category_code=body.category_code, source="manual", reason=body.reason or "",
            client_id=body.client_id, candidate_id=body.candidate_id, mapping_id=body.mapping_id,
            subject=body.subject, priority=body.priority, created_by_ce_id=body.created_by_ce_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"data": row, "ok": True}


@router.post("/tickets/{ticket_id}/claim")
async def claim_ticket_endpoint(ticket_id: uuid.UUID, body: ClaimBody, conn: asyncpg.Connection = Depends(get_conn)):
    row = await ticket_service.claim_ticket(conn, ticket_id, body.ce_id)
    return {"data": row, "ok": True}


@router.post("/tickets/{ticket_id}/reply")
async def reply_to_ticket(ticket_id: uuid.UUID, body: ReplyBody, conn: asyncpg.Connection = Depends(get_conn)):
    row = await ticket_service.send_reply(conn, ticket_id, body.ce_id, body.text)
    return {"data": row, "ok": True}


@router.post("/tickets/{ticket_id}/note")
async def add_ticket_note(ticket_id: uuid.UUID, body: NoteBody, conn: asyncpg.Connection = Depends(get_conn)):
    row = await ticket_service.add_note(conn, ticket_id, body.ce_id, body.text)
    return {"data": row, "ok": True}


@router.post("/tickets/{ticket_id}/reassign")
async def reassign_ticket_endpoint(ticket_id: uuid.UUID, body: ReassignBody, conn: asyncpg.Connection = Depends(get_conn)):
    row = await ticket_service.reassign_ticket(conn, ticket_id, body.to_ce_id)
    return {"data": row, "ok": True}


@router.post("/tickets/{ticket_id}/resolve")
async def resolve_ticket_endpoint(ticket_id: uuid.UUID, body: ResolveBody, conn: asyncpg.Connection = Depends(get_conn)):
    row = await ticket_service.resolve_ticket(conn, ticket_id, body.ce_id, body.resolution_note)
    return {"data": row, "ok": True}


@router.post("/tickets/{ticket_id}/reopen")
async def reopen_ticket_endpoint(ticket_id: uuid.UUID, body: ReopenBody, conn: asyncpg.Connection = Depends(get_conn)):
    row = await ticket_service.reopen_ticket(conn, ticket_id, body.ce_id, body.reason)
    return {"data": row, "ok": True}


@router.patch("/tickets/{ticket_id}")
async def patch_ticket(ticket_id: uuid.UUID, body: TicketPatchBody, conn: asyncpg.Connection = Depends(get_conn)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    keys = list(updates.keys())
    vals = list(updates.values())
    set_clause = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(keys))
    vals.append(ticket_id)
    row = await conn.fetchrow(
        f"UPDATE tickets SET {set_clause}, updated_at = now() WHERE id = ${len(vals)} RETURNING *", *vals,
    )
    if not row:
        raise HTTPException(404, "Ticket not found")
    return {"data": dict(row), "ok": True}


# ── Admin: queues ─────────────────────────────────────────────────────────────

@router.get("/admin/ticket-queues")
async def list_ticket_queues(conn: asyncpg.Connection = Depends(get_conn)):
    rows = await conn.fetch("SELECT * FROM ticket_queues ORDER BY name")
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.post("/admin/ticket-queues", status_code=201)
async def create_ticket_queue(body: QueueCreate, conn: asyncpg.Connection = Depends(get_conn)):
    row = await conn.fetchrow(
        "INSERT INTO ticket_queues (name, code, description, is_active) VALUES ($1, $2, $3, $4) RETURNING *",
        body.name, body.code, body.description, body.is_active,
    )
    return {"data": dict(row), "ok": True}


@router.patch("/admin/ticket-queues/{queue_id}")
async def update_ticket_queue(queue_id: uuid.UUID, body: QueueUpdate, conn: asyncpg.Connection = Depends(get_conn)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    keys = list(updates.keys())
    vals = list(updates.values())
    set_clause = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(keys))
    vals.append(queue_id)
    row = await conn.fetchrow(
        f"UPDATE ticket_queues SET {set_clause}, updated_at = now() WHERE id = ${len(vals)} RETURNING *", *vals,
    )
    if not row:
        raise HTTPException(404, "Queue not found")
    return {"data": dict(row), "ok": True}


@router.delete("/admin/ticket-queues/{queue_id}")
async def delete_ticket_queue(queue_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    try:
        result = await conn.execute("DELETE FROM ticket_queues WHERE id = $1", queue_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(409, "Queue has categories or tickets — deactivate instead of deleting.")
    if result == "DELETE 0":
        raise HTTPException(404, "Queue not found")
    return {"ok": True}


# ── Admin: categories ─────────────────────────────────────────────────────────

@router.get("/admin/ticket-categories")
async def list_ticket_categories(queue_id: uuid.UUID | None = None, conn: asyncpg.Connection = Depends(get_conn)):
    if queue_id:
        rows = await conn.fetch("SELECT * FROM ticket_categories WHERE queue_id = $1 ORDER BY name", queue_id)
    else:
        rows = await conn.fetch("SELECT * FROM ticket_categories ORDER BY name")
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


@router.post("/admin/ticket-categories", status_code=201)
async def create_ticket_category(body: CategoryCreate, conn: asyncpg.Connection = Depends(get_conn)):
    row = await conn.fetchrow(
        "INSERT INTO ticket_categories (queue_id, name, code, is_active) VALUES ($1, $2, $3, $4) RETURNING *",
        body.queue_id, body.name, body.code, body.is_active,
    )
    return {"data": dict(row), "ok": True}


@router.patch("/admin/ticket-categories/{category_id}")
async def update_ticket_category(category_id: uuid.UUID, body: CategoryUpdate, conn: asyncpg.Connection = Depends(get_conn)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    keys = list(updates.keys())
    vals = list(updates.values())
    set_clause = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(keys))
    vals.append(category_id)
    row = await conn.fetchrow(
        f"UPDATE ticket_categories SET {set_clause}, updated_at = now() WHERE id = ${len(vals)} RETURNING *", *vals,
    )
    if not row:
        raise HTTPException(404, "Category not found")
    return {"data": dict(row), "ok": True}


@router.delete("/admin/ticket-categories/{category_id}")
async def delete_ticket_category(category_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    try:
        result = await conn.execute("DELETE FROM ticket_categories WHERE id = $1", category_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(409, "Category has tickets — deactivate instead of deleting.")
    if result == "DELETE 0":
        raise HTTPException(404, "Category not found")
    return {"ok": True}


# ── Admin: executive queue access ─────────────────────────────────────────────

@router.get("/admin/ces/{ce_id}/queue-access")
async def get_queue_access(ce_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    rows = await conn.fetch("SELECT queue_id FROM executive_queue_access WHERE ce_id = $1", ce_id)
    return {"data": [r["queue_id"] for r in rows], "ok": True}


@router.put("/admin/ces/{ce_id}/queue-access")
async def set_queue_access(ce_id: uuid.UUID, body: QueueAccessBody, conn: asyncpg.Connection = Depends(get_conn)):
    ce = await conn.fetchval("SELECT 1 FROM customer_executives WHERE id = $1", ce_id)
    if not ce:
        raise HTTPException(404, "Customer executive not found")
    async with conn.transaction():
        await conn.execute("DELETE FROM executive_queue_access WHERE ce_id = $1", ce_id)
        for queue_id in body.queue_ids:
            await conn.execute(
                "INSERT INTO executive_queue_access (ce_id, queue_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                ce_id, queue_id,
            )
    rows = await conn.fetch("SELECT queue_id FROM executive_queue_access WHERE ce_id = $1", ce_id)
    return {"data": [r["queue_id"] for r in rows], "ok": True}
