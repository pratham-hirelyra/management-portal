import uuid
import re
import csv
import json
import io
import os
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from pydantic import BaseModel
import asyncpg
import httpx
from database import get_conn, get_pool
from models.candidate import CandidateCreate

_gcs_executor = ThreadPoolExecutor(max_workers=4)

router = APIRouter(prefix="/candidates", tags=["candidates"])

_CANDIDATE_SELECT = """
    SELECT
        c.*,
        (cl.candidate_id IS NOT NULL)  AS is_locked,
        cl.expires_at                  AS lock_expires_at,
        lc.company_name                AS locked_for_company,
        cl_all.companies               AS locked_companies,
        ib.interview_slot              AS interview_slot,
        ic.company_name                AS interview_with,
        am.active_mappings             AS active_mappings
    FROM candidates c
    LEFT JOIN LATERAL (
        SELECT candidate_id, client_id, expires_at
        FROM candidate_locks
        WHERE candidate_id = c.id AND unlocked_at IS NULL
        ORDER BY locked_at DESC
        LIMIT 1
    ) cl ON true
    LEFT JOIN clients lc ON lc.id = cl.client_id
    LEFT JOIN LATERAL (
        SELECT array_agg(lc2.company_name ORDER BY cl2.locked_at DESC) AS companies
        FROM candidate_locks cl2
        JOIN clients lc2 ON lc2.id = cl2.client_id
        WHERE cl2.candidate_id = c.id AND cl2.unlocked_at IS NULL
    ) cl_all ON true
    LEFT JOIN LATERAL (
        SELECT interview_slot, client_id
        FROM client_candidate_mappings
        WHERE candidate_id = c.id AND interview_slot IS NOT NULL
        ORDER BY interview_slot ASC
        LIMIT 1
    ) ib ON true
    LEFT JOIN clients ic ON ic.id = ib.client_id
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(jsonb_build_object(
            'company_name', cl2.company_name,
            'stage', ccm2.stage::text,
            'match_score', ccm2.match_score
        ) ORDER BY ccm2.updated_at DESC) AS active_mappings
        FROM client_candidate_mappings ccm2
        JOIN clients cl2 ON cl2.id = ccm2.client_id
        WHERE ccm2.candidate_id = c.id
          AND ccm2.stage::text NOT IN (
              'placed','rejected','not_interested','declined_for_interview','client_closed'
          )
    ) am ON true
"""


class CandidateOverrideBody(BaseModel):
    phone: str | None = None
    is_active: bool | None = None
    dnd_until: str | None = None
    current_salary: float | None = None
    current_location: str | None = None
    working_radius: int | None = None
    work_preference: str | None = None
    job_type: str | None = None
    gst: str | None = None
    tds: str | None = None
    tools: str | None = None
    technical_evaluation_score: float | None = None
    total_score: float | None = None
    recruiter_notes: str | None = None
    labels: list[str] | None = None
    photo_url: str | None = None
    notice_period: str | None = None
    religion: str | None = None


@router.get("")
async def list_candidates(
    evaluation_status: str = None,
    industry: str = None,
    search: str = None,
    is_active: bool = None,
    page: int = 1,
    page_size: int = 50,
    conn: asyncpg.Connection = Depends(get_conn),
):
    conditions: list[str] = []
    params: list = []
    i = 1

    if evaluation_status:
        conditions.append(f"c.evaluation_status = ${i}")
        params.append(evaluation_status)
        i += 1
    if industry:
        conditions.append(f"c.industry ILIKE ${i}")
        params.append(f"%{industry}%")
        i += 1
    if search:
        conditions.append(
            f"(c.name ILIKE ${i} OR c.phone ILIKE ${i} OR c.current_location ILIKE ${i})"
        )
        params.append(f"%{search}%")
        i += 1
    if is_active is not None:
        conditions.append(f"c.is_active = ${i}")
        params.append(is_active)
        i += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await conn.fetchval(
        f"SELECT COUNT(*) FROM candidates c {where}",
        *params,
    )

    page_size = max(1, min(page_size, 200))
    offset = (max(1, page) - 1) * page_size
    rows = await conn.fetch(
        f"{_CANDIDATE_SELECT} {where} ORDER BY c.created_at DESC LIMIT ${i} OFFSET ${i+1}",
        *params, page_size, offset,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows), "total": total, "ok": True}


@router.post("", status_code=201)
async def create_candidate(
    body: CandidateCreate,
    conn: asyncpg.Connection = Depends(get_conn),
):
    # Normalise phone: strip non-digits, remove leading 91 if 12-digit Indian number
    phone = re.sub(r'\D', '', body.phone)
    if len(phone) == 12 and phone.startswith('91'):
        phone = phone[2:]
    if not phone:
        raise HTTPException(400, "Valid phone number is required")

    try:
        row = await conn.fetchrow(
            """
            INSERT INTO candidates (
                name, phone, age_years, gender, religion, email, experience,
                cv_url, source, current_location, working_radius,
                current_salary, expected_salary, job_type, industry,
                category, work_preference
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
            RETURNING id
            """,
            body.name, phone, body.age_years, body.gender, body.religion,
            body.email, body.experience, body.cv_url, body.source,
            body.current_location, body.working_radius,
            body.current_salary, body.expected_salary,
            body.job_type, body.industry, body.category, body.work_preference,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, f"A candidate with phone {phone} already exists")

    full = await conn.fetchrow(f"{_CANDIDATE_SELECT} WHERE c.id = $1", row["id"])
    return {"data": dict(full), "ok": True}


