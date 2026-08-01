"""
Central registry of WhatsApp Business template names used across the app.

Every name here is resolvable at runtime the same way the old inline
`os.environ.get("X_TEMPLATE", "default")` calls resolved it — an env var
override still wins over the code default, nothing about deploy-time
overrides changes. This module just gives every template one canonical
name instead of leaving the string scattered across a dozen routers.

Template *content* (the approved copy on Meta) is managed separately —
see scripts/create_*.py for template registration and Meta Business
Manager for the source of truth on what's actually live/approved.
"""
import os


def _tpl(env_var: str, default: str = "") -> str:
    return os.environ.get(env_var, default).strip()


# ── OTP / login ──────────────────────────────────────────────────────────
CANDIDATE_OTP = _tpl("CANDIDATE_OTP_TEMPLATE", "hl_cand_otp_v2")
CLIENT_OTP = os.environ.get("CLIENT_OTP_TEMPLATE", CANDIDATE_OTP).strip()

# ── Candidate: outreach / JD share ──────────────────────────────────────
CANDIDATE_INTENT = _tpl("CANDIDATE_INTENT_TEMPLATE", "candidate_share_client_jd")
# Optional image-header variant of CANDIDATE_INTENT, used only when a candidate's
# form was already filled (see routers/admin.py send-JD-image flow).
CANDIDATE_INTENT_IMG = os.environ.get("CANDIDATE_INTENT_TEMPLATE_IMG") or CANDIDATE_INTENT
CANDIDATE_RE_REACHOUT_R1 = "hl_cand_re_r1"
CANDIDATE_RE_REACHOUT_R2 = "hl_cand_re_r2"
CANDIDATE_RE_REACHOUT_R3 = "hl_cand_re_r3"
CANDIDATE_RE_REACHOUT_R1_IMG = "hl_cand_re_r1_img"
CANDIDATE_RE_REACHOUT_R2_IMG = "hl_cand_re_r2_img"
CANDIDATE_RE_REACHOUT_R3B_IMG = "hl_cand_re_r3b_img"
CANDIDATE_JD_IMG = "hl_cand_jd_img_v3"
CANDIDATE_JD_SHARE_5 = "hl_cand_jd_share_5"
CANDIDATE_FORM_SUBMITTED = "candidate_form_submitted"
CANDIDATE_DROP_FINAL = "hl_cand_drop_final_v2"

# ── Candidate: interested/passive job-preference form nudges (cron drip) ──
CANDIDATE_INTERESTED_FORM_R1 = "hl_interested_form_r1"
CANDIDATE_INTERESTED_FORM_R2 = "hl_interested_form_r2"
CANDIDATE_INTERESTED_FORM_R3 = "hl_interested_form_r3"
CANDIDATE_PASSIVE_FORM_ACK = _tpl("CANDIDATE_PASSIVE_ACK_TEMPLATE", "hl_passive_form_ack")
CANDIDATE_PASSIVE_FORM_R1 = "hl_passive_form_r1"
CANDIDATE_PASSIVE_FORM_R2 = "hl_passive_form_r2"
CANDIDATE_PASSIVE_FORM_R3 = "hl_passive_form_r3"
CANDIDATE_ONBOARDING_LINK = _tpl("CANDIDATE_ONBOARDING_LINK_TEMPLATE")
CANDIDATE_NOT_LOOKING = _tpl("CANDIDATE_NOT_LOOKING_TEMPLATE")

# ── Candidate: slot selection & interview ───────────────────────────────
CANDIDATE_SLOT = _tpl("CANDIDATE_SLOT_TEMPLATE")
CANDIDATE_SLOT_SELECT = _tpl("CANDIDATE_SLOT_SELECT_TEMPLATE")
CANDIDATE_SLOT_SELECT_ROUND2 = _tpl("CANDIDATE_SLOT_SELECT_ROUND2_TEMPLATE")
CANDIDATE_SLOT_R1_V3 = "hl_cand_slot_r1_v3"
CANDIDATE_SLOT_R2_V3 = "hl_cand_slot_r2_v3"
CANDIDATE_SLOT_R3_V3 = "hl_cand_slot_r3_v3"
CANDIDATE_SLOT_R1_V4 = "hl_cand_slot_r1_v4"
CANDIDATE_SLOT_R2_V4 = "hl_cand_slot_r2_v4"
CANDIDATE_SLOT_R3_V4 = "hl_cand_slot_r3_v4"
CANDIDATE_SLOT_LINK_EXPIRED = _tpl("CANDIDATE_LINK_EXPIRED_TEMPLATE")
CANDIDATE_SLOT_CONFIRMED = _tpl("CANDIDATE_SLOT_CONFIRMED_TEMPLATE", "hl_cand_interview_confirmed_v3")
CANDIDATE_INTERVIEW_CONFIRM = _tpl("CANDIDATE_INTERVIEW_CONFIRM_TEMPLATE", "candidate_interview_confirmation")
CANDIDATE_IV_24H = "hl_cand_iv_24h"
CANDIDATE_IV_DAY = "hl_cand_iv_day"
CANDIDATE_IV_3H = "hl_cand_iv_3h"
CANDIDATE_IV_1H = "hl_cand_iv_1h"
CANDIDATE_POST_IV = "hl_cand_post_iv"
CANDIDATE_WAITING = _tpl("CANDIDATE_WAITING_TEMPLATE")
CANDIDATE_WAITLISTED = _tpl("CANDIDATE_WAITLISTED_TEMPLATE")
CANDIDATE_ROLE_NOT_FIT = _tpl("CANDIDATE_ROLE_NOT_FIT_TEMPLATE")
CANDIDATE_POSITION_CLOSED = _tpl("CANDIDATE_POSITION_CLOSED_TEMPLATE")

