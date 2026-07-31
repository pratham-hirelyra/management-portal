"""
AI Relationship Manager — advisory-only agent for the candidate WhatsApp channel.

Single tool-calling loop: the model calls read tools to build context, then must
end its turn on exactly one terminal tool — reply(), escalate(), or no_reply().
No tool here ever writes to client_candidate_mappings.stage, candidates, or any
other column that drives the deterministic workflow in flow_engine.py. It only
explains, and points to the button or portal screen that already performs the
action.

Entry point: handle_free_text(conn, from_phone, text) — called from
routers/whatsapp.py::_handle_candidate_reply() for any inbound free-text message
that doesn't match a button payload or a deterministic keyword.

Candidates often send a thought across several rapid WhatsApp messages instead
of one ("Hi", "wanted to ask", "about my interview"). handle_free_text() buffers
each fragment in candidate_ai_pending and only actually runs the agent once the
phone has gone quiet for AI_RM_DEBOUNCE_SECONDS, so the loop always sees the
whole thought at once instead of replying to each fragment separately.
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
import services.flow_engine as flow_engine
import services.msg91_service as msg91
from services.google_chat_service import send_alert

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


_MODEL = os.environ.get("AI_RM_MODEL", "gpt-5.4")
_MAX_ROUNDS = 6          # tool-call round-trips before a forced escalate
# Daily cap on successful replies to one candidate — a backstop against a
# genuinely stuck/looping conversation, not a quota on ordinary usage. Keep
# this generous: a candidate asking several different real questions in one
# day (job details, then timing, then documents) is normal, not a loop.
_TURN_BUDGET = int(os.environ.get("AI_RM_TURN_BUDGET", "10"))
_UNKNOWN_DAILY_CAP = 3   # AI turns per unrecognised phone per day — abuse guard
_DEBOUNCE_SECONDS = int(os.environ.get("AI_RM_DEBOUNCE_SECONDS", "6"))


def _phone_variants(phone: str) -> list[str]:
    phone = (phone or "").lstrip("+").strip()
    variants = [phone]
    if phone.startswith("91") and len(phone) == 12:
        variants.append(phone[2:])
    elif len(phone) == 10 and phone.isdigit():
        variants.append("91" + phone)
    return variants


# ── Safety pre-filter — deterministic, runs before the model ever sees the message ──
# Same shape as _check_and_flag_bot() in routers/whatsapp.py: a thing that must
# catch reliably, not something hoped for in a system prompt.
_ESCALATE_KEYWORDS = frozenset([
    "talk to a person", "talk to someone", "speak to a human", "call me now",
    "real person", "not a bot", "this is ridiculous", "waste of time", "scam",
    "fraud", "cheated", "lawyer", "legal action", "consumer court", "complaint",
])


def _trips_safety_filter(text: str) -> bool:
    lower = (text or "").lower()
    return any(kw in lower for kw in _ESCALATE_KEYWORDS)


# ── Read tools ──────────────────────────────────────────────────────────────
# Every SELECT here is an explicit, narrow column list — never SELECT *.
# `religion` (candidates) and `religion_requirement` (clients) are never
# selected by anything in this file, under any tool, for any reason.

async def _tool_get_candidate(conn: asyncpg.Connection, phone: str) -> dict | None:
    """
    Deliberately excludes: religion, gender, age_years (no candidate-facing use
    case, and religion specifically must never reach the model's context), plus
    evaluation_summary, hiring_decision_reason, technical_evaluation_score
    (internal scoring detail — only the pass/fail outcome is ever
    candidate-facing, matching what the existing eval-result WhatsApp templates
    already disclose).

    current_salary IS included (expected_salary isn't a real, populated field
    in this system) — needed so the agent can talk about salary fit without
    inventing a number.
    """
    row = await conn.fetchrow(
        """
        SELECT id, name, category, evaluation_status, current_salary, work_preference,
               form_submitted, call_status, call_drop_retry_count, drop_final_sent
        FROM candidates
        WHERE phone = ANY($1::text[])
        LIMIT 1
        """,
        _phone_variants(phone),
    )
    return dict(row) if row else None


async def _tool_get_active_mappings(conn: asyncpg.Connection, candidate_id: str) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT
            ccm.id AS mapping_id, ccm.client_id, ccm.stage,
            ccm.decline_reason, ccm.rejection_reason, ccm.offer_decline_reason, ccm.join_decline_reason,
            ccm.interview_slot, ccm.available_slots,
            ccm.interested_form_sent_at, ccm.interested_form_reminder_count,
            ccm.documents_requested_at, ccm.documents_submitted_at,
            ccm.join_intent_requested_at,
            ccm.offer_issued_at, ccm.offer_expires_at, ccm.offered_salary,
            ccm.joining_date, ccm.updated_at,
            cl.company_name, cl.job_title,
            (col.unlocked_at IS NULL) AS lock_active
        FROM client_candidate_mappings ccm
        JOIN clients cl ON cl.id = ccm.client_id
        LEFT JOIN candidate_locks col
            ON col.candidate_id = ccm.candidate_id AND col.client_id = ccm.client_id
        WHERE ccm.candidate_id = $1
        ORDER BY ccm.updated_at DESC
        LIMIT 10
        """,
        uuid.UUID(candidate_id),
    )
    return [dict(r) for r in rows]


