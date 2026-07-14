"""
WF1 — Client Acquisition Analytics  (C01–C30)
WF2 — Matchmaking Analytics         (M01–M25)
WF3 — Candidate Sourcing Analytics  (K01–K23)
WF4 — Joining Tracker Analytics     (T01–T12)

GET /analytics/wf1/leads        C01–C03  Lead Generation & Enrichment (basic counts)
GET /analytics/wf1/enrichment   C04–C06  Data Enrichment
GET /analytics/wf1/outreach     C07–C15  WhatsApp Outreach
GET /analytics/wf1/onboarding   C16–C20  Form & Agreement
GET /analytics/wf1/pipeline     C21–C26  Pipeline Health
GET /analytics/wf1/batches      C27–C30  Batch Analytics

Segment classification:
  Hot  = active_job_post | employer_lead | inbound
  Cold = ca_network | hr_contacts
"""

from fastapi import APIRouter, Depends, Query
from typing import Optional
import asyncpg
from database import get_conn

router = APIRouter(prefix="/analytics", tags=["analytics"])

_HOT_SEGS  = ("active_job_post", "employer_lead")
_COLD_SEGS = ("ca_network",)
_COLD_SRCS = ("ca_network", "hr_contacts")


def _heat(segment: Optional[str], source: Optional[str]) -> str:
    seg = (segment or "").lower()
    src = (source or "").lower()
    if seg in _COLD_SEGS or src in _COLD_SRCS:
        return "cold"
    if seg in _HOT_SEGS or src == "inbound":
        return "hot"
    return "other"


# ── C01–C03 : Lead Generation ─────────────────────────────────────────────────

@router.get("/wf1/leads")
async def wf1_leads(
    period: str = Query("day", pattern="^(day|week|month)$"),
    days: int = Query(30, ge=1, le=365),
    conn: asyncpg.Connection = Depends(get_conn),
):
    trunc = {"day": "day", "week": "week", "month": "month"}[period]

    # C01 — daily leads by segment (excludes recruiters)
    daily_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', created_at AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COALESCE(segment, 'unknown') AS segment,
            COUNT(*) AS count
        FROM clients
        WHERE created_at >= now() - make_interval(days => $1)
          AND is_recruiter = false
        GROUP BY period, segment
        ORDER BY period DESC
        """,
        days,
    )

    # Build [{period, ca_network, active_job_post, employer_lead, inbound, other}]
    from collections import defaultdict
    daily_map: dict = defaultdict(lambda: defaultdict(int))
    for r in daily_rows:
        daily_map[str(r["period"])][r["segment"]] += int(r["count"])

    daily = [
        {
            "period": p,
            "ca_network":     v.get("ca_network", 0),
            "active_job_post": v.get("active_job_post", 0),
            "employer_lead":  v.get("employer_lead", 0),
            "inbound":        v.get("inbound", 0),
            "other":          sum(cnt for k, cnt in v.items() if k not in (
                "ca_network", "active_job_post", "employer_lead", "inbound"
            )),
        }
        for p, v in sorted(daily_map.items())
    ]

    # C02 — one-time pool status (CA Network + HR Contacts, excludes recruiters)
    # Also counts recruiters found as a separate metric
    pool = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE segment = 'ca_network'  AND is_recruiter = false)                            AS ca_total,
            COUNT(*) FILTER (WHERE segment = 'ca_network'  AND is_recruiter = false AND stage::text != 'lead')  AS ca_reached,
            COUNT(*) FILTER (WHERE source  = 'hr_contacts' AND is_recruiter = false)                            AS hr_total,
            COUNT(*) FILTER (WHERE source  = 'hr_contacts' AND is_recruiter = false AND stage::text != 'lead')  AS hr_reached,
            COUNT(*) FILTER (WHERE is_recruiter = true)                                                          AS recruiters_found
        FROM clients
        """
    )

    ca_total         = int(pool["ca_total"]        or 0)
    ca_reached       = int(pool["ca_reached"]      or 0)
    hr_total         = int(pool["hr_total"]        or 0)
    hr_reached       = int(pool["hr_reached"]      or 0)
    recruiters_found = int(pool["recruiters_found"] or 0)

    # C03 — hot vs cold mix (last N days, excludes recruiters)
    mix = await conn.fetch(
        """
        SELECT
            CASE
                WHEN segment IN ('ca_network') OR source IN ('ca_network','hr_contacts') THEN 'cold'
                WHEN segment IN ('active_job_post','employer_lead') OR source = 'inbound' THEN 'hot'
                ELSE 'other'
            END AS heat,
            COUNT(*) AS count
        FROM clients
        WHERE created_at >= now() - make_interval(days => $1)
          AND is_recruiter = false
        GROUP BY heat
        """,
        days,
    )
    mix_data = {r["heat"]: int(r["count"]) for r in mix}

    return {
        "data": {
            "daily": daily,
            "pool": {
                "ca_total": ca_total,
                "ca_reached": ca_reached,
                "ca_remaining": ca_total - ca_reached,
                "ca_pct": round(ca_reached / ca_total * 100, 1) if ca_total else 0,
                "hr_total": hr_total,
                "hr_reached": hr_reached,
                "hr_remaining": hr_total - hr_reached,
                "hr_pct": round(hr_reached / hr_total * 100, 1) if hr_total else 0,
            },
            "source_mix": {
                "hot": mix_data.get("hot", 0),
                "cold": mix_data.get("cold", 0),
                "other": mix_data.get("other", 0),
            },
            "recruiters_found": recruiters_found,
        },
        "ok": True,
    }


# ── C04–C06 : Enrichment ──────────────────────────────────────────────────────

@router.get("/wf1/enrichment")
async def wf1_enrichment(
    days: int = Query(30, ge=1, le=365),
    conn: asyncpg.Connection = Depends(get_conn),
):
    # C04 — enrichment success rate by tool (excludes recruiter clients)
    tool_rows = await conn.fetch(
        """
        SELECT
            sj.scraper_type,
            COUNT(*) FILTER (WHERE sj.status = 'completed') AS success,
            COUNT(*) FILTER (WHERE sj.status = 'failed')    AS failed,
            COUNT(*)                                          AS total
        FROM scraping_jobs sj
        JOIN clients c ON c.id = sj.client_id
        WHERE sj.triggered_at >= now() - make_interval(days => $1)
          AND c.is_recruiter = false
        GROUP BY sj.scraper_type
        ORDER BY total DESC
        """,
        days,
    )

    by_tool = [
        {
            "tool":    r["scraper_type"],
            "success": int(r["success"] or 0),
            "failed":  int(r["failed"]  or 0),
            "total":   int(r["total"]   or 0),
            "rate":    round(int(r["success"] or 0) / int(r["total"]) * 100, 1) if int(r["total"]) else 0,
        }
        for r in tool_rows
    ]

    # C05 — failure reasons from error_msg
    fail_rows = await conn.fetch(
        """
        SELECT
            COALESCE(NULLIF(TRIM(sj.error_msg), ''), 'Unknown error') AS reason,
            COUNT(*) AS count
        FROM scraping_jobs sj
        JOIN clients c ON c.id = sj.client_id
        WHERE sj.status = 'failed'
          AND sj.triggered_at >= now() - make_interval(days => $1)
          AND c.is_recruiter = false
        GROUP BY reason
        ORDER BY count DESC
        LIMIT 10
        """,
        days,
    )

    return {
        "data": {
            "by_tool": by_tool,
            "failure_reasons": [{"reason": r["reason"], "count": int(r["count"])} for r in fail_rows],
        },
        "ok": True,
    }


# ── C07–C15 : WhatsApp Outreach ───────────────────────────────────────────────

@router.get("/wf1/outreach")
async def wf1_outreach(
    period: str = Query("day", pattern="^(day|week|month)$"),
    days: int = Query(30, ge=1, le=365),
    conn: asyncpg.Connection = Depends(get_conn),
):
    trunc = {"day": "day", "week": "week", "month": "month"}[period]

    # C07, C08, C09 — unique clients reached/delivered/read per period (excludes recruiters)
    msg_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', COALESCE(wm.sent_at, wm.created_at) AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COUNT(DISTINCT wm.client_id) FILTER (WHERE wm.direction = 'outbound')                                        AS sent,
            COUNT(DISTINCT wm.client_id) FILTER (WHERE wm.status IN ('delivered','read'))                                AS delivered,
            COUNT(DISTINCT wm.client_id) FILTER (WHERE wm.status = 'read')                                               AS read_count,
            COUNT(DISTINCT wm.client_id) FILTER (WHERE wm.direction = 'inbound')                                         AS responses,
            COUNT(DISTINCT wm.client_id) FILTER (WHERE wm.status LIKE 'failed_%')                                        AS failed,
            COUNT(DISTINCT wm.client_id) FILTER (WHERE wm.status = 'failed_131049')                                      AS opted_out,
            COUNT(DISTINCT wm.client_id) FILTER (WHERE wm.status LIKE 'failed_%'
                                                   AND wm.status != 'failed_131049')                                     AS bad_phone
        FROM whatsapp_messages wm
        JOIN clients c ON c.id = wm.client_id
        WHERE wm.message_type = 'client_reachout'
          AND wm.created_at >= now() - make_interval(days => $1)
          AND c.is_recruiter = false
          AND NOT wm.excluded_from_analytics
        GROUP BY period
        ORDER BY period
        """,
        days,
    )

    # C07 per-client breakdown
    client_rows = await conn.fetch(
        """
        SELECT
            c.id::text                                                          AS client_id,
            c.company_name,
            c.stage::text                                                       AS stage,
            COUNT(*) FILTER (WHERE wm.direction = 'outbound')                  AS sent,
            COUNT(*) FILTER (WHERE wm.status IN ('delivered','read'))          AS delivered,
            COUNT(*) FILTER (WHERE wm.status = 'read')                         AS read_count,
            COUNT(*) FILTER (WHERE wm.direction = 'inbound')                              AS responses,
            COUNT(*) FILTER (WHERE wm.status LIKE 'failed_%')                             AS failed,
            COUNT(*) FILTER (WHERE wm.status = 'failed_131049')                           AS opted_out,
            COUNT(*) FILTER (WHERE wm.status LIKE 'failed_%'
                               AND wm.status != 'failed_131049')                          AS bad_phone,
            MAX(wm.sent_at)                                                                AS last_sent_at
        FROM whatsapp_messages wm
        JOIN clients c ON c.id = wm.client_id
        WHERE wm.message_type = 'client_reachout'
          AND wm.created_at >= now() - make_interval(days => $1)
          AND c.is_recruiter = false
          AND NOT wm.excluded_from_analytics
        GROUP BY c.id, c.company_name, c.stage
        ORDER BY MAX(wm.sent_at) DESC NULLS LAST
        """,
        days,
    )

    per_client = [
        {
            "client_id":   r["client_id"],
            "company_name": r["company_name"],
            "stage":       r["stage"],
            "sent":        int(r["sent"]       or 0),
            "delivered":   int(r["delivered"]  or 0),
            "read":        int(r["read_count"] or 0),
            "responses":   int(r["responses"]  or 0),
            "failed":      int(r["failed"]     or 0),
            "opted_out":   int(r["opted_out"]  or 0),
            "bad_phone":   int(r["bad_phone"]  or 0),
            "last_sent_at": r["last_sent_at"].isoformat() if r["last_sent_at"] else None,
        }
        for r in client_rows
    ]

    per_period = [
        {
            "period":     str(r["period"]),
            "sent":       int(r["sent"]       or 0),
            "delivered":  int(r["delivered"]  or 0),
            "read":       int(r["read_count"] or 0),
            "responses":  int(r["responses"]  or 0),
            "failed":     int(r["failed"]     or 0),
            "opted_out":  int(r["opted_out"]  or 0),
            "bad_phone":  int(r["bad_phone"]  or 0),
        }
        for r in msg_rows
    ]

    # C10 — engagement rate by heat: clients who moved past reachout_sent (interested/agreement_sent/onboarded)
    heat_rows = await conn.fetch(
        """
        SELECT
            CASE
                WHEN segment IN ('ca_network') OR source IN ('ca_network','hr_contacts') THEN 'cold'
                WHEN segment IN ('active_job_post','employer_lead') OR source = 'inbound' THEN 'hot'
                ELSE 'other'
            END AS heat,
            COUNT(*) FILTER (WHERE stage::text IN (
                'reachout_sent','interested','agreement_sent','onboarded','disqualified'
            )) AS sent,
            COUNT(*) FILTER (WHERE stage::text IN (
                'interested','agreement_sent','onboarded'
            )) AS responses
        FROM clients
        WHERE created_at >= now() - make_interval(days => $1)
          AND is_recruiter = false
        GROUP BY heat
        """,
        days,
    )

    response_by_heat = {}
    for r in heat_rows:
        sent = int(r["sent"] or 0)
        resp = int(r["responses"] or 0)
        response_by_heat[r["heat"]] = {
            "sent": sent,
            "responses": resp,
            "rate": round(resp / sent * 100, 1) if sent else 0,
        }

    # C11 — overall engagement rate (reached out → interested/agreement_sent/onboarded + disqualified)
    cta_stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE stage::text IN ('reachout_sent','interested','agreement_sent','onboarded','disqualified')) AS reachout_total,
            COUNT(*) FILTER (WHERE stage::text IN ('interested','agreement_sent','onboarded'))                                AS cta_clicked,
            COUNT(*) FILTER (WHERE stage::text = 'disqualified')                                                             AS disqualified
        FROM clients
        WHERE created_at >= now() - make_interval(days => $1)
          AND is_recruiter = false
        """,
        days,
    )
    reachout_total = int(cta_stats["reachout_total"] or 0)
    cta_clicked    = int(cta_stats["cta_clicked"]    or 0)
    disqualified   = int(cta_stats["disqualified"]   or 0)

    # C13 — negative responses: clients marked disqualified from reachout stage
    neg_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', pe.created_at AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COUNT(*) AS count
        FROM pipeline_events pe
        WHERE pe.to_stage = 'disqualified'
          AND pe.from_stage = 'reachout_sent'
          AND pe.created_at >= now() - make_interval(days => $1)
        GROUP BY period
        ORDER BY period
        """,
        days,
    )

    # C14 — inbound lead volume per period (excludes recruiters)
    inbound_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', created_at AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COUNT(*) AS count
        FROM clients
        WHERE source = 'inbound'
          AND is_recruiter = false
          AND created_at >= now() - make_interval(days => $1)
        GROUP BY period
        ORDER BY period
        """,
        days,
    )

    # C15 — median time to first response (outbound sent → inbound reply)
    response_time = await conn.fetchrow(
        """
        WITH outbound AS (
            SELECT client_id, MIN(sent_at) AS first_sent
            FROM whatsapp_messages
            WHERE direction = 'outbound' AND message_type = 'client_reachout' AND sent_at IS NOT NULL
            GROUP BY client_id
        ),
        inbound AS (
            SELECT client_id, MIN(created_at) AS first_reply
            FROM whatsapp_messages
            WHERE direction = 'inbound'
            GROUP BY client_id
        )
        SELECT
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                EXTRACT(EPOCH FROM (i.first_reply - o.first_sent)) / 3600
            ) AS p50_hours,
            PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY
                EXTRACT(EPOCH FROM (i.first_reply - o.first_sent)) / 3600
            ) AS p90_hours
        FROM outbound o
        JOIN inbound i ON i.client_id = o.client_id
        WHERE i.first_reply > o.first_sent
        """
    )

    return {
        "data": {
            "per_period": per_period,
            "per_client": per_client,
            "response_by_heat": response_by_heat,
            "cta": {
                "reachout_total": reachout_total,
                "cta_clicked": cta_clicked,
                "disqualified": disqualified,
                "rate": round(cta_clicked / reachout_total * 100, 1) if reachout_total else 0,
                "disqualified_rate": round(disqualified / reachout_total * 100, 1) if reachout_total else 0,
            },
            "negative_per_period": [
                {"period": str(r["period"]), "count": int(r["count"])} for r in neg_rows
            ],
            "inbound_per_period": [
                {"period": str(r["period"]), "count": int(r["count"])} for r in inbound_rows
            ],
            "response_time": {
                "p50_hours": round(float(response_time["p50_hours"]), 1) if response_time["p50_hours"] else None,
                "p90_hours": round(float(response_time["p90_hours"]), 1) if response_time["p90_hours"] else None,
            },
        },
        "ok": True,
    }


