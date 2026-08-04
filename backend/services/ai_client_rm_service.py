"""
AI Relationship Manager — advisory-only agent for the client (employer) WhatsApp
channel. Mirrors services/ai_rm_service.py's architecture on the candidate side.

Single tool-calling loop: the model calls read tools to build context, then must
end its turn on exactly one terminal tool — reply(), escalate(), or no_reply().
No tool here ever writes to clients.stage or any other column that drives the
deterministic workflow in routers/whatsapp.py / flow_engine.py — it only
explains, and points to the exact client-portal screen that performs an action,
or hands off to a human customer executive (rms table) for anything commercial,
contractual, or otherwise sensitive.

Entry point: handle_free_text(conn, from_phone, text) — called from
routers/whatsapp.py::_handle_client_reply() for inbound client free-text that
didn't match a button payload or a deterministic keyword, and only for clients
NOT in the 'onboarded' stage (onboarded clients get the deterministic
client_support_bot instead — see routers/whatsapp.py for that branch).

Clients often send a thought across several rapid WhatsApp messages instead of
one. handle_free_text() buffers each fragment in client_ai_pending and only
actually runs the agent once the phone has gone quiet for
CLIENT_AI_RM_DEBOUNCE_SECONDS, so the loop always sees the whole thought at
once instead of replying to each fragment separately.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from urllib.parse import urlparse

import asyncpg
from openai import AsyncOpenAI

from database import get_pool
import services.msg91_service as msg91
import services.ticket_service as ticket_service
from services.ops_alert_service import send_alert

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


_MODEL = os.environ.get("AI_CLIENT_RM_MODEL", "gpt-5.4")
_MAX_ROUNDS = 6          # tool-call round-trips before a forced escalate
_TURN_BUDGET = int(os.environ.get("AI_CLIENT_RM_TURN_BUDGET", "10"))
_DEBOUNCE_SECONDS = int(os.environ.get("AI_CLIENT_RM_DEBOUNCE_SECONDS", "6"))


def _phone_variants(phone: str) -> list[str]:
    phone = (phone or "").lstrip("+").strip()
    variants = [phone]
    if phone.startswith("91") and len(phone) == 12:
        variants.append(phone[2:])
    elif len(phone) == 10 and phone.isdigit():
        variants.append("91" + phone)
    return variants


# ── Safety pre-filter — deterministic, runs before the model ever sees the message ──
_ESCALATE_KEYWORDS = frozenset([
    "talk to a person", "talk to someone", "speak to a human", "call me now",
    "real person", "not a bot", "this is ridiculous", "waste of time", "scam",
    "fraud", "cheated", "lawyer", "legal action", "consumer court", "complaint",
    "cancel", "refund", "not happy", "unsatisfied",
])


def _trips_safety_filter(text: str) -> bool:
    lower = (text or "").lower()
    return any(kw in lower for kw in _ESCALATE_KEYWORDS)


async def _find_client(conn: asyncpg.Connection, phone: str) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT id, company_name, job_title, industry, stage, min_salary, max_salary,
               job_location, location, job_timings, job_type, key_skills, num_employees,
               ai_rm_enabled
        FROM clients
        WHERE poc_phone = ANY($1::text[])
           OR EXISTS (
               SELECT 1 FROM jsonb_array_elements(
                   CASE WHEN jsonb_typeof(COALESCE(phone_numbers,'[]'::jsonb)) = 'array'
                        THEN COALESCE(phone_numbers,'[]'::jsonb) ELSE '[]'::jsonb END
               ) p WHERE COALESCE(p ->> 'number', p #>> '{}') = ANY($1::text[])
           )
        LIMIT 1
        """,
        _phone_variants(phone),
    )
    return dict(row) if row else None


