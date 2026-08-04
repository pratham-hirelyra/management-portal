"""
Ticket lifecycle logic for the bottom_end CE ticketing system. Mirrors the
ce_service.py/rm_service.py split — thin router, logic here.

Central entity: the ticket. Every "someone needs a human" signal in the
codebase (AI RM escalate()/escalate_commercial(), client_support_bot's
CLIENT_STUCK_* flow, and every ops_alert_service.send_alert() call) now
creates or reopens a ticket here instead of pinging Google Chat/an RM
directly. Multiple signals about the same phone+channel collapse into one
open ticket rather than spawning duplicates — "one issue, one ticket."
"""

from __future__ import annotations

import json

import asyncpg
from fastapi import HTTPException

REOPEN_WINDOW_DAYS = 7
# Long enough that a customer following up on the same resolved issue a few
# days later lands back on the same ticket with full context; short enough
# that older contact reads as a fresh issue rather than reviving stale state.


async def _log_activity(
    conn: asyncpg.Connection, ticket_id, *, type: str, body: str | None = None,
    actor_ce_id=None, actor_label: str | None = None, meta: dict | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO ticket_activities (ticket_id, type, actor_ce_id, actor_label, body, meta)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        """,
        ticket_id, type, actor_ce_id, actor_label, body,
        json.dumps(meta, default=str) if meta is not None else None,
    )


async def get_open_ticket_for_phone(conn: asyncpg.Connection, phone: str, channel: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT * FROM tickets
        WHERE phone = $1 AND channel = $2 AND status <> 'RESOLVED'
        ORDER BY created_at DESC LIMIT 1
        """,
        phone, channel,
    )


