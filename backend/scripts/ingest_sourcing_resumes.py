"""
ingest_sourcing_resumes.py

Reads candidate CVs from gs://hirelyra-sourcing/sourcing_resumes/,
extracts the current employer company name using OpenAI,
and inserts them as client leads (stage=lead, segment=employer_lead).

Usage:
    python3 scripts/ingest_sourcing_resumes.py [--dry-run] [--limit N]

Skips:
  - Files already processed (tracked in sourcing_resume_processed set in DB)
  - Company names already present in clients table
  - Obvious recruiter names (free keyword check)
"""

import argparse
import asyncio
import io
import os
import sys
import uuid

import asyncpg
import httpx
import pdfplumber
from dotenv import load_dotenv
from google.cloud import storage

load_dotenv()

_BUCKET       = "hirelyra-sourcing"
_PREFIX       = "sourcing_resumes/"
_OPENAI_URL   = "https://api.openai.com/v1/chat/completions"
_MODEL        = "gpt-4o-mini"
_BATCH_SLEEP  = 0.3   # seconds between OpenAI calls to avoid 429


def _extract_text_from_pdf(data: bytes) -> str:
    """Extract plain text from PDF bytes. Returns up to 3000 chars."""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = []
            for page in pdf.pages[:4]:   # first 4 pages is enough
                text = page.extract_text() or ""
                pages.append(text)
                if sum(len(t) for t in pages) >= 3000:
                    break
            return "\n".join(pages)[:3000]
    except Exception as e:
        print(f"  [pdf] parse error: {e}")
        return ""


def _extract_text_from_docx(data: bytes) -> str:
    """Extract plain text from DOCX bytes."""
    try:
        import zipfile, xml.etree.ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml_content = z.read("word/document.xml")
        root = ET.fromstring(xml_content)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        texts = [node.text for node in root.iter(f"{ns}t") if node.text]
        return " ".join(texts)[:3000]
    except Exception as e:
        print(f"  [docx] parse error: {e}")
        return ""


def _extract_text(blob_name: str, data: bytes) -> str:
    lower = blob_name.lower()
    if lower.endswith(".pdf"):
        return _extract_text_from_pdf(data)
    if lower.endswith(".docx"):
        return _extract_text_from_docx(data)
    # Plain text fallback
    try:
        return data.decode("utf-8", errors="ignore")[:3000]
    except Exception:
        return ""


async def _extract_info(text: str) -> dict:
    """
    Ask GPT to extract from a resume:
      - companies: list of company names at the latest job
                   (if candidate held multiple roles at the same time, include all)
      - location:  candidate's current city/location if present
    Returns {"companies": [...], "location": str|None}
    """
    if not text.strip():
        return {"companies": [], "location": None}

    system = """\
You are a resume parser. Extract two things from the resume text:

1. LATEST EMPLOYER(S): The company/companies where the candidate currently works or most recently worked.
   - Look for the job with the most recent start date or marked "Present"
   - If the candidate held multiple roles simultaneously at the same time period, include ALL of them
   - Each entry must be just the company name — no designation, no dates, no extra text
   - If uncertain, include all candidates

2. LOCATION: The candidate's current city or location (from the header/contact section).
   - Just city name, e.g. "Ahmedabad" or "Mumbai"
   - null if not found

Return ONLY valid JSON, no markdown:
{"companies": ["Company A", "Company B"], "location": "City or null"}
"""
    user = f"Resume text:\n{text}"

    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                _OPENAI_URL,
                headers={
                    "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": _MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 150,
                    "temperature": 0,
                },
            )
        if not resp.is_success:
            print(f"  [openai] error {resp.status_code}: {resp.text[:100]}")
            return {"companies": [], "location": None}

        import json as _json
        data = _json.loads(resp.json()["choices"][0]["message"]["content"] or "{}")
        companies = [c.strip() for c in data.get("companies", []) if c and c.strip()]
        location  = data.get("location") or None
        if isinstance(location, str) and location.lower() in ("null", "none", ""):
            location = None
        return {"companies": companies, "location": location}
    except Exception as e:
        print(f"  [openai] exception: {e}")
        return {"companies": [], "location": None}


_RECRUITER_KEYWORDS = frozenset([
    "staffing", "recruitment", "recruiter", "manpower", "hr solutions",
    "hr services", "hr consultancy", "placement services", "placement agency",
    "executive search", "headhunting", "talent hunt", "job consultancy",
    "hiring solutions", "workforce solutions",
])

def _is_recruiter(name: str) -> bool:
    lower = name.lower()
    return any(kw in lower for kw in _RECRUITER_KEYWORDS)