# ── Read tools ──────────────────────────────────────────────────────────────
# Every SELECT here is an explicit, narrow column list — never SELECT *.
# religion_requirement, age_requirements, gender_requirements (screening
# criteria) and commercial terms (fee_amount, payment_terms, salary_deductions,
# replacement_period, agreement_url, gst_tds) are never selected by anything in
# this file, under any tool, for any reason — those defer to a human, same as
# the seeded client_faqs "replacement_policy" answer.

async def _tool_get_client_status(conn: asyncpg.Connection, client_id) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT company_name, job_title, industry, stage, min_salary, max_salary,
               job_location, location, job_timings, job_type, key_skills, num_employees
        FROM clients
        WHERE id = $1
        """,
        client_id,
    )
    return dict(row) if row else None


async def _tool_get_pipeline_summary(conn: asyncpg.Connection, client_id) -> dict:
    """Counts of mapped candidates by stage — enough to answer "how many
    profiles do I have" / "what's the status" without exposing candidate PII."""
    rows = await conn.fetch(
        """
        SELECT stage::text AS stage, COUNT(*) AS count
        FROM client_candidate_mappings
        WHERE client_id = $1
        GROUP BY stage
        """,
        client_id,
    )
    return {r["stage"]: r["count"] for r in rows}


async def _tool_get_recent_messages(conn: asyncpg.Connection, phone: str, limit: int = 10) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT direction, message_text, created_at
        FROM whatsapp_messages
        WHERE phone = ANY($1::text[])
        ORDER BY created_at DESC
        LIMIT $2
        """,
        _phone_variants(phone), max(1, min(limit, 20)),
    )
    return [dict(r) for r in reversed(rows)]


async def _tool_search_knowledge(conn: asyncpg.Connection, query: str) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT topic, answer
        FROM client_faqs
        WHERE is_active
          AND (
            EXISTS (SELECT 1 FROM unnest(question_patterns) p WHERE $1 ILIKE '%' || p || '%')
            OR topic ILIKE '%' || $1 || '%'
          )
        LIMIT 5
        """,
        query,
    )
    return [dict(r) for r in rows]


def _tool_build_client_portal_link() -> str:
    return os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")


def _tool_build_onboarding_link() -> str:
    """The hiring-requirement intake form — filling this in is the single
    conversion goal for any client this agent talks to (see system prompt)."""
    portal_url = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")
    return f"{portal_url}/client-onboard"


# ── Terminal tools ────────────────────────────────────────────────────────────

def _sanitize_reply(text: str) -> str:
    """Server-side validation, regardless of what the prompt asked for: length
    cap, and strip any link that isn't our own portal domain."""
    text = (text or "").strip()
    if not text:
        return ""
    if len(text) > 700:
        text = text[:700].rsplit(" ", 1)[0] + "…"

    allowed_hosts = set()
    url = os.environ.get("CLIENT_PORTAL_URL", "").strip()
    if url:
        allowed_hosts.add(urlparse(url).netloc)

    for m in re.finditer(r"https?://\S+", text):
        host = urlparse(m.group(0)).netloc
        if allowed_hosts and host not in allowed_hosts:
            text = text.replace(m.group(0), "").strip()
    return text


async def _do_escalate(
    conn: asyncpg.Connection, client_id, from_phone: str, reason: str, text: str = "",
    category: str = "ai_escalation",
) -> None:
    """Holding message to the client + a claimable ticket. Never silent.

    Google Chat / RM-WhatsApp paging is deliberately not used here anymore —
    the ticket (and its in-page unclaimed-ticket toast on the bottom_end CE
    desk) is the notification now, not a parallel channel."""
    try:
        await msg91.send_text(
            from_phone,
            "Let me get our team to help you with that — someone will follow up with you shortly. 🙏",
            sender="client",
        )
    except Exception as e:
        print(f"[ai_client_rm] escalation holding message failed for {from_phone}: {e}")

    try:
        await ticket_service.create_or_reopen_ticket(
            conn, phone=from_phone, channel="client",
            queue_code="client_queue" if category == "ai_escalation" else "payments_queue",
            category_code=category, source=category, reason=reason,
            client_id=client_id, subject=(reason[:120] if reason else None),
        )
    except Exception as e:
        print(f"[ai_client_rm] ticket creation failed for {from_phone}: {e}")


