"""
Fixes two problems found after auditing live Meta template statuses via the
Graph API (message_templates?fields=category,status):

1. hl_cand_slot_r1/r2/r3 (and their _v2s) never actually carry a link/button —
   they just say "tap the link above" / "in our previous message", relying on
   the candidate scrolling back to the original invite. Every reminder here
   now carries the schedule link directly.

2. Three templates got auto-classified MARKETING by Meta instead of the
   UTILITY we submitted, despite identical category param:
     hl_cand_slot_select_v2       (round 1 invite, has a URL button)
     hl_cand_slot_select_round2   (round 2 invite, no button)
     hl_cand_slot_r2_v2           (reminder, no button)
   Comparing against templates that DID pass as UTILITY (hl_cand_slot_select,
   hl_cand_waiting_v2, hl_cand_slot_r1_v2, hl_cand_slot_r3_v2,
   client_portal_link_v1 — the last one even has a button), the common trait
   of the three that failed is "introducing a new opportunity" framing
   ("wants to interview you", "would like to meet you again", "filling
   fast") — Meta's classifier reads this as promotional. The ones that
   passed reference an EXISTING pending action ("is still waiting",
   "expires soon", "candidates are ready for review"). Reworded below to
   match that pattern; button kept on the round-1 invite to test whether
   wording alone was the trigger (this script checks status after creation).

Run: python scripts/create_slot_templates_v3.py

After Meta approves, add to backend/.env:
  CANDIDATE_SLOT_SELECT_TEMPLATE=hl_cand_slot_select_v3
  CANDIDATE_SLOT_SELECT_ROUND2_TEMPLATE=hl_cand_slot_select_round2_v2
And update routers/cron.py's _CAND_SLOT_TEMPLATES list to:
  ["hl_cand_slot_r1_v3", "hl_cand_slot_r2_v3", "hl_cand_slot_r3_v3"]
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
SAMPLE_CANDIDATE_ID  = "8a1f3c2e-4b5d-4e6f-9a0b-1c2d3e4f5a6b"

# (name, body_text, example_values, button_text | None, button_base_url | None, button_url_example | None)
TEMPLATES = [
    (
        "hl_cand_slot_select_v3",
        "Hi {{1}}, {{2}} has shortlisted you for the {{3}} role and would like to schedule an "
        "interview. Please confirm your preferred time slot using the link below.",
        ["Rahul Sharma", "ABC Corp", "Senior Accountant"],
        "Select Interview Slot", CANDIDATE_PORTAL_URL, f"{CANDIDATE_PORTAL_URL}/c/{SAMPLE_CANDIDATE_ID}",
    ),
    (
        "hl_cand_slot_select_round2_v2",
        "Hi {{1}}, {{2}} would like to schedule a second round interview for the {{3}} role. "
        "Please confirm your preferred time slot here:\n{{4}}\n\nThank you!",
        ["Rahul Sharma", "ABC Corp", "Senior Accountant", f"{CANDIDATE_PORTAL_URL}/c/{SAMPLE_CANDIDATE_ID}"],
        None, None, None,
    ),
    (
        "hl_cand_slot_r1_v3",
        "Hi {{1}}, reminder — your interview slot selection for the {{2}} role at {{3}} is still "
        "pending. Please confirm here:\n{{4}}\n\nThank you!",
        ["Rahul Sharma", "Senior Accountant", "ABC Corp", f"{CANDIDATE_PORTAL_URL}/c/{SAMPLE_CANDIDATE_ID}"],
        None, None, None,
    ),
    (
        "hl_cand_slot_r2_v3",
        "Hi {{1}}, we still need your interview slot confirmation for the {{2}} role at {{3}}. "
        "Please select a time here:\n{{4}}\n\nThank you!",
        ["Rahul Sharma", "Senior Accountant", "ABC Corp", f"{CANDIDATE_PORTAL_URL}/c/{SAMPLE_CANDIDATE_ID}"],
        None, None, None,
    ),
    (
        "hl_cand_slot_r3_v3",
        "Hi {{1}}, final reminder — your interview opportunity with {{3}} for {{2}} will expire "
        "soon. Please confirm your slot immediately:\n{{4}}\n\nThank you!",
        ["Rahul Sharma", "Senior Accountant", "ABC Corp", f"{CANDIDATE_PORTAL_URL}/c/{SAMPLE_CANDIDATE_ID}"],
        None, None, None,
    ),
]


async def create_one(http, name, body, examples, btn_text, btn_base_url, btn_url_example):
    components = [{"type": "BODY", "text": body, "example": {"body_text": [examples]}}]
    if btn_text:
        components.append({
            "type": "BUTTONS",
            "buttons": [{
                "type": "URL", "text": btn_text,
                "url": f"{btn_base_url}/{{{{1}}}}",
                "example": [btn_url_example],
            }],
        })
    resp = await http.post(
        f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
        json={"name": name, "language": "en", "category": "UTILITY", "components": components},
        headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}", "Content-Type": "application/json"},
    )
    return resp.status_code, resp.json()


async def main():
    print(f"\n{'─'*62}\n  Creating {len(TEMPLATES)} slot templates (v3 reword + links)\n{'─'*62}\n")
    async with httpx.AsyncClient(timeout=30.0) as http:
        for (name, body, examples, btn_text, btn_base_url, btn_url_example) in TEMPLATES:
            status, resp = await create_one(http, name, body, examples, btn_text, btn_base_url, btn_url_example)
            if status in (200, 201) or resp.get("id"):
                print(f"  ✓ {name}  (id={resp.get('id', '?')}, status={resp.get('status')})")
            elif resp.get("error", {}).get("error_subcode") == 2388085:
                print(f"  ~ {name}  (already exists)")
            else:
                print(f"  ✗ {name}  → {resp.get('error', resp)}")

        print(f"\n{'─'*62}\n  Verifying actual category assigned (Meta can override)\n{'─'*62}\n")
        await asyncio.sleep(2)
        list_resp = await http.get(
            f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
            params={"limit": 250, "fields": "name,category,status"},
            headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}"},
        )
        by_name = {t["name"]: t for t in list_resp.json().get("data", [])}
        for (name, *_rest) in TEMPLATES:
            t = by_name.get(name)
            if t:
                flag = "✓" if t["category"] == "UTILITY" else "✗ STILL " + t["category"]
                print(f"  {flag}  {name}  status={t['status']}")
            else:
                print(f"  ?  {name}  (not found yet — check again in a bit)")

    print(f"\n{'─'*62}\n  Once APPROVED, add to backend/.env:\n{'─'*62}\n")
    print("  CANDIDATE_SLOT_SELECT_TEMPLATE=hl_cand_slot_select_v3")
    print("  CANDIDATE_SLOT_SELECT_ROUND2_TEMPLATE=hl_cand_slot_select_round2_v2")
    print("\n  And update routers/cron.py's _CAND_SLOT_TEMPLATES list to:")
    print('  ["hl_cand_slot_r1_v3", "hl_cand_slot_r2_v3", "hl_cand_slot_r3_v3"]')
    print()


if __name__ == "__main__":
    asyncio.run(main())
