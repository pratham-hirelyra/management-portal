"""
Recreate all 33 client WABA templates in a new WABA.
Usage: python scripts/recreate_client_templates.py

Set in .env before running:
  NEW_CLIENT_WABA_ID=...
  NEW_CLIENT_API_TOKEN=...
"""

import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"
WABA_ID   = os.environ.get("NEW_CLIENT_WABA_ID") or os.environ["MSG91_CLIENT_WABA_ID"]
TOKEN     = os.environ.get("NEW_CLIENT_API_TOKEN") or os.environ["MSG91_CLIENT_API_TOKEN"]

# ── Text-only templates (auto-created via script) ─────────────────────────────
# (name, category, body_text, example_values, buttons)

TEMPLATES = [

    # ── Client reachout ───────────────────────────────────────────────────────
    (
        "client_reachout_new", "UTILITY",
        "We noticed that you may be looking to hire an Accountant. This is a follow-up regarding that requirement.\n\nCan you please confirm the current status.",
        [], [
            {"type": "QUICK_REPLY", "text": "Position Open"},
            {"type": "QUICK_REPLY", "text": "Not Required"},
            {"type": "QUICK_REPLY", "text": "STOP"},
        ],
    ),

    # ── Onboarding ────────────────────────────────────────────────────────────
    (
        "client_onboarding_1", "UTILITY",
        "Thank you for your interest in *Just Accountants* 🙏\n\nWe're excited to help you find the right accountant for your business.\n\nLink: {{1}}\nPlease fill out your hiring requirements using the link above",
        ["https://www.justaccountants.in/client-onboard"], [],
    ),
    (
        "client_onboarding_r1", "UTILITY",
        "Hi,\n\nWe noticed your hiring requirement form is still pending. To begin shortlisting candidates, please take 2 minutes to complete it:\n{{1}}\n\nWe look forward to hearing from you.",
        ["https://portal.justaccountants.in/client-onboard"], [],
    ),
    (
        "client_onboarding_r2", "UTILITY",
        "Hi,\n\nFollowing up on the hiring form which is still incomplete. Without this, we are unable to begin the search for a qualified candidate on your behalf:\n{{1}}\n\nPlease complete it at your earliest.",
        ["https://portal.justaccountants.in/client-onboard"], [],
    ),
    (
        "hl_ob_r3", "UTILITY",
        "Hi,\n\nThis is our final reminder to complete the hiring requirement form:\n{{1}}\n\nIf you no longer need our assistance, no action is needed.",
        ["https://portal.justaccountants.in/client-onboard"], [],
    ),

    # ── Agreement ─────────────────────────────────────────────────────────────
    (
        "client_send_agreement", "UTILITY",
        "Hi {{1}},\n\nYour recruitment agreement with JustAccountants is ready for review.\n  \nTap below to view the full terms and confirm your details.\n{{2}}\n  \nOnce confirmed, we'll assign your dedicated RM and start sharing candidate profiles.\n\nTeam JustAccountants",
        ["Pratham", "https://storage.googleapis.com/hirelyra-clients/R_Kumar_1/R_Kumar_1.pdf"], [],
    ),
    (
        "hl_agr_r1", "UTILITY",
        "Hi {{1}},\n\nThe service agreement for the {{2}} placement has been shared with you. Kindly review and confirm your acceptance so we can begin shortlisting candidates for you.",
        ["Rahul", "Accountant"], [],
    ),
    (
        "hl_agr_r2", "UTILITY",
        "Hi {{1}},\n\nFollowing up on the service agreement for {{2}}. Your confirmation is needed before we can proceed with the placement. Please review and accept at your earliest convenience.",
        ["Rahul", "Accountant"], [],
    ),
    (
        "hl_agr_r3", "UTILITY",
        "Hi {{1}},\n\nThis is a final reminder regarding the pending service agreement for {{2}}. Please confirm your acceptance to move forward. For any concerns about the terms, feel free to reach out directly.",
        ["Rahul", "Accountant"], [],
    ),

    # ── RM intro ──────────────────────────────────────────────────────────────
    (
        "client_share_rm_details", "UTILITY",
        "Hi {{1}},\n\nThank you for confirming your agreement with JustAccountants! 🎉\n\nSit back and relax while we find matching profiles for you.\nHere are the details for your dedicated Relationship Manager:\n\n- *Name*: {{2}}\n- *Phone*: {{3}}\n\nFeel free to reach out to them directly with any questions moving forward.\n\nWelcome aboard!\nTeam JustAccountants",
        ["Pratham", "Abhishek Shah", "9725549164"], [],
    ),

    # ── Search started ────────────────────────────────────────────────────────
    (
        "hl_client_search_started", "UTILITY",
        "Hi {{1}}! 👋\n\nThank you for onboarding with JustAccountants.\n\nWe have started shortlisting accountants that match your exact requirements and will share the candidate profiles with you within 48 hours.\n\nStay tuned!\nJustAccountants Team",
        ["Rohan Mehta"], [],
    ),

    # ── Slot selection ────────────────────────────────────────────────────────
    (
        "client_select_interview_time_slot", "UTILITY",
        "Please share 2–3 preferred dates and time slots for the interview.\n\nYou can use the link below to submit your availability:\n{{1}}\n\nWe will coordinate accordingly and share the confirmation update.",
        ["https://calendly.com"], [],
    ),
    (
        "hl_slot_r1", "UTILITY",
        "Hi {{1}},\n\nWe are waiting on your available interview slots for the {{2}} position. A shortlisted candidate is ready to schedule. Please share your preferred dates and times at your earliest convenience.",
        ["Rahul", "Accountant"], [],
    ),
    (
        "hl_slot_r2", "UTILITY",
        "Hi {{1}},\n\nQuick follow-up - we have not received your interview availability for {{2}} yet. Please share 2-3 preferred time slots so we can confirm the interview with the candidate.",
        ["Rahul", "Accountant"], [],
    ),
    (
        "hl_slot_r3", "UTILITY",
        "Hi {{1}},\n\nThis is our last reminder for interview slots for the {{2}} position. To avoid losing the shortlisted candidate, please share your availability as soon as possible.",
        ["Rahul", "Accountant"], [],
    ),
    (
        "hl_client_slots_received", "UTILITY",
        "Hi {{1}}, thank you for sharing your availability! 🙏\n\nHere are the slots you've shared:\n{{2}}\n\nWe'll confirm the interview schedule with the candidates and get back to you shortly.",
        ["Rohan Mehta", "Mon 9 Jun 10am, Mon 9 Jun 2pm"], [],
    ),

    # ── Interview confirmed ───────────────────────────────────────────────────
    (
        "client_interview_confirmed", "UTILITY",
        "Hi {{1}}, an interview has been scheduled for {{2}} on {{3}}.\n\nView {{2}}'s profile and all your pipeline candidates here:\n{{4}}\n\nLet us know if you need to reschedule.\n\nTeam JustAccountants",
        ["Rajesh", "Amit Sharma", "Wednesday, 28 May 2026 · 10:00 AM – 11:00 AM", "https://application.justaccountants.in/handover/abc123"], [],
    ),

    # ── Legacy candidate templates (on client WABA) ───────────────────────────
    (
        "candidate_re_reacout_step_two", "UTILITY",
        "Hi {{1}} ji, this is the last followup regarding your recent interest for accounting role on WorkIndia.\n\nWe are moving to the final stage of scheduling *face-to-face meetings* with other candidates. \n\nPlease confirm, if you wish to proceed with the next step.",
        ["Pratham"], [
            {"type": "QUICK_REPLY", "text": "YES"},
            {"type": "QUICK_REPLY", "text": "Not Looking For Job"},
        ],
    ),
    (
        "candidate_re_reachout_step1", "UTILITY",
        "Hi {{1}} ji, your profile for the Accountant role is currently pending at the '1st Technical Round' stage. We are ready to initiate your assessment.\n\nPlease confirm if you wish to proceed with this step.",
        ["Pratham"], [
            {"type": "QUICK_REPLY", "text": "YES"},
            {"type": "QUICK_REPLY", "text": "Not Looking For Job"},
        ],
    ),
    (
        "candidate_first_reachout_final", "UTILITY",
        "Hello {{1}} ji,\nThis is regarding your recent interest for the Accountant position on {{2}}.\n\nWe are sharing the job details for your review.\n\n*Job Details:*\n• *Company Name* : JustAccountants\n• *Location*: {{3}}\n• *Salary Range*: 25K–40K (based on experience)\n• *Open Positions*: 7\n\nIf you would like to continue, please confirm.",
        ["Pratham", "workindia", "Prahladnagar, Ahmedabad"], [
            {"type": "QUICK_REPLY", "text": "YES"},
            {"type": "QUICK_REPLY", "text": "Not Looking For Job"},
        ],
    ),
    (
        "candidate_first_reachouts", "UTILITY",
        "Hello,\nThis is regarding your recent interest for the Accountant position on {{1}}.\n\nWe are sharing the job details for your review.\n\n*Job Details:*\n• *Company Name* : JustAccountants\n• *Location*: {{2}}\n• *Salary Range*: 25K–40K (based on experience)\n• *Open Positions*: 7\n\nIf you would like to continue, please confirm.",
        ["workindia", "Prahladnagar, Ahmedabad"], [
            {"type": "QUICK_REPLY", "text": "YES"},
        ],
    ),
    (
        "candidate_voice_call_reschedule", "UTILITY",
        "Hello,\n\nWe noticed the call couldn't be completed earlier.\n\nYou can reschedule it using the button below to continue your application.",
        [], [
            {"type": "QUICK_REPLY", "text": "Reschedule"},
        ],
    ),
    (
        "candidate_voice_call_reminder", "UTILITY",
        "Hello,\n\nThis is a reminder that we will call you in the next 10 minutes.\n\nPlease be available to attend the call.",
        [], [],
    ),
    (
        "candidate_voice_call_remindier", "UTILITY",
        "Hello,\n\nThis is a reminder that we will be calling you in the next 10 minutes.\n\nPlease be available to attend the call.",
        [], [],
    ),
    (
        "candidate_evaluation_pass", "UTILITY",
        "Great news! You have successfully *passed* the test.🌟✨\n\nWe will contact you soon to understand your job preferences and then match you with suitable opportunities.",
        [], [],
    ),
    (
        "candidate_evaluation_fail", "UTILITY",
        "Thank you for taking our test! We really appreciate your effort.\n\nAt this time, we won't be able to share your CV with our clients for their current openings. Our clients are looking for people who are strong in the basics of accounting, and we feel you just need a little more practice in that area.\n\nPlease don't feel discouraged,  you are a good candidate with potential to grow.\n\nWe will keep your name on our list and lets stay in touch.\n\nKeep learning and keep trying!",
        [], [],
    ),
    (
        "ai_call_scheduled", "UTILITY",
        "Thank you for scheduling the call..!\n\nOur AI assistant Ravi will call you from *+91 8031137888* around *{{1}}*.\n\nThe call will take only 7-8 minutes of your time.",
        ["16th March 8:30 pm"], [],
    ),
    (
        "candidate_reachout_v3", "UTILITY",
        "Hello,\n\nThis is regarding your recent application for the Accountant position.\n\nApplication Details:\n• Location: {{1}}\n• Salary Range: 25K–40K (based on experience)\n• Open Positions: 25\n\nIf you would like to continue, please confirm.",
        ["Ahmedabad"], [
            {"type": "QUICK_REPLY", "text": "YES"},
            {"type": "QUICK_REPLY", "text": "Not Looking For Job"},
        ],
    ),
    (
        "candidate_reachout_v2", "UTILITY",
        "Hello,\n\nThis is an update regarding your recent application for the Accountant position.\n\nApplication Details:\n• Location: {{1}}\n• Salary Range: 25K–40K (based on experience)\n• Open Positions: 25\n\nYour application is currently under review.\n\nIf you would like to continue with the screening process, please confirm.",
        ["Ahmedabad"], [
            {"type": "QUICK_REPLY", "text": "YES"},
        ],
    ),
]