# ── Audit log ─────────────────────────────────────────────────────────────────

async def _log_turn(
    conn: asyncpg.Connection, client_id, phone: str, inbound_text: str, *,
    action: str, escalated: bool, reply_text: str | None = None,
    reason: str | None = None, tool_calls: list[str] | None = None,
) -> None:
    try:
        await conn.execute(
            """
            INSERT INTO client_ai_turns
                (client_id, phone, inbound_text, action_taken, reply_text, escalated, escalate_reason, tool_calls)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            """,
            client_id, phone, inbound_text, action,
            reply_text, escalated, reason, json.dumps(tool_calls or []),
        )
    except Exception as e:
        print(f"[ai_client_rm] log_turn failed: {e}")


async def _turn_budget_exceeded(conn: asyncpg.Connection, client_id) -> bool:
    count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM client_ai_turns
        WHERE client_id = $1 AND created_at::date = now()::date AND action_taken = 'reply'
        """,
        client_id,
    )
    return (count or 0) >= _TURN_BUDGET


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are an experienced Relationship Manager at JustAccountants, a \
recruitment firm placing accounting professionals with employer clients across India. \
You are replying to a hiring client (business owner, HR, or point of contact) on \
WhatsApp who has messaged something outside the normal scripted flow — a question, a \
concern, or something unclear.

WHO WE ARE / WHAT WE DO — have this ready on any turn, without needing to call \
search_knowledge for it:
- JustAccountants is a recruitment firm that sources, screens, and matches accounting \
professionals to employers across India, end-to-end from shortlisting to offer.
- We place Junior and Senior Accountants only — bookkeeping, GST/TDS filing, \
reconciliation, ledger work. We do not place Chartered Accountants (CAs) as a \
qualification-based hire, and we do not recruit for cashier roles.
- Every candidate is screened before being shared, including a short automated \
screening call, so a client only reviews candidates who already meet the basics.
- Once a client's hiring requirement is confirmed (the onboarding form — see CONVERSION \
GOAL below), matching starts and most clients see their first relevant profiles within \
a couple of days, reviewed and actioned entirely from the client portal.

CONVERSION GOAL — this is the single most important outcome of any conversation with a \
client who hasn't yet submitted their hiring requirement (stage is anything before \
agreement_sent — lead, scraping, scraped, reachout_sent, or interested): get them to \
complete the onboarding form via build_onboarding_link(). It's a one-time form (company \
details, role, salary range, location) — once submitted, we start matching immediately. \
Whenever the conversation naturally allows it — a process question, expressed interest, \
"what's next", timing, or anything that implies they haven't started — share the link \
and frame it as the concrete next step. Don't force it into a reply that has nothing to \
do with it (e.g. a plain thank-you), but default toward including it rather than \
leaving it out. If get_client_status shows stage is already agreement_sent or later, \
they've already submitted requirements — don't push this link; answer their actual \
question instead.

The next system message gives you this client's current status (company name, job \
title, industry, stage, salary range, location, job type) and their last 5 WhatsApp \
messages — already fetched, always current. Use it directly; you don't need to call \
get_client_status or get_recent_messages to get this same data again.

HARD CONSTRAINTS — never break these:
- Never state a commercial term that didn't come from that status message or a tool \
call this turn — fee amount, payment terms, replacement period, and contract specifics \
are NOT available to you and must always go to a human via escalate_commercial(). If \
asked, say your customer executive will confirm the specifics.
- Never disclose an individual candidate's personal details (name, phone, salary) over \
WhatsApp — the portal is where they review actual candidate profiles. You may reference \
counts/stages (e.g. "you have 3 profiles awaiting review") from get_pipeline_summary.
- Never promise a hiring outcome, a specific candidate, or a timeline you don't have data for.
- Never perform an action yourself — you only explain and link to the client portal, \
where they review profiles, book interviews, and manage offers. You have no tool that \
changes their stage or anything else.
- Keep replies WhatsApp-length (2-4 short sentences), plain text, no markdown, at most \
one link.
- Be professional and to the point. This is a business client, not a friend — no slang, \
no casual filler words ("bhai", "buddy", "dude", excessive emojis), no over-familiar \
tone. Write the way a courteous, efficient account manager would text.
- Your job is to INFORM and ROUTE, never to COLLECT. Don't ask the client for their own \
requirement details in chat — that's what the portal/onboarding form is for.

DECISION POLICY:
- If it's a genuine question you can answer from the provided status, tool data, or \
knowledge, call reply() with a grounded, honest answer.
- If it's off-topic, spam, or a wrong number, call no_reply().
- If it's a plain acknowledgment or a message that's just closing out the conversation — \
"ok", "thanks", "got it", "sure", a 👍 — and doesn't ask anything new, call no_reply(). \
Don't send a closing message back; that just invites another "ok" in return.
- If the client raises a commercial/contractual/legal question — fee, payment terms, \
replacement period, contract specifics, or wants to cancel — call escalate_commercial() \
immediately.
- If the client explicitly asks for a human, sounds frustrated, or disputes something \
non-commercial, call escalate() immediately — don't attempt to handle it yourself.
- If a tool call comes back empty or you're genuinely unsure, call escalate() rather \
than guess.

TOOL DISCIPLINE:
- Call get_pipeline_summary before answering anything about candidate status or counts.
- Never invent a client_id — only use IDs exactly as provided in the status message.
- End every turn with exactly one of reply(), escalate(), escalate_commercial(), or \
no_reply() — never answer in plain text without calling one of these.
"""