async def _get_resolved_ticket_within_window(conn: asyncpg.Connection, phone: str, channel: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT * FROM tickets
        WHERE phone = $1 AND channel = $2 AND status = 'RESOLVED'
          AND resolved_at > now() - make_interval(days => $3)
        ORDER BY resolved_at DESC LIMIT 1
        """,
        phone, channel, REOPEN_WINDOW_DAYS,
    )


async def _attach_wa_message(conn: asyncpg.Connection, ticket_id, wa_message_id) -> None:
    if wa_message_id:
        await conn.execute(
            "UPDATE whatsapp_messages SET ticket_id = $1 WHERE id = $2 AND ticket_id IS NULL",
            ticket_id, wa_message_id,
        )


async def _resolve_category(conn: asyncpg.Connection, queue_code: str, category_code: str) -> asyncpg.Record:
    row = await conn.fetchrow(
        """
        SELECT tc.id AS category_id, tc.queue_id
        FROM ticket_categories tc JOIN ticket_queues tq ON tq.id = tc.queue_id
        WHERE tq.code = $1 AND tc.code = $2
        """,
        queue_code, category_code,
    )
    if not row:
        raise ValueError(f"Unknown ticket queue/category: {queue_code}/{category_code}")
    return row


async def create_or_reopen_ticket(
    conn: asyncpg.Connection, *, phone: str, channel: str, queue_code: str, category_code: str,
    source: str, reason: str = "", client_id=None, candidate_id=None, mapping_id=None,
    wa_message_id=None, subject: str | None = None, priority: str | None = None,
    created_by_ce_id=None,
) -> dict:
    """
    Dedupe against any open ticket for phone+channel (append an activity,
    don't spawn a duplicate). Else reopen a ticket resolved within the last
    REOPEN_WINDOW_DAYS (ownership is preserved across reopen — "one issue,
    one owner" holds even after resolution). Else insert new.
    """
    open_ticket = await get_open_ticket_for_phone(conn, phone, channel)
    if open_ticket:
        await _log_activity(
            conn, open_ticket["id"], type="system", actor_label="System",
            body=f"Additional {source} signal: {reason}" if reason else f"Additional {source} signal",
        )
        await conn.execute(
            "UPDATE tickets SET last_activity_at = now(), updated_at = now() WHERE id = $1",
            open_ticket["id"],
        )
        await _attach_wa_message(conn, open_ticket["id"], wa_message_id)
        return dict(open_ticket)

    resolved = await _get_resolved_ticket_within_window(conn, phone, channel)
    if resolved:
        row = await conn.fetchrow(
            """
            UPDATE tickets
            SET status = 'REOPENED', reopened_at = now(), reopen_count = reopen_count + 1,
                last_activity_at = now(), updated_at = now()
            WHERE id = $1
            RETURNING *
            """,
            resolved["id"],
        )
        await _log_activity(conn, row["id"], type="reopened", actor_label="System", body=reason or None)
        await _attach_wa_message(conn, row["id"], wa_message_id)
        return dict(row)

    category = await _resolve_category(conn, queue_code, category_code)
    resolved_priority = priority or ("HIGH" if source == "commercial_escalation" else "MEDIUM")
    row = await conn.fetchrow(
        """
        INSERT INTO tickets (
            queue_id, category_id, status, priority, source, subject, phone, channel,
            client_id, candidate_id, mapping_id, created_by
        ) VALUES ($1, $2, 'NEW', $3, $4, $5, $6, $7, $8, $9, $10, $11)
        RETURNING *
        """,
        category["queue_id"], category["category_id"], resolved_priority, source,
        subject, phone, channel, client_id, candidate_id, mapping_id, created_by_ce_id,
    )
    await _log_activity(conn, row["id"], type="created", actor_label="System", body=reason or None)
    await _attach_wa_message(conn, row["id"], wa_message_id)
    return dict(row)


async def log_customer_reply(conn: asyncpg.Connection, phone: str, channel: str, wa_message_id, text: str) -> str | None:
    """
    Called at the top of both AI RM services' handle_free_text(). If an open
    ticket already exists for this phone+channel, attach the message, flip
    WAITING_FOR_CUSTOMER -> CUSTOMER_REPLIED, and return its id so the caller
    goes silent — a ticket, claimed or not, is human-owned; the AI must not
    keep auto-replying underneath it. If a resolved ticket exists within the
    reopen window, reopen it and return its id. Otherwise return None and the
    AI proceeds as normal.
    """
    open_ticket = await get_open_ticket_for_phone(conn, phone, channel)
    if open_ticket:
        new_status = "CUSTOMER_REPLIED" if open_ticket["status"] == "WAITING_FOR_CUSTOMER" else open_ticket["status"]
        await conn.execute(
            "UPDATE tickets SET status = $1, last_activity_at = now(), updated_at = now() WHERE id = $2",
            new_status, open_ticket["id"],
        )
        await _log_activity(conn, open_ticket["id"], type="customer_replied", actor_label="Customer", body=(text or "")[:500] or None)
        await _attach_wa_message(conn, open_ticket["id"], wa_message_id)
        return str(open_ticket["id"])

    resolved = await _get_resolved_ticket_within_window(conn, phone, channel)
    if resolved:
        row = await conn.fetchrow(
            """
            UPDATE tickets
            SET status = 'REOPENED', reopened_at = now(), reopen_count = reopen_count + 1,
                last_activity_at = now(), updated_at = now()
            WHERE id = $1
            RETURNING id
            """,
            resolved["id"],
        )
        await _log_activity(conn, row["id"], type="reopened", actor_label="Customer", body=(text or "")[:500] or None)
        await _attach_wa_message(conn, row["id"], wa_message_id)
        return str(row["id"])

    return None


async def create_engineering_ticket(
    conn: asyncpg.Connection, *, title: str, detail: str, severity: str, category: str,
    context: dict | None = None, fingerprint: str | None = None,
) -> dict:
    """
    channel='internal' / source='system_alert' path called by
    ops_alert_service.send_alert. Deliberately separate from
    create_or_reopen_ticket: no phone, no reopen window, dedupe key is a
    fingerprint rather than phone+channel — a broken integration can fire the
    same alert hundreds of times an hour, and that must collapse into one
    ticket with a bumped occurrence_count, not hundreds of rows.
    """
    fp = (fingerprint or f"{category}:{title}")[:250]
    priority = {"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}.get(severity, "MEDIUM")

    open_row = await conn.fetchrow(
        "SELECT * FROM tickets WHERE fingerprint = $1 AND status <> 'RESOLVED' LIMIT 1", fp,
    )
    if open_row:
        await conn.execute(
            "UPDATE tickets SET occurrence_count = occurrence_count + 1, last_activity_at = now(), updated_at = now() WHERE id = $1",
            open_row["id"],
        )
        await _log_activity(conn, open_row["id"], type="system", actor_label="System", body=detail, meta=context)
        return dict(open_row)

    category_row = await conn.fetchrow(
        """
        SELECT tc.id AS category_id, tc.queue_id
        FROM ticket_categories tc JOIN ticket_queues tq ON tq.id = tc.queue_id
        WHERE tq.code = 'engineering_queue' AND tc.code = $1
        """,
        category,
    )
    if not category_row:
        category_row = await conn.fetchrow(
            """
            SELECT tc.id AS category_id, tc.queue_id
            FROM ticket_categories tc JOIN ticket_queues tq ON tq.id = tc.queue_id
            WHERE tq.code = 'engineering_queue' AND tc.code = 'other'
            """
        )

    row = await conn.fetchrow(
        """
        INSERT INTO tickets (queue_id, category_id, status, priority, source, subject, channel, fingerprint)
        VALUES ($1, $2, 'NEW', $3, 'system_alert', $4, 'internal', $5)
        RETURNING *
        """,
        category_row["queue_id"], category_row["category_id"], priority, title, fp,
    )
    await _log_activity(conn, row["id"], type="created", actor_label="System", body=detail, meta=context)
    return dict(row)


async def claim_ticket(conn: asyncpg.Connection, ticket_id, ce_id) -> dict:
    ce = await conn.fetchrow("SELECT tier, is_active FROM customer_executives WHERE id = $1", ce_id)
    if not ce or ce["tier"] != "bottom_end" or not ce["is_active"]:
        raise HTTPException(403, "Not an active bottom_end executive")

    ticket = await conn.fetchrow("SELECT queue_id FROM tickets WHERE id = $1", ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    access = await conn.fetchval(
        "SELECT 1 FROM executive_queue_access WHERE ce_id = $1 AND queue_id = $2", ce_id, ticket["queue_id"],
    )
    if not access:
        raise HTTPException(403, "No access to this ticket's queue")

    row = await conn.fetchrow(
        """
        UPDATE tickets
        SET claimed_by = $1, claimed_at = now(), status = 'CLAIMED', last_activity_at = now(), updated_at = now()
        WHERE id = $2 AND claimed_by IS NULL
        RETURNING *
        """,
        ce_id, ticket_id,
    )
    if not row:
        raise HTTPException(409, "Ticket already claimed")
    await _log_activity(conn, ticket_id, type="claimed", actor_ce_id=ce_id)
    return dict(row)


async def send_reply(conn: asyncpg.Connection, ticket_id, ce_id, text: str) -> dict:
    import services.msg91_service as msg91

    ticket = await conn.fetchrow("SELECT * FROM tickets WHERE id = $1", ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if ticket["channel"] == "internal":
        raise HTTPException(400, "Internal tickets have no customer to reply to")
    if ticket["claimed_by"] != ce_id:
        raise HTTPException(403, "Only the assigned executive can reply")

    await msg91.send_text(ticket["phone"], text, sender=ticket["channel"])

    msg_row = await conn.fetchrow(
        """
        INSERT INTO whatsapp_messages
            (client_id, candidate_id, mapping_id, ticket_id, message_type, direction, phone, message_text, status, sent_at)
        VALUES ($1, $2, $3, $4, 'ticket_reply', 'outbound', $5, $6, 'sent', now())
        RETURNING id
        """,
        ticket["client_id"], ticket["candidate_id"], ticket["mapping_id"], ticket_id, ticket["phone"], text,
    )
    row = await conn.fetchrow(
        """
        UPDATE tickets
        SET first_response_at = COALESCE(first_response_at, now()), status = 'WAITING_FOR_CUSTOMER',
            last_activity_at = now(), updated_at = now()
        WHERE id = $1
        RETURNING *
        """,
        ticket_id,
    )
    await _log_activity(
        conn, ticket_id, type="system", actor_ce_id=ce_id, body="Reply sent",
        meta={"whatsapp_message_id": str(msg_row["id"])},
    )
    return dict(row)


async def add_note(conn: asyncpg.Connection, ticket_id, ce_id, text: str) -> dict:
    ticket = await conn.fetchrow("SELECT * FROM tickets WHERE id = $1", ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if ticket["claimed_by"] != ce_id:
        raise HTTPException(403, "Only the assigned executive can add notes")

    await _log_activity(conn, ticket_id, type="note", actor_ce_id=ce_id, body=text)

    # A note doesn't change who the ball is with if we're waiting on the
    # customer — only bump statuses that signal active work resuming.
    new_status = "IN_PROGRESS" if ticket["status"] in ("CLAIMED", "CUSTOMER_REPLIED", "REOPENED") else ticket["status"]
    row = await conn.fetchrow(
        "UPDATE tickets SET status = $1, last_activity_at = now(), updated_at = now() WHERE id = $2 RETURNING *",
        new_status, ticket_id,
    )
    return dict(row)


async def reassign_ticket(conn: asyncpg.Connection, ticket_id, to_ce_id) -> dict:
    """No ownership check on the actor — this is the one admin-usable
    mutation (spec: admin can view all tickets and reassign)."""
    ticket = await conn.fetchrow("SELECT * FROM tickets WHERE id = $1", ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    to_ce = await conn.fetchrow("SELECT tier, is_active FROM customer_executives WHERE id = $1", to_ce_id)
    if not to_ce or to_ce["tier"] != "bottom_end" or not to_ce["is_active"]:
        raise HTTPException(400, "Target is not an active bottom_end executive")

    access = await conn.fetchval(
        "SELECT 1 FROM executive_queue_access WHERE ce_id = $1 AND queue_id = $2", to_ce_id, ticket["queue_id"],
    )
    if not access:
        raise HTTPException(400, "Target executive has no access to this ticket's queue")

    new_status = "CLAIMED" if ticket["status"] in ("NEW", "CLAIMED") else ticket["status"]
    row = await conn.fetchrow(
        """
        UPDATE tickets
        SET claimed_by = $1, claimed_at = now(), status = $2, last_activity_at = now(), updated_at = now()
        WHERE id = $3
        RETURNING *
        """,
        to_ce_id, new_status, ticket_id,
    )
    await _log_activity(conn, ticket_id, type="reassigned", actor_ce_id=to_ce_id, body="Reassigned")
    return dict(row)


async def resolve_ticket(conn: asyncpg.Connection, ticket_id, ce_id, resolution_note: str | None = None) -> dict:
    ticket = await conn.fetchrow("SELECT * FROM tickets WHERE id = $1", ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if ticket["claimed_by"] != ce_id:
        raise HTTPException(403, "Only the assigned executive can resolve")

    row = await conn.fetchrow(
        "UPDATE tickets SET status = 'RESOLVED', resolved_at = now(), last_activity_at = now(), updated_at = now() WHERE id = $1 RETURNING *",
        ticket_id,
    )
    await _log_activity(conn, ticket_id, type="resolved", actor_ce_id=ce_id, body=resolution_note)
    return dict(row)


async def reopen_ticket(conn: asyncpg.Connection, ticket_id, ce_id, reason: str | None = None) -> dict:
    """Manual counterpart to the automatic reopen in log_customer_reply — e.g.
    an exec who hears from a customer off-WhatsApp about a resolved issue."""
    ticket = await conn.fetchrow("SELECT * FROM tickets WHERE id = $1", ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if ticket["claimed_by"] != ce_id:
        raise HTTPException(403, "Only the assigned executive can reopen")
    if ticket["status"] != "RESOLVED":
        raise HTTPException(409, "Ticket is not resolved")

    row = await conn.fetchrow(
        """
        UPDATE tickets
        SET status = 'REOPENED', reopened_at = now(), reopen_count = reopen_count + 1,
            last_activity_at = now(), updated_at = now()
        WHERE id = $1
        RETURNING *
        """,
        ticket_id,
    )
    await _log_activity(conn, ticket_id, type="reopened", actor_ce_id=ce_id, body=reason)
    return dict(row)


# ── System-detected tickets: stuck-in-pipeline / ready-to-advance ────────────
# stuck_in_pipeline is a client-level signal (nudge-forever-with-no-escalation
# client_approval_pending backlog); ready_to_advance is per-mapping
# (interview_done + client already liked, zero existing automation). See
# routers/cron.py's client-followups job for where these run.

_STUCK_REVIEW_HOURS = 8
_READY_TO_ADVANCE_BUFFER_HOURS = 6


async def create_or_reopen_mapping_ticket(
    conn: asyncpg.Connection, *, client_id, phone: str, queue_code: str,
    category_code: str, source: str, reason: str, subject: str | None = None,
    mapping_id=None,
) -> dict:
    """
    Same create/reopen shape as create_or_reopen_ticket, but deduped on
    (client_id, mapping_id, source) instead of (phone, channel) — each stuck/
    ready-to-advance signal is its own issue, independent of whatever else is
    open for that client's phone (e.g. an unrelated commercial escalation
    shouldn't swallow a completely separate stuck-review backlog). mapping_id
    is optional: pass it for a per-candidate signal (e.g. ready_to_advance);
    leave it None for a client-level signal (e.g. stuck_in_pipeline's review
    backlog, which isn't about any one specific candidate) — dedupe uses
    `IS NOT DISTINCT FROM` so two NULL-mapping_id tickets for the same client
    still collapse into one, which a plain `=` comparison wouldn't do.
    """
    open_ticket = await conn.fetchrow(
        """
        SELECT * FROM tickets
        WHERE client_id = $1 AND mapping_id IS NOT DISTINCT FROM $2 AND source = $3 AND status <> 'RESOLVED'
        LIMIT 1
        """,
        client_id, mapping_id, source,
    )
    if open_ticket:
        await conn.execute(
            "UPDATE tickets SET last_activity_at = now(), updated_at = now() WHERE id = $1", open_ticket["id"],
        )
        return {"ticket": dict(open_ticket), "created": False}

    resolved = await conn.fetchrow(
        """
        SELECT * FROM tickets
        WHERE client_id = $1 AND mapping_id IS NOT DISTINCT FROM $2 AND source = $3 AND status = 'RESOLVED'
          AND resolved_at > now() - make_interval(days => $4)
        ORDER BY resolved_at DESC LIMIT 1
        """,
        client_id, mapping_id, source, REOPEN_WINDOW_DAYS,
    )
    if resolved:
        row = await conn.fetchrow(
            """
            UPDATE tickets
            SET status = 'REOPENED', reopened_at = now(), reopen_count = reopen_count + 1,
                last_activity_at = now(), updated_at = now()
            WHERE id = $1
            RETURNING *
            """,
            resolved["id"],
        )
        await _log_activity(conn, row["id"], type="reopened", actor_label="System", body=reason)
        return {"ticket": dict(row), "created": False}

    category = await _resolve_category(conn, queue_code, category_code)
    row = await conn.fetchrow(
        """
        INSERT INTO tickets (
            queue_id, category_id, status, priority, source, subject, phone, channel, client_id, mapping_id
        ) VALUES ($1, $2, 'NEW', 'MEDIUM', $3, $4, $5, 'client', $6, $7)
        RETURNING *
        """,
        category["queue_id"], category["category_id"], source, subject, phone, client_id, mapping_id,
    )
    await _log_activity(conn, row["id"], type="created", actor_label="System", body=reason)
    return {"ticket": dict(row), "created": True}


_REVIEW_PENDING_CTE = """
    SELECT
        ccm.client_id,
        COALESCE(
            (SELECT MAX(ce.created_at) FROM candidate_events ce
             WHERE ce.mapping_id = ccm.id AND ce.field = 'mapping_stage'),
            ccm.created_at
        ) AS waiting_since
    FROM client_candidate_mappings ccm
    WHERE ccm.stage = 'client_approval_pending'
"""

_FEEDBACK_PENDING_CTE = """
    SELECT
        ccm.client_id,
        COALESCE(
            ccm.interview_done_at,
            (SELECT MAX(ce.created_at) FROM candidate_events ce
             WHERE ce.mapping_id = ccm.id AND ce.field = 'mapping_stage'),
            ccm.created_at
        ) AS waiting_since
    FROM client_candidate_mappings ccm
    WHERE ccm.stage = 'interview_done' AND ccm.feedback_client IS NULL
"""


def _stuck_subject(review_count: int, feedback_count: int) -> str:
    parts = []
    if review_count:
        parts.append(f"{review_count} awaiting review")
    if feedback_count:
        parts.append(f"{feedback_count} awaiting feedback")
    return "Client backlog — " + " · ".join(parts) if parts else "Client backlog"


async def _upsert_client_stuck_ticket(
    conn: asyncpg.Connection, *, client_id, phone: str, review_count: int, feedback_count: int,
) -> bool:
    """
    One ticket per client for the entire stuck_in_pipeline backlog (review +
    feedback, and any future signal added to this category) — a CE calls the
    client once and works through everything outstanding in that one call,
    so splitting these into separate tickets per signal just forces
    duplicate claims/resolves for a single phone call.

    Subject and a fresh activity note are refreshed on every sweep that
    still finds this client open or eligible to reopen, so the counts always
    reflect the CURRENT backlog — not whatever was true when the ticket was
    first created. If a CE only clears part of it before resolving, the next
    reopen shows the real remainder, not a stale snapshot. Reopen preserves
    claimed_by (see create_or_reopen_mapping_ticket's docstring for why) —
    the same CE gets the follow-up, not a random one.
    Returns True if a new ticket was created, False if an existing one was
    bumped/reopened.
    """
    subject = _stuck_subject(review_count, feedback_count)
    breakdown = f"{review_count} awaiting review, {feedback_count} awaiting feedback (as of this check)"

    open_ticket = await conn.fetchrow(
        "SELECT id FROM tickets WHERE client_id = $1 AND mapping_id IS NULL AND source = 'stuck_in_pipeline' AND status <> 'RESOLVED' LIMIT 1",
        client_id,
    )
    if open_ticket:
        await conn.execute(
            "UPDATE tickets SET subject = $1, last_activity_at = now(), updated_at = now() WHERE id = $2",
            subject, open_ticket["id"],
        )
        await _log_activity(conn, open_ticket["id"], type="system", actor_label="System", body=breakdown)
        return False

    resolved = await conn.fetchrow(
        """
        SELECT id FROM tickets WHERE client_id = $1 AND mapping_id IS NULL AND source = 'stuck_in_pipeline'
          AND status = 'RESOLVED' AND resolved_at > now() - make_interval(days => $2)
        ORDER BY resolved_at DESC LIMIT 1
        """,
        client_id, REOPEN_WINDOW_DAYS,
    )
    if resolved:
        await conn.execute(
            """
            UPDATE tickets
            SET status = 'REOPENED', subject = $1, reopened_at = now(), reopen_count = reopen_count + 1,
                last_activity_at = now(), updated_at = now()
            WHERE id = $2
            """,
            subject, resolved["id"],
        )
        await _log_activity(conn, resolved["id"], type="reopened", actor_label="System", body=breakdown)
        return False

    category = await _resolve_category(conn, "client_queue", "stuck_in_pipeline")
    row = await conn.fetchrow(
        """
        INSERT INTO tickets (
            queue_id, category_id, status, priority, source, subject, phone, channel, client_id, mapping_id
        ) VALUES ($1, $2, 'NEW', 'MEDIUM', 'stuck_in_pipeline', $3, $4, 'client', $5, NULL)
        RETURNING id
        """,
        category["queue_id"], category["category_id"], subject, phone, client_id,
    )
    await _log_activity(conn, row["id"], type="created", actor_label="System", body=breakdown)
    return True


async def sweep_stuck_in_pipeline(conn: asyncpg.Connection) -> dict:
    """
    Client-level, not per-candidate, ONE ticket per client covering the
    whole stuck_in_pipeline backlog together (review + feedback today; any
    future signal added to this category should plug in the same way) —
    see _upsert_client_stuck_ticket's docstring for why these are merged
    rather than split per signal. Fires once the OLDEST outstanding item
    across BOTH signals crosses _STUCK_REVIEW_HOURS, but reports the full
    current backlog at that point (including anything still under the
    threshold) since the CE is calling anyway.

    1. Review backlog — client_approval_pending mappings. Mirrors
       _followup_review_pending's target (nudges every 2h forever, no cap,
       no escalation).
    2. Feedback backlog — interview_done mappings with no feedback_client
       yet. Mirrors _followup_feedback_pending's target (same shape).

    "Waiting since" mirrors the /clients/stuck and /pipeline/stuck "time
    since last stage-change event, falling back to created_at" pattern,
    using candidate_events instead of pipeline_events (feedback backlog
    prefers the purpose-built interview_done_at first).
    """
    rows = await conn.fetch(
        f"""
        WITH pending AS (
            SELECT client_id, 'review' AS reason_type, waiting_since FROM ({_REVIEW_PENDING_CTE}) r
            UNION ALL
            SELECT client_id, 'feedback' AS reason_type, waiting_since FROM ({_FEEDBACK_PENDING_CTE}) f
        )
        SELECT
            cl.id AS client_id, cl.poc_phone AS phone,
            COUNT(*) FILTER (WHERE p.reason_type = 'review') AS review_count,
            COUNT(*) FILTER (WHERE p.reason_type = 'feedback') AS feedback_count
        FROM pending p
        JOIN clients cl ON cl.id = p.client_id
        WHERE cl.poc_phone IS NOT NULL AND cl.is_dnd = false
        GROUP BY cl.id, cl.poc_phone
        HAVING MIN(p.waiting_since) < now() - make_interval(hours => $1)
        """,
        _STUCK_REVIEW_HOURS,
    )

    created = 0
    for r in rows:
        did_create = await _upsert_client_stuck_ticket(
            conn, client_id=r["client_id"], phone=r["phone"],
            review_count=r["review_count"], feedback_count=r["feedback_count"],
        )
        if did_create:
            created += 1

    return {"checked": len(rows), "created": created}


async def sweep_ready_to_advance(conn: asyncpg.Connection) -> dict:
    """
    Candidate interviewed, client already said they liked them
    (feedback_client='liked'), but the mapping never moved past
    interview_done. Zero existing automation for this stage — confirmed by
    grep across routers/ and services/.
    """
    rows = await conn.fetch(
        """
        SELECT ccm.id AS mapping_id, ccm.client_id, cl.poc_phone AS phone
        FROM client_candidate_mappings ccm
        JOIN clients cl ON cl.id = ccm.client_id
        WHERE cl.poc_phone IS NOT NULL AND cl.is_dnd = false
        AND ccm.stage = 'interview_done' AND ccm.feedback_client = 'liked'
        AND ccm.updated_at < now() - make_interval(hours => $1)
        """,
        _READY_TO_ADVANCE_BUFFER_HOURS,
    )

    created = 0
    for r in rows:
        result = await create_or_reopen_mapping_ticket(
            conn, mapping_id=r["mapping_id"], client_id=r["client_id"], phone=r["phone"],
            queue_code="client_queue", category_code="ready_to_advance", source="ready_to_advance",
            reason="Client liked this candidate after interview but nothing moved them toward an offer",
            subject="Ready to advance — client liked candidate, no next step",
        )
        if result["created"]:
            created += 1

    return {"checked": len(rows), "created": created}


async def sweep_client_closure(conn: asyncpg.Connection) -> dict:
    """
    Both sides have already agreed — client hired via the portal
    (join_intent_requested), candidate accepted (respond_to_join_intent
    ACCEPT -> documents_requested), and the candidate has submitted the
    requested documents (documents_submitted_at set). The only thing left is
    the client manually reviewing those documents and clicking "Final Hire"
    in their portal (company_portal.final_hire) — nothing nudges them toward
    that today; the mapping just sits in documents_requested indefinitely.

    Per-mapping (each placement closing is its own event, not aggregated
    like the client-level stuck_in_pipeline signals), routed to
    closure_queue rather than client_queue — this is the business-closure
    team's job, not general client support. No staleness buffer: submitting
    documents is a definitive, one-time action (not a "went quiet" state
    like the other detectors), so this fires as soon as it's true.
    """
    rows = await conn.fetch(
        """
        SELECT ccm.id AS mapping_id, ccm.client_id, cl.poc_phone AS phone
        FROM client_candidate_mappings ccm
        JOIN clients cl ON cl.id = ccm.client_id
        WHERE cl.poc_phone IS NOT NULL AND cl.is_dnd = false
        AND ccm.stage = 'documents_requested' AND ccm.documents_submitted_at IS NOT NULL
        """
    )

    created = 0
    for r in rows:
        result = await create_or_reopen_mapping_ticket(
            conn, mapping_id=r["mapping_id"], client_id=r["client_id"], phone=r["phone"],
            queue_code="closure_queue", category_code="closure", source="client_closure",
            reason="Client and candidate both agreed and documents are submitted — ready to finalize",
            subject="Ready for closure — documents submitted",
        )
        if result["created"]:
            created += 1

    return {"checked": len(rows), "created": created}