# ── Must be created MANUALLY in MSG91 dashboard ───────────────────────────────
MANUAL_TEMPLATES = [
    {
        "name": "client_reachout_2",
        "reason": "IMAGE header",
        "body": "We noticed that you may be looking to hire an Accountant. This is a follow-up regarding that requirement.\n\nCan you please confirm the current status.",
        "buttons": ["View Profiles", "Not Required", "STOP"],
    },
    {
        "name": "client_reachout_1",
        "reason": "IMAGE header",
        "body": "Click on the below button to get started",
        "buttons": ["Interested", "Not Interested"],
    },
    {
        "name": "client_agreement_confirmed",
        "reason": "DOCUMENT header",
        "body": "Your recruitment agreement with JustAccountants has been confirmed. 📄\n\nPlease find your agreement attached for your records.\n\nIf you need any assistance during your hiring journey, feel free to reach out to your RM directly.\n\nTeam JustAccountants",
        "buttons": [],
    },
    {
        "name": "candidate_select_another_time",
        "reason": "FLOW button — needs flow setup in Meta first",
        "body": "No problem\n\nPlease choose a convenient time using the below button.",
        "buttons": ["Select Time Slot (FLOW)"],
    },
]


async def create_template(http: httpx.AsyncClient, name, category, body, examples, buttons):
    body_component: dict = {"type": "BODY", "text": body}
    if examples:
        body_component["example"] = {"body_text": [examples]}

    components = [body_component]
    if buttons:
        components.append({"type": "BUTTONS", "buttons": buttons})

    resp = await http.post(
        f"{GRAPH_URL}/{WABA_ID}/message_templates",
        json={"name": name, "language": "en", "category": category, "components": components},
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    return {"name": name, "status": resp.status_code, "body": resp.json()}


async def main():
    print(f"\n{'─'*60}")
    print(f"  Creating {len(TEMPLATES)} templates in WABA {WABA_ID}")
    print(f"{'─'*60}\n")

    created = skipped = failed = 0

    async with httpx.AsyncClient(timeout=30.0) as http:
        for (name, category, body, examples, buttons) in TEMPLATES:
            result = await create_template(http, name, category, body, examples, buttons)
            status = result["status"]
            resp   = result["body"]

            if status in (200, 201) or resp.get("id"):
                print(f"  ✓ {name}")
                created += 1
            elif resp.get("error", {}).get("error_subcode") == 2388085:
                print(f"  ~ {name}  (already exists)")
                skipped += 1
            else:
                err = resp.get("error", resp)
                print(f"  ✗ {name}  → {err}")
                failed += 1

    print(f"\n  Done — created: {created}, skipped: {skipped}, failed: {failed}")

    print(f"\n{'─'*60}")
    print(f"  ⚠️  Create these {len(MANUAL_TEMPLATES)} MANUALLY in MSG91 dashboard:")
    print(f"{'─'*60}\n")
    for t in MANUAL_TEMPLATES:
        print(f"  • {t['name']}  ({t['reason']})")
        if t['buttons']:
            print(f"    Buttons: {', '.join(t['buttons'])}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
