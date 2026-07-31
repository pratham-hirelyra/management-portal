"""
Create all missing MSG91/Meta WhatsApp templates.
Run: python scripts/create_templates.py
"""

import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GRAPH_URL = "https://graph.facebook.com/v19.0"

CANDIDATE_WABA_ID = os.environ["WHATSAPP_WABA_ID"]           # 2502285973557887
CANDIDATE_TOKEN   = os.environ["WHATSAPP_API_TOKEN"]

CLIENT_WABA_ID    = os.environ["MSG91_CLIENT_WABA_ID"]        # 1518080329260304
CLIENT_TOKEN      = os.environ["MSG91_CLIENT_API_TOKEN"]

FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://app.justaccountants.in")

# ── Template definitions ──────────────────────────────────────────────────────
# Each entry: (waba_id, token, name, category, body_text, example_values)
# example_values = list of values for {{1}}, {{2}}, ... (empty list = no variables)

TEMPLATES = [

    # ── CANDIDATE WABA ────────────────────────────────────────────────────────

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_ai_interview_notify", "UTILITY",
        (
            "Thank you for submitting your details!\n\n"
            "You are now one step away from your face-to-face interview. We will be conducting a short 5-minute AI call where you will be asked 5 basic accounting questions to assess your profile.\n\n"
            "You will receive a call from +918035383694 in the next 6 minutes. Please make sure to pick it up.\n\n"
            "Best of luck!\n"
            "JustAccountants Team"
        ),
        [],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_slot_select", "UTILITY",
        (
            "Hi {{1}}, great news! 🎉 *{{2}}* would like to interview you for the role of *{{3}}*.\n\n"
            "Please pick your preferred interview slot here:\n{{4}}\n\nTap the link above to confirm your slot."
        ),
        ["Rahul Sharma", "ABC Corp", "Senior Accountant", f"{FRONTEND_URL}/candidate-schedule/sample-id"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_waiting", "UTILITY",
        (
            "Hi {{1}}, you've been shortlisted by *{{2}}* for the role of *{{3}}*. 🌟\n\n"
            "The client is finalising interview slots — we'll send you a link to pick your time very soon!"
        ),
        ["Rahul Sharma", "ABC Corp", "Senior Accountant"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_role_not_fit", "UTILITY",
        (
            "Hi {{1}}, thank you for completing the screening for *{{2}}* ({{3}}).\n\n"
            "After careful review, this particular role isn't the right fit right now — "
            "but we'll keep you in mind for future opportunities that match your profile. 🙏"
        ),
        ["Rahul Sharma", "ABC Corp", "Senior Accountant"],
    ),

    # Sent right after form submission when the candidate's current salary is
    # outside the client's budget range — no AI call is conducted in this case.
    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_not_fit_client", "UTILITY",
        (
            "Hi {{1}}, thank you for sharing your details! 🙏\n\n"
            "After reviewing your profile, we feel you may not be the right fit for *{{2}}* at this time — "
            "but we'll keep your profile and reach out as soon as a more suitable opportunity comes up.\n\n"
            "Best of luck! 😊"
        ),
        ["Rahul Sharma", "ABC Corp"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_passive_ack", "UTILITY",
        (
            "Thank you, {{1}}! 🙏\n\n"
            "We've saved your requirements. As soon as we have a matching opportunity we'll reach out. "
            "Best of luck! 😊"
        ),
        ["Rahul Sharma"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_onboarding_link", "UTILITY",
        (
            "We're glad you're interested! 🎉\n\n"
            "Please fill in this short form so we can match you to the right roles:\n{{1}}\n\nWe look forward to hearing from you!"
        ),
        [f"{FRONTEND_URL}/apply"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_not_looking", "UTILITY",
        (
            "No problem at all! We respect your decision. 🙏\n\n"
            "If you're ever open to opportunities in the future, feel free to reach out. All the best!"
        ),
        [],
    ),

    # Passive form acknowledgement (sent after candidate submits form via NOT_INTERESTED_ROLE path)
    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_passive_form_ack", "UTILITY",
        (
            "Hi {{1}},\n\n"
            "Thank you for filling up the job preference form.\n\n"
            "Our recruitment team is already on it. We are scanning our network of 3,200+ active accounting roles in {{2}} to showcase the perfect matches for your profile.\n"
            "We will start sharing the most relevant JDs with you soon.\n\n"
            "Best regards,\n\n"
            "JustAccountants Team"
        ),
        ["Rahul Sharma", "Ahmedabad"],
    ),

    # Interested form reminders (candidate tapped INTERESTED but hasn't filled the form)
    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_interested_form_r1", "UTILITY",
        (
            "Hi {{1}},\n\n"
            "We noticed that you have yet not filled up your basic details. Please complete your basic profile details so we can forward your profile to the evaluation stage:\n\n"
            "📋 {{2}}\n\n"
            "Regards,\nJustAccountants Team"
        ),
        ["Rahul Sharma", f"{FRONTEND_URL}/apply"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_interested_form_r2", "UTILITY",
        (
            "Hi {{1}},\n\n"
            "Our system is currently shortlisting profiles and scheduling candidates for the face to face interview for this role.\n\n"
            "To ensure your application is included in this active batch, please submit your basic details now:\n\n"
            "🔗 {{2}}\n\n"
            "Regards,\nJustAccountants Team"
        ),
        ["Rahul Sharma", f"{FRONTEND_URL}/apply"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_interested_form_r3", "UTILITY",
        (
            "Hi {{1}},\n\n"
            "This is the final reminder to complete your application for the role you selected. If your basic details are not received shortly, your application slot for this position will expire.\n\n"
            "🔗 {{2}}\n\n"
            "Regards,\nJustAccountants Team"
        ),
        ["Rahul Sharma", f"{FRONTEND_URL}/apply"],
    ),

    # Passive form reminders (candidate tapped NOT_INTERESTED_ROLE but hasn't filled the form)
    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_passive_form_r1", "UTILITY",
        (
            "Hi {{1}},\n\n"
            "We noticed that your job preference form is still pending. Please complete it so our system can process your profile against our 3,200+ active accounting roles.\n\n"
            "📋 Fill your job preference form here: {{2}}\n\n"
            "Once submitted, we will begin finding relevant job profiles for you.\n\n"
            "Regards,\nJustAccountants Team"
        ),
        ["Rahul Sharma", f"{FRONTEND_URL}/apply?intent=passive"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_passive_form_r2", "UTILITY",
        (
            "Hi {{1}},\n\n"
            "Our recruitment team is currently shortlisting profiles for this week's face-to-face interviews.\n\n"
            "To ensure your profile is included in this matching cycle, please submit your job preferences form in the next few hours:\n\n"
            "🔗 {{2}}\n\n"
            "Regards,\nJustAccountants Team"
        ),
        ["Rahul Sharma", f"{FRONTEND_URL}/apply?intent=passive"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_passive_form_r3", "UTILITY",
        (
            "Hi {{1}},\n\n"
            "This is our final reminder regarding filling up your accounting job preferences form. To prevent your profile from being paused for active matches, please share your job requirements now:\n\n"
            "🔗 {{2}}\n\n"
            "If you are no longer looking for a change, you can safely ignore this message.\n\n"
            "Regards,\nJustAccountants Team"
        ),
        ["Rahul Sharma", f"{FRONTEND_URL}/apply?intent=passive"],
    ),

    # AI evaluation result messages
    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_eval_sr_pass", "UTILITY",
        (
            "Hi {{1}},\n\n"
            "Congratulations! 🎉 You have successfully passed the AI Technical Round for the *{{2}}* position.\n\n"
            "Next Step: We will soon ask you to block your face-to-face interview slot. We will send you a separate message with the scheduling link for that. Please note that it might take up to 48 hours for us to arrange your face to face interview.\n\n"
            "Best regards,\n\nJustAccountants Team"
        ),
        ["Rahul Sharma", "Sr. Accountant"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_eval_fail", "UTILITY",
        (
            "Hi {{1}},\n\n"
            "Thank you for taking the time to complete the AI Technical Round for the *{{2}}* position.\n\n"
            "While we appreciate your efforts, your performance in the technical round did not meet the required benchmark for this role. Therefore, we *regret* to inform you that we will not be able to take your profile further in this process.\n\n"
            "We truly appreciate your interest and wish you the absolute best in your future career.\n\n"
            "Best regards,\nJustAccountants Team"
        ),
        ["Rahul Sharma", "Sr. Accountant"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_eval_satisfactory", "UTILITY",
        (
            "Hi {{1}},\n\n"
            "Thank you for completing the AI Technical Round for the *{{2}}* position.\n\n"
            "Our client is strictly looking for a senior-level professional, and based on your test results, your technical knowledge is currently a little lower than what this specific job demands. Therefore, you do not qualify for this senior JD.\n\n"
            "However, your profile fits perfectly for a Jr. Accountant role! We see great potential in your skills and will soon share relevant Jr. Accountant job profiles that match your expertise.\n\n"
            "Best regards,\n\nJustAccountants Team"
        ),
        ["Rahul Sharma", "Sr. Accountant"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_eval_jr_pass", "UTILITY",
        (
            "Hi {{1}},\n\n"
            "Congratulations! 🎉 You have successfully passed the AI Technical Round for the *{{2}}* position.\n\n"
            "Next Step: We will soon ask you to block your face-to-face interview slot. We will send you a separate message with the scheduling link for that. Please note that it might take up to 48 hours for us to fix your face to face interview.\n\n"
            "Best regards,\n\nJustAccountants Team"
        ),
        ["Rahul Sharma", "Jr. Accountant"],
    ),

    # Drop-off / no-pickup final WA (sent after all 4 retry calls fail)
    # Buttons: Interested | Not Interested – Role | Not Looking for Job
    # Note: this template is submitted with create_template_with_buttons helper — not via TEMPLATES loop.
    # Kept here for documentation. Template name: hl_cand_drop_final_v2

    # Slot selection reminders (same text, escalating tone)
    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_slot_r1", "UTILITY",
        (
            "Hi {{1}}, just a gentle reminder 😊\n\n"
            "You still have a pending interview slot to select for *{{3}}* — *{{2}}* role.\n\n"
            "Please click the link in our previous message to confirm your slot before it fills up!"
        ),
        ["Rahul Sharma", "Senior Accountant", "ABC Corp"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_slot_r2", "UTILITY",
        (
            "Hi {{1}}, the interview slots for *{{3}}* (*{{2}}*) are filling up fast! ⏳\n\n"
            "Please select your preferred time from our earlier message before the slots run out."
        ),
        ["Rahul Sharma", "Senior Accountant", "ABC Corp"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_slot_r3", "UTILITY",
        (
            "Hi {{1}}, this is your final reminder ⚠️\n\n"
            "The interview opportunity with *{{3}}* for *{{2}}* will expire soon. "
            "Please select a slot from the link we shared, or the opportunity may be given to another candidate."
        ),
        ["Rahul Sharma", "Senior Accountant", "ABC Corp"],
    ),

    # Sent when a candidate doesn't pick an interview slot within 48h of receiving the link —
    # mapping is closed out and the candidate is unlocked back into the matching pool
    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_slot_link_expired", "UTILITY",
        (
            "Hi {{1}}, we did not receive your interview slot selection for *{{3}}* (*{{2}}*) in time, "
            "so this opportunity has now been closed on our end.\n\n"
            "No worries — we'll keep sharing newer matching opportunities with you. Stay tuned! 🙏"
        ),
        ["Rahul Sharma", "Senior Accountant", "ABC Corp"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_iv_24h", "UTILITY",
        (
            "Hi {{1}}, your interview with *{{2}}* for *{{3}}* is tomorrow! 📅\n\n"
            "🕐 Time: {{4}} (IST)\n\n"
            "Please be available 5 minutes before the scheduled time and keep your documents ready. All the best! 🌟"
        ),
        ["Rahul Sharma", "ABC Corp", "Senior Accountant", "10:00 AM, Mon 26 May"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_iv_day", "UTILITY",
        (
            "Good morning {{1}}! 🌅 Your interview with *{{2}}* for *{{3}}* is today.\n\n"
            "🕐 Time: {{4}} (IST)\n\n"
            "Stay calm, be confident, and give it your best. Rooting for you! 💪"
        ),
        ["Rahul Sharma", "ABC Corp", "Senior Accountant", "10:00 AM, Mon 26 May"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_post_iv", "UTILITY",
        (
            "Hi {{1}}, hope your interview with *{{2}}* for *{{3}}* went well! 🤞\n\n"
            "We'd love to hear how it went. Please share your feedback here:\n{{4}}\n\n"
            "Your input helps us serve you better. Thank you! 🙏"
        ),
        ["Rahul Sharma", "ABC Corp", "Senior Accountant", f"{FRONTEND_URL}/candidate-feedback/sample-token"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_docs_req", "UTILITY",
        (
            "Congratulations {{1}}! 🎉\n\n"
            "Both you and *{{2}}* have expressed a positive interest in moving forward for *{{3}}*.\n\n"
            "Please share the following documents at your earliest:\n"
            "• Aadhar Card\n• PAN Card\n• Last 3 months' salary slips\n• Offer / Appointment letter\n\n"
            "Reply to this message with the documents or share via Google Drive link."
        ),
        ["Rahul Sharma", "ABC Corp", "Senior Accountant"],
    ),

    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "candidate_position_closed", "UTILITY",
        (
            "Hi {{1}}, the position at *{{2}}* has been filled. 🙏\n\n"
            "Don't worry — we will keep sharing more and more relevant opportunities with you "
            "as per your requirements. Stay tuned! 😊\n\n"
            "Best regards,\nJustAccountants Team"
        ),
        ["Rahul Sharma", "ABC Corp"],
    ),

    # {{1}}=candidate name  {{2}}=company  {{3}}=slot label  {{4}}=location link
    (
        CANDIDATE_WABA_ID, CANDIDATE_TOKEN,
        "hl_cand_interview_confirmed_v3", "UTILITY",
        (
            "Hi {{1}}, your interview is confirmed! 🎉\n\n"
            "🏢 *Company:* {{2}}\n"
            "📅 *Slot:* {{3}}\n"
            "📍 *Location:* {{4}}\n\n"
            "Please be on time and keep your documents ready. All the best! 💪\n"
            "JustAccountants Team"
        ),
        ["Rahul Sharma", "ABC Corp", "Mon 30 Jun 3pm", "https://maps.google.com/?q=ABC+Corp"],
    ),

    # ── CLIENT WABA ───────────────────────────────────────────────────────────

    (
        CLIENT_WABA_ID, CLIENT_TOKEN,
        "hl_client_search_started", "UTILITY",
        (
            "Hi {{1}}! 👋\n\n"
            "Thank you for onboarding with JustAccountants.\n\n"
            "We have started shortlisting accountants that match your exact requirements and will "
            "share the candidate profiles with you within 48 hours.\n\n"
            "Stay tuned!\n"
            "JustAccountants Team"
        ),
        ["Rohan Mehta"],
    ),

    (
        CLIENT_WABA_ID, CLIENT_TOKEN,
        "hl_client_slots_received", "UTILITY",
        (
            "Hi {{1}}, thank you for sharing your availability! 🙏\n\n"
            "Here are the slots you've shared:\n{{2}}\n\n"
            "We'll confirm the interview schedule with the candidates and get back to you shortly."
        ),
        ["Rohan Mehta", "Mon 9 Jun 10am, Mon 9 Jun 2pm"],
    ),

    (
        CLIENT_WABA_ID, CLIENT_TOKEN,
        "hl_ob_r3", "UTILITY",
        (
            "Hi {{1}}, this is your final reminder 🔔\n\n"
            "Your onboarding form for *{{2}}* is still pending.\n\n"
            "Complete it here and let's get started:\n{{3}}"
        ),
        ["Rohan Mehta", "Senior Accountant", f"{FRONTEND_URL}/client-onboard?ref=sample"],
    ),

    (
        CLIENT_WABA_ID, CLIENT_TOKEN,
        "hl_agr_r1", "UTILITY",
        (
            "Hi {{1}}, just checking in 😊\n\n"
            "Your agreement for *{{2}}* is ready and waiting for your signature.\n\n"
            "Please review and sign it at your earliest convenience."
        ),
        ["Rohan Mehta", "Senior Accountant"],
    ),

    (
        CLIENT_WABA_ID, CLIENT_TOKEN,
        "hl_agr_r2", "UTILITY",
        (
            "Hi {{1}}, a quick reminder ⏳\n\n"
            "We're still waiting on your agreement for *{{2}}*.\n\n"
            "Signing it will help us kick off the hiring process right away!"
        ),
        ["Rohan Mehta", "Senior Accountant"],
    ),

    (
        CLIENT_WABA_ID, CLIENT_TOKEN,
        "hl_agr_r3", "UTILITY",
        (
            "Hi {{1}}, this is our final reminder ⚠️\n\n"
            "Your agreement for *{{2}}* is still unsigned. "
            "Please sign it to avoid any delays in your hiring process."
        ),
        ["Rohan Mehta", "Senior Accountant"],
    ),

    (
        CLIENT_WABA_ID, CLIENT_TOKEN,
        "hl_slot_r1", "UTILITY",
        (
            "Hi {{1}}, just a reminder 😊\n\n"
            "We're waiting for your preferred interview slots for *{{2}}*.\n\n"
            "Please click the scheduling link we shared to pick your availability."
        ),
        ["Rohan Mehta", "Senior Accountant"],
    ),

    (
        CLIENT_WABA_ID, CLIENT_TOKEN,
        "hl_slot_r2", "UTILITY",
        (
            "Hi {{1}}, our candidates are ready and waiting! ⏳\n\n"
            "We're still waiting on your interview slots for *{{2}}*.\n\n"
            "Please select your availability from the link we sent so we can schedule interviews."
        ),
        ["Rohan Mehta", "Senior Accountant"],
    ),

    (
        CLIENT_WABA_ID, CLIENT_TOKEN,
        "hl_slot_r3", "UTILITY",
        (
            "Hi {{1}}, final reminder ⚠️\n\n"
            "We haven't received your interview availability for *{{2}}* yet.\n\n"
            "Please share your slots at the earliest to keep the hiring process moving forward."
        ),
        ["Rohan Mehta", "Senior Accountant"],
    ),
]


JD_TEMPLATE_BODY = (
    "Hi {{13}}!\n\n"
    "{{14}}\n\n"
    "*Job Details Update*\n\n"
    "*Client Information*\n"
    "*Client*: {{1}}\n"
    "*Industry*: {{2}}\n"
    "*Business Type*: {{3}}\n"
    "*Work Location Type*: {{4}}\n"
    "*Location*: {{5}}\n"
    "*Employees*: {{6}} | Female%: {{7}}%\n\n"
    "*Job Information*\n"
    "*Skills Needed*: {{8}}\n"
    "*Softwares*: {{9}}\n"
    "*Salary*: {{10}}\n"
    "*Job Timings*: {{11}}\n"
    "{{12}}\n\n"
    "Please confirm your interest status."
)

JD_TEMPLATE_EXAMPLES = [
    "Tatvic Analytics Pvt. Ltd.", "Digital Marketing/IT",
    "Manufacturer – Own Factory", "Industrial Area",
    "https://maps.google.com/?q=23.0,72.5",
    "50", "40",
    "Accounting, GST & TDS return filing",
    "Tally Prime",
    "₹25,000 – ₹30,000 in hand + 1 month salary Diwali Bonus",
    "10am-7pm | Mon-Sat, 2nd & 4th Sat off",
    "Personal mobile phones not allowed.",
    "Rahul",
    "This is regarding your recent interest for the Senior Accountant position on WorkIndia.\n\nI am sharing the job details for your review:",
]

JD_TEMPLATE_BUTTONS = [
    {"type": "QUICK_REPLY", "text": "Interested"},
    {"type": "QUICK_REPLY", "text": "Not Interested – Role"},
    {"type": "QUICK_REPLY", "text": "Not Looking for Job"},
]


async def create_template(
    http: httpx.AsyncClient,
    waba_id: str,
    token: str,
    name: str,
    category: str,
    body_text: str,
    example_values: list,
) -> dict:
    body_component: dict = {"type": "BODY", "text": body_text}
    if example_values:
        body_component["example"] = {"body_text": [example_values]}

    payload = {
        "name": name,
        "language": "en",
        "category": category,
        "components": [body_component],
    }

    resp = await http.post(
        f"{GRAPH_URL}/{waba_id}/message_templates",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    return {"name": name, "status": resp.status_code, "body": resp.json()}


async def main():
    print(f"\n{'─'*62}")
    print(f"  Creating {len(TEMPLATES) + 1} WhatsApp templates")
    print(f"{'─'*62}\n")

    async with httpx.AsyncClient(timeout=30.0) as http:
        for (waba_id, token, name, category, body, examples) in TEMPLATES:
            waba_label = "CLIENT   " if waba_id == CLIENT_WABA_ID else "CANDIDATE"
            result = await create_template(http, waba_id, token, name, category, body, examples)
            status = result["status"]
            resp   = result["body"]

            if status in (200, 201) or resp.get("id"):
                print(f"  ✓ [{waba_label}] {name}  (id={resp.get('id', '?')})")
            elif resp.get("error", {}).get("error_subcode") == 2388085:
                print(f"  ~ [{waba_label}] {name}  (already exists)")
            else:
                err = resp.get("error", resp)
                print(f"  ✗ [{waba_label}] {name}  → {err}")

        body_component: dict = {
            "type": "BODY",
            "text": JD_TEMPLATE_BODY,
            "example": {"body_text": [JD_TEMPLATE_EXAMPLES]},
        }
        jd_payload = {
            "name": "hl_cand_jd_share",
            "language": "en",
            "category": "UTILITY",
            "components": [
                body_component,
                {"type": "BUTTONS", "buttons": JD_TEMPLATE_BUTTONS},
            ],
        }
        jd_resp = await http.post(
            f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
            json=jd_payload,
            headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}", "Content-Type": "application/json"},
        )
        jd_body = jd_resp.json()
        if jd_resp.status_code in (200, 201) or jd_body.get("id"):
            print(f"  ✓ [CANDIDATE] hl_cand_jd_share  (id={jd_body.get('id', '?')})")
        elif jd_body.get("error", {}).get("error_subcode") == 2388085:
            print(f"  ~ [CANDIDATE] hl_cand_jd_share  (already exists)")
        else:
            print(f"  ✗ [CANDIDATE] hl_cand_jd_share  → {jd_body.get('error', jd_body)}")

        # NOTE: hl_cand_re_r1_v3 / r2_v3 / r3_v3 (the templates actually wired
        # into routers/cron.py's _REACHOUT_TEMPLATES as of 2026-07-31) are NOT
        # created here. They're image-header templates matching
        # hl_cand_jd_img_v3's exact shape (real URL "Interested Role" /
        # "Not Interested Role" buttons, unlike the quick-reply-only _v2 set
        # below), which requires uploading an example image via Meta's
        # resumable upload API (POST /{app_id}/uploads, then POST the file
        # bytes to get a header_handle) before the template create call — this
        # script has no media-upload step, so they were submitted with a
        # one-off script instead. If they ever need recreating, replicate
        # hl_cand_jd_img_v3's components (see scripts, or query
        # GET /{waba_id}/message_templates?name=hl_cand_jd_img_v3) with the r1/
        # r2/r3 intro text from _RE_INTROS in routers/cron.py.

        # Re-reachout templates — same 14-param JD body as hl_cand_jd_share,
        # differentiated only by the {{14}} intro text passed at send time.
        # (Superseded by hl_cand_re_r*_v3 above — kept only so re-running this
        # script doesn't error on templates that already exist on Meta.)
        re_reachout_templates = [
            (
                "hl_cand_re_r1_v2",
                JD_TEMPLATE_BODY,
                JD_TEMPLATE_EXAMPLES[:12] + ["Rahul", "You haven't responded to our earlier message. Here are the job details again:"],
            ),
            (
                "hl_cand_re_r2_v2",
                JD_TEMPLATE_BODY,
                JD_TEMPLATE_EXAMPLES[:12] + ["Rahul", "We're still shortlisting candidates. Here are the job details one more time:"],
            ),
            (
                "hl_cand_re_r3_v2",
                JD_TEMPLATE_BODY,
                JD_TEMPLATE_EXAMPLES[:12] + ["Rahul", "This is our final follow-up. Interview slots are filling up fast:"],
            ),
        ]

        for re_name, re_body, re_examples in re_reachout_templates:
            re_payload = {
                "name": re_name,
                "language": "en",
                "category": "UTILITY",
                "components": [
                    {
                        "type": "BODY",
                        "text": re_body,
                        "example": {"body_text": [re_examples]},
                    },
                    {"type": "BUTTONS", "buttons": JD_TEMPLATE_BUTTONS},
                ],
            }
            re_resp = await http.post(
                f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
                json=re_payload,
                headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}", "Content-Type": "application/json"},
            )
            re_body_resp = re_resp.json()
            if re_resp.status_code in (200, 201) or re_body_resp.get("id"):
                print(f"  ✓ [CANDIDATE] {re_name}  (id={re_body_resp.get('id', '?')})")
            elif re_body_resp.get("error", {}).get("error_subcode") == 2388085:
                print(f"  ~ [CANDIDATE] {re_name}  (already exists)")
            else:
                print(f"  ✗ [CANDIDATE] {re_name}  → {re_body_resp.get('error', re_body_resp)}")

    print(f"\n{'─'*62}")
    print("  Done.")
    print(f"{'─'*62}\n")
    # ── OTP template (AUTHENTICATION category — separate API call) ────────────
    otp_payload = {
        "name": "hl_cand_otp_v2",
        "language": "en",
        "category": "AUTHENTICATION",
        "components": [
            {"type": "BODY", "add_security_recommendation": True},
            {"type": "FOOTER", "code_expiration_minutes": 10},
            {"type": "BUTTONS", "buttons": [{"type": "OTP", "otp_type": "COPY_CODE", "text": "Copy Code"}]},
        ],
    }
    otp_resp = await http.post(
        f"{GRAPH_URL}/{CANDIDATE_WABA_ID}/message_templates",
        json=otp_payload,
        headers={"Authorization": f"Bearer {CANDIDATE_TOKEN}", "Content-Type": "application/json"},
    )
    otp_body = otp_resp.json()
    if otp_resp.status_code in (200, 201) or otp_body.get("id"):
        print(f"  ✓ [CANDIDATE] hl_cand_otp_v2  (id={otp_body.get('id', '?')}, status={otp_body.get('status')})")
    elif otp_body.get("error", {}).get("error_subcode") == 2388085:
        print(f"  ~ [CANDIDATE] hl_cand_otp_v2  (already exists)")
    else:
        print(f"  ✗ [CANDIDATE] hl_cand_otp_v2  → {otp_body.get('error', otp_body)}")

    print("  Add these to .env once templates are APPROVED in Meta:")
    print("  CANDIDATE_SLOT_SELECT_TEMPLATE=hl_cand_slot_select")
    print("  CANDIDATE_WAITING_TEMPLATE=hl_cand_waiting")
    print("  CANDIDATE_ROLE_NOT_FIT_TEMPLATE=hl_cand_role_not_fit")
    print("  CANDIDATE_NOT_FIT_CLIENT_TEMPLATE=hl_cand_not_fit_client")
    print("  CANDIDATE_PASSIVE_ACK_TEMPLATE=hl_cand_passive_ack")
    print("  CANDIDATE_ONBOARDING_LINK_TEMPLATE=hl_cand_onboarding_link")
    print("  CANDIDATE_NOT_LOOKING_TEMPLATE=hl_cand_not_looking")
    print("  CANDIDATE_SLOT_TEMPLATE=hl_cand_slot_select")
    print("  CANDIDATE_LINK_EXPIRED_TEMPLATE=hl_cand_slot_link_expired")
    print("  CANDIDATE_OTP_TEMPLATE=hl_cand_otp_v2")
    print()


if __name__ == "__main__":
    asyncio.run(main())