async def _tool_get_job_details(conn: asyncpg.Connection, client_id: str) -> dict | None:
    """
    Allowlisted projection, not SELECT * — deliberately excludes commercial terms
    (fee_amount, payment_terms, salary_deductions, replacement_period,
    agreement_url, gst_tds — what the client pays JustAccountants, none of a
    candidate's business), screening criteria (age_requirements,
    gender_requirements, religion_requirement — religion_requirement is never
    selected here, full stop), and internal ops fields (poc_*, source, rm_id,
    location_lat/lng).
    """
    row = await conn.fetchrow(
        """
        SELECT company_name, company_description, job_title, industry, num_employees,
               location, job_location, min_salary, max_salary, key_skills,
               job_timings, job_type, tools_requirements
        FROM clients
        WHERE id = $1
        """,
        uuid.UUID(client_id),
    )
    return dict(row) if row else None


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
        FROM candidate_faqs
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


def _tool_build_portal_link(mapping_id: str, view: str = "documents") -> str:
    portal_url = os.environ.get(
        "CANDIDATE_PORTAL_URL", os.environ.get("FRONTEND_URL", "http://localhost:5174")
    ).rstrip("/")
    return f"{portal_url}{flow_engine.candidate_portal_deep_link(uuid.UUID(mapping_id), view)}"


def _tool_build_apply_link() -> str:
    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    return f"{frontend_url}/apply"


# ── Terminal tools ────────────────────────────────────────────────────────────

def _sanitize_reply(text: str) -> str:
    """Server-side validation, regardless of what the prompt asked for: length
    cap, and strip any link that isn't our own portal/apply domain."""
    text = (text or "").strip()
    if not text:
        return ""
    if len(text) > 700:
        text = text[:700].rsplit(" ", 1)[0] + "…"

    allowed_hosts = set()
    for env_key in ("CANDIDATE_PORTAL_URL", "FRONTEND_URL"):
        url = os.environ.get(env_key, "").strip()
        if url:
            allowed_hosts.add(urlparse(url).netloc)

    for m in re.finditer(r"https?://\S+", text):
        host = urlparse(m.group(0)).netloc
        if allowed_hosts and host not in allowed_hosts:
            text = text.replace(m.group(0), "").strip()
    return text


