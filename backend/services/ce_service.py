"""
Customer Executive auto-assignment sweep.

Mirrors rm_service.assign_rm's style (capacity-aware, least-loaded-first) but
runs as a periodic sweep rather than a single-event trigger, since the CE
pool has multiple, unrelated entry points (a candidate WhatsApp reply moving
a client to 'interested', or a fresh job-portal scrape) rather than one place
to hook into.
"""
import asyncpg


async def sweep_assign(conn: asyncpg.Connection) -> dict:
    """
    Assign any eligible, not-yet-assigned client to the least-loaded active
    top_end customer executive under their daily cap. Returns counts.

    Pool (top_end only — bottom_end queue logic not yet defined):
      segment = 'active_job_post' AND stage IN ('reachout_sent', 'interested')
      OR segment = 'employer_lead' AND stage = 'interested'
      excluding clients already in ce_assignments (permanent 1:1 attribution)

    Priority order:
      1. active_job_post + interested       (highest — already showed intent)
      2. employer_lead + interested
      3. active_job_post + reachout_sent    (lowest — not yet confirmed interest)
    Newest leads (by created_at) go first within each tier.
    """
    eligible = await conn.fetch(
        """
        SELECT c.id
        FROM clients c
        WHERE (
            (c.segment = 'active_job_post' AND c.stage::text IN ('reachout_sent', 'interested'))
            OR (c.segment = 'employer_lead' AND c.stage::text = 'interested')
        )
        AND NOT EXISTS (SELECT 1 FROM ce_assignments a WHERE a.client_id = c.id)
        ORDER BY
            CASE
                WHEN c.segment = 'active_job_post' AND c.stage::text = 'interested'    THEN 1
                WHEN c.segment = 'employer_lead'   AND c.stage::text = 'interested'    THEN 2
                WHEN c.segment = 'active_job_post' AND c.stage::text = 'reachout_sent' THEN 3
            END,
            c.created_at DESC
        """
    )
    if not eligible:
        return {"assigned": 0, "skipped_no_capacity": 0}

    assigned = 0
    for row in eligible:
        # Least-loaded active top_end CE with today's pending count under their cap
        ce = await conn.fetchrow(
            """
            SELECT ce.id,
                   COUNT(a.id) FILTER (WHERE a.status = 'pending' AND a.call_date <= CURRENT_DATE) AS today_pending
            FROM customer_executives ce
            LEFT JOIN ce_assignments a ON a.ce_id = ce.id
            WHERE ce.is_active = true AND ce.tier = 'top_end'
            GROUP BY ce.id, ce.max_clients_per_day
            HAVING COUNT(a.id) FILTER (WHERE a.status = 'pending' AND a.call_date <= CURRENT_DATE) < ce.max_clients_per_day
            ORDER BY today_pending ASC
            LIMIT 1
            """
        )
        if not ce:
            break  # no CE has capacity today — stop, leave the rest unassigned for the next sweep
        await conn.execute(
            "INSERT INTO ce_assignments (ce_id, client_id) VALUES ($1, $2) ON CONFLICT (client_id) DO NOTHING",
            ce["id"], row["id"],
        )
        assigned += 1

    return {"assigned": assigned, "skipped_no_capacity": len(eligible) - assigned}