def _list_blobs(bucket_name: str, prefix: str) -> list[str]:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=prefix)
    return [b.name for b in blobs if not b.name.endswith("/")]


def _download_blob(bucket_name: str, blob_name: str) -> bytes:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    return blob.download_as_bytes()


async def main(dry_run: bool, limit: int):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    # Ensure processed-tracking table exists
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sourcing_resume_log (
            blob_name text PRIMARY KEY,
            company_name text,
            client_id uuid,
            status text,   -- 'inserted' | 'duplicate' | 'recruiter' | 'no_company' | 'error'
            processed_at timestamptz DEFAULT now()
        )
    """)

    # Already-processed blobs
    processed = set(
        r["blob_name"] for r in await conn.fetch("SELECT blob_name FROM sourcing_resume_log")
    )

    print(f"Listing blobs in gs://{_BUCKET}/{_PREFIX} ...")
    blobs = _list_blobs(_BUCKET, _PREFIX)
    pending = [b for b in blobs if b not in processed]
    if limit:
        pending = pending[:limit]

    print(f"Total: {len(blobs)} | Already processed: {len(processed)} | To process: {len(pending)}")
    if not pending:
        print("Nothing to do.")
        await conn.close()
        return

    inserted = duplicate = recruiter = no_company = errors = 0

    for i, blob_name in enumerate(pending, 1):
        filename = blob_name.split("/")[-1]
        print(f"\n[{i}/{len(pending)}] {filename}")

        try:
            data = _download_blob(_BUCKET, blob_name)
        except Exception as e:
            print(f"  download error: {e}")
            await conn.execute(
                "INSERT INTO sourcing_resume_log (blob_name, status) VALUES ($1, 'error') ON CONFLICT DO NOTHING",
                blob_name,
            )
            errors += 1
            continue

        text = _extract_text(blob_name, data)
        if not text.strip():
            print(f"  no text extracted")
            await conn.execute(
                "INSERT INTO sourcing_resume_log (blob_name, status) VALUES ($1, 'no_company') ON CONFLICT DO NOTHING",
                blob_name,
            )
            no_company += 1
            continue

        info = await _extract_info(text)
        await asyncio.sleep(_BATCH_SLEEP)

        companies = info["companies"]
        location  = info["location"]

        if not companies:
            print(f"  no company found")
            await conn.execute(
                "INSERT INTO sourcing_resume_log (blob_name, status) VALUES ($1, 'no_company') ON CONFLICT DO NOTHING",
                blob_name,
            )
            no_company += 1
            continue

        print(f"  companies: {companies} | location: {location}")

        # Process each company separately
        blob_inserted = 0
        for company_name in companies:
            if _is_recruiter(company_name):
                print(f"  → [{company_name}] recruiter, skipping")
                recruiter += 1
                continue

            existing = await conn.fetchval(
                "SELECT id FROM clients WHERE LOWER(TRIM(company_name)) = LOWER(TRIM($1)) LIMIT 1",
                company_name,
            )
            if existing:
                print(f"  → [{company_name}] duplicate (client {existing}), skipping")
                duplicate += 1
                continue

            if dry_run:
                print(f"  [DRY RUN] would insert: {company_name} | location: {location}")
                inserted += 1
                blob_inserted += 1
                continue

            client_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO clients (id, company_name, location, stage, segment, source, created_at, updated_at)
                VALUES ($1, $2, $3, 'lead'::client_stage, 'employer_lead', 'sourcing_resume', now(), now())
                """,
                client_id, company_name, location,
            )
            print(f"  → [{company_name}] inserted as client {client_id}")
            inserted += 1
            blob_inserted += 1

        # Log the blob once — use first company name for reference
        if not dry_run:
            first_company = companies[0] if companies else None
            status = "inserted" if blob_inserted > 0 else "duplicate"
            await conn.execute(
                "INSERT INTO sourcing_resume_log (blob_name, company_name, status) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                blob_name, first_company, status,
            )

    await conn.close()

    print(f"\n{'='*50}")
    print(f"Done {'(DRY RUN) ' if dry_run else ''}— processed {len(pending)} resumes")
    print(f"  Inserted : {inserted}")
    print(f"  Duplicate: {duplicate}")
    print(f"  Recruiter: {recruiter}")
    print(f"  No company: {no_company}")
    print(f"  Errors   : {errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Extract but don't insert into DB")
    parser.add_argument("--limit",   type=int, default=0, help="Max resumes to process (0 = all)")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run, limit=args.limit))