# ── C16–C20 : Onboarding ──────────────────────────────────────────────────────

@router.get("/wf1/onboarding")
async def wf1_onboarding(
    period: str = Query("day", pattern="^(day|week|month)$"),
    days: int = Query(30, ge=1, le=365),
    conn: asyncpg.Connection = Depends(get_conn),
):
    trunc = {"day": "day", "week": "week", "month": "month"}[period]

    # C16/C19 — funnel: direct count of clients currently at each stage
    funnel = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE stage::text = 'reachout_sent') AS reachout_sent,
            COUNT(*) FILTER (WHERE stage::text = 'reachout_sent'
                AND EXISTS (
                    SELECT 1 FROM whatsapp_messages wm
                    WHERE wm.client_id = c.id
                      AND wm.message_type = 'client_reachout'
                      AND wm.status IN ('delivered','read')
                )) AS reachout_delivered,
            COUNT(*) FILTER (WHERE stage::text = 'interested')     AS interested,
            COUNT(*) FILTER (WHERE stage::text = 'agreement_sent') AS agreement_sent,
            COUNT(*) FILTER (WHERE stage::text = 'onboarded')      AS onboarded
        FROM clients c
        WHERE c.created_at >= now() - make_interval(days => $1)
          AND c.is_recruiter = false
        """,
        days,
    )

    reachout_sent      = int(funnel["reachout_sent"]      or 0)
    reachout_delivered = int(funnel["reachout_delivered"] or 0)
    interested         = int(funnel["interested"]         or 0)
    agreement_sent     = int(funnel["agreement_sent"]     or 0)
    onboarded          = int(funnel["onboarded"]          or 0)

    # Weekly trend: new clients reaching each stage per period
    trend_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', updated_at AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COUNT(*) FILTER (WHERE stage::text IN ('interested','agreement_sent','onboarded')) AS interested,
            COUNT(*) FILTER (WHERE stage::text IN ('agreement_sent','onboarded'))             AS agreement_sent,
            COUNT(*) FILTER (WHERE stage::text = 'onboarded')                                 AS placed
        FROM clients
        WHERE updated_at >= now() - make_interval(days => $1)
          AND stage::text IN ('interested','agreement_sent','onboarded')
          AND is_recruiter = false
        GROUP BY period
        ORDER BY period
        """,
        days,
    )

    return {
        "data": {
            "funnel": [
                {"stage": "Reached Out (Sent)",      "count": reachout_sent,      "pct": 100},
                {"stage": "Reached Out (Delivered)", "count": reachout_delivered, "pct": round(reachout_delivered / reachout_sent * 100, 1)      if reachout_sent      else 0},
                {"stage": "Interested",              "count": interested,         "pct": round(interested / reachout_sent * 100, 1)              if reachout_sent      else 0},
                {"stage": "Form Filled & Agreement", "count": agreement_sent,     "pct": round(agreement_sent / interested * 100, 1)             if interested         else 0},
                {"stage": "Onboarded",               "count": onboarded,          "pct": round(onboarded / agreement_sent * 100, 1)              if agreement_sent     else 0},
            ],
            "trend": [
                {
                    "period":        str(r["period"]),
                    "interested":    int(r["interested"]    or 0),
                    "agreement_sent": int(r["agreement_sent"] or 0),
                    "placed":        int(r["placed"]        or 0),
                }
                for r in trend_rows
            ],
        },
        "ok": True,
    }


# ── C21–C26 : Pipeline Health ─────────────────────────────────────────────────

