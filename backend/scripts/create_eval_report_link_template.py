"""
Create the WhatsApp template sent after a candidate passes the AI Technical
Round — points them at the candidate portal to view their evaluation report
and see all further job opportunities going forward.

The URL button is fully STATIC (no {{1}} suffix) since every candidate lands
on the same portal home/login page and authenticates there via phone + OTP —
there's no per-candidate deep link to get wrong, unlike client_reachout_3's
dynamic-URL button.

Meta templates are immutable once approved — this only creates it if it
doesn't already exist (safe to re-run).

Run: python scripts/create_eval_report_link_template.py

Once APPROVED, set in backend/.env AND Cloud Run:
  CANDIDATE_EVAL_REPORT_LINK_TEMPLATE=hl_cand_view_report_v1
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"

CANDIDATE_WABA_ID = os.environ["WHATSAPP_WABA_ID"]
CANDIDATE_TOKEN = os.environ["WHATSAPP_API_TOKEN"]
PORTAL_URL = os.environ.get("CANDIDATE_PORTAL_URL", "https://careers.justaccountants.in").rstrip("/")

TEMPLATE = {
    "name": "hl_cand_view_report_v1",
    "body": (
        "Hi {{1}},\n\n"
        "Your AI Technical Round is complete! 🎉\n\n"
        "Tap below to view your evaluation report and see all job opportunities "
        "matching your profile — available anytime on your JustAccountants portal.\n\n"
        "Abhishek Shah\nJustAccountants"
    ),
    "examples": ["Rahul Sharma"],
    "button_text": "View My Report",
}


async def main():
    components = [
        {"type": "BODY", "text": TEMPLATE["body"], "example": {"body_text": [TEMPLATE["examples"]]}},
        {"type": "BUTTONS", "buttons": [
            {"type": "URL", "text": TEMPLATE["button_text"], "url": PORTAL_URL},
        ]},
    ]
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
            json={"name": TEMPLATE["name"], "language": "en", "category": "UTILITY", "components": components},
            headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}", "Content-Type": "application/json"},
        )
        data = resp.json()
        if resp.status_code in (200, 201) or data.get("id"):
            print(f"  ✓ {TEMPLATE['name']}  (id={data.get('id', '?')}, status={data.get('status')})")
        elif data.get("error", {}).get("error_subcode") == 2388085:
            print(f"  ~ {TEMPLATE['name']}  (already exists)")
        else:
            print(f"  ✗ {TEMPLATE['name']}  → {data.get('error', data)}")

    print("\nOnce APPROVED, set in backend/.env AND Cloud Run:")
    print("  CANDIDATE_EVAL_REPORT_LINK_TEMPLATE=hl_cand_view_report_v1")


if __name__ == "__main__":
    asyncio.run(main())
