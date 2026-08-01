"""
Candidate-facing portal API.
Auth: phone + WhatsApp OTP → 30-day session token stored in candidates table.
Login requires form_submitted = true; evaluation result doesn't gate login (see
send_otp for why).
"""
import json
import os
import random
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header, Query, UploadFile, File, BackgroundTasks
from pydantic import BaseModel
from typing import Any
import asyncpg
from database import get_pool
import constants.whatsapp_templates as WT

router = APIRouter(prefix="/candidate-portal", tags=["candidate-portal"])

EDITABLE_FIELDS = {
    "name", "gender", "age_years", "current_location", "district",
    "working_radius", "industry", "job_type", "work_preference",
    "notice_period", "current_salary", "expected_salary",
}

PASS_STATUSES = {"pass", "passed", "hired"}


async def get_conn():
    async with get_pool().acquire() as conn:
        yield conn


# ── Auth dependency ───────────────────────────────────────────────────────────

async def get_portal_auth(
    authorization: str = Header(...),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    row = await conn.fetchrow(
        """
        SELECT * FROM candidates
        WHERE portal_session_token = $1
          AND portal_session_expires_at > now()
        """,
        token,
    )
    if not row:
        raise HTTPException(401, "Invalid or expired session — please log in again")
    return dict(row)


# ── OTP Login ─────────────────────────────────────────────────────────────────

class SendOtpBody(BaseModel):
    phone: str

class VerifyOtpBody(BaseModel):
    phone: str
    otp: str


@router.post("/send-otp")
async def send_otp(body: SendOtpBody, conn: asyncpg.Connection = Depends(get_conn)):
    import re
    phone = re.sub(r"\D", "", body.phone)
    if phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]
    if len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit phone number")

    row = await conn.fetchrow(
        "SELECT id, name, evaluation_status, form_submitted FROM candidates WHERE phone = $1 AND is_active = true",
        phone,
    )
    if not row:
        raise HTTPException(404, "No candidate found with this phone number")

    if not row["form_submitted"]:
        raise HTTPException(
            403,
            "Please complete your profile first — tap the link below to fill it in.",
        )

    # Portal access is open to everyone regardless of evaluation result — a
    # candidate who didn't pass still sees their result (framed as areas to
    # improve) and stays reachable for future roles. Matching itself already
    # excludes non-passed candidates (routers/matching.py filters every pool
    # query to evaluation_status IN ('pass','passed')), so this only affects
    # whether they can log in and see their own status, not who gets matched.
    otp = str(random.randint(100000, 999999))
    await conn.execute(
        """
        UPDATE candidates
        SET portal_otp = $1,
            portal_otp_expires_at = now() + interval '10 minutes',
            updated_at = now()
        WHERE id = $2
        """,
        otp, row["id"],
    )

    try:
        import services.msg91_service as msg91
        otp_template = WT.CANDIDATE_OTP
        # AUTHENTICATION templates: OTP goes in body AND in the copy-code button (index 0)
        components = [
            {"type": "body", "parameters": [{"type": "text", "text": otp}]},
            {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": otp}]},
        ]
        await msg91.send_template(phone, otp_template, "en", components, sender="candidate")
    except Exception as e:
        print(f"[portal/send-otp] WhatsApp send failed for {phone}: {e}")
        raise HTTPException(500, "Failed to send OTP — please try again")

    return {"ok": True, "message": "OTP sent on WhatsApp"}


@router.post("/verify-otp")
async def verify_otp(body: VerifyOtpBody, conn: asyncpg.Connection = Depends(get_conn)):
    import re
    phone = re.sub(r"\D", "", body.phone)
    if phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]

    row = await conn.fetchrow(
        """
        SELECT id, portal_otp, portal_otp_expires_at, evaluation_status
        FROM candidates
        WHERE phone = $1
        """,
        phone,
    )
    if not row:
        raise HTTPException(404, "Candidate not found")

    if not row["portal_otp"] or row["portal_otp"] != body.otp.strip():
        raise HTTPException(400, "Incorrect OTP")

    if not row["portal_otp_expires_at"] or row["portal_otp_expires_at"] < __import__("datetime").datetime.now(__import__("datetime").timezone.utc):
        raise HTTPException(400, "OTP has expired — request a new one")

    session_token = str(uuid.uuid4())
    await conn.execute(
        """
        UPDATE candidates
        SET portal_otp              = NULL,
            portal_otp_expires_at   = NULL,
            portal_session_token    = $1,
            portal_session_expires_at = now() + interval '30 days',
            updated_at              = now()
        WHERE id = $2
        """,
        session_token, row["id"],
    )

    candidate = await conn.fetchrow("SELECT * FROM candidates WHERE id = $1", row["id"])
    return {"ok": True, "token": session_token, "data": dict(candidate)}