@router.get("/wf1/pipeline")
async def wf1_pipeline(
    days: int = Query(90, ge=1, le=730),
    conn: asyncpg.Connection = Depends(get_conn),
):
    # C21 — full funnel (till date, excludes recruiters)
    funnel_rows = await conn.fetch(
        """
        SELECT stage::text AS stage, COUNT(*) AS count
        FROM clients
        WHERE is_recruiter = false
        GROUP BY stage
        """
    )
    stage_order = ["lead", "scraping", "scraped", "reachout_sent", "interested",
                   "agreement_sent", "onboarded", "deal_won", "disqualified"]
    stage_map = {r["stage"]: int(r["count"]) for r in funnel_rows}
    total = sum(stage_map.values())

    funnel = [
        {
            "stage": s,
            "count": stage_map.get(s, 0),
            "pct":   round(stage_map.get(s, 0) / total * 100, 1) if total else 0,
        }
        for s in stage_order
    ]

    # C22 — deal status: In Progress / Won (deal_won) / Lost (disqualified)
    deal_status = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE stage::text IN ('reachout_sent','interested','agreement_sent','onboarded')) AS wip,
            COUNT(*) FILTER (WHERE stage::text = 'deal_won')                                                   AS won,
            COUNT(*) FILTER (WHERE stage::text IN ('disqualified','churned'))                                  AS lost
        FROM clients
        WHERE is_recruiter = false
        """
    )

    # C23 — monthly won rate trend (excludes recruiters)
    won_trend = await conn.fetch(
        """
        SELECT
            DATE_TRUNC('month', created_at AT TIME ZONE 'Asia/Kolkata')::date AS month,
            COUNT(*) FILTER (WHERE stage::text = 'onboarded')    AS won,
            COUNT(*) FILTER (WHERE stage::text = 'disqualified') AS lost,
            COUNT(*) AS total
        FROM clients
        WHERE created_at >= now() - make_interval(days => $1)
          AND is_recruiter = false
        GROUP BY month
        ORDER BY month
        """,
        days,
    )

    # C24 — loss reasons from pipeline_events notes (disqualified transitions)
    loss_reasons = await conn.fetch(
        """
        SELECT
            COALESCE(NULLIF(TRIM(note), ''), 'No reason given') AS reason,
            COUNT(*) AS count
        FROM pipeline_events
        WHERE to_stage = 'disqualified'
        GROUP BY reason
        ORDER BY count DESC
        LIMIT 10
        """
    )

    # C25 — deal cycle time (agreement_sent → onboarded)
    cycle_time = await conn.fetchrow(
        """
        WITH agreement AS (
            SELECT client_id, MIN(created_at) AS agreement_at
            FROM pipeline_events
            WHERE to_stage = 'agreement_sent'
            GROUP BY client_id
        ),
        placed AS (
            SELECT client_id, MIN(created_at) AS placed_at
            FROM pipeline_events
            WHERE to_stage = 'onboarded'
            GROUP BY client_id
        )
        SELECT
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (p.placed_at - a.agreement_at)) / 86400
            ) AS p50_days,
            PERCENTILE_CONT(0.9) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (p.placed_at - a.agreement_at)) / 86400
            ) AS p90_days
        FROM agreement a
        JOIN placed p ON p.client_id = a.client_id
        WHERE p.placed_at > a.agreement_at
        """
    )

    # C26 — stage stall: clients stuck in stage > 7 days (excludes recruiters)
    stall_rows = await conn.fetch(
        """
        SELECT
            stage::text AS stage,
            COUNT(*) FILTER (WHERE updated_at < now() - interval '7 days')  AS stalled,
            COUNT(*) AS total
        FROM clients
        WHERE stage::text NOT IN ('onboarded','disqualified')
          AND is_recruiter = false
        GROUP BY stage
        """
    )

    return {
        "data": {
            "funnel": funnel,
            "deal_status": {
                "wip":  int(deal_status["wip"]  or 0),
                "won":  int(deal_status["won"]  or 0),
                "lost": int(deal_status["lost"] or 0),
            },
            "won_trend": [
                {
                    "month": str(r["month"]),
                    "won":   int(r["won"]   or 0),
                    "lost":  int(r["lost"]  or 0),
                    "total": int(r["total"] or 0),
                    "rate":  round(int(r["won"] or 0) / int(r["total"]) * 100, 1) if int(r["total"]) else 0,
                }
                for r in won_trend
            ],
            "loss_reasons": [{"reason": r["reason"], "count": int(r["count"])} for r in loss_reasons],
            "cycle_time": {
                "p50_days": round(float(cycle_time["p50_days"]), 1) if cycle_time and cycle_time["p50_days"] else None,
                "p90_days": round(float(cycle_time["p90_days"]), 1) if cycle_time and cycle_time["p90_days"] else None,
            },
            "stall_rates": [
                {
                    "stage":   r["stage"],
                    "stalled": int(r["stalled"] or 0),
                    "total":   int(r["total"]   or 0),
                    "pct":     round(int(r["stalled"] or 0) / int(r["total"]) * 100, 1) if int(r["total"]) else 0,
                }
                for r in stall_rows
            ],
        },
        "ok": True,
    }


# ── C27–C30 : Batch Analytics ─────────────────────────────────────────────────

@router.get("/wf1/batches")
async def wf1_batches(
    days: int = Query(90, ge=1, le=730),
    conn: asyncpg.Connection = Depends(get_conn),
):
    # C27 — cohort table: group clients by the day they entered reachout_sent
    cohort_rows = await conn.fetch(
        """
        WITH batch_date AS (
            SELECT client_id, DATE(MIN(created_at) AT TIME ZONE 'Asia/Kolkata') AS batch_day
            FROM pipeline_events
            WHERE to_stage = 'reachout_sent'
            GROUP BY client_id
        )
        SELECT
            bd.batch_day,
            COUNT(DISTINCT c.id)                                                                AS leads,
            COUNT(DISTINCT sj.client_id)                                                        AS enriched,
            COUNT(DISTINCT bd.client_id)                                                        AS messaged,
            COUNT(DISTINCT c.id) FILTER (WHERE c.stage::text IN (
                'interested','agreement_sent','onboarded'))                                     AS responded,
            COUNT(DISTINCT c.id) FILTER (WHERE c.stage::text IN (
                'interested','agreement_sent','onboarded'))                                     AS cta_clicked,
            COUNT(DISTINCT c.id) FILTER (WHERE c.stage::text IN (
                'agreement_sent','onboarded'))                                                  AS agreements,
            COUNT(DISTINCT c.id) FILTER (WHERE c.stage::text = 'onboarded')                   AS placements
        FROM batch_date bd
        JOIN clients c ON c.id = bd.client_id
        LEFT JOIN scraping_jobs sj ON sj.client_id = bd.client_id AND sj.status = 'completed'
        LEFT JOIN analytics_batch_notes abn ON abn.batch_day = bd.batch_day
        WHERE bd.batch_day >= CURRENT_DATE - make_interval(days => $1)
          AND c.is_recruiter = false
        GROUP BY bd.batch_day, abn.note, abn.skipped
        ORDER BY bd.batch_day DESC
        """,
        days,
    )

    cohort = [
        {
            "batch_day":   str(r["batch_day"]),
            "messaged":    int(r["messaged"]    or 0),
            "enriched":    int(r["enriched"]    or 0),
            "responded":   int(r["responded"]   or 0),
            "cta_clicked": int(r["cta_clicked"] or 0),
            "agreements":  int(r["agreements"]  or 0),
            "placements":  int(r["placements"]  or 0),
            "conv_rate":   round(int(r["agreements"] or 0) / int(r["messaged"]) * 100, 1) if int(r["messaged"]) else 0,
            "skipped":     bool(r["skipped"]),
            "note":        r["note"],
        }
        for r in cohort_rows
    ]

    comparable = [row for row in cohort if not row["skipped"]]

    # C28 — best batches by agreement conversion
    best = sorted(comparable, key=lambda x: x["conv_rate"], reverse=True)[:5]

    # C29 — batch size vs conversion (scatter data)
    scatter = [{"batch_size": r["messaged"], "conv_rate": r["conv_rate"]} for r in comparable if r["messaged"] > 0]

    return {
        "data": {
            "cohort": cohort,
            "best_batches": best,
            "scatter": scatter,
        },
        "ok": True,
    }


# ══════════════════════════════════════════════════════════════════════════════
# WF2 — MATCHMAKING ANALYTICS (M01–M25)
# ══════════════════════════════════════════════════════════════════════════════

def _jd_path(job_title: str | None) -> str:
    jt = (job_title or "").lower()
    if "data entry" in jt or "deo" in jt:
        return "DEO"
    if "junior" in jt:
        return "Junior Accountant"
    if "accountant" in jt or "accounts" in jt or "senior" in jt:
        return "Accountant"
    return "Other"


def _filter_category(reason: str | None) -> str:
    r = (reason or "").lower()
    if "salary" in r:
        return "Salary Ceiling"
    if "travel" in r or "radius" in r or "km" in r:
        return "Travel Radius"
    if "role" in r or "mismatch" in r:
        return "Role Alignment"
    if "employment" in r or "part time" in r:
        return "Employment Type"
    if "work zone" in r or "industrial" in r or "corporate" in r or "ca firm" in r:
        return "Work Zone"
    if "fresher" in r:
        return "Freshers"
    if "geocod" in r:
        return "Location Not Geocoded"
    return "Other"


# ── M01–M03 : Hard Filter ─────────────────────────────────────────────────────

@router.get("/wf2/filter")
async def wf2_filter(
    limit: int = Query(20, ge=1, le=50),
    days: int  = Query(90, ge=1, le=730),
    conn: asyncpg.Connection = Depends(get_conn),
):
    # M01 + M02 — per-JD entry counts and pass rates (most recent `limit` JDs)
    jd_rows = await conn.fetch(
        """
        SELECT
            c.id::text                                      AS client_id,
            c.company_name,
            c.job_title,
            COUNT(*)                                        AS total,
            COUNT(*) FILTER (WHERE ms.filter_passed = true) AS passed,
            COUNT(*) FILTER (WHERE ms.filter_passed = false) AS failed
        FROM mms_scores ms
        JOIN clients c ON c.id = ms.client_id
        WHERE ms.updated_at >= now() - make_interval(days => $1)
        GROUP BY c.id, c.company_name, c.job_title
        ORDER BY MAX(ms.updated_at) DESC
        LIMIT $2
        """,
        days, limit,
    )

    per_jd = [
        {
            "client_id":    r["client_id"],
            "label":        f"{r['company_name'] or 'Unknown'} — {r['job_title'] or '?'}",
            "path":         _jd_path(r["job_title"]),
            "total":        int(r["total"]  or 0),
            "passed":       int(r["passed"] or 0),
            "failed":       int(r["failed"] or 0),
            "pass_rate":    round(int(r["passed"] or 0) / int(r["total"]) * 100, 1) if int(r["total"]) else 0,
        }
        for r in jd_rows
    ]

    # M03 — rejection breakdown by filter rule (aggregated)
    reason_rows = await conn.fetch(
        """
        SELECT filter_exclusion_reason AS reason, COUNT(*) AS count
        FROM mms_scores
        WHERE filter_passed = false
          AND filter_exclusion_reason IS NOT NULL
          AND updated_at >= now() - make_interval(days => $1)
        GROUP BY filter_exclusion_reason
        ORDER BY count DESC
        """,
        days,
    )

    from collections import defaultdict
    category_map: dict[str, int] = defaultdict(int)
    for r in reason_rows:
        category_map[_filter_category(r["reason"])] += int(r["count"])

    rejection_by_category = [
        {"reason": k, "count": v}
        for k, v in sorted(category_map.items(), key=lambda x: -x[1])
    ]

    return {
        "data": {
            "per_jd": per_jd,
            "rejection_by_category": rejection_by_category,
        },
        "ok": True,
    }


# ── M04–M06 : MMS Scoring ─────────────────────────────────────────────────────

@router.get("/wf2/scoring")
async def wf2_scoring(
    days: int = Query(90, ge=1, le=730),
    conn: asyncpg.Connection = Depends(get_conn),
):
    # M04 — score distribution (0–100 in bins of 10) across all JDs
    dist_rows = await conn.fetch(
        """
        SELECT
            (FLOOR(mms_score / 10) * 10)::int AS bucket,
            COUNT(*) AS count
        FROM mms_scores
        WHERE filter_passed = true
          AND mms_score IS NOT NULL
          AND updated_at >= now() - make_interval(days => $1)
        GROUP BY bucket
        ORDER BY bucket
        """,
        days,
    )

    buckets = {i: 0 for i in range(0, 100, 10)}
    for r in dist_rows:
        b = min(int(r["bucket"] or 0), 90)
        buckets[b] = int(r["count"])
    score_distribution = [{"bucket": f"{k}–{k+9}", "count": v} for k, v in sorted(buckets.items())]

    # M05 — average score by JD path (Accountant vs DEO)
    path_rows = await conn.fetch(
        """
        SELECT
            c.job_title,
            AVG(ms.mms_score) AS avg_score,
            COUNT(*)          AS count
        FROM mms_scores ms
        JOIN clients c ON c.id = ms.client_id
        WHERE ms.filter_passed = true
          AND ms.mms_score IS NOT NULL
          AND ms.updated_at >= now() - make_interval(days => $1)
        GROUP BY c.job_title
        """,
        days,
    )

    path_agg: dict[str, dict] = {}
    for r in path_rows:
        path = _jd_path(r["job_title"])
        if path not in path_agg:
            path_agg[path] = {"total_score": 0.0, "count": 0}
        path_agg[path]["total_score"] += float(r["avg_score"] or 0) * int(r["count"])
        path_agg[path]["count"]       += int(r["count"])

    avg_by_path = [
        {
            "path":      path,
            "avg_score": round(v["total_score"] / v["count"], 1) if v["count"] else 0,
            "count":     v["count"],
        }
        for path, v in path_agg.items()
    ]

    # M06 — candidates scoring ≥ 70 per JD (most recent 20)
    pool_rows = await conn.fetch(
        """
        SELECT
            c.id::text   AS client_id,
            c.company_name,
            c.job_title,
            COUNT(*) FILTER (WHERE ms.mms_score >= 70 AND NOT ms.dnd_flagged) AS pool_worthy,
            COUNT(*) FILTER (WHERE ms.filter_passed = true)                    AS passed_filter
        FROM mms_scores ms
        JOIN clients c ON c.id = ms.client_id
        WHERE ms.filter_passed = true
          AND ms.updated_at >= now() - make_interval(days => $1)
        GROUP BY c.id, c.company_name, c.job_title
        ORDER BY MAX(ms.updated_at) DESC
        LIMIT 20
        """,
        days,
    )

    pool_per_jd = [
        {
            "label":        f"{r['company_name'] or 'Unknown'} — {r['job_title'] or '?'}",
            "pool_worthy":  int(r["pool_worthy"]    or 0),
            "passed_filter": int(r["passed_filter"] or 0),
        }
        for r in pool_rows
    ]

    return {
        "data": {
            "score_distribution": score_distribution,
            "avg_by_path": avg_by_path,
            "pool_per_jd": pool_per_jd,
        },
        "ok": True,
    }


# ── M07–M11 : Pool Depth ──────────────────────────────────────────────────────

@router.get("/wf2/pool")
async def wf2_pool(
    days: int = Query(90, ge=1, le=730),
    conn: asyncpg.Connection = Depends(get_conn),
):
    # M08 — Stage 2 trigger rate (sourcing_needed flag)
    stage2 = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE sourcing_needed = true) AS triggered,
            COUNT(*)                                        AS total
        FROM clients
        WHERE updated_at >= now() - make_interval(days => $1)
          AND stage::text NOT IN ('lead','scraping','scraped','disqualified')
        """,
        days,
    )
    s2_triggered = int(stage2["triggered"] or 0)
    s2_total     = int(stage2["total"]     or 0)

    # Monthly trend of Stage 2 triggers
    s2_trend = await conn.fetch(
        """
        SELECT
            DATE_TRUNC('month', updated_at AT TIME ZONE 'Asia/Kolkata')::date AS month,
            COUNT(*) FILTER (WHERE sourcing_needed = true) AS triggered,
            COUNT(*) AS total
        FROM clients
        WHERE updated_at >= now() - make_interval(days => $1)
          AND stage::text NOT IN ('lead','scraping','scraped','disqualified')
        GROUP BY month
        ORDER BY month
        """,
        days,
    )

    # M09 — scoring_path per JD (Path A DB reuse vs Path B fresh sourcing)
    path_rows = await conn.fetch(
        """
        SELECT
            c.id::text AS client_id,
            c.company_name,
            c.job_title,
            COUNT(*) FILTER (WHERE ms.scoring_path ILIKE '%a%' OR ms.scoring_path ILIKE '%db%') AS path_a,
            COUNT(*) FILTER (WHERE ms.scoring_path ILIKE '%b%' OR ms.scoring_path ILIKE '%source%') AS path_b,
            COUNT(*) AS total
        FROM mms_scores ms
        JOIN clients c ON c.id = ms.client_id
        WHERE ms.filter_passed = true
          AND ms.updated_at >= now() - make_interval(days => $1)
        GROUP BY c.id, c.company_name, c.job_title
        ORDER BY MAX(ms.updated_at) DESC
        LIMIT 20
        """,
        days,
    )

    sourcing_per_jd = [
        {
            "label":  f"{r['company_name'] or 'Unknown'} — {r['job_title'] or '?'}",
            "path_a": int(r["path_a"] or 0),
            "path_b": int(r["path_b"] or 0),
            "total":  int(r["total"]  or 0),
        }
        for r in path_rows
    ]

    # M11 — lock status across active candidates (currently locked vs available)
    lock_stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE cl.unlocked_at IS NULL
                AND (cl.expires_at IS NULL OR cl.expires_at > now())) AS locked,
            COUNT(*) AS total
        FROM candidate_locks cl
        """
    )
    locked    = int(lock_stats["locked"] or 0) if lock_stats else 0
    total_lk  = int(lock_stats["total"]  or 0) if lock_stats else 0
    available = total_lk - locked

    return {
        "data": {
            "stage2": {
                "triggered": s2_triggered,
                "total":     s2_total,
                "rate":      round(s2_triggered / s2_total * 100, 1) if s2_total else 0,
            },
            "stage2_trend": [
                {
                    "month":     str(r["month"]),
                    "triggered": int(r["triggered"] or 0),
                    "total":     int(r["total"]     or 0),
                    "rate":      round(int(r["triggered"] or 0) / int(r["total"]) * 100, 1) if int(r["total"]) else 0,
                }
                for r in s2_trend
            ],
            "sourcing_per_jd": sourcing_per_jd,
            "lock_status": {
                "locked":    locked,
                "available": available,
                "total":     total_lk,
                "locked_pct": round(locked / total_lk * 100, 1) if total_lk else 0,
            },
        },
        "ok": True,
    }


# ── M12–M15 : Interest Batching ───────────────────────────────────────────────

@router.get("/wf2/interest")
async def wf2_interest(
    days: int = Query(90, ge=1, le=730),
    conn: asyncpg.Connection = Depends(get_conn),
):
    # M13 — opt-in rate per JD + M12 delivery rate
    optin_rows = await conn.fetch(
        """
        SELECT
            c.id::text   AS client_id,
            c.company_name,
            c.job_title,
            COUNT(*) FILTER (WHERE ccm.wa_sent_at IS NOT NULL)                   AS jd_sent,
            COUNT(*) FILTER (WHERE ccm.stage::text IN (
                'interested','slot_booked','interview_scheduled',
                'interview_done','placed','rejected'))                             AS opted_in
        FROM client_candidate_mappings ccm
        JOIN clients c ON c.id = ccm.client_id
        WHERE ccm.wa_sent_at >= now() - make_interval(days => $1)
          OR  ccm.created_at >= now() - make_interval(days => $1)
        GROUP BY c.id, c.company_name, c.job_title
        HAVING COUNT(*) FILTER (WHERE ccm.wa_sent_at IS NOT NULL) > 0
        ORDER BY MAX(ccm.updated_at) DESC
        LIMIT 20
        """,
        days,
    )

    optin_per_jd = [
        {
            "label":     f"{r['company_name'] or 'Unknown'} — {r['job_title'] or '?'}",
            "path":      _jd_path(r["job_title"]),
            "jd_sent":   int(r["jd_sent"]   or 0),
            "opted_in":  int(r["opted_in"]  or 0),
            "optin_rate": round(int(r["opted_in"] or 0) / int(r["jd_sent"]) * 100, 1) if int(r["jd_sent"]) else 0,
        }
        for r in optin_rows
    ]

    # Overall opt-in stats
    overall = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE ccm.wa_sent_at IS NOT NULL) AS total_sent,
            COUNT(*) FILTER (WHERE ccm.stage::text IN (
                'interested','slot_booked','interview_scheduled',
                'interview_done','placed','rejected'))           AS total_opted
        FROM client_candidate_mappings ccm
        WHERE ccm.created_at >= now() - make_interval(days => $1)
        """,
        days,
    )

    # M14 — interviews booked per week
    interview_trend = await conn.fetch(
        """
        SELECT
            DATE_TRUNC('week', interview_slot AT TIME ZONE 'Asia/Kolkata')::date AS week,
            COUNT(*) AS count
        FROM client_candidate_mappings
        WHERE interview_slot IS NOT NULL
          AND interview_slot >= now() - make_interval(days => $1)
        GROUP BY week
        ORDER BY week
        """,
        days,
    )

    total_sent  = int(overall["total_sent"]  or 0)
    total_opted = int(overall["total_opted"] or 0)

    return {
        "data": {
            "optin_per_jd": optin_per_jd,
            "overall": {
                "total_sent":   total_sent,
                "total_opted":  total_opted,
                "optin_rate":   round(total_opted / total_sent * 100, 1) if total_sent else 0,
            },
            "interview_trend": [
                {"week": str(r["week"]), "count": int(r["count"])} for r in interview_trend
            ],
        },
        "ok": True,
    }


# ── M18–M25 : Post-Interview Outcomes ─────────────────────────────────────────