# ── Candidate: docs / offer / join intent ───────────────────────────────
CANDIDATE_DOCS_REQUEST = _tpl("DOCS_REQUEST_TEMPLATE", "hl_cand_docs_req_v2")
CANDIDATE_DOCS_REMINDER = _tpl("DOCS_REMINDER_TEMPLATE", "hl_cand_docs_reminder_v1")
CANDIDATE_JOIN_INTENT = _tpl("JOIN_INTENT_TEMPLATE", "hl_cand_join_intent_v1")
CANDIDATE_OFFER = _tpl("OFFER_TEMPLATE", "hl_cand_offer_v1")
CANDIDATE_OFFER_EXPIRED = _tpl("OFFER_EXPIRED_TEMPLATE", "hl_cand_offer_expired_v1")

# ── Candidate: AI evaluation ─────────────────────────────────────────────
CANDIDATE_AI_INTERVIEW_NOTIFY = _tpl("CANDIDATE_AI_INTERVIEW_TEMPLATE")
CANDIDATE_EVALUATION_FAIL = _tpl("CANDIDATE_EVALUATION_FAIL_TEMPLATE")
CANDIDATE_EVAL_FAIL = "hl_cand_eval_fail"
CANDIDATE_EVAL_PASS_GENERIC = _tpl("CANDIDATE_EVAL_PASS_GENERIC_TEMPLATE")
CANDIDATE_EVAL_REPORT_LINK = _tpl("CANDIDATE_EVAL_REPORT_LINK_TEMPLATE")
CANDIDATE_NOT_FIT_CLIENT = _tpl("CANDIDATE_NOT_FIT_CLIENT_TEMPLATE")
CANDIDATE_NOT_FIT_CLIENT_REASON = _tpl("CANDIDATE_NOT_FIT_CLIENT_REASON_TEMPLATE")

# ── Client: OTP / onboarding / portal ───────────────────────────────────
CLIENT_ONBOARDING = _tpl("CLIENT_ONBOARDING_TEMPLATE")
CLIENT_ONBOARDING_R1 = "client_onboarding_r1"
CLIENT_ONBOARDING_R2 = "client_onboarding_r2"
CLIENT_ONBOARDING_R3 = "hl_ob_r3"
CLIENT_PORTAL_LINK = _tpl("CLIENT_PORTAL_LINK_TEMPLATE", "client_portal_link_v1")
CLIENT_SEARCH_STARTED = _tpl("CLIENT_SEARCH_STARTED_TEMPLATE")

# ── Client: agreement ────────────────────────────────────────────────────
CLIENT_AGREEMENT = _tpl("CLIENT_AGREEMENT_TEMPLATE", "client_send_agreement")
CLIENT_AGREEMENT_CONFIRMED = _tpl("CLIENT_AGREEMENT_CONFIRMED_TEMPLATE", "client_agreement_confirmed")
CLIENT_AGREEMENT_REMINDER_R1 = "hl_agr_r1"
CLIENT_AGREEMENT_REMINDER_R2 = "hl_agr_r2"
CLIENT_AGREEMENT_REMINDER_R3 = "hl_agr_r3"

# ── Client: review / feedback / offer updates ───────────────────────────
CLIENT_PENDING_REVIEW = _tpl("CLIENT_PENDING_REVIEW_TEMPLATE", "hl_client_pending_review_v4")
CLIENT_REVIEW_REMINDER = _tpl("CLIENT_REVIEW_REMINDER_TEMPLATE", "hl_client_review_reminder_v1")
CLIENT_PENDING_FEEDBACK = _tpl("CLIENT_PENDING_FEEDBACK_TEMPLATE", "hl_client_pending_feedback_v1")
CLIENT_OFFER_UPDATE = _tpl("CLIENT_OFFER_UPDATE_TEMPLATE", "hl_client_offer_update_v2")

# ── Client: slots / interview confirmation ──────────────────────────────
CLIENT_SLOT = _tpl("CLIENT_SLOT_TEMPLATE")
CLIENT_AVAILABILITY = _tpl("CLIENT_AVAILABILITY_TEMPLATE")
CLIENT_SLOTS_RECEIVED = _tpl("CLIENT_SLOTS_RECEIVED_TEMPLATE")
CLIENT_SLOT_CONFIRMED = _tpl("CLIENT_SLOT_CONFIRMED_TEMPLATE")
CLIENT_SLOT_CONFIRMED_V2 = "client_interview_confirmed_v2"
CLIENT_SLOT_CONFIRMED_ROUND2 = _tpl("CLIENT_SLOT_CONFIRMED_ROUND2_TEMPLATE")
CLIENT_SLOT_CONFIRMED_ROUND2_V2 = "hl_client_interview_round2_confirmed_v2"
CLIENT_INTERVIEW_CONFIRM_REMINDER = _tpl("INTERVIEW_CONFIRM_TEMPLATE", "hl_client_iv_confirm_v1")
CLIENT_SLOT_REMINDER_R1 = "hl_slot_r1"
CLIENT_SLOT_REMINDER_R2 = "hl_slot_r2"
CLIENT_SLOT_REMINDER_R3 = "hl_slot_r3"

# ── Client: marketing reachout ───────────────────────────────────────────
CLIENT_REACHOUT_3 = "client_reachout_3"

# ── RM ────────────────────────────────────────────────────────────────────
RM_INTRO = _tpl("RM_INTRO_TEMPLATE", "client_share_rm_details")
RM_ALERT = _tpl("RM_ALERT_TEMPLATE", "hl_rm_client_alert_v1")
