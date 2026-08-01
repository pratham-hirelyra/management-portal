"""
Resubmit hl_cand_re_r1_ff / hl_cand_re_r3_ff as UTILITY templates.

Both were approved by Meta as MARKETING — never wired into any send flow yet
(not referenced anywhere in routers/ or services/), so no live traffic is
affected by resubmitting under new names.

Likely cause of the MARKETING classification: the original body used a
free-text {{2}} variable for the framing sentence ("Following up on this
opportunity...", "Here are the job details once more..."). The sibling
hl_cand_jd_share_5 template — same recipient family (form-filled candidates),
same buttons — was approved as UTILITY, and its body bakes that framing
sentence in as fixed text instead of a variable:
  "This is an update regarding your recruitment process."
This script mirrors that fixed-text pattern for the r1/r3 reminders, cutting
the free-text variable and reducing the body to the same 5 positional vars
as hl_cand_jd_share_5 (name, company, role, salary, location).

Run: python scripts/create_re_reachout_ff_utility.py

After Meta approves, wire into routers/cron.py's re-reachout drip and add
constants to backend/constants/whatsapp_templates.py.
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"
CANDIDATE_WABA_ID = os.environ["WHATSAPP_WABA_ID"]
CANDIDATE_TOKEN = os.environ["WHATSAPP_API_TOKEN"]
CANDIDATE_PORTAL_URL = os.environ.get("CANDIDATE_PORTAL_URL", "https://careers.justaccountants.in").rstrip("/")

TEMPLATES = [
    (
        "hl_cand_re_r1_ff_v2",
        "Hi {{1}},\n\nThis is a reminder regarding your recruitment process — here is the opportunity once more for your review:\n\n*Company*: {{2}}\n*Role*: {{3}}\n*Salary*: Up to ₹{{4}}\n*Location*: {{5}}\n\nPlease select an option below to continue.",
        ["Pratham", "Tatvic", "Senior Accountant", "40000", "Prahladnagar, Ahmedabad"],
    ),
    (
        "hl_cand_re_r3_ff_v2",
        "Hi {{1}},\n\nThis is a final reminder regarding your recruitment process — please review the opportunity details below:\n\n*Company*: {{2}}\n*Role*: {{3}}\n*Salary*: Up to ₹{{4}}\n*Location*: {{5}}\n\nPlease select an option below to continue.",
        ["Pratham", "Tatvic", "Senior Accountant", "40000", "Prahladnagar, Ahmedabad"],
    ),
]


async def create_one(http, name, body, examples):
    components = [
        {"type": "BODY", "text": body, "example": {"body_text": [examples]}},
        {"type": "BUTTONS", "buttons": [
            {
                "type": "URL", "text": "View Details",
                "url": "https://m.9m.io/{{1}}",
                "example": [f"{CANDIDATE_PORTAL_URL}/"],
            },
            {
                "type": "URL", "text": "Update Profile",
                "url": "https://m.9m.io/{{1}}",
                "example": [f"{CANDIDATE_PORTAL_URL}/?view=profile"],
            },
            {"type": "QUICK_REPLY", "text": "Not Looking For Job"},
        ]},
    ]
    resp = await http.post(
        f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
        json={"name": name, "language": "en", "category": "UTILITY", "components": components},
        headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}", "Content-Type": "application/json"},
    )
    print(f"{name}: HTTP {resp.status_code}")
    print("  ", resp.text[:500])


async def main():
    async with httpx.AsyncClient(timeout=30) as http:
        for name, body, examples in TEMPLATES:
            await create_one(http, name, body, examples)
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