_TOOLS = [
    {"type": "function", "function": {
        "name": "get_client_status",
        "description": "Look up this client's own requirement record — company name, job title, industry, stage, salary range, location, job type. Already provided in the status message at the start of this conversation; only call this again if you suspect it's changed mid-turn.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "get_pipeline_summary",
        "description": "Get counts of this client's mapped candidates grouped by pipeline stage (e.g. matched, intent_ask, client_approval_pending, interview_scheduled, placed). Use this for any question about candidate status or counts — never invent a number.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "get_recent_messages",
        "description": "Recent WhatsApp conversation history with this client, both directions, oldest first. The last 5 messages are already provided in the status message at the start of this conversation — only call this for more than 5 if you genuinely need older context.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "search_knowledge",
        "description": "Search static FAQ/policy answers — who we are, positions we fill, how matching works, replacement policy. Never use this for client-specific facts.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "build_client_portal_link",
        "description": "Get the client-portal URL, where the client reviews candidate profiles, books interviews, and manages offers.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "build_onboarding_link",
        "description": "Get the hiring-requirement onboarding form link. Share this with any client who hasn't yet submitted their requirement (stage before agreement_sent) — see CONVERSION GOAL.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "reply",
        "description": "Send your final answer to the client. Ends the turn.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "escalate",
        "description": "Hand this off to a human customer executive — sends the client a holding message and opens a support ticket. Ends the turn.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
    }},
    {"type": "function", "function": {
        "name": "escalate_commercial",
        "description": "Hand this off to a human for a commercial or contractual matter — fee, payment terms, replacement period, contract specifics. Sends the client a holding message and opens a ticket in the finance/commercial queue. Ends the turn.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
    }},
    {"type": "function", "function": {
        "name": "no_reply",
        "description": "Take no client-visible action — off-topic, spam, wrong number, or a plain acknowledgment/conversation-closer (\"ok\", \"thanks\", \"got it\") that doesn't need a response. Ends the turn.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": []},
    }},
]

_TERMINAL = {"reply", "escalate", "escalate_commercial", "no_reply"}


# ── Tool dispatch ─────────────────────────────────────────────────────────────