# ── Internal search (staff only, no auth) ────────────────────────────────────

@router.get("/search")
async def search_candidates(
    q: str = Query(..., min_length=2),
    conn: asyncpg.Connection = Depends(get_conn),
):
    pattern = f"%{q}%"
    rows = await conn.fetch(
        """
        SELECT * FROM candidates
        WHERE (name ILIKE $1 OR phone ILIKE $1)
          AND is_active = true
        ORDER BY name
        LIMIT 20
        """,
        pattern,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


# ── Profile ───────────────────────────────────────────────────────────────────

@router.get("/{candidate_id}/me")
async def get_me(
    candidate_id: uuid.UUID,
    auth: dict = Depends(get_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    if str(auth["id"]) != str(candidate_id):
        raise HTTPException(403, "Forbidden")
    return {"data": auth, "ok": True}


class ProfileUpdate(BaseModel):
    name: str | None = None
    gender: str | None = None
    age_years: int | None = None
    current_location: str | None = None
    district: str | None = None
    working_radius: int | None = None
    industry: str | None = None
    job_type: str | None = None
    work_preference: str | None = None
    notice_period: str | None = None
    current_salary: float | None = None
    expected_salary: float | None = None
    other_details: dict[str, Any] | None = None


@router.patch("/{candidate_id}/me")
async def update_me(
    candidate_id: uuid.UUID,
    body: ProfileUpdate,
    auth: dict = Depends(get_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    if str(auth["id"]) != str(candidate_id):
        raise HTTPException(403, "Cannot edit another candidate's profile")

    payload = body.model_dump(exclude_none=True)
    scalar = {k: v for k, v in payload.items() if k in EDITABLE_FIELDS}
    other_details = payload.get("other_details")

    sets, values = [], [candidate_id]
    idx = 2
    for k, v in scalar.items():
        sets.append(f"{k} = ${idx}")
        values.append(v)
        idx += 1
    if other_details is not None:
        sets.append(f"other_details = ${idx}::jsonb")
        values.append(json.dumps(other_details))
        idx += 1

    if not sets:
        current = await conn.fetchrow("SELECT * FROM candidates WHERE id = $1", candidate_id)
        return {"data": dict(current), "ok": True}

    sets.append("updated_at = now()")
    updated = await conn.fetchrow(
        f"UPDATE candidates SET {', '.join(sets)} WHERE id = $1 RETURNING *",
        *values,
    )
    return {"data": dict(updated), "ok": True}


# ── CV Upload ─────────────────────────────────────────────────────────────────

@router.post("/{candidate_id}/upload-cv")
async def upload_cv(
    candidate_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    resume: UploadFile = File(...),
    auth: dict = Depends(get_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    if str(auth["id"]) != str(candidate_id):
        raise HTTPException(403, "Cannot upload CV for another candidate")

    row = await conn.fetchrow("SELECT id, phone FROM candidates WHERE id = $1", candidate_id)
    if not row:
        raise HTTPException(404, "Candidate not found")

    content = await resume.read()
    if not content:
        raise HTTPException(400, "Empty file")

    filename = resume.filename or "resume.pdf"
    phone = row["phone"]

    background_tasks.add_task(_process_cv_and_reanalyze, str(candidate_id), phone, content, filename)
    return {"ok": True, "message": "CV upload started — stability will update shortly"}


# ── Interview documents (salary slips / bank statement) ───────────────────────

REQUIRED_INTERVIEW_DOCS = {"salary_slip", "bank_statement"}


@router.post("/{candidate_id}/mappings/{mapping_id}/upload-document")
async def upload_interview_document(
    candidate_id: uuid.UUID,
    mapping_id: uuid.UUID,
    doc_type: str = Query(..., description="salary_slip | bank_statement"),
    file: UploadFile = File(...),
    auth: dict = Depends(get_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    if str(auth["id"]) != str(candidate_id):
        raise HTTPException(403, "Cannot upload documents for another candidate")
    if doc_type not in REQUIRED_INTERVIEW_DOCS:
        raise HTTPException(400, f"doc_type must be one of {sorted(REQUIRED_INTERVIEW_DOCS)}")

    row = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.stage, ccm.documents, ccm.client_id,
               cl.company_name, cl.job_title
        FROM client_candidate_mappings ccm
        JOIN clients cl ON cl.id = ccm.client_id
        WHERE ccm.id = $1 AND ccm.candidate_id = $2
        """,
        mapping_id, candidate_id,
    )
    if not row:
        raise HTTPException(404, "Interview not found for this candidate")
    if row["stage"] != "documents_requested":
        raise HTTPException(409, f"Documents aren't being requested right now (stage: {row['stage']})")

    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")

    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "pdf"
    ext = ext if ext in ("pdf", "jpg", "jpeg", "png") else "pdf"
    content_type = "application/pdf" if ext == "pdf" else f"image/{ext}"

    from routers.candidates import _upload_gcs
    blob_path = f"interview_documents/{candidate_id}/{mapping_id}/{doc_type}.{ext}"
    doc_url = await _upload_gcs(content, blob_path, content_type)

    existing_docs: dict = row["documents"] or {}
    if isinstance(existing_docs, str):
        existing_docs = json.loads(existing_docs) if existing_docs else {}
    was_complete = REQUIRED_INTERVIEW_DOCS.issubset(existing_docs.keys())
    existing_docs[doc_type] = doc_url

    await conn.execute(
        "UPDATE client_candidate_mappings SET documents = $1::jsonb, updated_at = now() WHERE id = $2",
        existing_docs, mapping_id,
    )

    # Uploading both just makes the Submit button available — it does NOT
    # notify anyone by itself. The candidate must explicitly hit Submit
    # (see submit_interview_documents below) so they get one deliberate
    # confirmation moment, and can still replace a wrong file first.
    now_complete = REQUIRED_INTERVIEW_DOCS.issubset(existing_docs.keys())
    return {"data": {"documents": existing_docs, "complete": now_complete}, "ok": True}


@router.post("/{candidate_id}/mappings/{mapping_id}/submit-documents")
async def submit_interview_documents(
    candidate_id: uuid.UUID,
    mapping_id: uuid.UUID,
    auth: dict = Depends(get_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Candidate's explicit 'I'm done, send these to the client' action — the
    one moment that actually notifies the client and pulls in the RM, kept
    separate from individual uploads so a candidate can fix a wrong file
    before anyone downstream is told documents are ready."""
    if str(auth["id"]) != str(candidate_id):
        raise HTTPException(403, "Cannot submit documents for another candidate")

    row = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.stage, ccm.documents, ccm.client_id, ccm.documents_submitted_at,
               cl.company_name, cl.job_title
        FROM client_candidate_mappings ccm
        JOIN clients cl ON cl.id = ccm.client_id
        WHERE ccm.id = $1 AND ccm.candidate_id = $2
        """,
        mapping_id, candidate_id,
    )
    if not row:
        raise HTTPException(404, "Interview not found for this candidate")
    if row["stage"] != "documents_requested":
        raise HTTPException(409, f"Documents aren't being requested right now (stage: {row['stage']})")

    docs: dict = row["documents"] or {}
    if isinstance(docs, str):
        docs = json.loads(docs) if docs else {}
    missing = REQUIRED_INTERVIEW_DOCS - docs.keys()
    if missing:
        raise HTTPException(400, f"Please upload all documents first — missing: {sorted(missing)}")
    if row["documents_submitted_at"]:
        raise HTTPException(409, "Documents have already been submitted")

    await conn.execute(
        "UPDATE client_candidate_mappings SET documents_submitted_at = now(), updated_at = now() WHERE id = $1",
        mapping_id,
    )

    from services.flow_engine import notify_client_update, notify_rm_alert
    candidate_name = auth.get("name") or "The candidate"
    role = row["job_title"] or "the role"
    await notify_client_update(
        row["client_id"],
        f"{candidate_name}'s documents for the {role} role have been received — "
        f"our customer executive will call you shortly to close this hire.",
        "offer",
        conn,
    )
    await notify_rm_alert(
        row["client_id"],
        f"Documents for {candidate_name} are in — please call to close the position.",
        conn,
    )

    return {"data": {"submitted": True}, "ok": True}


async def _process_cv_and_reanalyze(candidate_id: str, phone: str, content: bytes, filename: str):
    from routers.candidates import _upload_gcs, _extract_text_from_resume, _gpt_analyze_resume, _stability_label
    try:
        blob_path = f"{phone}/final-cv-{phone}.pdf"
        cv_url = await _upload_gcs(content, blob_path, "application/pdf")

        resume_text = _extract_text_from_resume(content, filename)
        if resume_text.strip():
            gpt_data = await _gpt_analyze_resume(resume_text)
            total_years = float(gpt_data.get("total_experience_years") or 0)
            num_jobs = int(gpt_data.get("num_jobs") or 0)
            stability = _stability_label(total_years, num_jobs)
            experience_str = str(total_years) if total_years else None

            async with get_pool().acquire() as conn:
                existing = await conn.fetchval("SELECT labels FROM candidates WHERE id = $1", uuid.UUID(candidate_id))
                existing_labels: list = list(existing or [])
                stability_set = {"High Stability", "Average Stability", "High Turnover Risk"}
                existing_labels = [l for l in existing_labels if l not in stability_set]
                if stability:
                    existing_labels.append(stability)

                sets = ["cv_url = $2", "labels = $3::text[]", "updated_at = now()"]
                params: list = [uuid.UUID(candidate_id), cv_url, existing_labels]
                if experience_str:
                    sets.append(f"experience = ${len(params) + 1}")
                    params.append(experience_str)

                await conn.execute(
                    f"UPDATE candidates SET {', '.join(sets)} WHERE id = $1",
                    *params,
                )
        else:
            async with get_pool().acquire() as conn:
                await conn.execute(
                    "UPDATE candidates SET cv_url = $2, updated_at = now() WHERE id = $1",
                    uuid.UUID(candidate_id), cv_url,
                )
    except Exception as e:
        print(f"[candidate_portal/upload-cv] failed for {candidate_id}: {e}")


# ── Jobs ──────────────────────────────────────────────────────────────────────

@router.get("/{candidate_id}/jobs")
async def get_jobs(candidate_id: uuid.UUID, conn: asyncpg.Connection = Depends(get_conn)):
    rows = await conn.fetch(
        """
        SELECT
            ccm.id              AS mapping_id,
            ccm.stage,
            ccm.match_score,
            ccm.decline_reason,
            ccm.rejection_reason,
            ccm.wa_sent_at,
            ccm.interview_slot,
            ccm.interview_done,
            ccm.placement_confirmed,
            ccm.notes,
            ccm.documents,
            ccm.documents_submitted_at,
            ccm.intent_letter_url,
            ccm.offered_salary,
            ccm.offer_expires_at,
            ccm.interview_round,
            COALESCE(cl.available_slots, '[]'::jsonb) AS available_slots,
            ccm.created_at,
            ccm.updated_at,
            cl.company_name,
            cl.job_title,
            cl.industry,
            cl.location,
            cl.job_location,
            cl.min_salary,
            cl.max_salary,
            cl.job_timings,
            cl.job_type,
            cl.tools_requirements,
            cl.jd_card_url
        FROM client_candidate_mappings ccm
        JOIN clients cl ON cl.id = ccm.client_id
        WHERE ccm.candidate_id = $1
          AND ccm.stage NOT IN ('matched')
        ORDER BY ccm.updated_at DESC
        """,
        candidate_id,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "ok": True}


# ── Job respond (approve / decline from portal) ───────────────────────────────

class RespondBody(BaseModel):
    action: str  # 'INTERESTED' | 'NOT_INTERESTED_ROLE' | 'NOT_LOOKING_FOR_JOB'
    continue_anyway: bool = False  # INTERESTED only — resubmission after the
    # candidate saw the hard-filter mismatch choice and chose to proceed anyway.


@router.post("/{candidate_id}/jobs/{mapping_id}/respond")
async def respond_to_job(
    candidate_id: uuid.UUID,
    mapping_id: uuid.UUID,
    body: RespondBody,
    auth: dict = Depends(get_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    if str(auth["id"]) != str(candidate_id):
        raise HTTPException(403, "Forbidden")

    if body.action not in ("INTERESTED", "NOT_INTERESTED_ROLE", "NOT_LOOKING_FOR_JOB"):
        raise HTTPException(400, "Invalid action")

    mapping = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.stage, ccm.client_id, ccm.candidate_id, c.phone, c.name, c.evaluation_status
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.id = $1 AND ccm.candidate_id = $2
        """,
        mapping_id, candidate_id,
    )
    if not mapping:
        raise HTTPException(404, "Job mapping not found")

    if mapping["stage"] != "intent_ask":
        raise HTTPException(400, f"Cannot respond — current stage is '{mapping['stage']}'")

    from services.msg91_service import _format_phone
    import services.flow_engine as flow_engine

    phone = _format_phone(mapping["phone"] or "")

    # Hard-filter pre-check for INTERESTED, so a mismatch surfaces as an
    # interactive choice (update profile vs. continue anyway) instead of the
    # silent auto-decline _handle_interested_response falls back to — that
    # silent path stays in place for the WhatsApp-triggered reply, which has
    # no UI to show a choice in.
    if body.action == "INTERESTED":
        ok, reason_code, reason = await flow_engine._check_hard_filters(
            conn, mapping["client_id"], mapping["candidate_id"]
        )
        if not ok:
            if not body.continue_anyway:
                return {
                    "ok": True,
                    "outcome": "filters_not_matched_choice",
                    "reasons": [reason] if reason else [],
                }
            await flow_engine.decline_for_filter_mismatch(
                conn, mapping_id, mapping["candidate_id"], mapping["client_id"],
                phone, reason_code=reason_code, human_reason=reason, notify=False,
            )
            # Declined for this specific client, but the AI call still fires —
            # it's a general screening test, not gated on any one job's fit.
            eval_status = (mapping["evaluation_status"] or "").lower()
            ai_call_pending = eval_status not in ("pass", "passed", "fail", "failed", "reject")
            if ai_call_pending:
                await flow_engine.post_form_submission_flow(
                    phone, mapping["candidate_id"], mapping["name"] or ""
                )
            return {"ok": True, "action": body.action, "outcome": "filters_not_matched", "ai_call_pending": ai_call_pending}

    executed = await flow_engine.execute_candidate_flow(
        conn, mapping_id, mapping["stage"], body.action, phone
    )
    if not executed:
        raise HTTPException(400, "Could not process response — please contact support")

    # Tell the frontend what just happened so it can show the right popup
    # (e.g. "last step — complete your AI call") without waiting on WhatsApp.
    # Re-fetch the mapping's post-flow state rather than inferring purely from
    # evaluation_status/form_submitted — _handle_interested_response may have
    # already declined this mapping via decline_for_filter_mismatch (hard
    # filter fail) or moved it to client_approval_pending (eval already
    # passed), and guessing from the candidate row alone would misreport
    # "ai_call" for a mapping that was actually just declined on filters.
    outcome = None
    if body.action == "INTERESTED":
        mapping_after = await conn.fetchrow(
            "SELECT stage, decline_reason FROM client_candidate_mappings WHERE id = $1", mapping_id
        )
        stage_after = mapping_after["stage"] if mapping_after else None
        decline_reason_after = mapping_after["decline_reason"] if mapping_after else None

        if stage_after == "client_approval_pending":
            outcome = "reviewing"
        elif decline_reason_after in ("salary_mismatch", "hard_filter_mismatch"):
            outcome = "filters_not_matched"
        else:
            cand = await conn.fetchrow(
                "SELECT evaluation_status, form_submitted FROM candidates WHERE id = $1", candidate_id
            )
            if cand:
                es = (cand["evaluation_status"] or "").lower()
                if es in ("fail", "failed", "reject"):
                    outcome = "rejected"
                elif cand["form_submitted"]:
                    outcome = "ai_call"
                else:
                    outcome = "form_pending"

    return {"ok": True, "action": body.action, "outcome": outcome}


# ── Join-intent respond (willing to join?, before documents) ───────────────────

class RespondJoinIntentBody(BaseModel):
    action: str  # 'ACCEPT' | 'DECLINE'
    reason: str | None = None


@router.post("/{candidate_id}/mappings/{mapping_id}/respond-join-intent")
async def respond_to_join_intent(
    candidate_id: uuid.UUID,
    mapping_id: uuid.UUID,
    body: RespondJoinIntentBody,
    auth: dict = Depends(get_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Candidate's accept/decline on a join_intent_requested mapping — the
    counterpart to company_portal.hire_candidate. Accept moves to
    documents_requested and triggers the documents-request WhatsApp message;
    decline requires a reason, frees the candidate's lock for other clients,
    and skips straight past document collection entirely so nobody wastes
    time reviewing paperwork for someone who isn't joining."""
    if str(auth["id"]) != str(candidate_id):
        raise HTTPException(403, "Cannot respond for another candidate")

    if body.action not in ("ACCEPT", "DECLINE"):
        raise HTTPException(400, "action must be 'ACCEPT' or 'DECLINE'")
    reason = (body.reason or "").strip()
    if body.action == "DECLINE" and not reason:
        raise HTTPException(400, "A reason is required to decline")

    row = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.stage, ccm.client_id,
               cl.company_name, cl.job_title, c.phone AS candidate_phone
        FROM client_candidate_mappings ccm
        JOIN clients cl ON cl.id = ccm.client_id
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.id = $1 AND ccm.candidate_id = $2
        """,
        mapping_id, candidate_id,
    )
    if not row:
        raise HTTPException(404, "Offer not found for this candidate")
    if row["stage"] != "join_intent_requested":
        raise HTTPException(409, f"There's nothing to respond to right now (stage: {row['stage']})")

    candidate_name = auth.get("name") or "The candidate"
    role = row["job_title"] or "the role"

    if body.action == "ACCEPT":
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'documents_requested'::mapping_stage,
                join_intent_responded_at = now(),
                documents_requested_at = COALESCE(documents_requested_at, now()),
                updated_at = now()
            WHERE id = $1
            """,
            mapping_id,
        )

        if row["candidate_phone"]:
            try:
                import services.msg91_service as msg91
                from services.flow_engine import candidate_portal_deep_link
                template = WT.CANDIDATE_DOCS_REQUEST
                portal_url = os.environ.get("CANDIDATE_PORTAL_URL", "https://careers.justaccountants.in").rstrip("/")
                deep_link = candidate_portal_deep_link(mapping_id, "documents")
                if template:
                    components = [
                        {"type": "body", "parameters": [
                            {"type": "text", "text": candidate_name.split()[0] if candidate_name else "there"},
                            {"type": "text", "text": row["company_name"] or ""},
                            {"type": "text", "text": role},
                        ]},
                        {"type": "button", "sub_type": "url", "index": "0", "parameters": [
                            {"type": "text", "text": deep_link},
                        ]},
                    ]
                    await msg91.send_template(row["candidate_phone"], template, "en", components, sender="candidate")
                else:
                    await msg91.send_text(
                        row["candidate_phone"],
                        f"Hi {candidate_name}, please upload your documents here: {portal_url}/{deep_link}",
                        sender="candidate",
                    )
            except Exception as e:
                print(f"[candidate-portal/respond_join_intent] docs-request WA failed for {mapping_id}: {e}")

        from services.flow_engine import notify_client_update
        await notify_client_update(
            row["client_id"],
            f"{candidate_name} has confirmed they're willing to join for the {role} role — "
            f"we've asked them for documents next.",
            "offer",
            conn,
        )
    else:
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'rejected'::mapping_stage, rejection_reason = 'candidate_declined_join_intent',
                join_decline_reason = $1, join_intent_responded_at = now(), updated_at = now()
            WHERE id = $2
            """,
            reason, mapping_id,
        )
        await conn.execute(
            """
            UPDATE candidate_locks
            SET unlocked_at = now(), unlock_reason = 'candidate_declined_join_intent'
            WHERE candidate_id = $1 AND client_id = $2 AND unlocked_at IS NULL
            """,
            candidate_id, row["client_id"],
        )

        from services.flow_engine import notify_client_update, notify_rm_alert
        await notify_client_update(
            row["client_id"],
            f"{candidate_name} has let us know they're not able to join for the {role} role. Reason: {reason}",
            "offer",
            conn,
        )
        await notify_rm_alert(
            row["client_id"],
            f"{candidate_name} declined to join for {role} — reason: {reason}. Please follow up.",
            conn,
        )

    return {"data": {"action": body.action}, "ok": True}


# ── Offer respond (accept / decline from portal) ────────────────────────────────

class RespondOfferBody(BaseModel):
    action: str  # 'ACCEPT' | 'DECLINE'
    reason: str | None = None


@router.post("/{candidate_id}/mappings/{mapping_id}/respond-offer")
async def respond_to_offer(
    candidate_id: uuid.UUID,
    mapping_id: uuid.UUID,
    body: RespondOfferBody,
    auth: dict = Depends(get_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Candidate's accept/decline action on an offer_sent mapping — the
    counterpart to company_portal.final_hire. Accept runs the same placement
    cascade as every other 'placed' transition; decline requires a reason,
    frees the candidate's lock for other clients, and both outcomes notify
    the client (and RM, on decline) via the reminder-only portal ping."""
    if str(auth["id"]) != str(candidate_id):
        raise HTTPException(403, "Cannot respond to another candidate's offer")

    if body.action not in ("ACCEPT", "DECLINE"):
        raise HTTPException(400, "action must be 'ACCEPT' or 'DECLINE'")
    reason = (body.reason or "").strip()
    if body.action == "DECLINE" and not reason:
        raise HTTPException(400, "A reason is required to decline an offer")

    row = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.stage, ccm.client_id, ccm.offer_expires_at,
               cl.company_name, cl.job_title
        FROM client_candidate_mappings ccm
        JOIN clients cl ON cl.id = ccm.client_id
        WHERE ccm.id = $1 AND ccm.candidate_id = $2
        """,
        mapping_id, candidate_id,
    )
    if not row:
        raise HTTPException(404, "Offer not found for this candidate")
    if row["stage"] != "offer_sent":
        raise HTTPException(409, f"There's no active offer to respond to (stage: {row['stage']})")
    if row["offer_expires_at"] and row["offer_expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(409, "This offer has expired")

    candidate_name = auth.get("name") or "The candidate"
    role = row["job_title"] or "the role"

    if body.action == "ACCEPT":
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'placed'::mapping_stage, placement_confirmed = true, updated_at = now()
            WHERE id = $1
            """,
            mapping_id,
        )
        from routers.mappings import apply_placed_cascade
        await apply_placed_cascade(row["client_id"], candidate_id, conn)

        from services.flow_engine import notify_client_update
        await notify_client_update(
            row["client_id"],
            f"{candidate_name} has accepted the offer for the {role} role — the hire is confirmed.",
            "offer",
            conn,
        )
    else:
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'rejected'::mapping_stage, rejection_reason = 'candidate_declined_offer',
                offer_decline_reason = $1, updated_at = now()
            WHERE id = $2
            """,
            reason, mapping_id,
        )
        await conn.execute(
            """
            UPDATE candidate_locks
            SET unlocked_at = now(), unlock_reason = 'candidate_declined_offer'
            WHERE candidate_id = $1 AND client_id = $2 AND unlocked_at IS NULL
            """,
            candidate_id, row["client_id"],
        )

        from services.flow_engine import notify_client_update, notify_rm_alert
        await notify_client_update(
            row["client_id"],
            f"{candidate_name} has declined the offer for the {role} role. Reason: {reason}",
            "offer",
            conn,
        )
        await notify_rm_alert(
            row["client_id"],
            f"{candidate_name} declined the offer for {role} — reason: {reason}. Please follow up.",
            conn,
        )

    return {"data": {"action": body.action}, "ok": True}


# ── Slot selection (candidate picks interview time from portal) ────────────────

class PickSlotBody(BaseModel):
    slot_label: str


@router.post("/{candidate_id}/jobs/{mapping_id}/pick-slot")
async def pick_slot(
    candidate_id: uuid.UUID,
    mapping_id: uuid.UUID,
    body: PickSlotBody,
    auth: dict = Depends(get_portal_auth),
    conn: asyncpg.Connection = Depends(get_conn),
):
    if str(auth["id"]) != str(candidate_id):
        raise HTTPException(403, "Forbidden")

    if not body.slot_label.strip():
        raise HTTPException(400, "slot_label is required")

    mapping = await conn.fetchrow(
        """
        SELECT ccm.id, ccm.stage, ccm.client_id,
               c.name AS candidate_name, c.phone AS candidate_phone,
               cl.company_name, cl.job_title,
               cl.poc_name, cl.poc_phone, cl.job_location, cl.interview_location,
               cl.location_lat, cl.location_lng,
               cl.available_slots AS client_slots
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        JOIN clients cl ON cl.id = ccm.client_id
        WHERE ccm.id = $1 AND ccm.candidate_id = $2
        """,
        mapping_id, candidate_id,
    )
    if not mapping:
        raise HTTPException(404, "Job mapping not found")
    if mapping["stage"] not in ("invited_for_interview", "slot_sent_candidate"):
        raise HTTPException(400, f"Cannot pick slot — current stage is '{mapping['stage']}'")

    label = body.slot_label.strip()

    # Verify slot exists in client's available_slots
    client_slots = list(mapping["client_slots"] or [])
    slot_labels = [s["label"] for s in client_slots if s.get("label")]
    if label not in slot_labels:
        raise HTTPException(400, "Invalid slot — please pick from the available options")

    # Capacity check: max 2 candidates per slot
    booked = await conn.fetchval(
        """
        SELECT COUNT(*) FROM client_candidate_mappings
        WHERE client_id = $1
          AND stage IN ('slot_booked', 'interview_scheduled', 'interview_done', 'placed')
          AND (available_slots->0->>'confirmed_by') = 'candidate'
          AND (available_slots->0->>'label') = $2
          AND id != $3
        """,
        mapping["client_id"], label, mapping_id,
    )
    if booked >= 2:
        raise HTTPException(409, "This slot is fully booked — please pick another")

    # Prefer the slot's own iso field (set by company_portal.set_interview_slots)
    # over regex-parsing the human label — the client-portal calendar picker's
    # label format ("Sat, 11 Jul, 9:00 am") doesn't match schedule.py's parser,
    # which expects a 4-digit year and a middle-dot separator, so relying on
    # the parser alone silently left interview_slot NULL for every candidate
    # who picked via the new portal.
    matched_slot = next((s for s in client_slots if s.get("label") == label), None)
    interview_dt = None
    if matched_slot and matched_slot.get("iso"):
        try:
            interview_dt = datetime.fromisoformat(matched_slot["iso"])
        except ValueError:
            interview_dt = None
    if interview_dt is None:
        from routers.schedule import _parse_slot_label_to_dt
        interview_dt = _parse_slot_label_to_dt(label)

    confirmed_slot = [{"label": label, "confirmed_by": "candidate",
                       "confirmed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}]

    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage          = 'slot_booked'::mapping_stage,
            available_slots = $2,
            interview_slot  = $3,
            slot_sent_at    = COALESCE(slot_sent_at, now()),
            updated_at      = now()
        WHERE id = $1
        """,
        mapping_id,
        confirmed_slot,
        interview_dt,
    )

    import services.msg91_service as msg91
    from services.msg91_service import _format_phone

    # Format POC phone nicely for display (e.g. +91 98765 43210)
    raw_poc = (mapping["poc_phone"] or "").strip()
    poc_display = f"+91 {raw_poc}" if raw_poc and not raw_poc.startswith("+") else raw_poc
    # interview_location is the address the client confirmed specifically for
    # interviews (set via the client-portal invite flow's location picker) —
    # prefer it over job_location, which may be stale or just the company's
    # general address rather than where interviews actually happen.
    interview_location = (mapping.get("interview_location") or "").strip()
    job_location = (mapping["job_location"] or "").strip()
    display_location = interview_location or job_location
    import urllib.parse as _urlparse
    _lat, _lng = mapping.get("location_lat"), mapping.get("location_lng")
    if interview_location:
        location_link = f"https://maps.google.com/?q={_urlparse.quote(interview_location)}"
    elif _lat and _lng:
        location_link = f"https://maps.google.com/?q={_lat},{_lng}"
    elif job_location:
        location_link = f"https://maps.google.com/?q={_urlparse.quote(job_location)}"
    else:
        location_link = None

    # ── WA to candidate ───────────────────────────────────────────────────────
    cand_phone = _format_phone(mapping["candidate_phone"] or "")
    if cand_phone:
        # hl_cand_interview_confirmed_v3: {{1}}=name {{2}}=company {{3}}=slot {{4}}=location
        slot_template = WT.CANDIDATE_SLOT_CONFIRMED
        try:
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": (mapping["candidate_name"] or "").split()[0] or "there"},
                {"type": "text", "text": mapping["company_name"] or ""},
                {"type": "text", "text": label},
                {"type": "text", "text": location_link or "to be shared by HR"},
            ]}]
            await msg91.send_template(cand_phone, slot_template, "en", components, sender="candidate")
            print(f"[pick-slot] candidate WA sent to {cand_phone}")
        except Exception as e:
            print(f"[pick-slot] candidate template failed ({e}), trying plain text")
            try:
                await msg91.send_text(
                    cand_phone,
                    f"Hi {(mapping['candidate_name'] or '').split()[0] or 'there'}, your interview with "
                    f"*{mapping['company_name']}* is confirmed!\n\n"
                    f"Slot: {label}\n"
                    f"Location: {location_link or display_location or 'to be shared by HR'}\n\n"
                    f"All the best!",
                    sender="candidate",
                )
                print(f"[pick-slot] candidate WA fallback sent to {cand_phone}")
            except Exception as e2:
                print(f"[pick-slot] candidate WA failed: {e2}")

    # ── WA to client POC (reuse the same template as RM-booked flow) ─────────
    try:
        from routers.schedule import _send_interview_confirmation_to_client
        await _send_interview_confirmation_to_client(str(mapping_id), label)
        print(f"[pick-slot] client WA sent for mapping {mapping_id}")
    except Exception as e:
        print(f"[pick-slot] client WA failed: {e}")

    try:
        from services.google_chat_service import send_alert
        await send_alert(
            f"Slot confirmed — {mapping['candidate_name']}",
            f"{mapping['candidate_name']} confirmed: *{label}* for {mapping['company_name']}",
            severity="INFO",
            context={"mapping_id": str(mapping_id), "slot": label},
        )
    except Exception:
        pass

    return {"ok": True, "slot_label": label}
