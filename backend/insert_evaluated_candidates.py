"""
Insert evaluated (Pass) candidates from candidates_evaluated.txt
source from file, is_active=True, evaluation_status=Pass, category=Sr. Accountant
CV URL constructed from phone number.
"""
import asyncio, os, re, csv, json, asyncpg
from dotenv import load_dotenv

load_dotenv()

import sys
_default = "candidates_evaluated.txt"
BATCH_FILE = os.path.join(os.path.dirname(__file__), sys.argv[1] if len(sys.argv) > 1 else _default)


def clean_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits if len(digits) == 10 else None


def parse_age(raw: str) -> int | None:
    m = re.search(r"(\d+)", raw or "")
    return int(m.group(1)) if m else None


def clean_work_preference(raw: str) -> str | None:
    val = re.sub(r"\s*-\s*$", "", (raw or "").strip()).strip()
    return val or None


def parse_other_details(raw: str) -> dict | None:
    try:
        return json.loads(raw) if raw and raw.strip() else None
    except Exception:
        return None


def parse_float(raw: str) -> float | None:
    try:
        return float(raw) if raw and raw.strip() else None
    except Exception:
        return None


def parse_int(raw: str) -> int | None:
    try:
        return int(float(raw)) if raw and raw.strip() else None
    except Exception:
        return None


def cv_url(phone: str) -> str:
    return f"https://storage.googleapis.com/hirelyra-phone-screening-v2/{phone}/final-cv-{phone}.pdf"


def parse_candidates():
    candidates = []
    with open(BATCH_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            phone = clean_phone(row.get("Phone", ""))
            name = (row.get("Name") or "").strip()

            if not phone:
                print(f"  SKIP (bad phone): {name} — '{row.get('Phone')}'")
                continue
            if not name:
                continue

            candidates.append({
                "name": name,
                "phone": phone,
                "age_years": parse_age(row.get("Age", "")),
                "gender": (row.get("Gender") or "").strip() or None,
                "religion": (row.get("Religion") or "").strip() or None,
                "experience": (row.get("Experience") or "").strip() or None,
                "cv_url": cv_url(phone),
                "source": (row.get("Source") or "").strip() or None,
                "current_location": (row.get("Current Location") or "").strip() or None,
                "location_lat": parse_float(row.get("Location Latitude", "")),
                "location_lng": parse_float(row.get("Location Longitude", "")),
                "working_radius": parse_int(row.get("Working Radius", "")),
                "current_salary": parse_float(row.get("Current Salary", "")),
                "expected_salary": parse_float(row.get("Expected Salary", "")),
                "job_type": (row.get("Job Type") or "").strip() or None,
                "industry": (row.get("Industry") or "").strip() or None,
                "evaluation_status": (row.get("Evaluation Status") or "").strip() or None,
                "category": {"Sr. Accountant": "Senior Accountant", "Jr. Accountant": "Junior Accountant"}.get(
                    (row.get("Category") or "").strip(),
                    (row.get("Category") or "").strip() or None
                ),
                "total_score": parse_float(row.get("Total Score", "")),
                "technical_evaluation_score": parse_float(row.get("Total Score", "")),
                "voice_recording_url": (row.get("Voice Recording") or "").strip() or None,
                "work_preference": clean_work_preference(row.get("Work Preference", "")),
                "other_details": parse_other_details(row.get("Other Details", "")),
                "final_evaluation_report_url": (row.get("Final Evaluation Report") or "").strip() or None,
                "interview_count": parse_int(row.get("Interview Count", "")),
                "source_id": (row.get("ID") or "").strip() or None,
            })
    return candidates


async def main():
    candidates = parse_candidates()
    print(f"Parsed {len(candidates)} valid candidates")

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    inserted = updated = errors = 0

    for c in candidates:
        try:
            existing = await conn.fetchrow(
                "SELECT id FROM candidates WHERE phone = $1", c["phone"]
            )
            od = json.dumps(c["other_details"]) if c["other_details"] else None

            if existing:
                await conn.execute(
                    """
                    UPDATE candidates SET
                        name                       = $1,
                        age_years                  = $2,
                        gender                     = $3,
                        religion                   = $4,
                        experience                 = $5,
                        cv_url                     = $6,
                        source                     = $7,
                        current_location           = $8,
                        location_lat               = $9,
                        location_lng               = $10,
                        working_radius             = $11,
                        current_salary             = CASE WHEN form_submitted THEN current_salary ELSE $12 END,
                        expected_salary            = $13,
                        job_type                   = $14,
                        industry                   = $15,
                        evaluation_status          = $16,
                        category                   = $17,
                        total_score                = $18,
                        technical_evaluation_score = $18,
                        voice_recording_url        = $19,
                        work_preference            = $20,
                        other_details              = $21::jsonb,
                        final_evaluation_report_url= $22,
                        interview_count            = $23,
                        source_id                  = $24,
                        is_active                  = true,
                        dnd_until                  = NULL,
                        updated_at                 = now()
                    WHERE phone = $25
                    """,
                    c["name"], c["age_years"], c["gender"], c["religion"],
                    c["experience"], c["cv_url"], c["source"], c["current_location"],
                    c["location_lat"], c["location_lng"], c["working_radius"],
                    c["current_salary"], c["expected_salary"], c["job_type"],
                    c["industry"], c["evaluation_status"], c["category"],
                    c["total_score"], c["voice_recording_url"], c["work_preference"],
                    od, c["final_evaluation_report_url"], c["interview_count"],
                    c["source_id"], c["phone"],
                )
                updated += 1
                print(f"  UPDATED: {c['name']} ({c['phone']})")
            else:
                await conn.execute(
                    """
                    INSERT INTO candidates (
                        name, age_years, gender, religion, phone, experience,
                        cv_url, source, current_location, location_lat, location_lng,
                        working_radius, current_salary, expected_salary, job_type,
                        industry, evaluation_status, category, total_score,
                        technical_evaluation_score,
                        voice_recording_url, work_preference, other_details,
                        final_evaluation_report_url, interview_count, source_id,
                        is_active, created_at, updated_at
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
                        $16,$17,$18,$19,$19,$20,$21,$22::jsonb,$23,$24,$25,
                        true, now(), now()
                    )
                    """,
                    c["name"], c["age_years"], c["gender"], c["religion"],
                    c["phone"], c["experience"], c["cv_url"], c["source"],
                    c["current_location"], c["location_lat"], c["location_lng"],
                    c["working_radius"], c["current_salary"], c["expected_salary"],
                    c["job_type"], c["industry"], c["evaluation_status"], c["category"],
                    c["total_score"], c["voice_recording_url"], c["work_preference"],
                    od, c["final_evaluation_report_url"], c["interview_count"],
                    c["source_id"],
                )
                inserted += 1
                print(f"  INSERTED: {c['name']} ({c['phone']})")
        except Exception as e:
            print(f"  ERROR {c['name']} ({c['phone']}): {e}")
            errors += 1

    await conn.close()
    print(f"\nDone — inserted: {inserted}, updated: {updated}, errors: {errors}")

    count_conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    count = await count_conn.fetchval("SELECT COUNT(*) FROM candidates")
    await count_conn.close()
    print(f"Total candidates in DB: {count}")


if __name__ == "__main__":
    asyncio.run(main())