async def _execute_read_tool(conn: asyncpg.Connection, name: str, args: dict, client_id, ctx: dict):
    if name == "get_client_status":
        if ctx["client"] is None:
            ctx["client"] = await _tool_get_client_status(conn, client_id) or {}
        return ctx["client"] or {"found": False}

    if name == "get_pipeline_summary":
        if ctx["pipeline"] is None:
            ctx["pipeline"] = await _tool_get_pipeline_summary(conn, client_id)
        return ctx["pipeline"]

    if name == "get_recent_messages":
        return await _tool_get_recent_messages(conn, ctx["phone"], int(args.get("limit", 10)))

    if name == "search_knowledge":
        return await _tool_search_knowledge(conn, args.get("query", ""))

    if name == "build_client_portal_link":
        return {"url": _tool_build_client_portal_link()}

    if name == "build_onboarding_link":
        return {"url": _tool_build_onboarding_link()}

    return {"error": f"unknown tool {name}"}


async def _execute_terminal(conn: asyncpg.Connection, name: str, args: dict, from_phone: str, client_id, ctx: dict):
    if name == "reply":
        text = _sanitize_reply(args.get("text", ""))
        if text:
            await msg91.send_text(from_phone, text, sender="client")
        return

    if name == "escalate":
        await _do_escalate(conn, client_id, from_phone, reason=args.get("reason", ""))
        return

    if name == "escalate_commercial":
        await _do_escalate(conn, client_id, from_phone, reason=args.get("reason", ""), category="commercial_escalation")
        return

    # no_reply — nothing to do


# ── Agent loop ────────────────────────────────────────────────────────────────