@router.get("/wf2/outcomes")
async def wf2_outcomes(
    days: int = Query(90, ge=1, le=730),
    conn: asyncpg.Connection = Depends(get_conn),
):
    # M18 — feedback submission rate
    feedback_stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE interview_done = true)                         AS interviews_done,
            COUNT(*) FILTER (WHERE interview_done = true
                AND feedback_client IS NOT NULL)                                  AS client_submitted,
            COUNT(*) FILTER (WHERE interview_done = true
                AND feedback_candidate IS NOT NULL)                               AS cand_submitted
        FROM client_candidate_mappings
        WHERE updated_at >= now() - make_interval(days => $1)
        """,
        days,
    )
    done   = int(feedback_stats["interviews_done"]  or 0)
    cli_fb = int(feedback_stats["client_submitted"] or 0)
    can_fb = int(feedback_stats["cand_submitted"]   or 0)

    # M19 — outcome distribution from stages + feedback
    outcome_rows = await conn.fetch(
        """
        SELECT
            CASE
                WHEN stage::text = 'placed' THEN 'Mutual Match'
                WHEN stage::text = 'rejected'
                    AND feedback_client = 'disliked'
                    AND (feedback_candidate IS NULL OR feedback_candidate = 'liked') THEN 'Client Rejected'
                WHEN stage::text = 'rejected'
                    AND (feedback_client IS NULL OR feedback_client = 'liked')
                    AND feedback_candidate = 'disliked' THEN 'Candidate Rejected'
                WHEN stage::text = 'rejected' THEN 'Rejected (Both/Unknown)'
                WHEN interview_done = false
                    AND interview_slot < now() THEN 'No-Show'
                ELSE 'Pending'
            END AS outcome,
            COUNT(*) AS count
        FROM client_candidate_mappings
        WHERE interview_slot IS NOT NULL
          AND interview_slot >= now() - make_interval(days => $1)
        GROUP BY outcome
        ORDER BY count DESC
        """,
        days,
    )
    outcomes = [{"outcome": r["outcome"], "count": int(r["count"])} for r in outcome_rows]

    # M20 — client rejection reasons
    client_reasons = await conn.fetch(
        """
        SELECT
            COALESCE(NULLIF(TRIM(feedback_client_reason), ''), 'No reason given') AS reason,
            COUNT(*) AS count
        FROM client_candidate_mappings
        WHERE feedback_client = 'disliked'
          AND updated_at >= now() - make_interval(days => $1)
        GROUP BY reason
        ORDER BY count DESC
        LIMIT 10
        """,
        days,
    )

    # M21 — candidate rejection (feedback_candidate = 'disliked')
    cand_reject_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM client_candidate_mappings
        WHERE feedback_candidate = 'disliked'
          AND updated_at >= now() - make_interval(days => $1)
        """,
        days,
    )

    # M22 — no-show rate
    noshow_stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE interview_slot < now()
                AND interview_done = false
                AND stage::text NOT IN ('placed','rejected')) AS no_show,
            COUNT(*) FILTER (WHERE interview_slot IS NOT NULL
                AND interview_slot < now()) AS scheduled_past
        FROM client_candidate_mappings
        WHERE interview_slot >= now() - make_interval(days => $1)
        """,
        days,
    )
    no_show      = int(noshow_stats["no_show"]       or 0)
    sched_past   = int(noshow_stats["scheduled_past"] or 0)

    # M23 — mutual match → placement conversion
    placement_stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE stage::text = 'placed')         AS placed,
            COUNT(*) FILTER (WHERE feedback_client = 'liked'
                AND feedback_candidate = 'liked')                   AS mutual_match
        FROM client_candidate_mappings
        WHERE interview_done = true
          AND updated_at >= now() - make_interval(days => $1)
        """,
        days,
    )
    placed       = int(placement_stats["placed"]       or 0)
    mutual_match = int(placement_stats["mutual_match"] or 0)

    # M24 — cycle time: matching created_at → interview_slot
    cycle_time = await conn.fetchrow(
        """
        SELECT
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                EXTRACT(EPOCH FROM (interview_slot - created_at)) / 86400
            ) AS p50_days,
            PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY
                EXTRACT(EPOCH FROM (interview_slot - created_at)) / 86400
            ) AS p90_days
        FROM client_candidate_mappings
        WHERE interview_slot IS NOT NULL
          AND interview_slot >= now() - make_interval(days => $1)
          AND interview_slot > created_at
        """,
        days,
    )

    # M25 — re-trigger count per JD (clients who had multiple matching runs)
    retrigger_rows = await conn.fetch(
        """
        SELECT
            c.id::text   AS client_id,
            c.company_name,
            c.job_title,
            COUNT(DISTINCT ms.updated_at::date) AS run_days
        FROM mms_scores ms
        JOIN clients c ON c.id = ms.client_id
        WHERE ms.updated_at >= now() - make_interval(days => $1)
        GROUP BY c.id, c.company_name, c.job_title
        HAVING COUNT(DISTINCT ms.updated_at::date) > 1
        ORDER BY run_days DESC
        LIMIT 20
        """,
        days,
    )

    # Monthly outcome trend
    outcome_trend = await conn.fetch(
        """
        SELECT
            DATE_TRUNC('month', interview_slot AT TIME ZONE 'Asia/Kolkata')::date AS month,
            COUNT(*) FILTER (WHERE stage::text = 'placed')            AS placed,
            COUNT(*) FILTER (WHERE stage::text = 'rejected')          AS rejected,
            COUNT(*) FILTER (WHERE interview_done = false
                AND interview_slot < now())                            AS no_show,
            COUNT(*) AS total
        FROM client_candidate_mappings
        WHERE interview_slot IS NOT NULL
          AND interview_slot >= now() - make_interval(days => $1)
        GROUP BY month
        ORDER BY month
        """,
        days,
    )

    return {
        "data": {
            "feedback": {
                "interviews_done":  done,
                "client_submitted": cli_fb,
                "cand_submitted":   can_fb,
                "client_rate": round(cli_fb / done * 100, 1) if done else 0,
                "cand_rate":   round(can_fb / done * 100, 1) if done else 0,
            },
            "outcomes": outcomes,
            "outcome_trend": [
                {
                    "month":    str(r["month"]),
                    "placed":   int(r["placed"]   or 0),
                    "rejected": int(r["rejected"] or 0),
                    "no_show":  int(r["no_show"]  or 0),
                    "total":    int(r["total"]    or 0),
                }
                for r in outcome_trend
            ],
            "client_rejection_reasons": [
                {"reason": r["reason"], "count": int(r["count"])} for r in client_reasons
            ],
            "candidate_rejections": int(cand_reject_count or 0),
            "no_show": {
                "count": no_show,
                "scheduled_past": sched_past,
                "rate": round(no_show / sched_past * 100, 1) if sched_past else 0,
            },
            "placement": {
                "placed":        placed,
                "mutual_match":  mutual_match,
                "offer_rate":    round(placed / mutual_match * 100, 1) if mutual_match else 0,
            },
            "cycle_time": {
                "p50_days": round(float(cycle_time["p50_days"]), 1) if cycle_time and cycle_time["p50_days"] else None,
                "p90_days": round(float(cycle_time["p90_days"]), 1) if cycle_time and cycle_time["p90_days"] else None,
            },
            "retrigger_per_jd": [
                {
                    "label":    f"{r['company_name'] or 'Unknown'} — {r['job_title'] or '?'}",
                    "run_days": int(r["run_days"]),
                }
                for r in retrigger_rows
            ],
        },
        "ok": True,
    }


# ══════════════════════════════════════════════════════════════════════════════
# WF3 — CANDIDATE SOURCING ANALYTICS (K01–K23)
# ══════════════════════════════════════════════════════════════════════════════

_PORTAL_SOURCES = ("naukri", "apna", "workindia", "indeed", "jobshai", "linkedin",
                   "shine", "foundit", "monster", "sourced")


# ── K01–K05 : Precision Sourcing ──────────────────────────────────────────────