async def _do_escalate(
    conn: asyncpg.Connection, candidate_id, from_phone: str, reason: str,
    text: str = "", client_id: str | None = None,
) -> None:
    """Holding message to the candidate + notify a human. Never silent."""
    try:
        await msg91.send_text(
            from_phone,
            "Let me get our team to help you with that — someone will follow up with you shortly. 🙏",
            sender="candidate",
        )
    except Exception as e:
        print(f"[ai_rm] escalation holding message failed for {from_phone}: {e}")

    cand_name = ""
    if candidate_id:
        row = await conn.fetchrow("SELECT name FROM candidates WHERE id = $1", candidate_id)
        cand_name = (row["name"] if row else "") or ""

    message = f"Candidate {cand_name or from_phone} ({from_phone}) needs human follow-up: {reason}"
    if text:
        message += f'\nMessage: "{text[:300]}"'

    if client_id:
        try:
            await flow_engine.notify_rm_alert(uuid.UUID(client_id), message, conn)
            return
        except Exception as e:
            print(f"[ai_rm] notify_rm_alert failed, falling back to ops alert: {e}")

    await send_alert(
        "AI RM escalation", message, severity="WARNING",
        context={"phone": from_phone, "candidate_id": str(candidate_id) if candidate_id else "unknown"},
    )


# ── Audit log ─────────────────────────────────────────────────────────────────

async def _log_turn(
    conn: asyncpg.Connection, candidate_id, mapping_id, phone: str, inbound_text: str, *,
    action: str, escalated: bool, reply_text: str | None = None,
    reason: str | None = None, tool_calls: list[str] | None = None,
) -> None:
    try:
        await conn.execute(
            """
            INSERT INTO candidate_ai_turns
                (candidate_id, mapping_id, phone, inbound_text, action_taken,
                 reply_text, escalated, escalate_reason, tool_calls)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            """,
            candidate_id, mapping_id, phone, inbound_text, action,
            reply_text, escalated, reason, json.dumps(tool_calls or []),
        )
    except Exception as e:
        print(f"[ai_rm] log_turn failed: {e}")


async def _turn_budget_exceeded(conn: asyncpg.Connection, candidate_id) -> bool:
    count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM candidate_ai_turns
        WHERE candidate_id = $1 AND created_at::date = now()::date AND action_taken = 'reply'
        """,
        candidate_id,
    )
    return (count or 0) >= _TURN_BUDGET


async def _unknown_contact_over_cap(conn: asyncpg.Connection, phone: str) -> bool:
    count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM candidate_ai_turns
        WHERE phone = $1 AND candidate_id IS NULL AND created_at::date = now()::date
        """,
        phone,
    )
    return (count or 0) >= _UNKNOWN_DAILY_CAP


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are an experienced Relationship Manager at JustAccountants, a \
recruitment firm placing accounting professionals with employers across India. You are \
replying to a candidate on WhatsApp who has messaged something outside the normal \
scripted flow — a question, a concern, or something unclear.

The next system message gives you this candidate's current status (name, category, \
evaluation status, form-submission status, AI screening call status) and their last 5 \
WhatsApp messages — already fetched, always current. Use it directly; you don't need to \
call get_candidate or get_recent_messages to get this same data again.

HARD CONSTRAINTS — never break these:
- Never state a salary, company detail, or policy fact that didn't come from that status \
message or a tool call this turn. If you don't have it, say you'll check rather than guess.
- Never promise selection, placement, or a specific outcome.
- Never perform an action yourself — you only explain and link to the exact button or \
portal screen that performs it. You have no tool that changes anyone's stage, books \
anything, or triggers a call.
- Never disclose a client's screening criteria (age, gender, or religion requirements) \
even if asked directly — say that's not something you can share.
- Keep replies WhatsApp-length (2-4 short sentences), plain text, no markdown, at most \
one link.
- Use very simple English — short words, short sentences. Many candidates aren't fluent \
in English. No jargon, no complex phrasing.
- Be professional and to the point. No slang, no casual filler words ("bhai", "buddy", \
"dude", excessive emojis), no over-familiar tone. Simple English does not mean informal \
English — write the way a courteous, efficient HR professional would text, not a friend.
- Your job is to INFORM and ROUTE, never to COLLECT. Never ask the candidate for their \
own details — location, experience, salary, role preference, documents, anything about \
their profile. That's what the portal pages are for. If something needs to come from \
them, point them to the exact page below where they enter it themselves; don't ask for \
it in the chat.

