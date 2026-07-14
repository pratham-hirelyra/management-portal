"""
RinggAI webhook endpoints.

POST /ringg/save-recording  — saves voice recording URL to candidate
POST /ringg/evaluate        — receives call transcript, runs GPT scoring,
                              updates candidate, sends WA, triggers slot links
"""

import json
import os
import uuid
from fastapi import APIRouter, Depends, Request, BackgroundTasks
import asyncpg
from database import get_conn, get_pool
import services.msg91_service as msg91
import services.ringg_service as ringg

from pydantic import BaseModel

router = APIRouter(prefix="/ringg", tags=["ringg"])


class SimulateEvalBody(BaseModel):
    phone: str
    result: str = "pass"  # "pass" | "fail"
    score_override: float | None = None  # optional manual percentage (0-100)


def _normalise_phone(phone: str) -> tuple[str, str]:
    """Return (phone_10, phone_91) — 10-digit and 91-prefixed."""
    phone = phone.strip().lstrip("+")
    if len(phone) == 12 and phone.startswith("91"):
        return phone[2:], phone
    if len(phone) == 10 and phone.isdigit():
        return phone, "91" + phone
    return phone, phone


@router.post("/save-recording")
async def save_recording(
    request: Request,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Called by RinggAI when the call recording is ready.
    Expected body: { "phone": "...", "recording_url": "...", ... }
    """
    payload = await request.json()
    print(f"[ringg] save-recording payload: {payload}")

    phone_raw = (
        payload.get("phone") or
        payload.get("candidate_phone") or
        payload.get("to") or ""
    )
    recording_url = (
        payload.get("recording_url") or
        payload.get("recordingUrl") or
        payload.get("audio_url") or ""
    )

    if not phone_raw or not recording_url:
        return {"ok": False, "error": "phone and recording_url are required"}

    phone_10, phone_91 = _normalise_phone(phone_raw)

    result = await conn.execute(
        """
        UPDATE candidates
        SET voice_recording_url = $1, updated_at = now()
        WHERE phone = $2 OR phone = $3
        """,
        recording_url, phone_10, phone_91,
    )
    updated = int(result.split()[-1]) if result else 0
    return {"ok": True, "updated": updated}


@router.post("/evaluate")
async def ringg_evaluate(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Called by RinggAI after a call with the evaluation transcript.
    Processes async to respond immediately.

    Expected body (flexible):
    {
      "phone": "...",
      "transcript": "...",
      "recording_url": "...",   // optional
      "call_id": "...",         // optional
    }
    """
    payload = await request.json()
    background_tasks.add_task(_process_evaluation, payload)
    return {"ok": True}


async def _process_evaluation(payload: dict) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            await _do_evaluate(conn, payload)
        except Exception as e:
            print(f"[ringg] evaluation error: {e}")
            try:
                from services.google_chat_service import send_alert
                await send_alert("RinggAI evaluation processing failed", str(e), severity="ERROR", context={"phone": payload.get("phone") or payload.get("to_number", "")})
            except Exception:
                pass


async def _do_evaluate(conn: asyncpg.Connection, payload: dict) -> None:
    import json as _json
    status     = (payload.get("status") or "").lower()
    sub_status = (payload.get("sub_status") or "").lower()

    # Ringg sends status=retry while it's still cycling through its own retries — ignore
    if status == "retry":
        retry_count = payload.get("retry_count", 0)
        print(f"[ringg] retry webhook (attempt {retry_count}) — ignoring until final outcome")
        return

    # All Ringg retries exhausted and candidate never answered
    if status == "failed" and sub_status == "busy line":
        custom_args = payload.get("custom_args_values") or {}
        phone_raw = (
            custom_args.get("mobile_number") or
            payload.get("to_number") or
            payload.get("phone") or ""
        )
        if phone_raw:
            phone_10, phone_91 = _normalise_phone(phone_raw)
            updated = await conn.execute(
                """
                UPDATE candidates SET call_status = 'NOT_PICKED', updated_at = now()
                WHERE (phone = $1 OR phone = $2)
                  AND LOWER(COALESCE(evaluation_status, '')) NOT IN ('pass', 'fail', 'passed', 'failed')
                """,
                phone_10, phone_91,
            )
            print(f"[ringg] no-pickup — marked NOT_PICKED for {phone_raw} ({updated})")
        else:
            print(f"[ringg] no-pickup webhook but could not extract phone: {_json.dumps(payload)}")
        return

    # Only process completed + accepted calls (mirrors n8n Switch1)
    if not (status == "completed" and sub_status == "accepted"):
        print(f"[ringg] non-accepted webhook — status={status} sub_status={sub_status} full_payload={_json.dumps(payload)}")
        return

    # Phone: RinggAI sends it in custom_args_values.mobile_number or to_number
    custom_args = payload.get("custom_args_values") or {}
    phone_raw = (
        custom_args.get("mobile_number") or
        payload.get("to_number") or
        payload.get("phone") or
        payload.get("candidate_phone") or
        payload.get("to") or ""
    )

    # Transcript: RinggAI sends a list of {bot/user} objects — convert to readable string
    transcript_raw = payload.get("transcript") or payload.get("call_transcript") or payload.get("text") or ""
    if isinstance(transcript_raw, list):
        lines = []
        for msg in transcript_raw:
            if msg.get("bot"):
                lines.append(f"[Interviewer]: {msg['bot']}")
            elif msg.get("user"):
                lines.append(f"[Candidate]: {msg['user']}")
        transcript = "\n".join(lines)
    else:
        transcript = transcript_raw or ""

    recording_url = (
        payload.get("recording_url") or payload.get("recordingUrl") or payload.get("audio_url") or ""
    )

    if not phone_raw or not transcript:
        print(f"[ringg] evaluate: missing phone or transcript. keys={list(payload.keys())}, custom_args={list(custom_args.keys())}")
        return

    # Drop-off detection: count non-empty candidate responses.
    # RinggAI includes {"user": ""} entries for unanswered questions — only count non-blank turns.
    # There are 10 questions; require at least 7 meaningful responses to evaluate.
    if isinstance(transcript_raw, list):
        candidate_turns = sum(1 for m in transcript_raw if m.get("user", "").strip())
    else:
        candidate_turns = transcript.count("[Candidate]:")

    if candidate_turns < 7:
        print(f"[ringg] evaluate: call dropped off for {phone_raw} — only {candidate_turns} non-empty candidate turns, marking DROPPED for cron retry")
        phone_10, phone_91 = _normalise_phone(phone_raw)
        await conn.execute(
            """
            UPDATE candidates SET call_status = 'DROPPED', updated_at = now()
            WHERE (phone = $1 OR phone = $2)
              AND (LOWER(COALESCE(evaluation_status, '')) NOT IN ('pass', 'fail', 'passed', 'failed'))
            """,
            phone_10, phone_91,
        )
        return

    print(f"[ringg] evaluate: call sufficiently complete for {phone_raw} ({candidate_turns} candidate turns) — proceeding with evaluation")

    phone_10, phone_91 = _normalise_phone(phone_raw)
    candidate = await conn.fetchrow(
        "SELECT * FROM candidates WHERE phone = $1 OR phone = $2", phone_10, phone_91,
    )
    if not candidate:
        print(f"[ringg] evaluate: candidate not found for phone {phone_raw}")
        return

    cand_id = candidate["id"]

    # Idempotency — skip if candidate already has a completed evaluation.
    # Guard 1: evaluation_status set (primary signal)
    already_evaluated = (candidate.get("evaluation_status") or "").lower() in ("pass", "fail", "passed", "failed")
    # Guard 2: evaluation_summary.completed = True (catches edge cases where status was cleared)
    if not already_evaluated:
        es = candidate.get("evaluation_summary") or {}
        if isinstance(es, str):
            try:
                es = json.loads(es)
            except Exception:
                es = {}
        if isinstance(es, dict) and es.get("completed"):
            already_evaluated = True
    if already_evaluated:
        print(f"[ringg] evaluate: candidate {cand_id} already has a completed evaluation — skipping second call")
        return

    result = await ringg.evaluate_transcript(
        transcript,
        candidate.get("expected_salary"),
        candidate.get("current_salary"),
    )
    eval_status = result["eval_status"]
    category    = result["category"]

    # Preserve candidate_summary stored by form submission
    existing_es = candidate.get("evaluation_summary") or {}
    if isinstance(existing_es, str):
        try:
            existing_es = json.loads(existing_es)
        except Exception:
            existing_es = {}
    preserved_candidate_summary = existing_es.get("candidate_summary", "")

    await conn.execute(
        """
        UPDATE candidates
        SET evaluation_status          = $2,
            technical_evaluation_score = $3,
            total_score                = $3,
            evaluation_summary         = $4::jsonb,
            voice_recording_url        = COALESCE(NULLIF($5, ''), voice_recording_url),
            call_status                = 'COMPLETED',
            category                   = $6,
            updated_at                 = now()
        WHERE id = $1
        """,
        cand_id, eval_status, result["percentage"],
        {
            "overview_summary": result["summary"],
            "technical_scoring": result.get("technical_scoring", {}),
            "hiring_decision": {
                "status": eval_status,
                "category": category,
                "reason": result["summary"],
            },
            "candidate_summary": preserved_candidate_summary,
            "percentage": result["percentage"],
            "scores": result["scores"],
            "completed": True,
        },
        recording_url, category,
    )
    await _apply_evaluation_result(conn, cand_id, phone_91, eval_status, category)

    # Regenerate the HTML report now that evaluation data is stored
    try:
        from routers.candidates import _regenerate_report
        updated_cand = await conn.fetchrow("SELECT * FROM candidates WHERE id = $1", cand_id)
        if updated_cand:
            report_url = await _regenerate_report(dict(updated_cand))
            if report_url:
                await conn.execute(
                    "UPDATE candidates SET final_evaluation_report_url = $1, updated_at = now() WHERE id = $2",
                    report_url, cand_id,
                )
                print(f"[ringg] regenerated report for {cand_id}: {report_url}")
    except Exception as e:
        print(f"[ringg] report regeneration failed for {cand_id}: {e}")
        try:
            from services.google_chat_service import send_alert
            await send_alert("Evaluation report regeneration failed", str(e), severity="ERROR", context={"cand_id": str(cand_id)})
        except Exception:
            pass


@router.post("/client-evaluate")
async def ringg_client_evaluate(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Called by RinggAI after a client AI-voice-reachout call with the transcript.
    Processes async to respond immediately.
    """
    payload = await request.json()
    background_tasks.add_task(_process_client_evaluation, payload)
    return {"ok": True}


async def _process_client_evaluation(payload: dict) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            await _do_client_evaluate(conn, payload)
        except Exception as e:
            print(f"[ringg] client evaluation error: {e}")
            try:
                from services.google_chat_service import send_alert
                await send_alert("RinggAI client evaluation processing failed", str(e), severity="ERROR", context={"phone": payload.get("phone") or payload.get("to_number", "")})
            except Exception:
                pass


async def _do_client_evaluate(conn: asyncpg.Connection, payload: dict) -> None:
    import json as _json

    status     = (payload.get("status") or "").lower()
    sub_status = (payload.get("sub_status") or "").lower()

    # Ringg sends status=retry while it's still cycling through its own retries — ignore
    if status == "retry":
        retry_count = payload.get("retry_count", 0)
        print(f"[ringg] client-evaluate retry webhook (attempt {retry_count}) — ignoring until final outcome")
        return

    custom_args = payload.get("custom_args_values") or {}
    phone_raw = (
        custom_args.get("mobile_number") or
        payload.get("to_number") or
        payload.get("phone") or ""
    )
    if not phone_raw:
        print(f"[ringg] client-evaluate: missing phone. payload keys={list(payload.keys())}")
        return

    phone_10, phone_91 = _normalise_phone(phone_raw)
    phone_candidates = list({phone_raw, phone_raw.lstrip("+"), phone_10, phone_91})

    call_row = await conn.fetchrow(
        """
        SELECT * FROM client_ai_calls
        WHERE phone = ANY($1::text[]) AND status IN ('queued', 'calling')
        ORDER BY triggered_at DESC LIMIT 1
        """,
        phone_candidates,
    )
    if not call_row:
        print(f"[ringg] client-evaluate: no in-flight client_ai_calls row found for phone {phone_raw}")
        return

    # All Ringg retries exhausted and the client never picked up
    if status == "failed" and sub_status == "busy line":
        await conn.execute(
            "UPDATE client_ai_calls SET status='no_pickup', completed_at=now() WHERE id=$1",
            call_row["id"],
        )
        return

    # Only process completed + accepted calls (mirrors candidate evaluate flow)
    if not (status == "completed" and sub_status == "accepted"):
        print(f"[ringg] client-evaluate: non-accepted webhook — status={status} sub_status={sub_status}")
        await conn.execute(
            "UPDATE client_ai_calls SET status='dropped', completed_at=now() WHERE id=$1",
            call_row["id"],
        )
        return

    transcript_raw = payload.get("transcript") or payload.get("call_transcript") or payload.get("text") or ""
    if isinstance(transcript_raw, list):
        lines = []
        for msg in transcript_raw:
            if msg.get("bot"):
                lines.append(f"[Agent]: {msg['bot']}")
            elif msg.get("user"):
                lines.append(f"[Client]: {msg['user']}")
        transcript = "\n".join(lines)
    else:
        transcript = transcript_raw or ""

    recording_url = (
        payload.get("recording_url") or payload.get("recordingUrl") or payload.get("audio_url") or ""
    )

    if not transcript:
        print(f"[ringg] client-evaluate: empty transcript for call {call_row['id']}")
        await conn.execute(
            "UPDATE client_ai_calls SET status='dropped', recording_url=$1, completed_at=now() WHERE id=$2",
            recording_url, call_row["id"],
        )
        return

    call_type = call_row["call_type"]
    result = (
        await ringg.classify_funnel_stuck_call(transcript)
        if call_type == "funnel_stuck"
        else await ringg.classify_client_call(transcript)
    )
    classification = result["classification"]
    reason = result["reason"]
    extracted_info = result["extracted_info"]

    await conn.execute(
        """
        UPDATE client_ai_calls
        SET status = 'completed',
            transcript = $1,
            recording_url = $2,
            classification = $3,
            classification_reason = $4,
            extracted_info = $5::jsonb,
            completed_at = now()
        WHERE id = $6
        """,
        transcript, recording_url, classification, reason, extracted_info, call_row["id"],
    )

    client_id = call_row["client_id"]
    print(f"[ringg] client-evaluate: call {call_row['id']} for client {client_id} classified as {classification} ({reason})")

    if call_type == "funnel_stuck":
        # Funnel Stuck agent — only side-effect is marking DND if the client says
        # they're not interested / clicked "Interested" by mistake. No stage change.
        if classification == "not_interested":
            await conn.execute(
                "UPDATE clients SET is_dnd = true, updated_at = now() WHERE id = $1",
                client_id,
            )
        return

    if classification == "interested":
        client_row = await conn.fetchrow("SELECT stage FROM clients WHERE id = $1", client_id)
        if client_row and client_row["stage"] not in ("agreement_sent", "onboarded"):
            await conn.execute(
                """
                UPDATE clients
                SET stage = 'interested'::client_stage,
                    poc_phone = COALESCE(poc_phone, $1),
                    onboarding_form_sent_at = COALESCE(onboarding_form_sent_at, now()),
                    updated_at = now()
                WHERE id = $2
                """,
                phone_91, client_id,
            )
            try:
                from routers.whatsapp import _send_onboarding_link
                await _send_onboarding_link(phone_91, str(client_id))
            except Exception as e:
                print(f"[ringg] client-evaluate: onboarding link send failed for {client_id}: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("Client onboarding link send failed after AI call", str(e), severity="ERROR", context={"client_id": str(client_id), "phone": phone_91})
                except Exception:
                    pass

    elif classification == "not_interested":
        client_row = await conn.fetchrow("SELECT stage FROM clients WHERE id = $1", client_id)
        if client_row and client_row["stage"] not in ("agreement_sent", "onboarded"):
            await conn.execute(
                """
                UPDATE clients
                SET is_dnd = true, stage = 'disqualified'::client_stage, updated_at = now()
                WHERE id = $1
                """,
                client_id,
            )
        else:
            await conn.execute(
                "UPDATE clients SET is_dnd = true, updated_at = now() WHERE id = $1",
                client_id,
            )


def _is_junior_role(job_title: str | None) -> bool:
    if not job_title:
        return False
    lower = job_title.lower()
    return "junior" in lower or "jr." in lower or lower.startswith("jr ")


async def _apply_evaluation_result(
    conn: asyncpg.Connection,
    cand_id,
    phone_91: str,
    eval_status: str,
    category: str = "",
) -> None:
    """Send WA and update mapping stages based on pass/fail/category."""

    # Fetch candidate name and client job_title from their interested mapping
    cand_row = await conn.fetchrow("SELECT name FROM candidates WHERE id = $1", cand_id)
    cand_name = (cand_row["name"] or "").split()[0] if cand_row and cand_row["name"] else "there"

    client_row = await conn.fetchrow(
        """
        SELECT cl.job_title FROM client_candidate_mappings ccm
        JOIN clients cl ON cl.id = ccm.client_id
        WHERE ccm.candidate_id = $1 AND ccm.stage = 'interested'::mapping_stage
        ORDER BY ccm.updated_at DESC LIMIT 1
        """,
        cand_id,
    )
    client_job_title = client_row["job_title"] if client_row else None

    is_pass = eval_status.lower() == "pass"
    is_junior_category = (category or "").lower() == "junior accountant"
    client_is_junior = _is_junior_role(client_job_title)

    # Determine template + position label
    if is_pass and not is_junior_category:
        template_name = os.environ.get("CANDIDATE_EVAL_SR_PASS_TEMPLATE", "hl_cand_eval_sr_pass").strip()
        position = "Sr. Accountant"
    elif is_pass and is_junior_category and client_is_junior:
        template_name = os.environ.get("CANDIDATE_EVAL_JR_PASS_TEMPLATE", "hl_cand_eval_jr_pass").strip()
        position = "Jr. Accountant"
    elif is_pass and is_junior_category and not client_is_junior:
        template_name = "hl_cand_eval_satisfactory"
        position = "Sr. Accountant"
    else:
        # Fail
        template_name = "hl_cand_eval_fail"
        position = "Jr. Accountant" if client_is_junior else "Sr. Accountant"

    components = [{"type": "body", "parameters": [
        {"type": "text", "text": cand_name},
        {"type": "text", "text": position},
    ]}]
    try:
        await msg91.send_template(phone_91, template_name, "en", components, sender="candidate")
    except Exception as e:
        print(f"[ringg] eval result template '{template_name}' failed: {e}")
        try:
            from services.google_chat_service import send_alert
            await send_alert("Eval result template send failed", str(e), severity="ERROR", context={"phone": phone_91, "template": template_name, "cand_id": str(cand_id)})
        except Exception:
            pass

    if is_pass:
        # Find all interested mappings with MMS ≥ 70 and send scheduling links
        interested = await conn.fetch(
            """
            SELECT ccm.id
            FROM client_candidate_mappings ccm
            LEFT JOIN mms_scores ms
                ON ms.client_id = ccm.client_id AND ms.candidate_id = ccm.candidate_id
            WHERE ccm.candidate_id = $1
              AND ccm.stage = 'interested'::mapping_stage
              AND COALESCE(ms.mms_score, 0) >= 70
            """,
            cand_id,
        )

        frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5174")
        slot_template = os.environ.get("CANDIDATE_SLOT_TEMPLATE", "").strip()

        for m in interested:
            mapping_id = m["id"]
            schedule_url = f"{frontend_url}/candidate-schedule/{mapping_id}"
            try:
                if slot_template:
                    components = [{"type": "body", "parameters": [{"type": "text", "text": schedule_url}]}]
                    await msg91.send_template(phone_91, slot_template, "en", components, sender="candidate")
                else:
                    await msg91.send_text(
                        phone_91,
                        f"Here is your interview scheduling link:\n{schedule_url}",
                        sender="candidate",
                    )
            except Exception as e:
                print(f"[ringg] failed to send slot link for mapping {mapping_id}: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("Slot link send failed after evaluation pass", str(e), severity="ERROR", context={"phone": phone_91, "mapping_id": str(mapping_id), "cand_id": str(cand_id)})
                except Exception:
                    pass

    else:
        # Move all interested mappings to not_interested
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'not_interested'::mapping_stage, updated_at = now()
            WHERE candidate_id = $1 AND stage = 'interested'::mapping_stage
            """,
            cand_id,
        )

        # Release all locks
        await conn.execute(
            """
            UPDATE candidate_locks
            SET unlocked_at = now(), unlock_reason = 'evaluation_failed'
            WHERE candidate_id = $1 AND unlocked_at IS NULL
            """,
            cand_id,
        )

        # DND logic: fail + salary > 20k → 1yr DND; fail + salary ≤ 20k → Data Entry pool, no DND
        candidate = await conn.fetchrow(
            "SELECT current_salary FROM candidates WHERE id = $1", cand_id
        )
        current_salary = candidate["current_salary"] if candidate else None
        try:
            current_salary = float(current_salary) if current_salary else None
        except (TypeError, ValueError):
            current_salary = None

        if current_salary is None or current_salary > 20000:
            await conn.execute(
                "UPDATE candidates SET is_active = false, dnd_until = now() + interval '12 months', updated_at = now() WHERE id = $1",
                cand_id,
            )
            print(f"[ringg] candidate {cand_id} set to 1yr DND (failed, salary > 20k)")

    # Always re-run matching decision point — evaluation changes the free pool
    try:
        from routers.matching import on_evaluation_complete
        await on_evaluation_complete(cand_id, conn)
        print(f"[ringg] on_evaluation_complete triggered for {cand_id}")
    except Exception as e:
        print(f"[ringg] on_evaluation_complete failed for {cand_id}: {e}")
        try:
            from services.google_chat_service import send_alert
            await send_alert("on_evaluation_complete failed", str(e), severity="ERROR", context={"cand_id": str(cand_id)})
        except Exception:
            pass


@router.post("/trigger-interested/{mapping_id}")
async def trigger_interested_hook(
    mapping_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Re-fires the INTERESTED after_hook for a mapping stuck at 'interested'
    (e.g. the hook failed or was skipped the first time).
    """
    from services.flow_engine import _handle_interested_response
    import services.msg91_service as _msg91

    row = await conn.fetchrow(
        """
        SELECT ccm.stage, c.phone
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.id = $1
        """,
        mapping_id,
    )
    if not row:
        from fastapi import HTTPException
        raise HTTPException(404, "Mapping not found")
    if row["stage"] != "interested":
        from fastapi import HTTPException
        raise HTTPException(400, f"Mapping is at '{row['stage']}', not 'interested'")

    phone = _msg91._format_phone(row["phone"] or "")
    await _handle_interested_response(conn, mapping_id, phone)
    return {"ok": True, "phone": phone}


@router.post("/simulate-evaluation")
async def simulate_evaluation(
    body: SimulateEvalBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Dev/test endpoint — bypasses GPT and directly applies a pass or fail result
    for a candidate identified by phone. Runs the same downstream logic as the
    real evaluate webhook (WA sends, mapping stage updates, lock releases).
    """
    phone_10, phone_91 = _normalise_phone(body.phone)

    candidate = await conn.fetchrow(
        "SELECT * FROM candidates WHERE phone = $1 OR phone = $2",
        phone_10, phone_91,
    )
    if not candidate:
        from fastapi import HTTPException
        raise HTTPException(404, f"No candidate found with phone {body.phone}")

    cand_id = candidate["id"]
    pct = body.score_override if body.score_override is not None else (
        85.0 if body.result.lower() == "pass" else 45.0
    )

    salary = candidate.get("current_salary") or candidate.get("expected_salary")
    try:
        salary = float(salary) if salary else None
    except (TypeError, ValueError):
        salary = None

    eval_status, sim_category = ringg.classify_result(pct, salary)

    sim_scores = [5, 5, 5, 5, 5, 5, 5, 5, 5, 5] if eval_status == "Pass" else [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    summary_dict = {
        "overview_summary": f"Simulated {eval_status} result for testing.",
        "technical_scoring": {},
        "hiring_decision": {
            "status": eval_status,
            "category": sim_category,
            "reason": f"Simulated {eval_status} result for testing.",
        },
        "current_salary": None,
        "percentage": pct,
        "scores": sim_scores,
        "completed": True,
    }

    await conn.execute(
        """
        UPDATE candidates
        SET evaluation_status          = $2,
            technical_evaluation_score = $3,
            total_score                = $3,
            evaluation_summary         = $4::jsonb,
            category                   = $5,
            updated_at                 = now()
        WHERE id = $1
        """,
        cand_id, eval_status, pct, summary_dict, sim_category,
    )

    await _apply_evaluation_result(conn, cand_id, phone_91, eval_status, sim_category)

    try:
        from routers.candidates import _regenerate_report
        updated_cand = await conn.fetchrow("SELECT * FROM candidates WHERE id = $1", cand_id)
        if updated_cand:
            report_url = await _regenerate_report(dict(updated_cand))
            if report_url:
                await conn.execute(
                    "UPDATE candidates SET final_evaluation_report_url = $1, updated_at = now() WHERE id = $2",
                    report_url, cand_id,
                )
    except Exception as e:
        print(f"[ringg] simulate report regeneration failed: {e}")

    return {
        "data": {
            "candidate_id": str(cand_id),
            "eval_status": eval_status,
            "percentage": pct,
        },
        "ok": True,
    }
