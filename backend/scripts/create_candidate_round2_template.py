"""
Candidate-facing "invited for a second round" template — distinct from the
round-1 slot-select invite so a candidate who already interviewed once isn't
sent what looks like a duplicate "wants to interview you" message with no
context that the client specifically asked to meet them again.

Run: python scripts/create_candidate_round2_template.py

After Meta approves, add to backend/.env:
  CANDIDATE_SLOT_SELECT_ROUND2_TEMPLATE=hl_cand_slot_select_round2
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"

CANDIDATE_WABA_ID = os.environ["WHATSAPP_WABA_ID"]
CANDIDATE_TOKEN   = os.environ["WHATSAPP_API_TOKEN"]

NAME = "hl_cand_slot_select_round2"
BODY = (
    "Hi {{1}}! 🎯 Action Required: Great news — {{2}} would like to meet you again for a "
    "*second round* for the {{3}} role. Pick your slot now to lock it in:\n{{4}}\n\nSee you soon!"
)
EXAMPLES = ["Rahul Sharma", "ABC Corp", "Senior Accountant", "https://careers.justaccountants.in/c/sample-id"]


async def main():
    components = [{"type": "BODY", "text": BODY, "example": {"body_text": [EXAMPLES]}}]
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
            json={"name": NAME, "language": "en", "category": "UTILITY", "components": components},
            headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}", "Content-Type": "application/json"},
        )
        status, data = resp.status_code, resp.json()
        if status in (200, 201) or data.get("id"):
            print(f"✓ {NAME}  (id={data.get('id', '?')}, status={data.get('status')})")
        elif data.get("error", {}).get("error_subcode") == 2388085:
            print(f"~ {NAME}  (already exists)")
        else:
            print(f"✗ {NAME}  → {data.get('error', data)}")
    print("\nOnce APPROVED in Meta Business Manager, add to backend/.env:")
    print("  CANDIDATE_SLOT_SELECT_ROUND2_TEMPLATE=hl_cand_slot_select_round2")


if __name__ == "__main__":
    asyncio.run(main())