CANDIDATE PORTAL PAGES — route to the specific one that matches what they need, not a \
vague "check the portal":
- Application form — build_apply_link(). For anyone who hasn't submitted the form yet \
(form_submitted is false, or no candidate record at all). One-time form, no login needed.
- Main portal (build_portal_link(), needs phone login) — two tabs:
  • Jobs tab (default): see their job matches, pick an interview slot, accept/decline an \
offer, upload interview documents (salary slips, bank statement).
  • Profile tab: view or update their own profile details, upload/replace their CV, see \
their evaluation result.
- build_portal_link(mapping_id, view) jumps straight to one action on a specific job — \
view="documents" for document upload, view="offer" for accept/decline, view="jobs" for \
the general job view. mapping_id must come from get_active_mappings.

DECISION POLICY:
- If the message matches something a button or portal action already handles (e.g. \
"yes I'm interested", "done, uploaded"), explain briefly and point to the exact button \
or the relevant portal link via reply(). Never treat this as if you performed the action.
- If it's a genuine question you can answer from the provided status, tool data, or \
knowledge, call reply() with a grounded, honest answer.
- If it's off-topic, spam, or a wrong number, call no_reply().
- If it's a plain acknowledgment or a message that's just closing out the conversation — \
"ok", "thanks", "got it", "alright", "sure", a 👍 — and doesn't ask anything new, call \
no_reply(). Don't send a closing message back; that just invites another "ok" in return.
- If the candidate explicitly asks for a human, sounds frustrated, disputes an outcome, \
wants to negotiate money, or asks something legal or contractual, call escalate() \
immediately.
- If a tool call comes back empty or you're genuinely unsure, call escalate() rather \
than guess.

APPLICATION STATUS — read from the provided candidate status on every turn about jobs, \
opportunities, status, or next steps, and steer accordingly:
- form_submitted is false: nothing else can move forward until the form is in, regardless \
of what they actually asked — tell them to complete it and share build_apply_link() with \
the real URL. Don't just say "check the portal" without the link.
- form_submitted is true and evaluation_status is empty: their AI screening call hasn't \
completed yet. Tell them once that's done we'll share matching opportunities with them — \
don't claim a call is scheduled or in progress unless call_status confirms it.
- evaluation_status is pass/fail: that's settled — answer from get_active_mappings instead \
of repeating either of the above.
- No candidate record at all (status shows null): this is a first-time prospective \
contact, not an error — greet them briefly and share build_apply_link() directly. Don't \
ask a qualifying question first.

