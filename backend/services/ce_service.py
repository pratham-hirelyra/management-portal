"""
Customer Executive shared-pool fill.

Mirrors the bottom_end ticket desk's claim-based model, not
rm_service.assign_rm's auto-pick style — this just tops up a shared pool of
unclaimed (ce_id=NULL) ce_assignments rows that any active top_end CE can
claim from (see routers/ce.py's claim endpoint), rather than picking a
specific CE itself.
"""
from datetime import datetime, timedelta, timezone

import asyncpg

_IST = timezone(timedelta(hours=5, minutes=30))  # matches routers/cron.py's convention
_ELIGIBLE_SINCE = datetime(2026, 7, 15, tzinfo=_IST)
# Explicit cutoff — only clients created on/after midnight IST on this date
# enter the top_end pool; older backlog stays out of this queue. Deliberately
# a tz-aware datetime, not a bare date: asyncpg encodes a bare
# datetime.date compared against a timestamptz column using the *local
# machine's* timezone for the implicit midnight, which is not what you want
# on a server that isn't itself running in IST.


async def sweep_assign(conn: asyncpg.Connection) -> dict:
    """
    Add any newly-eligible, not-yet-in-pool client to the shared pool as an
    unclaimed row. Returns the count added.

    Pool (top_end only — bottom_end is the claim-based ticket desk instead,
    see services/ticket_service.py):
      segment = 'active_job_post' AND stage IN ('reachout_sent', 'interested')
      OR segment = 'employer_lead' AND stage = 'interested'
      OR segment = 'ca_network'    AND stage = 'interested'
      AND created_at >= _ELIGIBLE_SINCE
      excluding clients already in ce_assignments (permanent 1:1 attribution
      once claimed — a client that's already in the pool, claimed or not,
      never gets a second row)

    Claim priority (see routers/ce.py::get_ce_queue) is a fixed tier order —
    Job Post Interested > Employer Lead Interested > CA Network Interested >
    Job Post Reachout Sent — not insertion order, so which rows land here
    first doesn't itself determine call priority.
    """
    rows = await conn.fetch(
        """
        INSERT INTO ce_assignments (ce_id, client_id)
        SELECT NULL, c.id
        FROM clients c
        WHERE (
            (c.segment = 'active_job_post' AND c.stage::text IN ('reachout_sent', 'interested'))
            OR (c.segment = 'employer_lead' AND c.stage::text = 'interested')
            OR (c.segment = 'ca_network'    AND c.stage::text = 'interested')
        )
        AND c.created_at >= $1
        AND NOT EXISTS (SELECT 1 FROM ce_assignments a WHERE a.client_id = c.id)
        ON CONFLICT (client_id) DO NOTHING
        RETURNING client_id
        """,
        _ELIGIBLE_SINCE,
    )
    return {"added_to_pool": len(rows)}