@router.get("/wf3/sourcing")
async def wf3_sourcing(
    period: str = Query("day", pattern="^(day|week|month)$"),
    days: int   = Query(30, ge=1, le=365),
    conn: asyncpg.Connection = Depends(get_conn),
):
    trunc = {"day": "day", "week": "week", "month": "month"}[period]

    # K01 — sourcing triggers: distinct (client, day) pairs when sourced candidates were injected
    trigger_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', ccm.created_at AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COUNT(DISTINCT ccm.client_id) AS triggers,
            COUNT(*) AS profiles_injected
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE c.source IN {tuple(_PORTAL_SOURCES) if len(_PORTAL_SOURCES) > 1 else f"('{_PORTAL_SOURCES[0]}')"}
          AND ccm.created_at >= now() - make_interval(days => $1)
        GROUP BY period
        ORDER BY period
        """,
        days,
    )

    # K03 — profiles by source portal
    portal_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', created_at AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COALESCE(NULLIF(LOWER(source), ''), 'unknown') AS portal,
            COUNT(*) AS count
        FROM candidates
        WHERE source IS NOT NULL
          AND source NOT IN ('inbound', 'onboarding_form', 'ca_network', 'hr_contacts')
          AND created_at >= now() - make_interval(days => $1)
        GROUP BY period, portal
        ORDER BY period, count DESC
        """,
        days,
    )

    from collections import defaultdict
    portal_map: dict = defaultdict(lambda: defaultdict(int))
    all_portals: set[str] = set()
    for r in portal_rows:
        portal_map[str(r["period"])][r["portal"]] += int(r["count"])
        all_portals.add(r["portal"])

    portal_trend = [
        {"period": p, **{port: v.get(port, 0) for port in all_portals}}
        for p, v in sorted(portal_map.items())
    ]
    portal_totals = [
        {"portal": port, "count": sum(portal_map[p].get(port, 0) for p in portal_map)}
        for port in sorted(all_portals, key=lambda x: -sum(portal_map[p].get(x, 0) for p in portal_map))
    ]

    # K05 — inbound candidates (organically submitted the form)
    inbound_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', created_at AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COUNT(*) AS count
        FROM candidates
        WHERE (source = 'inbound' OR form_submitted = true)
          AND created_at >= now() - make_interval(days => $1)
        GROUP BY period
        ORDER BY period
        """,
        days,
    )

    return {
        "data": {
            "trigger_trend": [
                {
                    "period":           str(r["period"]),
                    "triggers":         int(r["triggers"]          or 0),
                    "profiles_injected": int(r["profiles_injected"] or 0),
                }
                for r in trigger_rows
            ],
            "portal_trend":  portal_trend,
            "portal_totals": portal_totals,
            "all_portals":   sorted(all_portals),
            "inbound_trend": [
                {"period": str(r["period"]), "count": int(r["count"])} for r in inbound_rows
            ],
        },
        "ok": True,
    }


# ── K06–K10 : WA Outreach to Candidates ──────────────────────────────────────

@router.get("/wf3/outreach")
async def wf3_outreach(
    period: str = Query("week", pattern="^(day|week|month)$"),
    days: int   = Query(60, ge=1, le=365),
    conn: asyncpg.Connection = Depends(get_conn),
):
    trunc = {"day": "day", "week": "week", "month": "month"}[period]

    # K06 — JD delivery rate to candidates
    delivery_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', COALESCE(sent_at, created_at) AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COUNT(*) FILTER (WHERE direction = 'outbound')                   AS sent,
            COUNT(*) FILTER (WHERE status IN ('delivered','read'))           AS delivered,
            COUNT(*) FILTER (WHERE direction = 'inbound')                    AS responses
        FROM whatsapp_messages
        WHERE candidate_id IS NOT NULL
          AND message_type IN ('candidate_intent_ask','candidate_bulk')
          AND created_at >= now() - make_interval(days => $1)
        GROUP BY period
        ORDER BY period
        """,
        days,
    )

    # K07 + K08 — response distribution + interest rate from mapping stages
    response_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', ccm.wa_sent_at AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COUNT(*) FILTER (WHERE ccm.wa_sent_at IS NOT NULL) AS messaged,
            COUNT(*) FILTER (WHERE ccm.stage::text IN (
                'interested','slot_booked','interview_scheduled',
                'interview_done','placed','rejected')) AS interested,
            COUNT(*) FILTER (WHERE c.work_preference = 'not_looking') AS not_looking
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.wa_sent_at IS NOT NULL
          AND ccm.wa_sent_at >= now() - make_interval(days => $1)
        GROUP BY period
        ORDER BY period
        """,
        days,
    )

    # Overall response donut data
    overall_resp = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE ccm.wa_sent_at IS NOT NULL) AS total_messaged,
            COUNT(*) FILTER (WHERE ccm.stage::text IN (
                'interested','slot_booked','interview_scheduled',
                'interview_done','placed','rejected')) AS interested,
            COUNT(*) FILTER (WHERE c.work_preference = 'not_looking') AS not_looking,
            COUNT(*) FILTER (WHERE ccm.stage::text = 'intent_ask'
                AND ccm.wa_sent_at < now() - interval '48 hours') AS no_response
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.wa_sent_at >= now() - make_interval(days => $1)
        """,
        days,
    )

    total_msg   = int(overall_resp["total_messaged"] or 0)
    interested  = int(overall_resp["interested"]     or 0)
    not_looking = int(overall_resp["not_looking"]    or 0)
    no_response = int(overall_resp["no_response"]    or 0)
    other       = max(0, total_msg - interested - not_looking - no_response)

    # K10 — inbound WA messages from candidates (counter-questions / general)
    inbound_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', created_at AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COUNT(*) AS count
        FROM whatsapp_messages
        WHERE direction = 'inbound'
          AND candidate_id IS NOT NULL
          AND created_at >= now() - make_interval(days => $1)
        GROUP BY period
        ORDER BY period
        """,
        days,
    )

    return {
        "data": {
            "delivery_trend": [
                {
                    "period":    str(r["period"]),
                    "sent":      int(r["sent"]       or 0),
                    "delivered": int(r["delivered"]  or 0),
                    "responses": int(r["responses"]  or 0),
                    "delivery_rate": round(int(r["delivered"] or 0) / int(r["sent"]) * 100, 1) if int(r["sent"]) else 0,
                }
                for r in delivery_rows
            ],
            "response_trend": [
                {
                    "period":      str(r["period"]),
                    "messaged":    int(r["messaged"]    or 0),
                    "interested":  int(r["interested"]  or 0),
                    "not_looking": int(r["not_looking"] or 0),
                    "interest_rate": round(int(r["interested"] or 0) / int(r["messaged"]) * 100, 1) if int(r["messaged"]) else 0,
                }
                for r in response_rows
            ],
            "response_donut": [
                {"label": "Interested",   "count": interested,  "color": "#10b981"},
                {"label": "Not Looking",  "count": not_looking, "color": "#ef4444"},
                {"label": "No Response",  "count": no_response, "color": "#d1d5db"},
                {"label": "Not Interested", "count": other,     "color": "#f59e0b"},
            ],
            "overall": {
                "total_messaged": total_msg,
                "interested":     interested,
                "interest_rate":  round(interested / total_msg * 100, 1) if total_msg else 0,
            },
            "inbound_counter_trend": [
                {"period": str(r["period"]), "count": int(r["count"])} for r in inbound_rows
            ],
        },
        "ok": True,
    }


# ── K11–K16 : AI Technical Vetting ───────────────────────────────────────────

@router.get("/wf3/vetting")
async def wf3_vetting(
    period: str = Query("week", pattern="^(day|week|month)$"),
    days: int   = Query(90, ge=1, le=365),
    conn: asyncpg.Connection = Depends(get_conn),
):
    trunc = {"day": "day", "week": "week", "month": "month"}[period]

    # K11 + K12 + K15 — call attempt, pickup, completion from call_status
    call_stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE call_status IS NOT NULL)                                AS attempted,
            COUNT(*) FILTER (WHERE call_status IN ('COMPLETED','DROPPED'))                 AS picked_up,
            COUNT(*) FILTER (WHERE call_status = 'COMPLETED')                             AS completed,
            COUNT(*) FILTER (WHERE call_status = 'DROPPED')                               AS dropped,
            COUNT(*) FILTER (WHERE call_status = 'NO_ANSWER'
                OR call_status ILIKE '%no%answer%' OR call_status ILIKE '%not%pick%')     AS no_answer,
            COUNT(*) FILTER (WHERE form_submitted = true)                                  AS form_submitted
        FROM candidates
        WHERE updated_at >= now() - make_interval(days => $1)
        """,
        days,
    )

    attempted     = int(call_stats["attempted"]     or 0)
    picked_up     = int(call_stats["picked_up"]     or 0)
    completed     = int(call_stats["completed"]     or 0)
    dropped       = int(call_stats["dropped"]       or 0)
    form_sub      = int(call_stats["form_submitted"] or 0)

    # K14 — AI score distribution (total_score in bins of 10)
    score_dist = await conn.fetch(
        """
        SELECT
            (FLOOR(total_score / 10) * 10)::int AS bucket,
            COUNT(*) AS count
        FROM candidates
        WHERE total_score IS NOT NULL
          AND updated_at >= now() - make_interval(days => $1)
        GROUP BY bucket
        ORDER BY bucket
        """,
        days,
    )

    buckets = {i: 0 for i in range(0, 100, 10)}
    for r in score_dist:
        b = min(int(r["bucket"] or 0), 90)
        buckets[b] = int(r["count"])
    score_distribution = [{"bucket": f"{k}–{k+9}", "count": v} for k, v in sorted(buckets.items())]

    # K12 trend — pickup rate per period
    pickup_trend = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', updated_at AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COUNT(*) FILTER (WHERE call_status IS NOT NULL) AS attempted,
            COUNT(*) FILTER (WHERE call_status IN ('COMPLETED','DROPPED')) AS picked_up,
            COUNT(*) FILTER (WHERE call_status = 'COMPLETED') AS completed
        FROM candidates
        WHERE call_status IS NOT NULL
          AND updated_at >= now() - make_interval(days => $1)
        GROUP BY period
        ORDER BY period
        """,
        days,
    )

    # K16 — stability label distribution from candidates.labels array
    label_rows = await conn.fetch(
        """
        SELECT
            unnest(labels) AS label,
            COUNT(*) AS count
        FROM candidates
        WHERE labels IS NOT NULL
          AND array_length(labels, 1) > 0
          AND created_at >= now() - make_interval(days => $1)
        GROUP BY label
        ORDER BY count DESC
        """,
        days,
    )

    stability_labels = [
        {"label": r["label"], "count": int(r["count"])}
        for r in label_rows
        if any(kw in (r["label"] or "").lower() for kw in ("high", "average", "turnover", "stability", "risk"))
    ]

    return {
        "data": {
            "call_summary": {
                "form_submitted":   form_sub,
                "attempted":        attempted,
                "picked_up":        picked_up,
                "completed":        completed,
                "dropped":          dropped,
                "attempt_rate":     round(attempted   / form_sub  * 100, 1) if form_sub  else 0,
                "pickup_rate":      round(picked_up   / attempted * 100, 1) if attempted else 0,
                "completion_rate":  round(completed   / picked_up * 100, 1) if picked_up else 0,
            },
            "pickup_trend": [
                {
                    "period":       str(r["period"]),
                    "attempted":    int(r["attempted"]  or 0),
                    "picked_up":    int(r["picked_up"]  or 0),
                    "completed":    int(r["completed"]  or 0),
                    "pickup_rate":  round(int(r["picked_up"] or 0) / int(r["attempted"]) * 100, 1) if int(r["attempted"]) else 0,
                }
                for r in pickup_trend
            ],
            "score_distribution": score_distribution,
            "stability_labels": stability_labels,
        },
        "ok": True,
    }


# ── K17–K23 : Classification & DB ─────────────────────────────────────────────

@router.get("/wf3/db")
async def wf3_db(
    period: str = Query("week", pattern="^(day|week|month)$"),
    days: int   = Query(90, ge=1, le=365),
    conn: asyncpg.Connection = Depends(get_conn),
):
    trunc = {"day": "day", "week": "week", "month": "month"}[period]

    # K17 — classification outcome distribution (category + pass/fail)
    class_rows = await conn.fetch(
        """
        SELECT
            CASE
                WHEN evaluation_status ILIKE 'pass%' AND category IS NOT NULL AND category != 'Reject'
                    THEN COALESCE(category, 'Pass')
                WHEN evaluation_status ILIKE 'fail%' OR category = 'Reject' THEN 'Rejected'
                WHEN evaluation_status IS NULL AND category IS NOT NULL THEN category
                ELSE 'Unclassified'
            END AS classification,
            COUNT(*) AS count
        FROM candidates
        WHERE call_status IS NOT NULL
          AND updated_at >= now() - make_interval(days => $1)
        GROUP BY classification
        ORDER BY count DESC
        """,
        days,
    )

    classification = [{"label": r["classification"], "count": int(r["count"])} for r in class_rows]

    # K18 — DB composition: the 6 buckets (evaluated from current state)
    db_comp = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE evaluation_status ILIKE 'pass%'
                AND (dnd_until IS NULL OR dnd_until < now())) AS bucket_pass,
            COUNT(*) FILTER (WHERE dnd_until IS NOT NULL
                AND dnd_until > now()) AS bucket_dnd,
            COUNT(*) FILTER (WHERE work_preference = 'not_looking') AS bucket_not_looking,
            COUNT(*) FILTER (WHERE evaluation_status ILIKE 'fail%'
                OR category = 'Reject') AS bucket_rejected,
            COUNT(*) FILTER (WHERE interview_count > 0 AND evaluation_status ILIKE 'pass%') AS bucket_hired_via_ja,
            COUNT(*) FILTER (WHERE call_status IS NULL AND form_submitted = false
                AND (dnd_until IS NULL OR dnd_until < now())) AS bucket_no_response,
            COUNT(*) AS total
        FROM candidates
        """
    )

    db_buckets = [
        {"label": "Pass (Qualified)",     "count": int(db_comp["bucket_pass"]         or 0), "color": "#10b981"},
        {"label": "Rejected / DND (1yr)", "count": int(db_comp["bucket_dnd"]          or 0), "color": "#ef4444"},
        {"label": "Not Looking (4mo)",    "count": int(db_comp["bucket_not_looking"]   or 0), "color": "#f97316"},
        {"label": "Evaluation Failed",    "count": int(db_comp["bucket_rejected"]      or 0), "color": "#d1d5db"},
        {"label": "Hired via JA",         "count": int(db_comp["bucket_hired_via_ja"]  or 0), "color": "#a78bfa"},
        {"label": "No Response",          "count": int(db_comp["bucket_no_response"]   or 0), "color": "#94a3b8"},
    ]
    db_total = int(db_comp["total"] or 0)

    # K19 — DB growth: weekly new candidates
    growth_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', created_at AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COUNT(*) AS new_candidates
        FROM candidates
        WHERE created_at >= now() - make_interval(days => $1)
        GROUP BY period
        ORDER BY period
        """,
        days,
    )

    # Compute cumulative from DB start
    cumulative_base = await conn.fetchval(
        "SELECT COUNT(*) FROM candidates WHERE created_at < now() - make_interval(days => $1)",
        days,
    )
    cumulative = int(cumulative_base or 0)
    growth = []
    for r in growth_rows:
        cumulative += int(r["new_candidates"])
        growth.append({
            "period":         str(r["period"]),
            "new_candidates": int(r["new_candidates"]),
            "total":          cumulative,
        })

    # K21 — coaching report delivery
    report_stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE evaluation_status ILIKE 'pass%') AS total_pass,
            COUNT(*) FILTER (WHERE evaluation_status ILIKE 'pass%'
                AND final_evaluation_report_url IS NOT NULL) AS report_delivered
        FROM candidates
        """
    )
    pass_total       = int(report_stats["total_pass"]       or 0)
    report_delivered = int(report_stats["report_delivered"] or 0)

    # K22 — back-trigger volume: pass candidates per period
    backtrigger_rows = await conn.fetch(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', updated_at AT TIME ZONE 'Asia/Kolkata')::date AS period,
            COUNT(*) AS count
        FROM candidates
        WHERE evaluation_status ILIKE 'pass%'
          AND updated_at >= now() - make_interval(days => $1)
        GROUP BY period
        ORDER BY period
        """,
        days,
    )

    # K23 — back-triggered hard filter pass rate (sourced candidates in mms_scores)
    filter_pass = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE ms.filter_passed = true)  AS passed,
            COUNT(*)                                          AS total
        FROM mms_scores ms
        JOIN candidates c ON c.id = ms.candidate_id
        WHERE c.source IN ('sourced', 'apna', 'naukri', 'workindia', 'indeed',
                           'jobshai', 'linkedin', 'shine', 'foundit', 'monster')
          AND ms.updated_at >= now() - make_interval(days => $1)
        """,
        days,
    )
    fp_passed = int(filter_pass["passed"] or 0)
    fp_total  = int(filter_pass["total"]  or 0)

    return {
        "data": {
            "classification": classification,
            "db_buckets":     db_buckets,
            "db_total":       db_total,
            "growth":         growth,
            "report": {
                "pass_total":       pass_total,
                "report_delivered": report_delivered,
                "delivery_rate":    round(report_delivered / pass_total * 100, 1) if pass_total else 0,
            },
            "backtrigger_trend": [
                {"period": str(r["period"]), "count": int(r["count"])} for r in backtrigger_rows
            ],
            "filter_pass_rate": {
                "passed": fp_passed,
                "total":  fp_total,
                "rate":   round(fp_passed / fp_total * 100, 1) if fp_total else 0,
            },
        },
        "ok": True,
    }


# ─── WF4: Joining Tracker Analytics (T01–T12) ────────────────────────────────

@router.get("/wf4/overview")
async def wf4_overview(conn: asyncpg.Connection = Depends(get_conn)):
    """T01 active placements · T02 joining date adherence · T05 days-to-joining"""

    active = await conn.fetchval("""
        SELECT COUNT(*) FROM client_candidate_mappings
        WHERE placement_confirmed = true
          AND COALESCE(payment_collected,  false) = false
          AND COALESCE(pre_joining_churn,  false) = false
          AND COALESCE(post_joining_churn, false) = false
    """)

    adherence_row = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE joining_date IS NOT NULL)               AS expected,
            COUNT(*) FILTER (WHERE COALESCE(joined, false) = true)         AS actually_joined,
            ROUND(100.0 * COUNT(*) FILTER (WHERE COALESCE(joined, false) = true)
                  / NULLIF(COUNT(*) FILTER (WHERE joining_date IS NOT NULL), 0)) AS rate
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
    """)

    adherence_trend = await conn.fetch("""
        SELECT
            DATE_TRUNC('month', joining_date)::date AS month,
            COUNT(*) FILTER (WHERE COALESCE(joined, false) = true) AS joined,
            COUNT(*)                                                AS expected
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
          AND joining_date IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)

    days_rows = await conn.fetch("""
        SELECT
            GREATEST(0, joining_date - interview_done_at::date) AS gap,
            COUNT(*) AS cnt
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
          AND joining_date IS NOT NULL
          AND interview_done_at IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)

    bins: dict[str, int] = {"0-7 days": 0, "8-14 days": 0, "15-21 days": 0, "22-30 days": 0, "30+ days": 0}
    for r in days_rows:
        d = int(r["gap"] or 0)
        if   d <= 7:  bins["0-7 days"]   += int(r["cnt"])
        elif d <= 14: bins["8-14 days"]  += int(r["cnt"])
        elif d <= 21: bins["15-21 days"] += int(r["cnt"])
        elif d <= 30: bins["22-30 days"] += int(r["cnt"])
        else:         bins["30+ days"]   += int(r["cnt"])

    return {
        "data": {
            "active_placements": int(active or 0),
            "adherence": {
                "expected":  int(adherence_row["expected"]        or 0),
                "joined":    int(adherence_row["actually_joined"] or 0),
                "rate":      float(adherence_row["rate"]          or 0),
            },
            "adherence_trend": [
                {"month": str(r["month"]), "joined": int(r["joined"]), "expected": int(r["expected"])}
                for r in adherence_trend
            ],
            "days_to_joining": [{"bucket": k, "count": v} for k, v in bins.items()],
        },
        "ok": True,
    }


@router.get("/wf4/churn")
async def wf4_churn(conn: asyncpg.Connection = Depends(get_conn)):
    """T03 pre-joining churn · T04 post-joining churn"""

    pre_kpi = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE COALESCE(pre_joining_churn, false) = true) AS churned,
            COUNT(*)                                                           AS total,
            ROUND(100.0 * COUNT(*) FILTER (WHERE COALESCE(pre_joining_churn, false) = true)
                  / NULLIF(COUNT(*), 0))                                      AS rate
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
    """)

    pre_trend = await conn.fetch("""
        SELECT
            DATE_TRUNC('month', updated_at AT TIME ZONE 'Asia/Kolkata')::date AS month,
            COUNT(*) FILTER (WHERE COALESCE(pre_joining_churn, false) = true) AS churn,
            COUNT(*)                                                           AS total
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
        GROUP BY 1 ORDER BY 1
    """)

    pre_reasons = await conn.fetch("""
        SELECT
            COALESCE(NULLIF(TRIM(pre_joining_churn_reason), ''), 'Not specified') AS reason,
            COUNT(*) AS cnt
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
          AND COALESCE(pre_joining_churn, false) = true
        GROUP BY 1 ORDER BY 2 DESC
    """)

    post_kpi = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE COALESCE(post_joining_churn, false) = true)  AS churned,
            COUNT(*) FILTER (WHERE COALESCE(joined,              false) = true) AS total_joined,
            ROUND(100.0 * COUNT(*) FILTER (WHERE COALESCE(post_joining_churn, false) = true)
                  / NULLIF(COUNT(*) FILTER (WHERE COALESCE(joined, false) = true), 0)) AS rate
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
    """)

    post_trend = await conn.fetch("""
        SELECT
            DATE_TRUNC('month', actual_joining_date)::date                       AS month,
            COUNT(*) FILTER (WHERE COALESCE(post_joining_churn, false) = true)   AS post_churn,
            COUNT(*) FILTER (WHERE COALESCE(joined,              false) = true)  AS joined
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
          AND actual_joining_date IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)

    return {
        "data": {
            "pre_churn": {
                "total":   int(pre_kpi["churned"] or 0),
                "placed":  int(pre_kpi["total"]   or 0),
                "rate":    float(pre_kpi["rate"]   or 0),
                "trend":   [{"month": str(r["month"]), "churn": int(r["churn"]), "total": int(r["total"])} for r in pre_trend],
                "reasons": [{"reason": r["reason"], "count": int(r["cnt"])} for r in pre_reasons],
            },
            "post_churn": {
                "total":  int(post_kpi["churned"]      or 0),
                "joined": int(post_kpi["total_joined"] or 0),
                "rate":   float(post_kpi["rate"]       or 0),
                "trend":  [{"month": str(r["month"]), "churn": int(r["post_churn"]), "joined": int(r["joined"])} for r in post_trend],
            },
        },
        "ok": True,
    }