@router.get("/{candidate_id}")
async def get_candidate(
    candidate_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    row = await conn.fetchrow(
        f"{_CANDIDATE_SELECT} WHERE c.id = $1",
        candidate_id,
    )
    if not row:
        raise HTTPException(404, "Candidate not found")
    return {"data": dict(row), "ok": True}


@router.delete("/{candidate_id}")
async def delete_candidate(
    candidate_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    exists = await conn.fetchval("SELECT 1 FROM candidates WHERE id = $1", candidate_id)
    if not exists:
        raise HTTPException(404, "Candidate not found")

    # Remove dependent rows first (mappings, locks, overrides, WA messages)
    await conn.execute("DELETE FROM candidate_locks WHERE candidate_id = $1", candidate_id)
    await conn.execute(
        "DELETE FROM whatsapp_messages WHERE candidate_id = $1", candidate_id
    )
    await conn.execute(
        "DELETE FROM client_candidate_mappings WHERE candidate_id = $1", candidate_id
    )
    await conn.execute("DELETE FROM candidates WHERE id = $1", candidate_id)
    return {"ok": True}


def _derive_eval_fields(tech_score: float, current_salary: float | None) -> dict:
    """Mirror ringg logic: derive evaluation_status + category from score and current salary."""
    s = float(current_salary) if current_salary is not None else None
    if s is None:            cutoff = 65.0
    elif s >= 41000:         cutoff = 80.0
    elif s >= 36000:         cutoff = 75.0
    elif s >= 31000:         cutoff = 70.0
    elif s >= 26000:         cutoff = 65.0
    elif s >= 21000:         cutoff = 60.0
    elif s >= 16000:         cutoff = 55.0
    else:                    cutoff = 50.0

    passed = tech_score >= cutoff
    if passed:
        category = "Senior Accountant"
    elif s is not None and s <= 20_000:
        category = "Junior Accountant"
    else:
        category = ""

    return {
        "evaluation_status": "Pass" if passed else "Fail",
        "category": category,
    }


@router.patch("/{candidate_id}/override")
async def upsert_override(
    candidate_id: uuid.UUID,
    body: CandidateOverrideBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    exists = await conn.fetchval("SELECT 1 FROM candidates WHERE id = $1", candidate_id)
    if not exists:
        raise HTTPException(404, "Candidate not found")

    from datetime import date

    sets = ["updated_at = now()"]
    params: list = [candidate_id]
    i = 2

    def add(field, val):
        nonlocal i
        if val is not None:
            sets.append(f"{field} = ${i}")
            params.append(val)
            i += 1

    # Normalise and update phone if provided
    if body.phone is not None:
        new_phone = re.sub(r'\D', '', body.phone)
        if len(new_phone) == 12 and new_phone.startswith('91'):
            new_phone = new_phone[2:]
        if len(new_phone) != 10:
            raise HTTPException(400, "Phone must be a 10-digit mobile number")
        existing = await conn.fetchval(
            "SELECT id FROM candidates WHERE phone = $1 AND id != $2", new_phone, candidate_id
        )
        if existing:
            raise HTTPException(409, f"Phone {new_phone} is already used by another candidate")
        add("phone", new_phone)

    add("is_active", body.is_active)

    # dnd_until: truthy string → set date; None/'' → clear to NULL
    if body.dnd_until:
        try:
            sets.append(f"dnd_until = ${i}")
            params.append(date.fromisoformat(body.dnd_until))
            i += 1
        except ValueError:
            raise HTTPException(400, "Invalid dnd_until date format, expected YYYY-MM-DD")
    else:
        sets.append("dnd_until = NULL")

    add("current_salary", body.current_salary)
    add("working_radius", body.working_radius)
    add("work_preference", body.work_preference)
    add("job_type", body.job_type)
    add("technical_evaluation_score", body.technical_evaluation_score)
    add("total_score", body.total_score)
    add("recruiter_notes", body.recruiter_notes)
    add("photo_url", body.photo_url)
    add("notice_period", body.notice_period)
    add("religion", body.religion)

    # Geocode location when provided
    if body.current_location is not None:
        add("current_location", body.current_location)
        lat, lng = await _geocode(body.current_location)
        if lat is not None:
            add("location_lat", lat)
            add("location_lng", lng)

    # Merge gst/tds/tools into other_details jsonb without overwriting other keys
    other_patch: dict = {}
    if body.gst is not None:
        other_patch["gst"] = body.gst
    if body.tds is not None:
        other_patch["tds"] = body.tds
    if body.tools is not None:
        other_patch["tools"] = body.tools
    if other_patch:
        sets.append(f"other_details = COALESCE(other_details, '{{}}' ::jsonb) || ${i}")
        params.append(other_patch)  # pass dict directly — asyncpg codec handles encoding
        i += 1

    # labels needs explicit cast
    if body.labels is not None:
        sets.append(f"labels = ${i}::text[]")
        params.append(body.labels)
        i += 1

    await conn.execute(
        f"UPDATE candidates SET {', '.join(sets)} WHERE id = $1",
        *params,
    )

    # Recompute evaluation_status + category + regenerate report when scores or salary change
    score_changed = (
        body.technical_evaluation_score is not None
        or body.total_score is not None
        or body.current_salary is not None
    )
    if score_changed:
        try:
            score_row = await conn.fetchrow("SELECT * FROM candidates WHERE id = $1", candidate_id)
            if score_row:
                d = dict(score_row)
                tech_raw = d.get("technical_evaluation_score")
                # Only derive evaluation_status if there's an actual AI score stored
                if tech_raw is not None:
                    tech = float(tech_raw)
                    derived = _derive_eval_fields(tech, d.get("current_salary"))
                    await conn.execute(
                        """UPDATE candidates
                           SET category = $1, evaluation_status = $2, updated_at = now()
                           WHERE id = $3""",
                        derived["category"] or None,
                        derived["evaluation_status"],
                        candidate_id,
                    )
                if body.technical_evaluation_score is not None or body.total_score is not None:
                    report_url = await _regenerate_report(d)
                    if report_url:
                        await conn.execute(
                            "UPDATE candidates SET final_evaluation_report_url = $1, updated_at = now() WHERE id = $2",
                            report_url, candidate_id,
                        )
        except Exception as e:
            print(f"[override] eval fields/report update failed: {e}")

    row = await conn.fetchrow(
        f"{_CANDIDATE_SELECT} WHERE c.id = $1", candidate_id
    )
    return {"data": dict(row), "ok": True}


@router.post("/{candidate_id}/trigger-ai-call")
async def trigger_ai_call(
    candidate_id: uuid.UUID,
    retry: bool = True,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Manually trigger a RinggAI evaluation call for a candidate."""
    import services.ringg_service as ringg

    row = await conn.fetchrow("SELECT id, name, phone FROM candidates WHERE id = $1", candidate_id)
    if not row:
        raise HTTPException(404, "Candidate not found")
    if not row["phone"]:
        raise HTTPException(400, "Candidate has no phone number")

    result = await ringg.trigger_ai_call(
        str(row["id"]),
        row["phone"],
        row["name"] or "",
        retry_on_no_pickup=retry,
    )
    return {"data": result, "ok": True}


@router.post("/{candidate_id}/reanalyze-cv")
async def reanalyze_cv(
    candidate_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Download candidate CV from GCS, re-run GPT analysis, update stability label + experience."""
    row = await conn.fetchrow("SELECT id, name, phone, cv_url, labels FROM candidates WHERE id = $1", candidate_id)
    if not row:
        raise HTTPException(404, "Candidate not found")
    cv_url = row["cv_url"]
    if not cv_url:
        raise HTTPException(400, "Candidate has no CV on file")

    # Strip query params to get the bare GCS URL for download
    bare_url = cv_url.split("?")[0]
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.get(bare_url)
        resp.raise_for_status()
        content = resp.content
    except Exception as e:
        raise HTTPException(502, f"Failed to download CV: {e}")

    filename = bare_url.rsplit("/", 1)[-1]
    resume_text = _extract_text_from_resume(content, filename)
    if not resume_text.strip():
        raise HTTPException(422, "Could not extract text from CV")

    gpt_data = await _gpt_analyze_resume(resume_text)
    total_years = float(gpt_data.get("total_experience_years") or 0)
    num_jobs = int(gpt_data.get("num_jobs") or 0)
    stability = _stability_label(total_years, num_jobs)
    summary = gpt_data.get("candidate_summary") or None
    experience_str = str(total_years) if total_years else None

    existing_labels: list = list(row["labels"] or [])
    stability_set = {"High Stability", "Average Stability", "High Turnover Risk"}
    existing_labels = [l for l in existing_labels if l not in stability_set]
    if stability:
        existing_labels.append(stability)

    sets = ["updated_at = now()", "labels = $2::text[]"]
    params: list = [candidate_id, existing_labels]
    idx = 3
    if experience_str:
        sets.append(f"experience = ${idx}")
        params.append(experience_str)
        idx += 1

    await conn.execute(
        f"UPDATE candidates SET {', '.join(sets)} WHERE id = $1",
        *params,
    )

    return {
        "data": {
            "stability": stability,
            "total_years": total_years,
            "num_jobs": num_jobs,
            "labels": existing_labels,
        },
        "ok": True,
    }


# ── Candidate Apply (form submission) ─────────────────────────────────────────

_GCS_BUCKET = "hirelyra-phone-screening-v2"


def _extract_text_from_resume(content: bytes, filename: str) -> str:
    fname = (filename or "").lower()
    if fname.endswith('.pdf'):
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return ""
    if fname.endswith('.docx'):
        try:
            import docx
            doc = docx.Document(io.BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception:
            return ""
    try:
        return content.decode("utf-8", errors="ignore")
    except Exception:
        return ""


_NOTICE_PERIOD_MAP = {
    "immediate":          "Immediate",
    "15 days":            "Short Notice",
    "1 month":            "Short Notice",
    "2 months":           "Long Notice",
    "3 months":           "Long Notice",
    "more than 3 months": "Very Long Notice",
}


def _notice_period_category(raw: str | None) -> str | None:
    if not raw:
        return None
    return _NOTICE_PERIOD_MAP.get(raw.strip().lower())


def _stability_label(total_years: float, num_jobs: int) -> str | None:
    if num_jobs <= 0:
        return None
    avg = total_years / num_jobs
    if avg > 2:
        return "High Stability"
    if avg >= 1:
        return "Average Stability"
    return "High Turnover Risk"


async def _gpt_analyze_resume(resume_text: str) -> dict:
    """Extract experience, stability, summary, and current employer from resume text."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    truncated = resume_text[:6000]
    resp = await client.chat.completions.create(
        model=os.environ.get("OPENAI_RESUME_MODEL", "gpt-5.2"),
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an HR assistant analyzing a resume. Extract:\n"
                    "1. total_experience_years: total years of work experience as a float. "
                    "Convert months to a decimal fraction (e.g. 2 years 6 months = 2.5). Set to 0 if fresher.\n"
                    "2. num_jobs: number of distinct employers the candidate has worked at (int).\n"
                    "3. candidate_summary: a SHORT 3-4 sentence recruiter brief covering GST/TDS depth "
                    "(Filing vs Preparation) and accounting tools used. Be plain and direct.\n"
                    "4. current_employer: name of the most recent / current employer (string or null).\n"
                    "5. current_job_title: the candidate's most recent job title (string or null).\n"
                    "6. employer_location: city or location of the current employer if mentioned (string or null).\n\n"
                    "Return JSON: {\"total_experience_years\": float, \"num_jobs\": int, \"candidate_summary\": \"...\", "
                    "\"current_employer\": \"...\", \"current_job_title\": \"...\", \"employer_location\": \"...\"}"
                ),
            },
            {"role": "user", "content": truncated},
        ],
        temperature=0,
    )
    return json.loads(resp.choices[0].message.content or "{}")


async def _geocode(location: str) -> tuple[float | None, float | None]:
    if not location:
        return None, None
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        return None, None
    try:
        async with httpx.AsyncClient(timeout=8.0) as http:
            r = await http.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": location, "key": key},
            )
        results = r.json().get("results", [])
        if results:
            loc = results[0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
    except Exception:
        pass
    return None, None


def _upload_gcs_sync(content: bytes, blob_path: str, content_type: str) -> str:
    from google.cloud import storage as _gcs
    gcs = _gcs.Client()
    bucket = gcs.bucket(_GCS_BUCKET)
    blob = bucket.blob(blob_path)
    blob.cache_control = "no-cache, no-store"
    blob.upload_from_string(content, content_type=content_type)
    try:
        blob.make_public()
    except Exception as e:
        # Uniform bucket-level access — per-object ACLs are disabled, so grant
        # allUsers objectViewer at the bucket level instead (one-time, idempotent).
        print(f"[gcs] make_public failed for {blob_path} (likely Uniform Access — grant allUsers objectViewer at bucket level): {e}")
        try:
            policy = bucket.get_iam_policy(requested_policy_version=3)
            already_public = any(
                b.get("role") == "roles/storage.objectViewer"
                and "allUsers" in b.get("members", [])
                for b in policy.bindings
            )
            if not already_public:
                policy.bindings.append({
                    "role": "roles/storage.objectViewer",
                    "members": frozenset(["allUsers"]),
                })
                bucket.set_iam_policy(policy)
        except Exception as iam_err:
            print(f"[gcs] bucket-level IAM grant also failed for {_GCS_BUCKET}: {iam_err}")
    return f"https://storage.googleapis.com/{_GCS_BUCKET}/{blob_path}"


async def _upload_gcs(content: bytes, blob_path: str, content_type: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _gcs_executor, _upload_gcs_sync, content, blob_path, content_type
    )


def _build_report_html(data: dict) -> str:
    name = data.get("name", "Candidate")
    phone = data.get("phone", "")
    age = data.get("age", "") or data.get("age_years", "")
    gender = data.get("gender", "")
    current_location = data.get("current_location", "")
    industry = data.get("industry", "")
    current_salary = data.get("current_salary")
    expected_salary = data.get("expected_salary")
    overview_summary = data.get("overview_summary", "")
    candidate_summary = data.get("candidate_summary", "")
    technical_scoring = data.get("technical_scoring") or {}
    total_score = data.get("total_score")
    eval_status = str(data.get("eval_status", "")).lower()

    def fmt_salary(s):
        if s is None or s == "":
            return "N/A"
        try:
            return f"&#8377;{int(float(s)):,}"
        except Exception:
            return str(s)

    age_gender = " | ".join(filter(None, [f"{age} yrs" if age else "", gender]))

    has_scoring = bool(technical_scoring)
    has_eval = eval_status in ("pass", "fail")

    def q(n):
        return technical_scoring.get(f"question_{n}", {})

    core_pct = gst_pct = tds_pct = 0
    if has_scoring:
        core = sum(q(i).get("score", 0) for i in range(1, 7))
        gst = sum(q(i).get("score", 0) for i in range(7, 9))
        tds = sum(q(i).get("score", 0) for i in range(9, 11))
        core_pct = round((core / 30) * 100)
        gst_pct = round((gst / 10) * 100)
        tds_pct = round((tds / 10) * 100)

    def bar(pct):
        return (
            f'<div style="background:#e6e6e6;height:14px;border-radius:6px;">'
            f'<div style="width:{pct}%;background:#2c5c8a;height:14px;border-radius:6px;"></div></div>'
        )

    score_html = ""
    if total_score is not None:
        cls = "selected" if eval_status == "pass" else "rejected"
        label = "SELECTED" if eval_status == "pass" else "REJECTED"
        status_box = f'<div class="status-box {cls}">Status: {label}</div>' if has_eval else ""
        score_html = f'<div class="section overall-score">Overall Weighted Score: {total_score}%<br>{status_box}</div>'

    scorecard_html = ""
    if has_scoring:
        scorecard_html = f"""
<div class="section">
<h2>Skill Scorecard</h2>
<table>
<tr><th style="width:40%">Parameter</th><th style="width:15%">Score</th><th style="width:45%">Visual</th></tr>
<tr><td>Core Accounting</td><td>{core_pct}%</td><td>{bar(core_pct)}</td></tr>
<tr><td>GST Compliance</td><td>{gst_pct}%</td><td>{bar(gst_pct)}</td></tr>
<tr><td>TDS Compliance</td><td>{tds_pct}%</td><td>{bar(tds_pct)}</td></tr>
</table>
</div>"""

    assessment_parts = []
    if overview_summary:
        assessment_parts.append(f"<p>{overview_summary}</p>")
    if candidate_summary:
        assessment_parts.append(f"<p>{candidate_summary}</p>")
    assessment_html = ""
    if assessment_parts:
        assessment_html = f'<div class="section"><h2>JustAccountants\'s Assessment</h2>{"".join(assessment_parts)}</div>'

    detail_rows = ""
    for i in range(1, 11):
        qd = q(i)
        if qd:
            detail_rows += (
                f"<tr><td>{i}</td>"
                f"<td>{qd.get('question', '')}</td>"
                f"<td>{qd.get('candidate_response', '')}</td>"
                f"<td>{qd.get('justification', '')}</td>"
                f"<td>{qd.get('score', 0)}/5</td></tr>"
            )
    detail_html = ""
    if detail_rows:
        detail_html = f"""
<div class="page-break"></div>
<div class="section">
<h2>Detailed Skill Evaluation</h2>
<table>
<tr><th>Sr No.</th><th>Question</th><th>Candidate Response</th><th>JustAccountants's Evaluation</th><th>Score</th></tr>
{detail_rows}
</table>
</div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body {{ font-family: Arial, sans-serif; padding: 40px; color: #222; }}
.header {{ display: flex; align-items: center; gap: 15px; }}
.logo-img {{ width: 80px; }}
.logo-text {{ font-size: 26px; font-weight: bold; }}
.subtitle {{ font-size: 14px; color: #777; margin-top: -5px; }}
.section {{ margin-top: 25px; }}
h2 {{ margin-bottom: 8px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }}
th {{ background-color: #244a73; color: white; padding: 8px; text-align: left; }}
td {{ border: 1px solid #999; padding: 8px; vertical-align: top; word-break: break-word; }}
.overall-score {{ font-size: 18px; font-weight: bold; margin-top: 10px; }}
.status-box {{ display: inline-block; padding: 6px 12px; border-radius: 6px; font-weight: bold; margin-top: 8px; }}
.rejected {{ background-color: #f8d7da; color: #842029; }}
.selected {{ background-color: #d1e7dd; color: #0f5132; }}
.footer {{ margin-top: 30px; font-size: 12px; color: #777; }}
.page-break {{ page-break-after: always; }}
</style>
</head>
<body>
<div class="header">
  <img class="logo-img" src="https://storage.googleapis.com/hirelyra-website/images/unnamed%20(1).jpg" />
  <div>
    <div class="logo-text">JustAccountants</div>
    <div class="subtitle">Candidate Evaluation Summary</div>
  </div>
</div>
<div class="section">
<b>Candidate Name:</b> {name}<br>
<b>Location:</b> {current_location}<br>
<b>Age:</b> {age_gender}<br>
<b>Current Salary:</b> {fmt_salary(current_salary)} per month<br>
<b>Industry:</b> {industry}
</div>
{score_html}
{scorecard_html}
{assessment_html}
{detail_html}
<div class="footer">
This assessment is based on JustAccountant's structured accounting evaluation workflow.
</div>
</body>
</html>"""


async def _regenerate_report(candidate: dict) -> str | None:
    """Build and upload the final evaluation HTML report from a DB candidate dict. Returns GCS URL or None."""
    try:
        eval_summary = candidate.get("evaluation_summary") or {}
        if isinstance(eval_summary, str):
            try:
                eval_summary = json.loads(eval_summary)
            except Exception:
                eval_summary = {}

        # Support both flat and legacy nested-output structures
        output_block = eval_summary.get("output") or {}
        overview_summary = eval_summary.get("overview_summary") or output_block.get("overview_summary", "")
        technical_scoring = eval_summary.get("technical_scoring") or output_block.get("technical_scoring") or {}
        candidate_summary = eval_summary.get("candidate_summary", "")

        phone = candidate.get("phone", "")
        report_html = _build_report_html({
            "name": candidate.get("name", ""),
            "phone": phone,
            "age": candidate.get("age_years", ""),
            "gender": candidate.get("gender", ""),
            "current_location": candidate.get("current_location", ""),
            "industry": candidate.get("industry", ""),
            "current_salary": candidate.get("current_salary"),
            "expected_salary": candidate.get("expected_salary"),
            "overview_summary": overview_summary,
            "candidate_summary": candidate_summary,
            "technical_scoring": technical_scoring,
            "total_score": candidate.get("total_score"),
            "eval_status": candidate.get("evaluation_status", ""),
        })
        url = await _upload_gcs(
            report_html.encode("utf-8"),
            f"{phone}/final-evaluation-{phone}.html",
            "text/html",
        )
        return f"{url}?v={int(time.time())}"
    except Exception as e:
        print(f"[candidates] _regenerate_report failed: {e}")
        return None


async def _process_apply(phone: str, form: dict, resume_content: bytes | None,
                         resume_filename: str, photo_content: bytes | None,
                         photo_filename: str) -> None:
    """Background task: uploads, GPT analysis, DB update.
    AI call fires ONLY when the candidate's latest mapping is 'interested' —
    i.e. we sent them a JD for a specific client and they tapped INTERESTED.
    All other cases (no mapping, declined role, shared link to a friend, walk-ins)
    → passive flow: store requirements, send ack, no AI call."""
    cv_url = None
    photo_url = None
    lat = lon = None
    gpt_data: dict = {}

    tasks = []

    async def _noop():
        return None

    if resume_content:
        tasks.append(_upload_gcs(
            resume_content,
            f"{phone}/final-cv-{phone}.pdf",
            "application/pdf",
        ))
    else:
        tasks.append(_noop())

    if photo_content:
        ext = "jpg" if "jpeg" in (photo_filename or "").lower() or "jpg" in (photo_filename or "").lower() else "jpg"
        tasks.append(_upload_gcs(
            photo_content,
            f"{phone}/photo-{phone}.{ext}",
            "image/jpeg",
        ))
    else:
        tasks.append(_noop())

    tasks.append(_geocode(form.get("current_location", "")))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    if resume_content and not isinstance(results[0], Exception):
        cv_url = f"{results[0]}?v={int(time.time())}"
    if photo_content and not isinstance(results[1], Exception):
        photo_url = results[1]
    if not isinstance(results[2], Exception) and results[2]:
        lat, lon = results[2]

    if resume_content:
        resume_text = _extract_text_from_resume(resume_content, resume_filename)
        if resume_text.strip():
            try:
                gpt_data = await _gpt_analyze_resume(resume_text)
            except Exception:
                pass

    total_years = float(gpt_data.get("total_experience_years") or 0)
    num_jobs = int(gpt_data.get("num_jobs") or 0)
    stability = _stability_label(total_years, num_jobs)
    summary = gpt_data.get("candidate_summary", "")
    experience_str = str(total_years) if total_years else None

    od_raw = form.get("other_details", "{}")
    try:
        od = json.loads(od_raw) if isinstance(od_raw, str) else od_raw
    except Exception:
        od = {}

    report_url = None  # generated after DB update so eval data is included if already present

    # Update DB — create candidate if they don't exist (inbound WhatsApp flow)
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT id, labels, evaluation_status FROM candidates WHERE phone = $1", phone)
        if not row:
            row = await conn.fetchrow(
                """
                INSERT INTO candidates (name, phone, source, is_active, created_at, updated_at)
                VALUES ($1, $2, 'whatsapp_inbound', true, now(), now())
                ON CONFLICT (phone) DO UPDATE SET updated_at = now()
                RETURNING id, labels, evaluation_status
                """,
                form.get("candidate_name") or phone, phone,
            )
        if not row:
            return

        existing_labels: list = list(row["labels"] or [])
        stability_set = {"High Stability", "Average Stability", "High Turnover Risk"}
        existing_labels = [l for l in existing_labels if l not in stability_set]
        if stability:
            existing_labels.append(stability)

        sets = ["updated_at = now()", "labels = $2::text[]", "form_submitted = true"]
        params: list = [phone, existing_labels]
        idx = 3

        def add(col, val):
            nonlocal idx
            if val is not None and val != "":
                sets.append(f"{col} = ${idx}")
                params.append(val)
                idx += 1

        def safe_int(val, lo=None, hi=None):
            try:
                v = int(val)
                if lo is not None and v < lo: return None
                if hi is not None and v > hi: return None
                return v
            except (TypeError, ValueError):
                return None

        add("current_location", form.get("current_location") or None)
        add("district", form.get("district") or None)
        add("working_radius", safe_int(form.get("travel_distance"), lo=0, hi=2000))
        add("gender", form.get("gender") or None)
        add("age_years", safe_int(form.get("age"), lo=16, hi=80))
        add("industry", form.get("industry_exposure") or None)
        add("job_type", form.get("opportunity_type") or None)
        add("work_preference", form.get("job_requirement") or None)
        add("notice_period", form.get("notice_period") or None)
        add("current_salary", float(form["current_salary"]) if form.get("current_salary") else None)
        add("location_lat", lat)
        add("location_lng", lon)
        add("cv_url", cv_url)
        add("photo_url", photo_url)
        add("experience", experience_str)
        add("final_evaluation_report_url", report_url)
        if od:
            sets.append(f"other_details = ${idx}::jsonb")
            params.append(od)
            idx += 1
        # Store candidate_summary so the ringg report regeneration can use it
        if summary:
            sets.append(
                f"evaluation_summary = COALESCE(evaluation_summary, '{{}}' ::jsonb) || ${idx}::jsonb"
            )
            params.append({"candidate_summary": summary})
            idx += 1

        await conn.execute(
            f"UPDATE candidates SET {', '.join(sets)} WHERE phone = $1",
            *params,
        )

        # Regenerate report with combined form + eval data (eval may have run before form submission)
        updated_row = None
        try:
            updated_row = await conn.fetchrow("SELECT * FROM candidates WHERE phone = $1", phone)
            if updated_row:
                report_url = await _regenerate_report(dict(updated_row))
                if report_url:
                    await conn.execute(
                        "UPDATE candidates SET final_evaluation_report_url = $1, updated_at = now() WHERE phone = $2",
                        report_url, phone,
                    )
        except Exception as e:
            print(f"[apply] report generation failed for {phone}: {e}")

        # AI call only when latest mapping = 'interested' (candidate tapped INTERESTED on a JD).
        # Inbound organic (no mapping) → store data silently, no AI call.
        # All other stages (declined role, etc.) → passive ack only.
        eval_status = (row["evaluation_status"] or "").lower()
        if eval_status not in ("pass", "passed", "fail", "failed", "reject"):
            latest = await conn.fetchrow(
                """
                SELECT ccm.id, ccm.client_id, ccm.stage::text AS stage
                FROM client_candidate_mappings ccm
                WHERE ccm.candidate_id = $1
                ORDER BY COALESCE(ccm.intent_received_at, ccm.updated_at) DESC NULLS LAST
                LIMIT 1
                """,
                row["id"],
            )
            latest_stage = latest["stage"] if latest else None
            if latest_stage == "interested":
                from services.flow_engine import (
                    post_form_submission_flow, _check_hard_filters, decline_for_filter_mismatch,
                )
                ok, reason_code, reason = await _check_hard_filters(conn, latest["client_id"], row["id"])
                if ok:
                    try:
                        await post_form_submission_flow(
                            phone,
                            row["id"],
                            form.get("candidate_name") or "",
                        )
                    except Exception as e:
                        print(f"[apply] post-form flow failed for {phone}: {e}")
                else:
                    print(f"[apply] candidate {row['id']} failed hard filter for client {latest['client_id']}: {reason}")
                    await decline_for_filter_mismatch(conn, latest["id"], row["id"], latest["client_id"], phone, reason_code)
            else:
                # latest_stage is not None (declined role, etc.) OR None (inbound organic, no mapping)
                # → passive ack: "we'll share opportunities soon"
                from services.flow_engine import post_form_passive_flow
                try:
                    await post_form_passive_flow(phone, form.get("candidate_name") or "", form.get("current_location") or "")
                except Exception as e:
                    print(f"[apply] passive flow failed for {phone}: {e}")

        # Insert current employer as a client lead (skip if already exists)
        current_employer = gpt_data.get("current_employer")
        if current_employer and current_employer.strip():
            try:
                existing = await conn.fetchval(
                    "SELECT 1 FROM clients WHERE LOWER(company_name) = LOWER($1) LIMIT 1",
                    current_employer.strip(),
                )
                if not existing:
                    await conn.execute(
                        """
                        INSERT INTO clients (
                            company_name, location, job_title, source, segment, stage, created_at, updated_at
                        ) VALUES ($1, $2, $3, 'candidate_resume', 'employer_lead', 'lead'::client_stage, now(), now())
                        """,
                        current_employer.strip(),
                        gpt_data.get("employer_location") or form.get("current_location") or None,
                        gpt_data.get("current_job_title") or None,
                    )
                    print(f"[apply] Created client lead from resume: {current_employer}")
            except Exception as e:
                print(f"[apply] Client lead creation failed for {current_employer}: {e}")


@router.post("/apply", status_code=200)
async def apply_candidate(
    background_tasks: BackgroundTasks,
    candidate_name: str = Form(...),
    candidate_phone_number: str = Form(...),
    gender: str = Form(""),
    age: str = Form(""),
    current_location: str = Form(""),
    district: str = Form(""),
    travel_distance: str = Form(""),
    industry_exposure: str = Form(""),
    job_requirement: str = Form(""),
    opportunity_type: str = Form(""),
    notice_period: str = Form(""),
    current_salary: str = Form(""),
    other_details: str = Form("{}"),
):
    """
    Accepts form fields only — no file upload here.
    Files are sent separately via POST /candidates/upload-cv after the candidate
    sees the success screen, so they never wait for the upload.
    """
    cleaned = re.sub(r'\D', '', candidate_phone_number)
    if len(cleaned) == 12 and cleaned.startswith('91'):
        cleaned = cleaned[2:]

    form = {
        "candidate_name": candidate_name,
        "age": age,
        "gender": gender,
        "current_location": current_location,
        "district": district,
        "travel_distance": travel_distance,
        "industry_exposure": industry_exposure,
        "job_requirement": job_requirement,
        "opportunity_type": opportunity_type,
        "notice_period": notice_period,
        "current_salary": current_salary,
        "other_details": other_details,
    }

    # Mark form_submitted immediately so cron reminders stop before the
    # background task (GPT, uploads) finishes — avoids race-condition re-sends.
    # Also decide, synchronously, which success screen the frontend should show —
    # same "latest mapping stage" check _process_apply uses below to pick between
    # post_form_submission_flow (AI call) and post_form_passive_flow (no call).
    outcome = "passive"
    try:
        async with get_pool().acquire(timeout=5) as _conn:
            await _conn.execute(
                "UPDATE candidates SET form_submitted = true, updated_at = now() WHERE phone = $1",
                cleaned,
            )
            latest = await _conn.fetchrow(
                """
                SELECT ccm.stage::text AS stage
                FROM client_candidate_mappings ccm
                JOIN candidates c ON c.id = ccm.candidate_id
                WHERE c.phone = $1
                ORDER BY COALESCE(ccm.intent_received_at, ccm.updated_at) DESC NULLS LAST
                LIMIT 1
                """,
                cleaned,
            )
            if latest and latest["stage"] == "interested":
                outcome = "ai_call"
    except Exception as _e:
        print(f"[apply] form_submitted pre-mark / outcome check failed for {cleaned}: {_e}")

    background_tasks.add_task(
        _process_apply,
        cleaned, form,
        None, "",
        None, "",
    )

    return {"ok": True, "outcome": outcome}


@router.post("/upload-cv", status_code=200)
async def upload_cv(
    background_tasks: BackgroundTasks,
    candidate_phone_number: str = Form(...),
    resume: UploadFile | None = File(None),
    photo: UploadFile | None = File(None),
):
    """
    Receives the CV/photo after the candidate has already seen the success screen.
    Runs upload + GPT analysis in a background task — fires and forgets.
    """
    cleaned = re.sub(r'\D', '', candidate_phone_number)
    if len(cleaned) == 12 and cleaned.startswith('91'):
        cleaned = cleaned[2:]

    resume_content = await resume.read() if resume else None
    resume_filename = resume.filename or "resume.pdf" if resume else ""
    photo_content = await photo.read() if photo else None
    photo_filename = photo.filename or "photo.jpg" if photo else ""

    if not resume_content and not photo_content:
        return {"ok": True}

    background_tasks.add_task(
        _process_cv_upload,
        cleaned, resume_content, resume_filename, photo_content, photo_filename,
    )
    return {"ok": True}


async def _process_cv_upload(
    phone: str,
    resume_content: bytes | None,
    resume_filename: str,
    photo_content: bytes | None,
    photo_filename: str,
) -> None:
    """Upload CV/photo to GCS, run GPT analysis, update candidate record."""
    tasks = []

    async def _noop():
        return None

    if resume_content:
        tasks.append(_upload_gcs(resume_content, f"{phone}/final-cv-{phone}.pdf", "application/pdf"))
    else:
        tasks.append(_noop())

    if photo_content:
        ext = "jpg" if "jpeg" in (photo_filename or "").lower() or "jpg" in (photo_filename or "").lower() else "jpg"
        tasks.append(_upload_gcs(photo_content, f"{phone}/photo-{phone}.{ext}", "image/jpeg"))
    else:
        tasks.append(_noop())

    results = await asyncio.gather(*tasks, return_exceptions=True)

    cv_url = None
    photo_url = None
    if resume_content and not isinstance(results[0], Exception):
        cv_url = f"{results[0]}?v={int(time.time())}"
    if photo_content and not isinstance(results[1], Exception):
        photo_url = results[1]

    gpt_data: dict = {}
    if resume_content:
        resume_text = _extract_text_from_resume(resume_content, resume_filename)
        if resume_text.strip():
            try:
                gpt_data = await _gpt_analyze_resume(resume_text)
            except Exception:
                pass

    total_years = float(gpt_data.get("total_experience_years") or 0)
    num_jobs = int(gpt_data.get("num_jobs") or 0)
    stability = _stability_label(total_years, num_jobs)
    experience_str = str(total_years) if total_years else None

    try:
        async with get_pool().acquire() as conn:
            row = await conn.fetchrow("SELECT id, labels FROM candidates WHERE phone = $1", phone)
            if not row:
                print(f"[upload-cv] candidate not found for phone {phone}")
                return

            sets, params = [], [row["id"]]
            i = 2
            if cv_url:
                sets.append(f"cv_url = ${i}"); params.append(cv_url); i += 1
            if photo_url:
                sets.append(f"photo_url = ${i}"); params.append(photo_url); i += 1
            if experience_str:
                sets.append(f"experience = ${i}"); params.append(experience_str); i += 1

            existing_labels = [l for l in (row["labels"] or [])
                               if l not in {"High Stability", "Average Stability", "High Turnover Risk"}]
            if stability:
                existing_labels.append(stability)
            sets.append(f"labels = ${i}"); params.append(existing_labels); i += 1
            sets.append(f"updated_at = now()")

            if sets:
                await conn.execute(
                    f"UPDATE candidates SET {', '.join(sets)} WHERE id = $1",
                    *params,
                )
            print(f"[upload-cv] cv + GPT updated for {phone}: cv={bool(cv_url)} stability={stability}")
    except Exception as e:
        print(f"[upload-cv] DB update failed for {phone}: {e}")


# ── Google Sheets import ───────────────────────────────────────────────────────

class SheetImportRequest(BaseModel):
    sheet_url: str


def _extract_sheet_id(url: str) -> Optional[str]:
    m = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    return m.group(1) if m else None


def _extract_gid(url: str) -> Optional[str]:
    m = re.search(r'[#&?]gid=(\d+)', url)
    return m.group(1) if m else None


def _num(v: str) -> Optional[float]:
    v = v.strip()
    if not v or v.upper() in ('NA', 'N/A', '-', ''):
        return None
    v = v.replace(',', '').replace('₹', '').strip()
    try:
        return float(v)
    except ValueError:
        return None


def _int(v: str) -> Optional[int]:
    n = _num(v)
    return int(n) if n is not None else None


def _age(v: str) -> Optional[int]:
    m = re.match(r'(\d+)', v.strip())
    return int(m.group(1)) if m else None


def _json(v: str) -> Optional[dict]:
    v = v.strip()
    if not v or v.upper() == 'NA':
        return None
    try:
        return json.loads(v)
    except Exception:
        return None


def _str(v: str) -> Optional[str]:
    v = v.strip()
    return v if v and v.upper() not in ('NA', 'N/A') else None


def _job_type(v: str) -> Optional[str]:
    v = v.strip().upper()
    if v == 'TRUE':
        return 'full_time'
    if v == 'FALSE':
        return 'part_time'
    return _str(v)


def _parse_row(row: dict) -> Optional[dict]:
    phone = _str(row.get('Phone', ''))
    if not phone:
        return None
    # Clean phone: keep digits only, strip leading country code if 12 digits starting with 91
    phone = re.sub(r'\D', '', phone)
    if len(phone) == 12 and phone.startswith('91'):
        phone = phone[2:]

    return {
        'name': _str(row.get('Name', '')) or 'Unknown',
        'phone': phone,
        'age_years': _age(row.get('Age', '')),
        'gender': _str(row.get('Gender', '')),
        'religion': _str(row.get('Religion', '')),
        'email': _str(row.get('Email', '')),
        'experience': _str(row.get('Experience', '')),
        'cv_url': _str(row.get('CV', '')),
        'source': _str(row.get('Source', '')),
        'mentioned_location': _str(row.get('Mentioned Location', '')),
        'state': _str(row.get('State', '')),
        'current_location': _str(row.get('Current Location', '')),
        'working_radius': _int(row.get('Working Radius', '')),
        'current_salary': _num(row.get('Current Salary', '')),
        'expected_salary': _num(row.get('Expected Salary', '')),
        'job_type': _job_type(row.get('Full Time', '')),
        'industry': _str(row.get('Industry', '')),
        'call_status': _str(row.get('Call Status', '')),
        'category': _str(row.get('Category', '')),
        'evaluation_status': _str(row.get('Evaluation Status', '')),
        'technical_evaluation_score': _num(row.get('Technical Evaluation Score', '')),
        'total_score': _num(row.get('Total Score', '')),
        'evaluation_pdf_url': _str(row.get('Evaluation PDF', '')),
        'evaluation_summary': _json(row.get('Evaluation Summary', '')),
        'voice_recording_url': _str(row.get('Voice Recording', '')),
        'work_preference': _str(row.get('Work Preference', '')),
        'other_details': _json(row.get('Other Details', '')),
        'final_evaluation_report_url': _str(row.get('Final Evaluation Report', '')),
    }


@router.post("/import")
async def import_from_sheet(
    body: SheetImportRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    sheet_id = _extract_sheet_id(body.sheet_url)
    if not sheet_id:
        raise HTTPException(400, "Could not extract sheet ID from URL")

    gid = _extract_gid(body.sheet_url)
    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    if gid:
        csv_url += f"&gid={gid}"

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as http:
        resp = await http.get(csv_url)

    if not resp.is_success:
        raise HTTPException(400, f"Failed to fetch Google Sheet (HTTP {resp.status_code}). Make sure the sheet is shared publicly (Anyone with link → Viewer).")

    reader = csv.DictReader(io.StringIO(resp.text))
    inserted = updated = skipped = 0
    errors: list[dict] = []

    for line_num, raw in enumerate(reader, start=2):
        try:
            c = _parse_row(raw)
            if c is None:
                skipped += 1
                continue

            row = await conn.fetchrow(
                """
                INSERT INTO candidates (
                    name, phone, age_years, gender, religion, email, experience,
                    cv_url, source, mentioned_location, state, current_location,
                    working_radius, current_salary, expected_salary, job_type,
                    industry, call_status, category, evaluation_status,
                    technical_evaluation_score, total_score, evaluation_pdf_url,
                    evaluation_summary, voice_recording_url, work_preference,
                    other_details, final_evaluation_report_url
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                    $17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28
                )
                ON CONFLICT (phone) DO UPDATE SET
                    name                      = EXCLUDED.name,
                    age_years                 = EXCLUDED.age_years,
                    gender                    = EXCLUDED.gender,
                    religion                  = EXCLUDED.religion,
                    email                     = EXCLUDED.email,
                    experience                = EXCLUDED.experience,
                    cv_url                    = EXCLUDED.cv_url,
                    source                    = EXCLUDED.source,
                    mentioned_location        = EXCLUDED.mentioned_location,
                    state                     = EXCLUDED.state,
                    current_location          = EXCLUDED.current_location,
                    working_radius            = EXCLUDED.working_radius,
                    current_salary            = CASE WHEN candidates.form_submitted
                                                  THEN candidates.current_salary
                                                  ELSE EXCLUDED.current_salary
                                              END,
                    expected_salary           = EXCLUDED.expected_salary,
                    job_type                  = EXCLUDED.job_type,
                    industry                  = EXCLUDED.industry,
                    call_status               = EXCLUDED.call_status,
                    category                  = EXCLUDED.category,
                    evaluation_status         = EXCLUDED.evaluation_status,
                    technical_evaluation_score= EXCLUDED.technical_evaluation_score,
                    total_score               = EXCLUDED.total_score,
                    evaluation_pdf_url        = EXCLUDED.evaluation_pdf_url,
                    evaluation_summary        = EXCLUDED.evaluation_summary,
                    voice_recording_url       = EXCLUDED.voice_recording_url,
                    work_preference           = EXCLUDED.work_preference,
                    other_details             = EXCLUDED.other_details,
                    final_evaluation_report_url = EXCLUDED.final_evaluation_report_url,
                    updated_at                = now()
                RETURNING (xmax = 0) AS was_inserted
                """,
                c['name'], c['phone'], c['age_years'], c['gender'], c['religion'],
                c['email'], c['experience'], c['cv_url'], c['source'],
                c['mentioned_location'], c['state'], c['current_location'],
                c['working_radius'], c['current_salary'], c['expected_salary'],
                c['job_type'], c['industry'], c['call_status'], c['category'],
                c['evaluation_status'], c['technical_evaluation_score'],
                c['total_score'], c['evaluation_pdf_url'],
                json.dumps(c['evaluation_summary']) if c['evaluation_summary'] else None,
                c['voice_recording_url'], c['work_preference'],
                json.dumps(c['other_details']) if c['other_details'] else None,
                c['final_evaluation_report_url'],
            )
            if row['was_inserted']:
                inserted += 1
            else:
                updated += 1
        except Exception as e:
            errors.append({"row": line_num, "phone": raw.get('Phone', ''), "error": str(e)})

    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "ok": True,
    }
