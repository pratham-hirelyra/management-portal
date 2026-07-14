"""
Create hl_cand_docs_req_v3 — replaces hl_cand_docs_req_v2 for two reasons:
1. v2's button URL was baked in with the retired ja-candidate-portal.web.app
   domain (approved before the migration to careers.justaccountants.in).
2. Document requirements dropped Aadhaar — only salary slips + bank statement
   are collected now (see candidate_portal.REQUIRED_INTERVIEW_DOCS).

Meta templates are immutable once approved, so this is a new version rather
than an edit. Once approved, flip DOCS_REQUEST_TEMPLATE=hl_cand_docs_req_v3
in .env (and Cloud Run) — do not flip early, v2 stays live until then.

Run: python scripts/create_docs_req_v3_template.py
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"

CANDIDATE_WABA_ID = os.environ["WHATSAPP_WABA_ID"]
CANDIDATE_TOKEN   = os.environ["WHATSAPP_API_TOKEN"]
CANDIDATE_PORTAL_URL = os.environ.get("CANDIDATE_PORTAL_URL", "https://careers.justaccountants.in").rstrip("/")

SAMPLE_MAPPING_ID = "8a1f3c2e-4b5d-4e6f-9a0b-1c2d3e4f5a6b"

NAME = "hl_cand_docs_req_v3"
BODY = (
    "Hi {{1}}, great news — {{2}} would like to move forward with you for the {{3}} role. "
    "To proceed, please upload a few documents: your last 3 months' salary slips and bank statement."
)
EXAMPLES = ["Rahul Sharma", "ABC Corp", "Senior Accountant"]
BTN_TEXT = "Upload Your Documents"
BTN_URL_EXAMPLE = f"{CANDIDATE_PORTAL_URL}/?view=documents&mapping={SAMPLE_MAPPING_ID}"


async def main():
    components = [
        {"type": "BODY", "text": BODY, "example": {"body_text": [EXAMPLES]}},
        {"type": "BUTTONS", "buttons": [{
            "type": "URL", "text": BTN_TEXT,
            "url": f"{CANDIDATE_PORTAL_URL}/{{{{1}}}}",
            "example": [BTN_URL_EXAMPLE],
        }]},
    ]
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
            json={"name": NAME, "language": "en", "category": "UTILITY", "components": components},
            headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}", "Content-Type": "application/json"},
        )
        data = resp.json()
    if resp.status_code in (200, 201) or data.get("id"):
        print(f"  ✓ {NAME}  (id={data.get('id', '?')}, status={data.get('status')})")
    elif data.get("error", {}).get("error_subcode") == 2388085:
        print(f"  ~ {NAME}  (already exists)")
    else:
        print(f"  ✗ {NAME}  → {data.get('error', data)}")
    print("\nOnce APPROVED, set in backend/.env AND Cloud Run:")
    print("  DOCS_REQUEST_TEMPLATE=hl_cand_docs_req_v3")


if __name__ == "__main__":
    asyncio.run(main())
