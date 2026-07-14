"""
v2 templates for the candidate post-AI-call messaging revamp:
  - AI call pass result (crisp, no client name — that's message 2's job)
  - Client review / shortlisted (per-client, fires once per qualifying mapping)
  - Interview slot-selection invite (link moved to a URL button, "Action Required")
  - Role-mismatch (now states an actual reason, never gender/religion)
  - Slot-selection reminders r1/r2/r3 ("Action Required" framing)

Run: python scripts/create_candidate_messaging_v2_templates.py

After Meta approves these, flip the following in backend/.env to go live:
  CANDIDATE_EVAL_SR_PASS_TEMPLATE=hl_cand_eval_sr_pass_v2
  CANDIDATE_EVAL_JR_PASS_TEMPLATE=hl_cand_eval_jr_pass_v2
  CANDIDATE_WAITING_TEMPLATE=hl_cand_waiting_v2
  CANDIDATE_SLOT_TEMPLATE=hl_cand_slot_select_v2
  CANDIDATE_SLOT_SELECT_TEMPLATE=hl_cand_slot_select_v2
  CANDIDATE_ROLE_NOT_FIT_TEMPLATE=hl_cand_role_not_fit_v2

The slot-reminder crons (hl_cand_slot_r1/r2/r3) are hardcoded in
routers/cron.py's _CAND_SLOT_TEMPLATES list (no env var) — update that list to
the _v2 names directly in code once approved.
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
        "hl_cand_eval_sr_pass_v2",
        "Hi {{1}}! 🎉 You've passed the AI Technical Round for the {{2}} tier. We'll now share your profile with matching clients — expect updates soon.",
        ["Rahul Sharma", "Sr. Accountant"],
        None, None, None,
    ),
    (
        "hl_cand_eval_jr_pass_v2",
        "Hi {{1}}! 🎉 You've passed the AI Technical Round for the {{2}} tier. We'll now share your profile with matching clients — expect updates soon.",
        ["Rahul Sharma", "Jr. Accountant"],
        None, None, None,
    ),
    (
        "hl_cand_waiting_v2",
        "Hi {{1}}, {{2}} is reviewing your profile for the {{3}} role! If they'd like to move forward, we'll send you the interview slot link.",
        ["Rahul Sharma", "ABC Corp", "Senior Accountant"],
        None, None, None,
    ),
    (
        "hl_cand_slot_select_v2",
        "Hi {{1}}! 🎯 Action Required: {{2}} wants to interview you for the {{3}} role — pick your slot now to lock it in.",
        ["Rahul Sharma", "ABC Corp", "Senior Accountant"],
        "Pick My Slot", CANDIDATE_PORTAL_URL, f"{CANDIDATE_PORTAL_URL}/c/{SAMPLE_CANDIDATE_ID}",
    ),
    (
        "hl_cand_role_not_fit_v2",
        "Hi {{1}}, the {{3}} role at {{2}} isn't the right match right now — {{4}}. We'll keep your profile active and reach out as soon as a better-fitting role comes up!",
        ["Rahul Sharma", "ABC Corp", "Senior Accountant", "your salary expectations are a bit above this role's budget"],
        None, None, None,
    ),
    (
        "hl_cand_slot_r1_v2",
        "Hi {{1}}, ⏰ Action Required: {{3}} is still waiting on your interview slot pick for the {{2}} role. Tap the link above to lock it in.",
        ["Rahul Sharma", "Senior Accountant", "ABC Corp"],
        None, None, None,
    ),
    (
        "hl_cand_slot_r2_v2",
        "Hi {{1}}, ⏳ Action Required: slots for {{3}} ({{2}}) are filling fast — pick yours now before they're gone.",
        ["Rahul Sharma", "Senior Accountant", "ABC Corp"],
        None, None, None,
    ),
    (
        "hl_cand_slot_r3_v2",
        "Hi {{1}}, ⚠️ Final Action Required: your interview opportunity with {{3}} for {{2}} expires soon. Pick a slot now or it may go to another candidate.",
        ["Rahul Sharma", "Senior Accountant", "ABC Corp"],
        None, None, None,
    ),
]


async def create_one(http: httpx.AsyncClient, name, body, examples, btn_text, btn_base_url, btn_url_example):
    components = [{"type": "BODY", "text": body, "example": {"body_text": [examples]}}]
    if btn_text:
        components.append({
            "type": "BUTTONS",
            "buttons": [{
                "type": "URL",
                "text": btn_text,
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
    print(f"\n{'─'*62}\n  Creating {len(TEMPLATES)} candidate-messaging v2 templates\n{'─'*62}\n")
    async with httpx.AsyncClient(timeout=30.0) as http:
        for (name, body, examples, btn_text, btn_base_url, btn_url_example) in TEMPLATES:
            status, resp = await create_one(http, name, body, examples, btn_text, btn_base_url, btn_url_example)
            if status in (200, 201) or resp.get("id"):
                print(f"  ✓ {name}  (id={resp.get('id', '?')}, status={resp.get('status')})")
            elif resp.get("error", {}).get("error_subcode") == 2388085:
                print(f"  ~ {name}  (already exists)")
            else:
                print(f"  ✗ {name}  → {resp.get('error', resp)}")
    print(f"\n{'─'*62}\n  Done. Once APPROVED in Meta Business Manager, flip these in backend/.env:\n{'─'*62}\n")
    print("  CANDIDATE_EVAL_SR_PASS_TEMPLATE=hl_cand_eval_sr_pass_v2")
    print("  CANDIDATE_EVAL_JR_PASS_TEMPLATE=hl_cand_eval_jr_pass_v2")
    print("  CANDIDATE_WAITING_TEMPLATE=hl_cand_waiting_v2")
    print("  CANDIDATE_SLOT_TEMPLATE=hl_cand_slot_select_v2")
    print("  CANDIDATE_SLOT_SELECT_TEMPLATE=hl_cand_slot_select_v2")
    print("  CANDIDATE_ROLE_NOT_FIT_TEMPLATE=hl_cand_role_not_fit_v2")
    print("\n  And update routers/cron.py's _CAND_SLOT_TEMPLATES list to:")
    print('  ["hl_cand_slot_r1_v2", "hl_cand_slot_r2_v2", "hl_cand_slot_r3_v2"]')
    print()


if __name__ == "__main__":
    asyncio.run(main())