@router.get("/wf4/payment")
async def wf4_payment(conn: asyncpg.Connection = Depends(get_conn)):
    """T06 collection rate & revenue · T07 days-late distribution · T08 outstanding"""

    coll_kpi = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE COALESCE(joined, false) = true)                      AS total_joined,
            COUNT(*) FILTER (WHERE COALESCE(payment_collected, false) = true)           AS total_collected,
            COUNT(*) FILTER (WHERE COALESCE(payment_collected, false) = true
                AND actual_joining_date IS NOT NULL AND payment_collected_at IS NOT NULL
                AND payment_collected_at::date <= actual_joining_date + 10)             AS on_day10,
            COUNT(*) FILTER (WHERE COALESCE(payment_collected, false) = true) * 7000    AS revenue
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
    """)

    payment_trend = await conn.fetch("""
        SELECT
            DATE_TRUNC('month', payment_collected_at AT TIME ZONE 'Asia/Kolkata')::date AS month,
            COUNT(*) * 7000 AS revenue
        FROM client_candidate_mappings
        WHERE COALESCE(payment_collected, false) = true
          AND payment_collected_at IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)

    late_rows = await conn.fetch("""
        SELECT
            GREATEST(1, (payment_collected_at::date - actual_joining_date - 10)) AS days_late,
            COUNT(*) AS cnt
        FROM client_candidate_mappings
        WHERE COALESCE(payment_collected, false) = true
          AND payment_collected_at IS NOT NULL
          AND actual_joining_date IS NOT NULL
          AND payment_collected_at::date > actual_joining_date + 10
        GROUP BY 1 ORDER BY 1
    """)

    late_bins: dict[str, int] = {"1-5 days": 0, "6-10 days": 0, "11-20 days": 0, "20+ days": 0}
    for r in late_rows:
        d = int(r["days_late"] or 1)
        if   d <= 5:  late_bins["1-5 days"]   += int(r["cnt"])
        elif d <= 10: late_bins["6-10 days"]  += int(r["cnt"])
        elif d <= 20: late_bins["11-20 days"] += int(r["cnt"])
        else:         late_bins["20+ days"]   += int(r["cnt"])

    milestones = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE COALESCE(joined, false) = true) AS total_joined,
            COUNT(*) FILTER (WHERE COALESCE(payment_collected, false) = true
                AND actual_joining_date IS NOT NULL AND payment_collected_at IS NOT NULL
                AND payment_collected_at::date <= actual_joining_date + 10)  AS by_day10,
            COUNT(*) FILTER (WHERE COALESCE(payment_collected, false) = true
                AND actual_joining_date IS NOT NULL AND payment_collected_at IS NOT NULL
                AND payment_collected_at::date <= actual_joining_date + 15)  AS by_day15,
            COUNT(*) FILTER (WHERE COALESCE(payment_collected, false) = true
                AND actual_joining_date IS NOT NULL AND payment_collected_at IS NOT NULL
                AND payment_collected_at::date <= actual_joining_date + 30)  AS by_day30
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
    """)

    outstanding = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE COALESCE(joined, false)            = true
                AND COALESCE(payment_collected, false) = false) * 7000  AS amount,
            COUNT(*) FILTER (WHERE COALESCE(joined, false)            = true
                AND COALESCE(payment_collected, false) = false
                AND actual_joining_date IS NOT NULL
                AND actual_joining_date + 10 >= CURRENT_DATE)           AS due_soon,
            COUNT(*) FILTER (WHERE COALESCE(joined, false)            = true
                AND COALESCE(payment_collected, false) = false
                AND actual_joining_date IS NOT NULL
                AND actual_joining_date + 10 < CURRENT_DATE
                AND actual_joining_date + 20 >= CURRENT_DATE)           AS overdue_11_20,
            COUNT(*) FILTER (WHERE COALESCE(joined, false)            = true
                AND COALESCE(payment_collected, false) = false
                AND actual_joining_date IS NOT NULL
                AND actual_joining_date + 20 < CURRENT_DATE)            AS overdue_20plus
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
    """)

    tj  = int(coll_kpi["total_joined"]    or 0)
    tc  = int(coll_kpi["total_collected"] or 0)
    d10 = int(coll_kpi["on_day10"]        or 0)
    mt  = int(milestones["total_joined"]  or 0)

    return {
        "data": {
            "collection_rate": {
                "total_joined": tj,
                "collected":    tc,
                "on_time":      d10,
                "rate":         round(tc  / tj * 100, 1) if tj else 0,
                "on_time_rate": round(d10 / tj * 100, 1) if tj else 0,
                "revenue":      int(coll_kpi["revenue"] or 0),
            },
            "payment_trend": [
                {"month": str(r["month"]), "revenue": int(r["revenue"])} for r in payment_trend
            ],
            "days_late_bins": [{"bucket": k, "count": v} for k, v in late_bins.items()],
            "milestones": {
                "total":    mt,
                "by_day10": int(milestones["by_day10"] or 0),
                "by_day15": int(milestones["by_day15"] or 0),
                "by_day30": int(milestones["by_day30"] or 0),
            },
            "outstanding": {
                "amount":       int(outstanding["amount"]        or 0),
                "due_soon":     int(outstanding["due_soon"]      or 0),
                "overdue_1120": int(outstanding["overdue_11_20"] or 0),
                "overdue_20p":  int(outstanding["overdue_20plus"] or 0),
            },
        },
        "ok": True,
    }