async def _run_agent_loop(conn: asyncpg.Connection, from_phone: str, text: str, client: dict) -> None:
    ai = _get_client()

    client_id = client["id"]
    status = await _tool_get_client_status(conn, client_id)
    recent_messages = await _tool_get_recent_messages(conn, from_phone, limit=5)

    context_blob = {"client": status, "recent_conversation": recent_messages}
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "system", "content": "Current client status and last 5 messages (already up to date — no need to call get_client_status or get_recent_messages again unless you need a field not shown here):\n" + json.dumps(context_blob, default=str)},
        {"role": "user", "content": text},
    ]
    ctx: dict = {"client": status, "pipeline": None, "phone": from_phone, "tool_log": []}

    for _ in range(_MAX_ROUNDS):
        resp = await ai.chat.completions.create(
            model=_MODEL, messages=messages, tools=_TOOLS, tool_choice="auto", temperature=0.3,
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            messages.append({"role": "assistant", "content": msg.content or ""})
            messages.append({
                "role": "user",
                "content": "You must end your turn by calling reply(), escalate(), escalate_commercial(), or no_reply().",
            })
            continue

        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
        })

        terminal_call = None
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            ctx["tool_log"].append(name)

            if name in _TERMINAL:
                terminal_call = (name, args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": "{}"})
                continue

            result = await _execute_read_tool(conn, name, args, client_id, ctx)
            messages.append({
                "role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str),
            })

        if terminal_call:
            name, args = terminal_call
            await _execute_terminal(conn, name, args, from_phone, client_id, ctx)
            await _log_turn(
                conn, client_id, from_phone, text,
                action=name, escalated=(name == "escalate"),
                reply_text=args.get("text"), reason=args.get("reason"),
                tool_calls=ctx["tool_log"],
            )
            return

    # Exhausted rounds without a terminal call — don't leave the client hanging.
    await _do_escalate(conn, client_id, from_phone, reason="agent exceeded tool-call round limit", text=text)
    await _log_turn(
        conn, client_id, from_phone, text,
        action="escalate", escalated=True, reason="round limit exceeded", tool_calls=ctx["tool_log"],
    )


# ── Entry point ───────────────────────────────────────────────────────────────

async def handle_free_text(conn: asyncpg.Connection, from_phone: str, text: str, wa_message_id=None) -> None:
    """
    Called from routers/whatsapp.py for inbound client free-text that didn't
    match a deterministic button/keyword flow, for clients NOT in the
    'onboarded' stage (onboarded clients get client_support_bot instead).

    Buffers this fragment against client_ai_pending rather than running the
    agent immediately — see the debounce note in the module docstring.
    """
    ticket_id = await ticket_service.log_customer_reply(conn, from_phone, "client", wa_message_id, text)
    if ticket_id:
        return  # an open (or just-reopened) ticket exists — human-owned, AI stays silent

    if os.environ.get("CLIENT_AI_RM_ENABLED", "false").strip().lower() != "true":
        return

    # Per-client gate is worth checking before buffering at all — no point
    # writing a pending row for someone outside the rollout cohort. Re-checked
    # again in _process_turn() once the debounce window closes, since state
    # can change during the wait.
    client = await _find_client(conn, from_phone)
    if not client or not client["ai_rm_enabled"]:
        return

    stamp = await conn.fetchval(
        """
        INSERT INTO client_ai_pending (phone, buffered_text, updated_at)
        VALUES ($1, $2, now())
        ON CONFLICT (phone) DO UPDATE
            SET buffered_text = client_ai_pending.buffered_text || E'\n' || $2,
                updated_at = now()
        RETURNING updated_at
        """,
        from_phone, text,
    )

    # Don't hold the caller's pooled connection through the debounce sleep —
    # this task acquires its own connection only once it actually wakes up.
    asyncio.create_task(_debounced_process(from_phone, stamp))


async def _debounced_process(from_phone: str, my_stamp) -> None:
    await asyncio.sleep(_DEBOUNCE_SECONDS)

    pool = get_pool()
    async with pool.acquire() as conn:
        current = await conn.fetchval(
            "SELECT updated_at FROM client_ai_pending WHERE phone = $1", from_phone
        )
        if current is None or current != my_stamp:
            return  # a newer fragment arrived — that task owns the (now larger) buffer

        combined = await conn.fetchval(
            "DELETE FROM client_ai_pending WHERE phone = $1 AND updated_at = $2 RETURNING buffered_text",
            from_phone, my_stamp,
        )
        if combined is None:
            return  # lost a race to claim it — bail rather than double-process

        await _process_turn(conn, from_phone, combined)


async def _process_turn(conn: asyncpg.Connection, from_phone: str, text: str) -> None:
    """The actual gated agent turn, run once per debounced (possibly multi-fragment) message."""
    client = await _find_client(conn, from_phone)
    if not client or not client["ai_rm_enabled"]:
        return  # flipped off, or client vanished, during the debounce window — bail silently

    client_id = client["id"]

    if _trips_safety_filter(text):
        await _do_escalate(conn, client_id, from_phone, reason="frustration/human-request keyword match", text=text)
        await _log_turn(conn, client_id, from_phone, text, action="escalate", escalated=True, reason="safety pre-filter")
        return

    if await _turn_budget_exceeded(conn, client_id):
        await _do_escalate(conn, client_id, from_phone, reason="turn budget exceeded", text=text)
        await _log_turn(conn, client_id, from_phone, text, action="escalate", escalated=True, reason="turn budget exceeded")
        return

    try:
        await _run_agent_loop(conn, from_phone, text, client)
    except Exception as e:
        print(f"[ai_client_rm] agent loop failed for {from_phone}: {e}")
        try:
            await _do_escalate(conn, client_id, from_phone, reason=f"agent error: {e}", text=text)
            await _log_turn(conn, client_id, from_phone, text, action="escalate", escalated=True, reason=f"agent error: {e}")
        except Exception as e2:
            print(f"[ai_client_rm] fallback escalation also failed for {from_phone}: {e2}")
        try:
            await send_alert("AI client RM agent loop crashed", str(e), severity="ERROR", context={"phone": from_phone})
        except Exception:
            pass