CANDIDATE SCENARIOS — these only cover candidates who replied with free text; if they \
already tapped a button, the WhatsApp bot handled it directly and you won't see it here:
- Says they're not looking for a job right now: tell them to tap the "Not Looking for \
Job" button on our earlier message — that's what updates their status and stops new \
opportunities. Don't try to record this yourself.
- Not interested in the specific JD shared (but might be open to others): tell them to \
tap "Not Interested Role" — mention we're a specialised accounting-only recruiter with \
other roles open, and that button takes them to the portal to set their job preferences \
so we can match them elsewhere.
- Has a specific constraint on this role (too far, current salary too high, only wants a \
CA firm): same as above — tap "Not Interested Role", then fill the preference form.
- Doesn't know GST/TDS but this specific JD needs it: tell them this JD likely isn't the \
right match, but to tap "Not Interested Role", fill the preference form, and complete \
their AI screening call so we can match them to something that fits their actual skills.
- Was not job-hunting before but says they are now: this is the one case where you ask a \
clarifying question first — confirm "are you actively looking for an accounting job \
right now?" and only share build_apply_link() once they say yes. Everywhere else, route \
without asking.
- Having trouble filling out the form generally (not the location field specifically — \
see build_apply_link() guidance above for that): apologise, ask what specific issue or \
error they're seeing, then call escalate() so a human can help — don't try to diagnose it \
yourself.
- Says upfront they can't talk right now / won't be able to attend the upcoming call \
(before it's happened yet): different from the case below — this one has a real answer, \
see search_knowledge topic "cant_talk_right_now". Tell them to still pick up when it \
rings and tell the AI agent what time works; no escalation needed.
- Already missed their AI screening call and asks for a callback (the call already \
happened and they didn't pick up): we don't currently have a tool that can queue or \
trigger a new call — call escalate() so a human follows up. Don't tell them we'll call \
back in a few minutes; that's not something you can actually guarantee yet.
- Asks to delay applying (exams, travelling, etc.): tell them that's completely fine — \
meanwhile they can tap Interested and fill out the form now, we'll share JDs on WhatsApp \
for them to consider, and interviews get set up once they're free.

TOOL DISCIPLINE:
- If they have any client relationship, call get_active_mappings before answering \
anything specific to them.
- Never invent a candidate_id, mapping_id, or client_id — only use IDs exactly as \
returned by an earlier tool call this turn.
- End every turn with exactly one of reply(), escalate(), or no_reply() — never answer \
in plain text without calling one of these.
"""

_TOOLS = [
    {"type": "function", "function": {
        "name": "get_candidate",
        "description": "Look up this candidate's own record — name, category, evaluation status, current salary, work preference, form-submission status, and AI screening call status. Already provided in the status message at the start of this conversation; only call this again if you suspect it's changed mid-turn.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "get_active_mappings",
        "description": "List this candidate's client relationships — stage, company name, job title, interview slot, form/document/offer timestamps, and whether their match with each client is still active. Newest first.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "get_job_details",
        "description": "Get role details for one specific client — salary range, location, timings, skills, job type, company description. client_id must come from a get_active_mappings result.",
        "parameters": {"type": "object", "properties": {"client_id": {"type": "string"}}, "required": ["client_id"]},
    }},
    {"type": "function", "function": {
        "name": "get_recent_messages",
        "description": "Recent WhatsApp conversation history with this candidate, both directions, oldest first. The last 5 messages are already provided in the status message at the start of this conversation — only call this for more than 5 if you genuinely need older context.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "search_knowledge",
        "description": "Search static FAQ/policy answers — interview process, the AI screening call, document requirements, joining process. Never use this for candidate- or client-specific facts.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "build_portal_link",
        "description": "Get the exact candidate-portal URL for a specific mapping and screen. mapping_id must come from a get_active_mappings result.",
        "parameters": {"type": "object", "properties": {
            "mapping_id": {"type": "string"},
            "view": {"type": "string", "enum": ["documents", "jobs", "offer"], "default": "documents"},
        }, "required": ["mapping_id"]},
    }},
    {"type": "function", "function": {
        "name": "build_apply_link",
        "description": "Get the application-form link, for a prospective candidate with no mapping yet.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "reply",
        "description": "Send your final answer to the candidate. Ends the turn.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "escalate",
        "description": "Hand this off to a human RM — sends the candidate a holding message and notifies a human. Ends the turn.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
    }},
    {"type": "function", "function": {
        "name": "no_reply",
        "description": "Take no candidate-visible action — off-topic, spam, wrong number, or a plain acknowledgment/conversation-closer (\"ok\", \"thanks\", \"got it\") that doesn't need a response. Ends the turn.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
    }},
]

_TERMINAL = {"reply", "escalate", "no_reply"}


# ── Tool dispatch ─────────────────────────────────────────────────────────────

async def _execute_read_tool(conn: asyncpg.Connection, name: str, args: dict, from_phone: str, ctx: dict):
    if name == "get_candidate":
        if ctx["candidate"] is None:
            ctx["candidate"] = await _tool_get_candidate(conn, from_phone) or {}
        return ctx["candidate"] or {"found": False}

    if name == "get_active_mappings":
        if ctx["mappings"] is None:
            if ctx["candidate"] is None:
                ctx["candidate"] = await _tool_get_candidate(conn, from_phone) or {}
            cand = ctx["candidate"]
            if cand and cand.get("id"):
                ctx["mappings"] = await _tool_get_active_mappings(conn, str(cand["id"]))
                if ctx["mappings"]:
                    ctx["last_client_id"] = str(ctx["mappings"][0]["client_id"])
                    ctx["last_mapping_id"] = str(ctx["mappings"][0]["mapping_id"])
            else:
                ctx["mappings"] = []
        return ctx["mappings"]

    if name == "get_job_details":
        try:
            return await _tool_get_job_details(conn, args["client_id"]) or {"found": False}
        except Exception:
            return {"found": False, "error": "invalid client_id"}

    if name == "get_recent_messages":
        return await _tool_get_recent_messages(conn, from_phone, int(args.get("limit", 10)))

    if name == "search_knowledge":
        return await _tool_search_knowledge(conn, args.get("query", ""))

    if name == "build_portal_link":
        try:
            return {"url": _tool_build_portal_link(args["mapping_id"], args.get("view", "documents"))}
        except Exception:
            return {"error": "invalid mapping_id"}

    if name == "build_apply_link":
        return {"url": _tool_build_apply_link()}

    return {"error": f"unknown tool {name}"}


async def _execute_terminal(conn: asyncpg.Connection, name: str, args: dict, from_phone: str, candidate_id, ctx: dict):
    if name == "reply":
        text = _sanitize_reply(args.get("text", ""))
        if text:
            await msg91.send_text(from_phone, text, sender="candidate")
        return

    if name == "escalate":
        await _do_escalate(
            conn, candidate_id, from_phone, reason=args.get("reason", ""),
            client_id=ctx.get("last_client_id"),
        )
        return

    # no_reply — nothing to do


# ── Agent loop ────────────────────────────────────────────────────────────────

async def _run_agent_loop(conn: asyncpg.Connection, from_phone: str, text: str, candidate_id) -> None:
    ai = _get_client()

    # Eagerly fetched, not tool-gated — form_submitted / evaluation_status must
    # be known on every turn regardless of whether the model chooses to call
    # get_candidate (tool_choice="auto" means it's never guaranteed to). Recent
    # conversation is capped at 5 messages for the same reason: fixed, known
    # context rather than an optional, variable-depth tool call.
    candidate = await _tool_get_candidate(conn, from_phone)
    recent_messages = await _tool_get_recent_messages(conn, from_phone, limit=5)

    context_blob = {"candidate": candidate, "recent_conversation": recent_messages}
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "system", "content": "Current candidate status and last 5 messages (already up to date — no need to call get_candidate or get_recent_messages again unless you need a field not shown here):\n" + json.dumps(context_blob, default=str)},
        {"role": "user", "content": text},
    ]
    ctx: dict = {"candidate": candidate, "mappings": None, "last_client_id": None, "last_mapping_id": None, "tool_log": []}

    for _ in range(_MAX_ROUNDS):
        resp = await ai.chat.completions.create(
            model=_MODEL, messages=messages, tools=_TOOLS, tool_choice="auto", temperature=0.3,
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            messages.append({"role": "assistant", "content": msg.content or ""})
            messages.append({
                "role": "user",
                "content": "You must end your turn by calling reply(), escalate(), or no_reply().",
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

            result = await _execute_read_tool(conn, name, args, from_phone, ctx)
            messages.append({
                "role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str),
            })

        if terminal_call:
            name, args = terminal_call
            await _execute_terminal(conn, name, args, from_phone, candidate_id, ctx)
            await _log_turn(
                conn, candidate_id, ctx.get("last_mapping_id"), from_phone, text,
                action=name, escalated=(name == "escalate"),
                reply_text=args.get("text"), reason=args.get("reason"),
                tool_calls=ctx["tool_log"],
            )
            return

    # Exhausted rounds without a terminal call — don't leave the candidate hanging.
    await _do_escalate(
        conn, candidate_id, from_phone, reason="agent exceeded tool-call round limit",
        text=text, client_id=ctx.get("last_client_id"),
    )
    await _log_turn(
        conn, candidate_id, ctx.get("last_mapping_id"), from_phone, text,
        action="escalate", escalated=True, reason="round limit exceeded", tool_calls=ctx["tool_log"],
    )


# ── Entry point ───────────────────────────────────────────────────────────────

async def handle_free_text(conn: asyncpg.Connection, from_phone: str, text: str) -> None:
    """
    Called from routers/whatsapp.py for any inbound free-text message from the
    candidate number that didn't match a button payload or a deterministic
    keyword. Replaces the old static unknown-contact greeting entirely.

    Buffers this fragment against candidate_ai_pending rather than running the
    agent immediately — see the debounce note in the module docstring.
    """
    if os.environ.get("CANDIDATE_AI_RM_ENABLED", "false").strip().lower() != "true":
        return

    # Per-candidate gate is worth checking before buffering at all — no point
    # writing a pending row for someone outside the rollout cohort. Re-checked
    # again in _process_turn() once the debounce window closes, since state
    # can change during the wait.
    gate = await conn.fetchrow(
        "SELECT ai_rm_enabled FROM candidates WHERE phone = ANY($1::text[]) LIMIT 1",
        _phone_variants(from_phone),
    )
    if gate and not gate["ai_rm_enabled"]:
        return

    stamp = await conn.fetchval(
        """
        INSERT INTO candidate_ai_pending (phone, buffered_text, updated_at)
        VALUES ($1, $2, now())
        ON CONFLICT (phone) DO UPDATE
            SET buffered_text = candidate_ai_pending.buffered_text || E'\n' || $2,
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
            "SELECT updated_at FROM candidate_ai_pending WHERE phone = $1", from_phone
        )
        if current is None or current != my_stamp:
            return  # a newer fragment arrived — that task owns the (now larger) buffer

        combined = await conn.fetchval(
            "DELETE FROM candidate_ai_pending WHERE phone = $1 AND updated_at = $2 RETURNING buffered_text",
            from_phone, my_stamp,
        )
        if combined is None:
            return  # lost a race to claim it — bail rather than double-process

        await _process_turn(conn, from_phone, combined)


async def _process_turn(conn: asyncpg.Connection, from_phone: str, text: str) -> None:
    """The actual gated agent turn, run once per debounced (possibly multi-fragment) message."""
    gate = await conn.fetchrow(
        "SELECT id, ai_rm_enabled FROM candidates WHERE phone = ANY($1::text[]) LIMIT 1",
        _phone_variants(from_phone),
    )

    if gate:
        candidate_id = gate["id"]
        if not gate["ai_rm_enabled"]:
            return  # flipped off during the debounce window — bail silently
    else:
        candidate_id = None
        if await _unknown_contact_over_cap(conn, from_phone):
            await _log_turn(conn, None, None, from_phone, text, action="no_reply", escalated=False, reason="daily reply cap")
            return

    if _trips_safety_filter(text):
        await _do_escalate(conn, candidate_id, from_phone, reason="frustration/human-request keyword match", text=text)
        await _log_turn(conn, candidate_id, None, from_phone, text, action="escalate", escalated=True, reason="safety pre-filter")
        return

    if candidate_id and await _turn_budget_exceeded(conn, candidate_id):
        await _do_escalate(conn, candidate_id, from_phone, reason="turn budget exceeded", text=text)
        await _log_turn(conn, candidate_id, None, from_phone, text, action="escalate", escalated=True, reason="turn budget exceeded")
        return

    try:
        await _run_agent_loop(conn, from_phone, text, candidate_id)
    except Exception as e:
        print(f"[ai_rm] agent loop failed for {from_phone}: {e}")
        try:
            await _do_escalate(conn, candidate_id, from_phone, reason=f"agent error: {e}", text=text)
            await _log_turn(conn, candidate_id, None, from_phone, text, action="escalate", escalated=True, reason=f"agent error: {e}")
        except Exception as e2:
            print(f"[ai_rm] fallback escalation also failed for {from_phone}: {e2}")
        try:
            await send_alert("AI RM agent loop crashed", str(e), severity="ERROR", context={"phone": from_phone})
        except Exception:
            pass