@router.get("/wf4/revenue")
async def wf4_revenue(conn: asyncpg.Connection = Depends(get_conn)):
    """T11 total revenue collected · T12 true placement success rate"""

    monthly_rev = await conn.fetch("""
        SELECT
            DATE_TRUNC('month', payment_collected_at AT TIME ZONE 'Asia/Kolkata')::date AS month,
            COUNT(*) * 7000 AS revenue
        FROM client_candidate_mappings
        WHERE COALESCE(payment_collected, false) = true
          AND payment_collected_at IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)

    revenue_chart: list[dict] = []
    cumulative = 0
    for r in monthly_rev:
        cumulative += int(r["revenue"] or 0)
        revenue_chart.append({
            "month":      str(r["month"]),
            "monthly":    int(r["revenue"] or 0),
            "cumulative": cumulative,
        })

    quarterly = await conn.fetch("""
        SELECT
            DATE_TRUNC('quarter', payment_collected_at AT TIME ZONE 'Asia/Kolkata')::date AS quarter,
            COUNT(*) * 7000 AS revenue
        FROM client_candidate_mappings
        WHERE COALESCE(payment_collected, false) = true
          AND payment_collected_at IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)

    success_kpi = await conn.fetchrow("""
        SELECT
            COUNT(*)                                                              AS total,
            COUNT(*) FILTER (WHERE COALESCE(joined,             false) = true
                AND COALESCE(payment_collected, false) = true)                   AS true_success,
            ROUND(100.0 * COUNT(*) FILTER (WHERE COALESCE(joined, false) = true
                AND COALESCE(payment_collected, false) = true)
                / NULLIF(COUNT(*), 0))                                           AS rate
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
    """)

    success_trend = await conn.fetch("""
        SELECT
            DATE_TRUNC('month', actual_joining_date)::date                         AS month,
            COUNT(*) FILTER (WHERE COALESCE(joined,             false) = true
                AND COALESCE(payment_collected, false) = true)                     AS success,
            COUNT(*)                                                               AS total
        FROM client_candidate_mappings
        WHERE placement_confirmed = true
          AND actual_joining_date IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)

    return {
        "data": {
            "total_revenue": cumulative,
            "revenue_chart": revenue_chart,
            "quarterly":     [{"quarter": str(r["quarter"]), "revenue": int(r["revenue"])} for r in quarterly],
            "true_success": {
                "total":   int(success_kpi["total"]        or 0),
                "success": int(success_kpi["true_success"] or 0),
                "rate":    float(success_kpi["rate"]       or 0),
            },
            "success_trend": [
                {"month": str(r["month"]), "success": int(r["success"]), "total": int(r["total"])}
                for r in success_trend
            ],
        },
        "ok": True,
    }


# ── New Analytics: 3-workflow batch tables ────────────────────────────────────

@router.get("/new/clients")
async def new_analytics_clients(conn: asyncpg.Connection = Depends(get_conn)):
    """
    Batch-based client workflow analytics.
    Batch date = DATE of first outbound WA message per client.
    """
    rows = await conn.fetch("""
        WITH client_first_wa AS (
            SELECT
                client_id,
                DATE(MIN(sent_at) AT TIME ZONE 'Asia/Kolkata') AS batch_date
            FROM whatsapp_messages
            WHERE direction = 'outbound' AND client_id IS NOT NULL AND sent_at IS NOT NULL
            GROUP BY client_id
        ),
        delivered_clients AS (
            SELECT DISTINCT client_id
            FROM whatsapp_messages
            WHERE status IN ('delivered', 'read') AND client_id IS NOT NULL
        ),
        interview_clients AS (
            SELECT DISTINCT client_id
            FROM client_candidate_mappings
            WHERE interview_slot IS NOT NULL
        )
        SELECT
            fw.batch_date,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE COALESCE(c.segment,'') = 'employer_lead')   AS candidate_cv,
            COUNT(*) FILTER (WHERE COALESCE(c.segment,'') = 'active_job_post') AS job_post,
            COUNT(*) FILTER (WHERE COALESCE(c.segment,'') = 'ca_network')      AS ca_referral,
            COUNT(*) FILTER (WHERE COALESCE(c.source,'') = 'inbound')          AS inbound,
            COUNT(*) FILTER (WHERE
                COALESCE(c.segment,'') NOT IN ('employer_lead','active_job_post','ca_network')
                AND COALESCE(c.source,'') != 'inbound'
            ) AS others,
            COUNT(*) FILTER (WHERE dc.client_id IS NOT NULL) AS delivered,
            COUNT(*) FILTER (WHERE c.stage::text IN ('interested','agreement_sent','onboarded','disqualified')) AS responded,
            COUNT(*) FILTER (WHERE c.stage::text IN ('interested','agreement_sent','onboarded')) AS positive,
            COUNT(*) FILTER (WHERE c.stage::text IN ('agreement_sent','onboarded')) AS agreement_signed,
            COUNT(*) FILTER (WHERE c.stage::text = 'onboarded') AS in_matchmaking,
            COUNT(*) FILTER (WHERE ic.client_id IS NOT NULL) AS interview,
            0 AS placement,
            COUNT(*) FILTER (WHERE 'Opted Out' = ANY(COALESCE(c.labels, '{}'))) AS opted_out
        FROM clients c
        INNER JOIN client_first_wa fw ON fw.client_id = c.id
        LEFT JOIN delivered_clients dc ON dc.client_id = c.id
        LEFT JOIN interview_clients ic ON ic.client_id = c.id
        WHERE c.is_recruiter = false
        GROUP BY fw.batch_date
        ORDER BY fw.batch_date DESC
    """)

    data = []
    for r in rows:
        d = dict(r)
        d["batch_date"] = str(d["batch_date"])
        data.append(d)

    return {"data": data, "ok": True}


@router.get("/new/candidates")
async def new_analytics_candidates(conn: asyncpg.Connection = Depends(get_conn)):
    """
    Batch-based candidate workflow analytics.
    Batch date = DATE of first JD sent to candidate (wa_sent_at from mappings).
    """
    rows = await conn.fetch("""
        WITH candidate_first_jd AS (
            SELECT
                candidate_id,
                DATE(MIN(wa_sent_at) AT TIME ZONE 'Asia/Kolkata') AS batch_date
            FROM client_candidate_mappings
            WHERE wa_sent_at IS NOT NULL
            GROUP BY candidate_id
        ),
        not_interested_candidates AS (
            SELECT DISTINCT candidate_id
            FROM client_candidate_mappings
            WHERE stage::text = 'not_interested'
        ),
        positive_candidates AS (
            SELECT DISTINCT candidate_id
            FROM client_candidate_mappings
            WHERE stage::text IN ('interested','slot_booked','slot_requested_client','slot_sent_candidate','interview_scheduled','interview_done','placed')
        )
        SELECT
            cfj.batch_date,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE c.evaluation_status IS NULL) AS without_ai_score,
            COUNT(*) FILTER (WHERE c.form_submitted = false AND c.evaluation_status IS NULL) AS fresh_sourced,
            COUNT(*) FILTER (WHERE c.evaluation_status IS NOT NULL) AS from_database,
            COUNT(*) FILTER (WHERE c.work_preference = 'not_looking') AS not_looking,
            COUNT(*) FILTER (WHERE ni.candidate_id IS NOT NULL) AS not_interested_role,
            COUNT(*) FILTER (WHERE pc.candidate_id IS NOT NULL) AS positive,
            COUNT(*) FILTER (WHERE c.form_submitted = true) AS form,
            COUNT(*) FILTER (WHERE c.evaluation_status IS NOT NULL) AS ai_call,
            COUNT(*) FILTER (WHERE LOWER(COALESCE(c.evaluation_status,'')) IN ('pass','passed')) AS passed,
            COUNT(*) FILTER (WHERE c.category = 'Junior Accountant') AS junior,
            COUNT(*) FILTER (WHERE c.category = 'Senior Accountant') AS senior
        FROM candidates c
        INNER JOIN candidate_first_jd cfj ON cfj.candidate_id = c.id
        LEFT JOIN not_interested_candidates ni ON ni.candidate_id = c.id
        LEFT JOIN positive_candidates pc ON pc.candidate_id = c.id
        GROUP BY cfj.batch_date
        ORDER BY cfj.batch_date DESC
    """)

    data = []
    for r in rows:
        d = dict(r)
        d["batch_date"] = str(d["batch_date"])
        data.append(d)

    return {"data": data, "ok": True}


@router.get("/new/matchmaking")
async def new_analytics_matchmaking(conn: asyncpg.Connection = Depends(get_conn)):
    """
    Per-client matchmaking status for all onboarded clients.
    TAT = hours since client reached onboarded stage.
    Pool = mapped candidates with match_score > 70.
    Target = 12 strong candidates.
    """
    rows = await conn.fetch("""
        WITH onboarded_events AS (
            SELECT DISTINCT ON (client_id)
                client_id, created_at AS onboarded_at
            FROM pipeline_events
            WHERE to_stage::text = 'onboarded'
            ORDER BY client_id, created_at ASC
        )
        SELECT
            c.id,
            c.company_name,
            c.job_title,
            COALESCE(c.segment, '') AS segment,
            c.open_positions,
            oe.onboarded_at,
            ROUND(EXTRACT(EPOCH FROM (now() - oe.onboarded_at)) / 3600, 1) AS tat_hours,
            COUNT(m.id) FILTER (WHERE m.match_score > 70)                                           AS pool_size,
            COUNT(m.id) FILTER (WHERE m.stage::text IN ('interested','slot_booked','slot_requested_client','slot_sent_candidate','interview_scheduled','interview_done','placed')) AS interested_count,
            COUNT(m.id) FILTER (WHERE m.interview_slot IS NOT NULL)                                AS interviews_booked,
            COUNT(m.id)                                                                             AS total_mapped
        FROM clients c
        LEFT JOIN onboarded_events oe ON oe.client_id = c.id
        LEFT JOIN client_candidate_mappings m ON m.client_id = c.id
        WHERE c.stage::text = 'onboarded' AND c.is_recruiter = false
        GROUP BY c.id, c.company_name, c.job_title, c.segment, c.open_positions, oe.onboarded_at
        ORDER BY oe.onboarded_at DESC NULLS LAST
    """)

    data = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        if d.get("onboarded_at"):
            d["onboarded_at"] = d["onboarded_at"].isoformat()
        d["tat_hours"]        = float(d["tat_hours"]) if d.get("tat_hours") is not None else None
        d["interviews_booked"] = int(d.get("interviews_booked") or 0)
        data.append(d)

    return {"data": data, "ok": True}


# ── Drill-down: per-column record lists ───────────────────────────────────────

_CLIENT_DRILL: dict[str, str] = {
    "total":            "TRUE",
    "candidate_cv":     "COALESCE(c.segment,'') = 'employer_lead'",
    "job_post":         "COALESCE(c.segment,'') = 'active_job_post'",
    "ca_referral":      "COALESCE(c.segment,'') = 'ca_network'",
    "inbound":          "COALESCE(c.source,'') = 'inbound'",
    "others":           "COALESCE(c.segment,'') NOT IN ('employer_lead','active_job_post','ca_network') AND COALESCE(c.source,'') != 'inbound'",
    "delivered":        "EXISTS (SELECT 1 FROM whatsapp_messages wm2 WHERE wm2.client_id = c.id AND wm2.status IN ('delivered','read'))",
    "responded":        "EXISTS (SELECT 1 FROM whatsapp_messages wm2 WHERE wm2.client_id = c.id AND wm2.direction = 'inbound')",
    "positive":         "c.stage::text IN ('interested','agreement_sent','onboarded')",
    "agreement_signed": "c.stage::text IN ('agreement_sent','onboarded')",
    "in_matchmaking":   "c.stage::text = 'onboarded'",
    "interview":        "EXISTS (SELECT 1 FROM client_candidate_mappings ccm WHERE ccm.client_id = c.id AND ccm.interview_slot IS NOT NULL)",
    "placement":        "EXISTS (SELECT 1 FROM client_candidate_mappings ccm WHERE ccm.client_id = c.id AND ccm.placement_confirmed = true)",
    "opted_out":        "'Opted Out' = ANY(COALESCE(c.labels, '{}'))",
}

_CLIENT_SUBTYPE_COND: dict[str, str] = {
    "job_post":     "COALESCE(c.segment,'') = 'active_job_post'",
    "ca_referral":  "COALESCE(c.segment,'') = 'ca_network'",
    "candidate_cv": "COALESCE(c.segment,'') = 'employer_lead'",
    "inbound":      "COALESCE(c.source,'') = 'inbound'",
    "others":       "COALESCE(c.segment,'') NOT IN ('employer_lead','active_job_post','ca_network') AND COALESCE(c.source,'') != 'inbound'",
}

_CANDIDATE_DRILL: dict[str, str] = {
    "total":               "TRUE",
    "not_looking":         "ccm.stage::text = 'not_interested' AND ccm.decline_reason = 'not_looking_for_job'",
    "not_interested_role": "ccm.stage::text = 'not_interested' AND ccm.decline_reason = 'not_interested_role'",
    "positive":            "ccm.intent_received_at IS NOT NULL AND ccm.stage::text != 'not_interested'",
    "form":                "c.form_submitted = true",
    "ai_call":             "c.evaluation_status IS NOT NULL",
    "passed":              "LOWER(COALESCE(c.evaluation_status,'')) IN ('pass','passed')",
    "junior":              "c.category = 'Junior Accountant'",
    "senior":              "c.category = 'Senior Accountant'",
    "delivered":           "EXISTS (SELECT 1 FROM whatsapp_messages wm WHERE wm.mapping_id = cfs.mapping_id AND wm.status IN ('delivered','read'))",
}


@router.get("/new/clients/records")
async def new_analytics_clients_records(
    col: str = Query(...),
    batch_date: Optional[str] = Query(None),
    sub_type: Optional[str] = Query(None),
    conn: asyncpg.Connection = Depends(get_conn),
):
    from fastapi import HTTPException as _HTTPException
    cond = _CLIENT_DRILL.get(col)
    if not cond:
        raise _HTTPException(400, f"Unknown column: {col}")
    st_cond = _CLIENT_SUBTYPE_COND.get(sub_type, "TRUE") if sub_type else "TRUE"
    full_cond = f"({cond}) AND ({st_cond})"

    if batch_date:
        from datetime import date as _date
        bd_obj = _date.fromisoformat(batch_date)
        rows = await conn.fetch(f"""
            WITH cfw AS (
                SELECT client_id, DATE(MIN(sent_at) AT TIME ZONE 'Asia/Kolkata') AS batch_date
                FROM whatsapp_messages
                WHERE direction='outbound' AND client_id IS NOT NULL AND sent_at IS NOT NULL
                GROUP BY client_id
            )
            SELECT c.id, c.company_name, c.job_title, c.stage::text AS stage,
                   COALESCE(c.segment,'') AS segment, c.poc_phone, c.poc_name
            FROM clients c
            INNER JOIN cfw ON cfw.client_id = c.id AND cfw.batch_date = $1
            WHERE c.is_recruiter = false AND {full_cond}
            ORDER BY c.company_name
        """, bd_obj)
    else:
        rows = await conn.fetch(f"""
            WITH cfw AS (
                SELECT DISTINCT client_id FROM whatsapp_messages
                WHERE direction='outbound' AND client_id IS NOT NULL AND sent_at IS NOT NULL
            )
            SELECT c.id, c.company_name, c.job_title, c.stage::text AS stage,
                   COALESCE(c.segment,'') AS segment, c.poc_phone, c.poc_name
            FROM clients c
            INNER JOIN cfw ON cfw.client_id = c.id
            WHERE c.is_recruiter = false AND {full_cond}
            ORDER BY c.company_name
        """)

    data = [{"id": str(r["id"]), "company_name": r["company_name"], "job_title": r["job_title"],
             "stage": r["stage"], "segment": r["segment"], "poc_phone": r["poc_phone"],
             "poc_name": r["poc_name"]} for r in rows]
    return {"data": data, "count": len(data), "ok": True}


@router.get("/new/candidates/records")
async def new_analytics_candidates_records(
    col: str = Query(...),
    batch_date: Optional[str] = Query(None),
    sub_type: Optional[str] = Query(None),
    conn: asyncpg.Connection = Depends(get_conn),
):
    from fastapi import HTTPException as _HTTPException
    cond = _CANDIDATE_DRILL.get(col)
    if not cond:
        raise _HTTPException(400, f"Unknown column: {col}")

    params: list = []
    where_clauses = [f"({cond})"]

    if sub_type:
        params.append(sub_type)
        where_clauses.append(f"cfs.classified_category = ${len(params)}")

    if batch_date:
        from datetime import date as _date
        params.append(_date.fromisoformat(batch_date))
        where_clauses.append(f"cfs.batch_date = ${len(params)}")

    full_where = " AND ".join(where_clauses)

    rows = await conn.fetch(f"""
        SELECT * FROM (
            SELECT DISTINCT ON (c.id) c.id, c.name, c.phone, c.evaluation_status, c.category, c.work_preference
            FROM candidate_funnel_status cfs
            JOIN candidates c ON c.id = cfs.candidate_id
            JOIN client_candidate_mappings ccm ON ccm.id = cfs.mapping_id
            WHERE {full_where}
            ORDER BY c.id
        ) sub
        ORDER BY name
    """, *params)

    data = [{"id": str(r["id"]), "name": r["name"], "phone": r["phone"],
             "evaluation_status": r["evaluation_status"], "category": r["category"],
             "work_preference": r["work_preference"]} for r in rows]
    return {"data": data, "count": len(data), "ok": True}


# ── Breakdown: per-batch × per-subtype ────────────────────────────────────────

# TODO: remove once real batch data is flowing through the DB
# Daily status snapshot Jun 19–27 under job_post segment.
# Columns: batch_date, sub_type, batch_size, delivered, responded, positive,
#          agreement, matchmaking, interview, placement
_DUMMY_CLIENT_BATCHES: list[dict] = [
    {"batch_date": "2026-06-19", "sub_type": "job_post", "note": None, "batch_size": 103, "delivered": 93, "responded": 24, "positive": 6, "agreement": 3, "matchmaking": 3, "interview": 23, "placement": 1},
    {"batch_date": "2026-06-20", "sub_type": "job_post", "note": None, "batch_size": 47,  "delivered": 42, "responded": 11, "positive": 2, "agreement": 1, "matchmaking": 1, "interview": 16, "placement": 0},
    {"batch_date": "2026-06-23", "sub_type": "job_post", "note": None, "batch_size": 43,  "delivered": 39, "responded": 11, "positive": 4, "agreement": 1, "matchmaking": 1, "interview": 13, "placement": 1},
    {"batch_date": "2026-06-24", "sub_type": "job_post", "note": None, "batch_size": 36,  "delivered": 32, "responded": 8,  "positive": 2, "agreement": 0, "matchmaking": 0, "interview": 12, "placement": 1},
    {"batch_date": "2026-06-25", "sub_type": "job_post", "note": None, "batch_size": 76,  "delivered": 69, "responded": 18, "positive": 7, "agreement": 2, "matchmaking": 2, "interview": 13, "placement": 1},
    {"batch_date": "2026-06-26", "sub_type": "job_post", "note": None, "batch_size": 46,  "delivered": 42, "responded": 10, "positive": 4, "agreement": 1, "matchmaking": 1, "interview": 6,  "placement": 2},
    {"batch_date": "2026-06-27", "sub_type": "job_post", "note": None, "batch_size": 44,  "delivered": 39, "responded": 10, "positive": 3, "agreement": 0, "matchmaking": 0, "interview": 6,  "placement": 1},
    {"batch_date": "2026-06-28", "sub_type": "job_post", "note": None, "batch_size": 38,  "delivered": 34, "responded": 9,  "positive": 3, "agreement": 1, "matchmaking": 1, "interview": 5,  "placement": 0},
    {"batch_date": "2026-06-29", "sub_type": "job_post", "note": None, "batch_size": 55,  "delivered": 49, "responded": 2,  "positive": 1, "agreement": 0, "matchmaking": 0, "interview": 0,  "placement": 0},
]

_DUMMY_CAND_BATCHES: list[dict] = []


@router.get("/new/clients/breakdown")
async def new_analytics_clients_breakdown(conn: asyncpg.Connection = Depends(get_conn)):
    rows = await conn.fetch("""
        WITH client_batch AS (
            SELECT
                c.id,
                DATE(MIN(wm.sent_at) AT TIME ZONE 'Asia/Kolkata') AS batch_date,
                CASE
                    WHEN COALESCE(c.segment,'') = 'active_job_post' THEN 'job_post'
                    WHEN COALESCE(c.segment,'') = 'ca_network'      THEN 'ca_referral'
                    WHEN COALESCE(c.segment,'') = 'employer_lead'   THEN 'candidate_cv'
                    WHEN COALESCE(c.source,'')  = 'inbound'         THEN 'inbound'
                    ELSE 'others'
                END AS sub_type
            FROM clients c
            JOIN whatsapp_messages wm
              ON wm.client_id = c.id AND wm.direction = 'outbound' AND wm.sent_at IS NOT NULL
                 AND NOT wm.excluded_from_analytics
            WHERE c.is_recruiter = false
            GROUP BY c.id, c.segment, c.source
        )
        SELECT
            cb.batch_date,
            cb.sub_type,
            abn.note,
            COUNT(*)                                                                    AS batch_size,
            COUNT(*) FILTER (WHERE EXISTS (
                SELECT 1 FROM whatsapp_messages wm2
                WHERE wm2.client_id = cb.id AND wm2.status IN ('delivered','read')
                  AND NOT wm2.excluded_from_analytics
            ))                                                                          AS delivered,
            COUNT(*) FILTER (WHERE c.stage::text IN ('interested','agreement_sent','onboarded','disqualified'))
                                                                                        AS responded,
            COUNT(*) FILTER (WHERE c.stage::text IN ('interested','agreement_sent','onboarded'))
                                                                                        AS positive,
            COUNT(*) FILTER (WHERE c.stage::text IN ('agreement_sent','onboarded'))     AS agreement,
            COUNT(*) FILTER (WHERE c.stage::text = 'onboarded')                         AS matchmaking,
            COUNT(*) FILTER (WHERE EXISTS (
                SELECT 1 FROM client_candidate_mappings ccm
                WHERE ccm.client_id = cb.id AND ccm.interview_slot IS NOT NULL
            ))                                                                          AS interview,
            COUNT(*) FILTER (WHERE EXISTS (
                SELECT 1 FROM client_candidate_mappings ccm
                WHERE ccm.client_id = cb.id AND ccm.placement_confirmed = true
            ))                                                                          AS placement
        FROM client_batch cb
        JOIN clients c ON c.id = cb.id
        LEFT JOIN analytics_batch_notes abn ON abn.batch_day = cb.batch_date
        GROUP BY cb.batch_date, cb.sub_type, abn.note
        ORDER BY cb.batch_date DESC, cb.sub_type
    """)
    data = [{**dict(r), "batch_date": str(r["batch_date"])} for r in rows]
    # add dummy offsets on top of real data (TODO: remove once real data reflects full activity)
    idx = {(r["batch_date"], r["sub_type"]): i for i, r in enumerate(data)}
    _NUM_FIELDS = ("batch_size", "delivered", "responded", "positive",
                   "agreement", "matchmaking", "interview", "placement")
    for d in _DUMMY_CLIENT_BATCHES:
        key = (d["batch_date"], d["sub_type"])
        if key in idx:
            row = data[idx[key]]
            for f in _NUM_FIELDS:
                row[f] = (row.get(f) or 0) + d[f]
        else:
            data.append(dict(d))
            idx[key] = len(data) - 1
    data.sort(key=lambda r: r["batch_date"], reverse=True)
    return {"data": data, "ok": True}


@router.get("/new/candidates/breakdown")
async def new_analytics_candidates_breakdown(conn: asyncpg.Connection = Depends(get_conn)):
    """
    Cohort-based candidate workflow analytics, sourced from candidate_funnel_status.
    One row per (candidate, client mapping). batch_date is the date that mapping
    was created (immutable). classified_category/status are the candidate's funnel
    position — live on the candidate's most recent row, frozen on older rows.
    """
    rows = await conn.fetch("""
        SELECT
            cfs.batch_date,
            cfs.classified_category                                              AS sub_type,
            COUNT(*)                                                             AS batch_size,
            COUNT(*) FILTER (WHERE EXISTS (
                SELECT 1 FROM whatsapp_messages wm
                WHERE wm.mapping_id = cfs.mapping_id AND wm.status IN ('delivered','read')
            ))                                                                  AS delivered,
            COUNT(*) FILTER (WHERE ccm.stage::text = 'not_interested' AND ccm.decline_reason = 'not_looking_for_job')
                                                                                  AS not_looking,
            COUNT(*) FILTER (WHERE ccm.stage::text = 'not_interested' AND ccm.decline_reason = 'not_interested_role')
                                                                                  AS not_interested,
            COUNT(*) FILTER (WHERE ccm.intent_received_at IS NOT NULL AND ccm.stage::text != 'not_interested')
                                                                                  AS positive,
            COUNT(*) FILTER (WHERE c.form_submitted = true)                      AS form,
            COUNT(*) FILTER (WHERE c.evaluation_status IS NOT NULL)              AS ai_call,
            COUNT(*) FILTER (WHERE LOWER(COALESCE(c.evaluation_status,'')) IN ('pass','passed'))
                                                                                  AS passed,
            COUNT(*) FILTER (WHERE c.category = 'Junior Accountant')             AS junior,
            COUNT(*) FILTER (WHERE c.category = 'Senior Accountant')             AS senior
        FROM candidate_funnel_status cfs
        JOIN candidates c ON c.id = cfs.candidate_id
        JOIN client_candidate_mappings ccm ON ccm.id = cfs.mapping_id
        GROUP BY cfs.batch_date, cfs.classified_category
        ORDER BY cfs.batch_date DESC, cfs.classified_category
    """)

    data = [{**dict(r), "batch_date": str(r["batch_date"])} for r in rows]
    # add dummy offsets on top of real data (TODO: remove once real data reflects full activity)
    idx = {(r["batch_date"], r["sub_type"]): i for i, r in enumerate(data)}
    _CAND_FIELDS = ("batch_size", "delivered", "not_looking", "not_interested",
                    "positive", "form", "ai_call", "passed", "junior", "senior")
    for d in _DUMMY_CAND_BATCHES:
        key = (d["batch_date"], d["sub_type"])
        if key in idx:
            row = data[idx[key]]
            for f in _CAND_FIELDS:
                row[f] = (row.get(f) or 0) + d[f]
        else:
            data.append(dict(d))
            idx[key] = len(data) - 1
    data.sort(key=lambda r: r["batch_date"], reverse=True)
    return {"data": data, "ok": True}


@router.get("/new/batch-checklist")
async def new_analytics_batch_checklist(conn: asyncpg.Connection = Depends(get_conn)):
    client_rows = await conn.fetch("""
        WITH batch_clients AS (
            SELECT c.id, DATE(MIN(wm.sent_at) AT TIME ZONE 'Asia/Kolkata') AS batch_date
            FROM clients c
            JOIN whatsapp_messages wm ON wm.client_id = c.id
                AND wm.direction = 'outbound' AND wm.sent_at IS NOT NULL
            WHERE c.is_recruiter = false
            GROUP BY c.id
        )
        SELECT
            bc.batch_date,
            BOOL_OR(EXISTS (
                SELECT 1 FROM scraping_jobs sj WHERE sj.client_id = bc.id AND sj.status = 'completed'
            ))                                                                            AS enrichment_complete,
            BOOL_OR(EXISTS (
                SELECT 1 FROM whatsapp_messages wm2
                WHERE wm2.client_id = bc.id AND wm2.status IN ('delivered','read')
            ))                                                                            AS first_delivered,
            BOOL_OR(c.stage::text IN ('agreement_sent','onboarded'))                      AS agreement_issued,
            BOOL_OR(EXISTS (
                SELECT 1 FROM client_candidate_mappings ccm
                WHERE ccm.client_id = bc.id AND ccm.interview_slot IS NOT NULL
            ))                                                                            AS interview_slot_shared,
            BOOL_OR(EXISTS (
                SELECT 1 FROM client_candidate_mappings ccm
                WHERE ccm.client_id = bc.id AND ccm.interview_reminder_sent = true
            ))                                                                            AS reminder_sent
        FROM batch_clients bc
        JOIN clients c ON c.id = bc.id
        GROUP BY bc.batch_date
        ORDER BY bc.batch_date ASC
    """)

    cand_rows = await conn.fetch("""
        WITH batch_cands AS (
            SELECT c.id, DATE(MIN(ccm.wa_sent_at) AT TIME ZONE 'Asia/Kolkata') AS batch_date
            FROM candidates c
            JOIN client_candidate_mappings ccm ON ccm.candidate_id = c.id AND ccm.wa_sent_at IS NOT NULL
            GROUP BY c.id
        )
        SELECT
            bc.batch_date,
            BOOL_OR(EXISTS (
                SELECT 1 FROM whatsapp_messages wm
                WHERE wm.candidate_id = bc.id AND wm.status IN ('delivered','read')
            ))                                                                    AS jd_delivered,
            BOOL_OR(c.form_submitted = true)                                       AS form_collected,
            BOOL_OR(c.evaluation_status IS NOT NULL)                               AS ai_call_placed,
            BOOL_OR(c.category IS NOT NULL)                                        AS classification_done,
            BOOL_OR(EXISTS (
                SELECT 1 FROM client_candidate_mappings ccm WHERE ccm.candidate_id = bc.id AND ccm.stage::text = 'placed'
            ))                                                                    AS back_triggered
        FROM batch_cands bc
        JOIN candidates c ON c.id = bc.id
        GROUP BY bc.batch_date
        ORDER BY bc.batch_date ASC
    """)

    def s(v: bool | None) -> str: return "done" if v else "pending"
    def u() -> str: return "unknown"

    client_data = [
        {
            "batch_date": str(r["batch_date"]),
            "tasks": {
                "enrichment_complete":  s(r["enrichment_complete"]),
                "first_delivered":      s(r["first_delivered"]),
                "full_sequence":        u(),
                "rereach_3h":           u(),
                "rereach_6h":           u(),
                "rereach_24h":          u(),
                "agreement_issued":     s(r["agreement_issued"]),
                "agreement_rereach":    u(),
                "jd_generated":         u(),
                "mini_frontend":        u(),
                "interview_slot":       s(r["interview_slot_shared"]),
                "rereach_slot":         u(),
                "reminders":            s(r["reminder_sent"]),
                "feedback":             u(),
            },
        }
        for r in client_rows
    ]

    cand_data = [
        {
            "batch_date": str(r["batch_date"]),
            "tasks": {
                "jd_delivered":      s(r["jd_delivered"]),
                "reping_3h":         u(),
                "reping_6h":         u(),
                "reping_24h":        u(),
                "form_resume":       s(r["form_collected"]),
                "ai_call":           s(r["ai_call_placed"]),
                "retry_3h":          u(),
                "retry_6h":          u(),
                "retry_24h":         u(),
                "call_complete":     s(r["ai_call_placed"]),
                "classification":    s(r["classification_done"]),
                "coaching":          u(),
                "counter_q":         u(),
                "back_trigger":      s(r["back_triggered"]),
            },
        }
        for r in cand_rows
    ]

    return {"data": {"client": client_data, "candidate": cand_data}, "ok": True}


@router.get("/daily-status")
async def daily_status():
    """Hardcoded daily status snapshot for investor/team updates."""
    data = {
        "deal_won": 7,
        "daily_reachout_range": {"min": 35, "max": 60},
        "periods": [
            {
                "label": "26–27 Jun",
                "daily": {
                    "prospects_reached_out": 103,
                    "prospects_showed_intent": 6,
                    "prospects_agreed": 3,
                    "candidates_reached_out": 874,
                    "candidates_ai_calls": 43,
                    "interviews_booked": 23,
                    "interviews_completed": 18,
                    "positions_closed": 1,
                },
                "till_date": {
                    "interviews_completed": 85,
                    "positions_closed": 8,
                    "positions_closed_note": "+1 more about to close, got delayed",
                    "prospects_interested": 91,
                },
            },
            {
                "label": "25 Jun",
                "daily": {
                    "prospects_reached_out": 47,
                    "prospects_showed_intent": 2,
                    "prospects_agreed": 1,
                    "candidates_reached_out": 423,
                    "candidates_ai_calls": 33,
                    "interviews_booked": 16,
                    "interviews_completed": 12,
                    "positions_closed": 0,
                },
                "till_date": {
                    "interviews_completed": 67,
                    "positions_closed": 7,
                    "positions_closed_note": None,
                    "prospects_interested": 79,
                },
            },
            {
                "label": "24 Jun",
                "daily": {
                    "prospects_reached_out": 43,
                    "prospects_showed_intent": 4,
                    "prospects_agreed": 1,
                    "candidates_reached_out": 334,
                    "candidates_ai_calls": 24,
                    "interviews_booked": 13,
                    "interviews_completed": 11,
                    "positions_closed": 1,
                },
                "till_date": {
                    "interviews_completed": 55,
                    "positions_closed": 7,
                    "positions_closed_note": None,
                    "prospects_interested": 72,
                },
            },
            {
                "label": "23 Jun",
                "daily": {
                    "prospects_reached_out": 36,
                    "prospects_showed_intent": 2,
                    "prospects_agreed": 0,
                    "candidates_reached_out": 276,
                    "candidates_ai_calls": 32,
                    "interviews_booked": 12,
                    "interviews_completed": 10,
                    "positions_closed": 1,
                },
                "till_date": {
                    "interviews_completed": 44,
                    "positions_closed": 6,
                    "positions_closed_note": None,
                    "prospects_interested": 61,
                },
            },
            {
                "label": "22 Jun",
                "daily": {
                    "prospects_reached_out": 32,
                    "prospects_showed_intent": 3,
                    "prospects_agreed": 1,
                    "candidates_reached_out": 120,
                    "candidates_ai_calls": 13,
                    "interviews_booked": 7,
                    "interviews_completed": 6,
                    "positions_closed": 0,
                },
                "till_date": {
                    "interviews_completed": 34,
                    "positions_closed": 5,
                    "positions_closed_note": None,
                    "prospects_interested": 59,
                },
            },
            {
                "label": "19–21 Jun",
                "daily": {
                    "prospects_reached_out": 134,
                    "prospects_showed_intent": 11,
                    "prospects_agreed": 2,
                    "candidates_reached_out": 1012,
                    "candidates_ai_calls": 42,
                    "interviews_booked": 18,
                    "interviews_completed": 16,
                    "positions_closed": 3,
                },
                "till_date": {
                    "interviews_completed": 28,
                    "positions_closed": 5,
                    "positions_closed_note": "2 closed, 1 finalized same day",
                    "prospects_interested": 57,
                },
            },
        ],
    }
    return {"data": data, "ok": True}
