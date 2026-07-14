"""
Candidate WhatsApp flow engine.

Adding a new flow step = one new entry in CANDIDATE_FLOWS.
No changes to the router or webhook handler needed.

Key: (current_stage, trigger)
  - trigger is the button_payload prefix ("INTERESTED", "NOT_INTERESTED_ROLE", "SLOT_1", ...)
    or a normalised text keyword (upper-cased, spaces→underscores)

Value: FlowStep — what stage to move to, what template to send next (if any).
"""

from __future__ import annotations

import asyncio
import asyncpg
import os
import uuid
from dataclasses import dataclass, field
from typing import Callable, Awaitable


@dataclass
class FlowStep:
    next_stage: str
    # MSG91/Meta template name to send after transitioning; None = silent transition
    template: str | None = None
    sender: str = "candidate"
    # Raw SQL fragment appended after "SET stage = ...,", e.g. "intent_received_at = now(),"
    # Must end with a comma if non-empty.
    extra_updates: str = ""
    # async fn(conn, mapping_id) -> list[dict] in Meta component format
    build_components: Callable[..., Awaitable[list[dict]]] | None = None
    # async fn(conn, mapping_id, from_phone) -> None — runs after stage update, for free-text sends
    after_hook: Callable[..., Awaitable[None]] | None = None


# ── Hooks ─────────────────────────────────────────────────────────────────────

async def _send_slot_selection_link(phone: str, mapping_id: uuid.UUID, candidate_id: uuid.UUID | None = None) -> None:
    import os
    import services.msg91_service as msg91
    portal_url = os.environ.get("CANDIDATE_PORTAL_URL", os.environ.get("FRONTEND_URL", "http://localhost:5174")).rstrip("/")
    schedule_url = f"{portal_url}/c/{candidate_id}" if candidate_id else f"{portal_url}/candidate-schedule/{mapping_id}"
    slot_template = os.environ.get("CANDIDATE_SLOT_TEMPLATE", "").strip()
    if slot_template:
        components = [{"type": "body", "parameters": [{"type": "text", "text": schedule_url}]}]
        await msg91.send_template(phone, slot_template, "en", components, sender="candidate")
    else:
        msg = (
            f"You've been shortlisted for an interview. Pick your preferred time here:\n"
            f"{schedule_url}"
        )
        await msg91.send_text(phone, msg, sender="candidate")


async def _send_intro_video(phone: str) -> None:
    import os
    import services.msg91_service as msg91
    video_url = os.environ.get("CANDIDATE_INTRO_VIDEO_URL", "").strip()
    if not video_url:
        return
    try:
        await msg91.send_video(
            phone, video_url,
            caption=(
                "Ever wondered why Just Accountants gets you better results than a job portal? 🎯\n\n"
                "Watch this short video to see how we personally match you with the right opportunity — "
                "not just send your CV everywhere."
            ),
            sender="candidate",
        )
    except Exception as e:
        print(f"[flow] intro video send failed for {phone}: {e}")
        try:
            from services.google_chat_service import send_alert
            await send_alert("Intro video send failed", str(e), severity="ERROR", context={"phone": phone})
        except Exception:
            pass


async def _send_onboarding_link(phone: str, intent: str = "INTERESTED") -> None:
    """Send candidate onboarding form link immediately on INTERESTED / NOT_INTERESTED_ROLE."""
    import os
    import services.msg91_service as msg91
    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173").strip().rstrip("/")
    full_url = f"{frontend_url}/apply" if intent == "INTERESTED" else f"{frontend_url}/apply?intent=passive"
    template = os.environ.get("CANDIDATE_ONBOARDING_LINK_TEMPLATE", "").strip()

    if intent == "INTERESTED":
        msg = (
            f"Please fill in this short form so we can match you to the right role:\n"
            f"{full_url}"
        )
    else:
        msg = (
            f"No worries! 🙏\n\n"
            f"Please fill in this form to help us know more about you:\n"
            f"{full_url}"
        )

    try:
        if template:
            components = [{"type": "body", "parameters": [{"type": "text", "text": full_url}]}]
            await msg91.send_template(phone, template, "en", components, sender="candidate")
        else:
            await msg91.send_text(phone, msg, sender="candidate")
    except Exception as e:
        print(f"[flow] onboarding link send failed for {phone}: {e}")
        try:
            from services.google_chat_service import send_alert
            await send_alert("Onboarding link send failed", str(e), severity="ERROR", context={"phone": phone, "intent": intent})
        except Exception:
            pass


async def _trigger_ai_call_with_notify(
    conn, mapping_id, cand_id, from_phone: str, cand_name: str
) -> None:
    """Trigger RinggAI call and send the AI interview notification WA."""
    import services.msg91_service as msg91
    import services.ringg_service as ringg
    import os
    try:
        await ringg.trigger_ai_call(cand_id, from_phone, cand_name)
    except Exception as e:
        print(f"[flow] RinggAI trigger failed for {from_phone}: {e}")
        try:
            from services.google_chat_service import send_alert
            await send_alert("RinggAI trigger failed", str(e), severity="ERROR", context={"phone": from_phone, "cand_id": str(cand_id)})
        except Exception:
            pass
        try:
            _conn = conn
            if _conn is None:
                from database import get_pool
                async with get_pool().acquire() as _conn:
                    await _conn.execute(
                        "UPDATE candidates SET call_status = 'DROPPED', updated_at = now() WHERE id = $1",
                        cand_id,
                    )
            else:
                await _conn.execute(
                    "UPDATE candidates SET call_status = 'DROPPED', updated_at = now() WHERE id = $1",
                    cand_id,
                )
        except Exception as db_e:
            print(f"[flow] failed to mark DROPPED for {from_phone}: {db_e}")
            try:
                from services.google_chat_service import send_alert
                await send_alert("Failed to mark candidate DROPPED", str(db_e), severity="ERROR", context={"phone": from_phone, "cand_id": str(cand_id)})
            except Exception:
                pass

    ai_notify_template = os.environ.get("CANDIDATE_AI_INTERVIEW_TEMPLATE", "").strip()
    sent = False
    if ai_notify_template:
        try:
            result = await msg91.send_template(from_phone, ai_notify_template, "en", [], sender="candidate")
            print(f"[flow] ai_interview template sent to {from_phone}, MSG91 response: {result}")
            sent = True
        except Exception as e:
            print(f"[flow] ai_interview template '{ai_notify_template}' failed: {e} — falling back to text")
            try:
                from services.google_chat_service import send_alert
                await send_alert("AI interview template send failed", str(e), severity="ERROR", context={"phone": from_phone, "template": ai_notify_template})
            except Exception:
                pass
    if not sent:
        try:
            result = await msg91.send_text(
                from_phone,
                "Thank you for submitting your details!\n\n"
                "You are now one step away from your face-to-face interview. We will be conducting a short 5-minute AI call where you will be asked 5 basic accounting questions to assess your profile.\n\n"
                "You will receive a call from +918035383694 in the next 6 minutes. Please make sure to pick it up.\n\n"
                "Best of luck!\n"
                "JustAccountants Team",
                sender="candidate",
            )
            print(f"[flow] plain text WA sent to {from_phone}, MSG91 response: {result}")
        except Exception as e:
            print(f"[flow] plain text WA failed for {from_phone}: {e}")
            try:
                from services.google_chat_service import send_alert
                await send_alert("AI interview plain text WA failed", str(e), severity="ERROR", context={"phone": from_phone})
            except Exception:
                pass


# Travel-radius rejections caused by missing geocoding reflect a data gap, not a real
# mismatch — ignore them here so we don't auto-decline a candidate over the client's
# missing coordinates right after they submit the form.
_UNGEOCODED_PREFIXES = (
    "Travel radius: client location not geocoded",
    "Travel radius: candidate location not geocoded",
)


async def _check_hard_filters(conn: asyncpg.Connection, client_id, candidate_id) -> tuple[bool, str | None, str | None]:
    """Run the Phase 2 hard-filter gate (filter_service.filter_candidates) for a single
    candidate against a specific client, using the candidate's freshly-saved form data.
    Returns (passed, decline_reason_code, rejection_message)."""
    from services.filter_service import filter_candidates

    client = await conn.fetchrow("SELECT * FROM clients WHERE id = $1", client_id)
    candidate = await conn.fetchrow("SELECT * FROM candidates WHERE id = $1", candidate_id)
    if not client or not candidate:
        return True, None, None

    _, rejected = filter_candidates([dict(candidate)], dict(client))
    rejected = [r for r in rejected if not r["reason"].startswith(_UNGEOCODED_PREFIXES)]
    if not rejected:
        return True, None, None

    reason = rejected[0]["reason"]
    reason_code = "salary_mismatch" if reason.lower().startswith("salary") else "hard_filter_mismatch"
    return False, reason_code, reason


async def decline_for_filter_mismatch(
    conn: asyncpg.Connection, mapping_id: uuid.UUID, candidate_id, client_id, phone: str,
    reason_code: str = "hard_filter_mismatch",
) -> None:
    """Candidate fails a hard filter (salary, travel radius, gender, etc.) for this client —
    decline this mapping, free the candidate's lock, and send a polite ack.
    No AI call is triggered."""
    import services.msg91_service as msg91

    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = 'not_interested'::mapping_stage, decline_reason = $2, updated_at = now()
        WHERE id = $1
        """,
        mapping_id, reason_code,
    )
    await conn.execute(
        """
        UPDATE candidate_locks
        SET unlocked_at = now(), unlock_reason = $3
        WHERE candidate_id = $1 AND client_id = $2 AND unlocked_at IS NULL
        """,
        candidate_id, client_id, reason_code,
    )
    print(f"[flow] candidate {candidate_id} failed hard filter ({reason_code}) for client {client_id} — declined, no AI call")

    cand = await conn.fetchrow("SELECT name FROM candidates WHERE id = $1", candidate_id)
    client = await conn.fetchrow("SELECT company_name FROM clients WHERE id = $1", client_id)
    candidate_name = (cand["name"] if cand else "") or ""
    company_name = (client["company_name"] if client else "") or "this client"

    # Don't reuse CANDIDATE_EVALUATION_FAIL_TEMPLATE — its copy says "Thank you for
    # taking our test!", which is wrong here since no AI evaluation ever ran.
    template = os.environ.get("CANDIDATE_NOT_FIT_CLIENT_TEMPLATE", "").strip()
    try:
        if template:
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": candidate_name},
                {"type": "text", "text": company_name},
            ]}]
            await msg91.send_template(phone, template, "en", components, sender="candidate")
        else:
            await msg91.send_text(
                phone,
                f"Hi {candidate_name or 'there'}, thank you for sharing your details! 🙏\n\n"
                f"After reviewing your profile, we feel you may not be the right fit for {company_name} at this time — "
                f"but we'll keep your profile and reach out as soon as a more suitable opportunity comes up.\n\n"
                f"Best of luck! 😊",
                sender="candidate",
            )
    except Exception as e:
        print(f"[flow] salary_mismatch ack failed for {phone}: {e}")


async def _handle_interested_response(
    conn: asyncpg.Connection, mapping_id: uuid.UUID, from_phone: str
) -> None:
    """
    Called after a candidate replies INTERESTED.
    - null eval  → send form link only (video + AI call triggered after form is submitted)
    - pass       → check client slots: send slot-selection if available, else waiting msg
    - fail       → send polite rejection
    """
    import os
    import services.msg91_service as msg91

    row = await conn.fetchrow(
        """
        SELECT c.id AS candidate_id, c.name, c.evaluation_status, c.form_submitted, c.current_salary,
               ccm.client_id
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        WHERE ccm.id = $1
        """,
        mapping_id,
    )
    if not row:
        return

    eval_status = row["evaluation_status"]

    if eval_status and eval_status.lower() in ("pass", "passed"):
        # Lock the candidate to this client (they're now actively in pipeline)
        await conn.execute(
            """
            INSERT INTO candidate_locks (candidate_id, client_id)
            VALUES ($1, $2)
            ON CONFLICT (candidate_id, client_id)
            DO UPDATE SET locked_at = now(), unlocked_at = NULL, unlock_reason = NULL
            """,
            row["candidate_id"], row["client_id"],
        )
        # Always move to client_approval_pending — RM reviews regardless of slot availability
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'client_approval_pending'::mapping_stage, updated_at = now()
            WHERE id = $1
            """,
            mapping_id,
        )
        from routers.matching import _load_client, _send_waiting_message
        client = await _load_client(row["client_id"], conn)
        await _send_waiting_message(
            mapping_id, row["candidate_id"], from_phone,
            row["name"] or "", client, conn,
        )

    elif eval_status and eval_status.lower() in ("fail", "failed", "reject"):
        await conn.execute(
            """
            UPDATE client_candidate_mappings
            SET stage = 'not_interested'::mapping_stage, decline_reason = 'evaluation_failed', updated_at = now()
            WHERE id = $1
            """,
            mapping_id,
        )
        await conn.execute(
            """
            UPDATE candidate_locks cl
            SET unlocked_at = now(), unlock_reason = 'evaluation_failed'
            FROM client_candidate_mappings ccm
            WHERE ccm.id = $1
              AND cl.candidate_id = ccm.candidate_id
              AND cl.client_id = ccm.client_id
              AND cl.unlocked_at IS NULL
            """,
            mapping_id,
        )
        fail_template = os.environ.get("CANDIDATE_EVALUATION_FAIL_TEMPLATE", "").strip()
        sent = False
        if fail_template:
            try:
                await msg91.send_template(from_phone, fail_template, "en", [], sender="candidate")
                sent = True
            except Exception as e:
                print(f"[flow] fail template '{fail_template}' failed: {e} — falling back to text")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("Evaluation fail template send failed", str(e), severity="ERROR", context={"phone": from_phone, "template": fail_template})
                except Exception:
                    pass
        if not sent:
            await msg91.send_text(
                from_phone,
                "Thank you for your interest! 🙏 Unfortunately, we don't have a suitable opportunity for you at this time. "
                "We'll keep your profile and reach out when something matches. Best of luck! 😊",
                sender="candidate",
            )

    else:
        # evaluation_status is NULL.
        # form_submitted=true → already have their data + they got the video before; skip to AI call.
        # form_submitted=false → scraped/imported candidate: send form link, then video after 10s.
        if row["form_submitted"]:
            ok, reason_code, reason = await _check_hard_filters(conn, row["client_id"], row["candidate_id"])
            if ok:
                try:
                    await post_form_submission_flow(from_phone, row["candidate_id"], row["name"] or "")
                except Exception as e:
                    print(f"[flow] direct AI call trigger failed for {from_phone}: {e}")
                    try:
                        from services.google_chat_service import send_alert
                        await send_alert("Direct AI call trigger failed", str(e), severity="ERROR", context={"phone": from_phone, "cand_id": str(row["candidate_id"])})
                    except Exception:
                        pass
            else:
                print(f"[flow] candidate {row['candidate_id']} failed hard filter for client {row['client_id']}: {reason}")
                await decline_for_filter_mismatch(conn, mapping_id, row["candidate_id"], row["client_id"], from_phone, reason_code)
        else:
            import os
            frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173").strip().rstrip("/")
            form_url = f"{frontend_url}/apply"
            first_name = (row["name"] or "").split()[0] if row["name"] else "there"
            try:
                await msg91.send_text(
                    from_phone,
                    f"Hi,\n\nThanks for your interest in this role!\n\nTo move forward, please fill out this short form with your basic details.\n\n🔗 {form_url}\n\nAbhishek Shah\nJustAccountants",
                    sender="candidate",
                )
                await conn.execute(
                    """
                    UPDATE client_candidate_mappings
                    SET interested_form_sent_at = now(), updated_at = now()
                    WHERE id = $1
                    """,
                    mapping_id,
                )
            except Exception as e:
                print(f"[flow] interested form link failed for {from_phone}: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("Interested form link send failed", str(e), severity="ERROR", context={"phone": from_phone, "mapping_id": str(mapping_id)})
                except Exception:
                    pass
            await asyncio.sleep(10)
            await _send_intro_video(from_phone)


async def _handle_not_interested_role_response(
    conn: asyncpg.Connection, mapping_id: uuid.UUID, from_phone: str
) -> None:
    """
    Candidate is not interested in this specific role but we still want their profile.
    - eval null  → send form link only (video + AI call triggered after form submission)
    - eval pass  → do nothing (already evaluated, no slot since not interested)
    - eval fail  → send polite rejection
    """
    import os
    import services.msg91_service as msg91

    row = await conn.fetchrow(
        """
        SELECT c.name, c.evaluation_status, c.form_submitted, c.current_location,
               cl.company_name
        FROM client_candidate_mappings ccm
        JOIN candidates c ON c.id = ccm.candidate_id
        JOIN clients cl ON cl.id = ccm.client_id
        WHERE ccm.id = $1
        """,
        mapping_id,
    )
    if not row:
        return

    eval_status = row["evaluation_status"]

    if eval_status and eval_status.lower() in ("pass", "passed"):
        candidate_name = (row["name"] or "").split()[0] if row["name"] else "there"
        company_name = row["company_name"] or "this company"
        try:
            await msg91.send_text(
                from_phone,
                f"No worries, {candidate_name}! 😊 We understand {company_name} may not be the right fit. We'll keep your profile and reach out as soon as we find a more suitable opportunity for you. Stay tuned!",
                sender="candidate",
            )
        except Exception as e:
            print(f"[flow] not_interested_role pass ack failed: {e}")
            try:
                from services.google_chat_service import send_alert
                await send_alert("Not-interested-role pass ack failed", str(e), severity="ERROR", context={"phone": from_phone})
            except Exception:
                pass

    elif eval_status and eval_status.lower() in ("fail", "failed", "reject"):
        fail_template = os.environ.get("CANDIDATE_EVALUATION_FAIL_TEMPLATE", "").strip()
        sent = False
        if fail_template:
            try:
                await msg91.send_template(from_phone, fail_template, "en", [], sender="candidate")
                sent = True
            except Exception as e:
                print(f"[flow] not_interested_role fail template failed: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("Not-interested-role fail template send failed", str(e), severity="ERROR", context={"phone": from_phone, "template": fail_template})
                except Exception:
                    pass
        if not sent:
            await msg91.send_text(
                from_phone,
                "Thank you for your interest! 🙏 Unfortunately, we don't have a suitable opportunity for you at this time. "
                "We'll keep your profile and reach out when something matches. Best of luck! 😊",
                sender="candidate",
            )

    else:
        # eval_status is NULL.
        # form_submitted=false → still send the form link (intent=passive) so we can
        # collect their profile for future opportunities, even though they declined
        # this specific role — form submission itself decides AI-call vs. passive
        # ack next (see _process_apply's latest-mapping-stage check).
        # form_submitted=true → we already have their data, just acknowledge.
        if row["form_submitted"]:
            try:
                await msg91.send_text(
                    from_phone,
                    "No worries! 😊 Thanks for letting us know. We'll reach out again when we find another suitable opportunity for you.",
                    sender="candidate",
                )
            except Exception as e:
                print(f"[flow] not_interested_role null-eval ack failed: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("Not-interested-role null-eval ack failed", str(e), severity="ERROR", context={"phone": from_phone})
                except Exception:
                    pass
        else:
            try:
                await _send_onboarding_link(from_phone, "NOT_INTERESTED_ROLE")
            except Exception as e:
                print(f"[flow] not_interested_role form link failed: {e}")
                try:
                    from services.google_chat_service import send_alert
                    await send_alert("Not-interested-role form link send failed", str(e), severity="ERROR", context={"phone": from_phone})
                except Exception:
                    pass
            await asyncio.sleep(10)
            await _send_intro_video(from_phone)


async def post_form_submission_flow(
    phone: str, candidate_id, candidate_name: str
) -> None:
    """
    Triggered from /candidates/apply after a candidate submits the onboarding form
    and their evaluation_status is still null.
    Video was already sent when they tapped Interested/Not-Interested-Role,
    so we go straight to the AI call.
    """
    await _trigger_ai_call_with_notify(None, None, candidate_id, phone, candidate_name)


async def post_form_passive_flow(phone: str, candidate_name: str, location: str = "") -> None:
    """
    Triggered from /candidates/apply when the candidate filled the form after
    declining a specific role (NOT_INTERESTED_ROLE) or came in organically.
    No AI call — acknowledge and confirm we'll share relevant JDs.
    """
    import services.msg91_service as msg91
    first_name = (candidate_name or "").split()[0] if candidate_name else "there"
    # Extract city from Google Maps autocomplete string: "Area, City, State, India"
    raw = location.strip() if location else ""
    if raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        # Drop "India" suffix if present
        if parts and parts[-1].lower() == "india":
            parts = parts[:-1]
        # Now format is typically "Area, City, State" — city is second-to-last
        city = parts[-2] if len(parts) >= 2 else parts[0] if parts else "your city"
    else:
        city = "your city"
    template = os.environ.get("CANDIDATE_PASSIVE_ACK_TEMPLATE", "hl_passive_form_ack").strip()
    portal_url = os.environ.get("CANDIDATE_PORTAL_URL", "https://careers.justaccountants.in").rstrip("/")
    try:
        if template:
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": first_name},
                {"type": "text", "text": city},
            ]}]
            await msg91.send_template(phone, template, "en", components, sender="candidate")
        else:
            await msg91.send_text(
                phone,
                f"Hi {first_name}!\n\nThanks for sharing your details. We'll share all relevant "
                f"opportunities based on your requirements with you from here on.\n\n"
                f"You can also view your profile anytime on our portal:\n{portal_url}",
                sender="candidate",
            )
    except Exception as e:
        print(f"[flow] passive ack send failed for {phone}: {e}")


async def _handle_not_looking_for_job(
    conn: asyncpg.Connection, mapping_id: uuid.UUID, from_phone: str
) -> None:
    """
    Called after a candidate replies NOT_LOOKING. Deactivates the candidate:
    moves all active mappings to not_interested, releases all locks,
    and puts the candidate in DND so they auto re-enter matchmaking later.
    """
    import services.msg91_service as msg91
    import os

    row = await conn.fetchrow(
        "SELECT candidate_id FROM client_candidate_mappings WHERE id = $1", mapping_id
    )
    if not row:
        return
    cand_id = row["candidate_id"]

    # Move all active mappings to not_interested
    await conn.execute(
        """
        UPDATE client_candidate_mappings
        SET stage = 'not_interested'::mapping_stage, decline_reason = 'not_looking_for_job', updated_at = now()
        WHERE candidate_id = $1
          AND stage != 'not_interested'::mapping_stage
          AND stage != 'placed'::mapping_stage
        """,
        cand_id,
    )

    # Release all locks
    await conn.execute(
        """
        UPDATE candidate_locks
        SET unlocked_at = now(), unlock_reason = 'candidate_not_looking'
        WHERE candidate_id = $1 AND unlocked_at IS NULL
        """,
        cand_id,
    )

    # Mark candidate as not looking — 120-day DND, auto re-engages after 4 months
    await conn.execute(
        "UPDATE candidates SET is_active = false, dnd_until = now() + interval '120 days', updated_at = now() WHERE id = $1",
        cand_id,
    )
    await conn.execute(
        "UPDATE candidates SET is_active = false, dnd_until = now() + interval '120 days', updated_at = now() WHERE id = $1",
        cand_id,
    )

    not_looking_template = os.environ.get("CANDIDATE_NOT_LOOKING_TEMPLATE", "").strip()
    sent = False
    if not_looking_template:
        try:
            await msg91.send_template(from_phone, not_looking_template, "en", [], sender="candidate")
            sent = True
        except Exception as e:
            print(f"[flow] not_looking template failed: {e}")
    if not sent:
        await msg91.send_text(
            from_phone,
            "No worries!\n\nIn future if you find yourself looking for a new accounting role, please reach out to us.",
            sender="candidate",
        )



async def _handle_drop_final_interested(
    conn: asyncpg.Connection, mapping_id: uuid.UUID, from_phone: str
) -> None:
    """Candidate re-confirms interest after all drop-off retries — send confirmation + schedule AI call in 5 mins, no retry."""
    import services.msg91_service as msg91
    import services.ringg_service as ringg

    row = await conn.fetchrow(
        "SELECT c.id, c.name, c.phone FROM client_candidate_mappings ccm JOIN candidates c ON c.id = ccm.candidate_id WHERE ccm.id = $1",
        mapping_id,
    )
    if not row:
        return

    first_name = (row["name"] or "").split()[0] if row["name"] else ""
    try:
        await msg91.send_text(
            from_phone,
            f"Hi {first_name},\n\nThanks for confirming.\n\nPlease complete the 5-minute AI technical round. This will be the last chance since we have to move ahead with the face to face interviews.\n\nAbhishek Shah",
            sender="candidate",
        )
    except Exception as e:
        print(f"[flow] drop_final interested msg failed: {e}")

    try:
        await ringg.trigger_ai_call(row["id"], row["phone"] or "", row["name"] or "", retry_on_no_pickup=False)
    except Exception as e:
        print(f"[flow] drop_final AI call trigger failed: {e}")


async def _handle_drop_final_not_interested_role(
    conn: asyncpg.Connection, mapping_id: uuid.UUID, from_phone: str
) -> None:
    import services.msg91_service as msg91
    try:
        await msg91.send_text(
            from_phone,
            "Hi,\n\nNoted. Thanks for informing.",
            sender="candidate",
        )
    except Exception as e:
        print(f"[flow] drop_final not_interested_role msg failed: {e}")


async def _handle_drop_final_not_looking(
    conn: asyncpg.Connection, mapping_id: uuid.UUID, from_phone: str
) -> None:
    import services.msg91_service as msg91
    try:
        await msg91.send_text(
            from_phone,
            "Hi,\n\nNoted. Thanks for informing.",
            sender="candidate",
        )
    except Exception as e:
        print(f"[flow] drop_final not_looking msg failed: {e}")
    await _handle_not_looking_for_job(conn, mapping_id, from_phone)


# ── Flow definitions ──────────────────────────────────────────────────────────
# To add a new step: append an entry here. No other file needs to change.

CANDIDATE_FLOWS: dict[tuple[str, str], FlowStep] = {

    ("intent_ask", "INTERESTED"): FlowStep(
        next_stage="interested",
        extra_updates="intent_received_at = now(),",
        template=None,
        after_hook=_handle_interested_response,
    ),

    # ── Not interested in this role ───────────────────────────────────────────
    ("intent_ask", "NOT_INTERESTED_ROLE"): FlowStep(
        next_stage="not_interested",
        extra_updates="intent_received_at = now(), decline_reason = 'not_interested_role',",
        template=None,
        after_hook=_handle_not_interested_role_response,
    ),

    # ── Drop-final re-confirmation (candidate re-confirms after all retries exhausted) ──
    ("interested", "INTERESTED"): FlowStep(
        next_stage="interested",
        extra_updates="",
        template=None,
        after_hook=_handle_drop_final_interested,
    ),
    ("interested", "NOT_INTERESTED_ROLE"): FlowStep(
        next_stage="not_interested",
        extra_updates="decline_reason = 'not_interested_role',",
        template=None,
        after_hook=_handle_drop_final_not_interested_role,
    ),
    ("interested", "NOT_LOOKING_FOR_JOB"): FlowStep(
        next_stage="not_interested",
        extra_updates="decline_reason = 'not_looking_for_job',",
        template=None,
        after_hook=_handle_drop_final_not_looking,
    ),

    # ── Not looking for a job at all — deactivate candidate ───────────────────
    ("intent_ask", "NOT_LOOKING_FOR_JOB"): FlowStep(
        next_stage="not_interested",
        extra_updates="intent_received_at = now(), decline_reason = 'not_looking_for_job',",
        template=None,
        after_hook=_handle_not_looking_for_job,
    ),

    # ── Slot selection (add when template is ready in MSG91) ──────────────────
    # ("interested", "SLOT_1"): FlowStep(
    #     next_stage="slot_booked",
    #     extra_updates="slot_sent_at = now(),",
    #     template="candidate_interview_confirmation",
    #     build_components=_build_slot_confirmation_components,
    # ),
    # ("interested", "SLOT_2"): FlowStep(
    #     next_stage="slot_booked",
    #     extra_updates="slot_sent_at = now(),",
    #     template="candidate_interview_confirmation",
    #     build_components=_build_slot_confirmation_components,
    # ),
    # ("interested", "SLOT_3"): FlowStep(
    #     next_stage="slot_booked",
    #     extra_updates="slot_sent_at = now(),",
    #     template="candidate_interview_confirmation",
    #     build_components=_build_slot_confirmation_components,
    # ),

}


# ── Pool health check after reply ─────────────────────────────────────────────

async def _check_client_pool_after_reply(conn: asyncpg.Connection, mapping_id: uuid.UUID) -> None:
    """
    Run after every intent_ask reply.
    If everyone has now replied AND fewer than 5 are interested → flag sourcing needed.
    If everyone has replied AND 5+ are interested → clear the flag.
    If anyone is still pending → do nothing (too early to judge).
    """
    row = await conn.fetchrow(
        "SELECT client_id FROM client_candidate_mappings WHERE id = $1", mapping_id
    )
    if not row:
        return
    client_id = row["client_id"]

    stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE stage = 'interested'::mapping_stage)                             AS interested_count,
            COUNT(*) FILTER (WHERE stage IN ('wa_sent'::mapping_stage, 'intent_ask'::mapping_stage)) AS pending_count
        FROM client_candidate_mappings
        WHERE client_id = $1
        """,
        client_id,
    )
    interested = int(stats["interested_count"])
    pending    = int(stats["pending_count"])

    print(f"[flow] pool check for client {client_id}: {interested} interested, {pending} still pending reply")

    if pending > 0:
        return  # others haven't replied yet — check again on their response

    sourcing_needed = interested < 5
    await conn.execute(
        """
        UPDATE clients
        SET sourcing_needed = $2,
            labels = CASE
                WHEN $2 THEN array_append(array_remove(COALESCE(labels, '{}'), 'Sourcing Needed'), 'Sourcing Needed')
                ELSE array_remove(COALESCE(labels, '{}'), 'Sourcing Needed')
            END,
            updated_at = now()
        WHERE id = $1
        """,
        client_id, sourcing_needed,
    )
    print(f"[flow] client {client_id}: sourcing_needed={sourcing_needed} ({interested} interested, all replied)")


# ── Engine ────────────────────────────────────────────────────────────────────

async def execute_candidate_flow(
    conn: asyncpg.Connection,
    mapping_id: uuid.UUID,
    current_stage: str,
    trigger: str,
    from_phone: str = "",
) -> bool:
    """
    Look up the next step for (current_stage, trigger) and execute it:
    1. Update mapping stage in DB (guarded by current_stage to prevent races).
    2. Send the next template via MSG91 if configured.

    Returns True if a matching step was found and executed.
    """
    trigger_key = trigger.strip().upper().replace(" ", "_")
    step = CANDIDATE_FLOWS.get((current_stage, trigger_key))
    if not step:
        return False

    updated = await conn.execute(
        f"""
        UPDATE client_candidate_mappings
        SET stage = $1::mapping_stage, {step.extra_updates} updated_at = now()
        WHERE id = $2 AND stage = $3::mapping_stage
        """,
        step.next_stage, mapping_id, current_stage,
    )
    # "UPDATE 0" means another webhook already processed this — skip send
    if updated == "UPDATE 0":
        return True

    if step.template and from_phone:
        import services.msg91_service as msg91
        components: list[dict] = []
        if step.build_components:
            components = await step.build_components(conn, mapping_id)
        await msg91.send_template(from_phone, step.template, "en", components, sender=step.sender)

    if step.next_stage in ("not_interested", "rejected"):
        await conn.execute(
            """
            UPDATE candidate_locks cl
            SET unlocked_at = now(), unlock_reason = $2
            FROM client_candidate_mappings ccm
            WHERE ccm.id = $1
              AND cl.candidate_id = ccm.candidate_id
              AND cl.client_id = ccm.client_id
              AND cl.unlocked_at IS NULL
            """,
            mapping_id, step.next_stage,
        )

    if step.after_hook:
        # Resolve phone from DB if caller didn't provide one
        if not from_phone:
            row = await conn.fetchrow(
                "SELECT c.phone FROM client_candidate_mappings ccm JOIN candidates c ON c.id = ccm.candidate_id WHERE ccm.id = $1",
                mapping_id,
            )
            if row and row["phone"]:
                import services.msg91_service as _msg91
                from_phone = _msg91._format_phone(row["phone"])
        try:
            await step.after_hook(conn, mapping_id, from_phone)
        except Exception as e:
            print(f"[flow] after_hook error for mapping {mapping_id}: {e}")
            try:
                from services.google_chat_service import send_alert
                await send_alert("Flow after_hook failed", str(e), severity="ERROR", context={"mapping_id": str(mapping_id), "stage": current_stage, "trigger": trigger})
            except Exception:
                pass

    # After any intent_ask reply, check if the client pool needs sourcing
    if current_stage == "intent_ask":
        try:
            await _check_client_pool_after_reply(conn, mapping_id)
        except Exception as e:
            print(f"[flow] pool check error for mapping {mapping_id}: {e}")
            try:
                from services.google_chat_service import send_alert
                await send_alert("Client pool health check failed", str(e), severity="ERROR", context={"mapping_id": str(mapping_id)})
            except Exception:
                pass

    if step.next_stage == "interested":
        try:
            client_row = await conn.fetchrow(
                "SELECT client_id FROM client_candidate_mappings WHERE id = $1", mapping_id
            )
            if client_row:
                await notify_client_pending_review(client_row["client_id"], conn)
        except Exception as e:
            print(f"[flow] client pending-review notify error for mapping {mapping_id}: {e}")

    return True


def client_portal_deep_link(client_id: uuid.UUID, tab: str = "overview") -> str:
    """Build the URL-button suffix for a client-portal deep link.

    Used as the dynamic {{1}} parameter on a WhatsApp URL-button template (see
    hl_client_pending_review_v4 for the pattern: a template whose button URL is
    "<CLIENT_PORTAL_URL>/{{1}}"). `tab` must match a client-portal route —
    overview, review, interviews, feedback, or offer.

    Reuse this for every future client-facing notification so the button always
    lands the client on the exact page the message is about (e.g. a "candidates
    pending review" ping opens Candidate Review directly, not just the portal
    root) rather than making them navigate there themselves.
    """
    return f"requirement/{client_id}/{tab}"


async def notify_client_pending_review(client_id: uuid.UUID, conn: asyncpg.Connection) -> None:
    """Ping the client's POC on WhatsApp when a fresh 'interested' reply comes in,
    telling them how many candidates are currently awaiting their review, with a
    link into the client portal. Skips silently if there's nothing pending yet
    (this candidate still needs to pass evaluation before landing in review)."""
    try:
        client = await conn.fetchrow(
            "SELECT poc_name, poc_phone, job_title FROM clients WHERE id = $1", client_id
        )
        if not client or not client["poc_phone"]:
            return

        pending = await conn.fetchval(
            """
            SELECT COUNT(*) FROM client_candidate_mappings
            WHERE client_id = $1 AND stage = 'client_approval_pending'::mapping_stage
            """,
            client_id,
        ) or 0
        if pending < 1:
            return

        portal_url = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")
        poc_name = client["poc_name"] or "there"
        role = client["job_title"] or "your role"
        plural = "s" if pending != 1 else ""
        verb = "are" if pending != 1 else "is"

        import services.msg91_service as msg91
        # hl_client_pending_review_v4 (UTILITY, business-initiated — see feedback memory
        # on the template rule): body takes name/count/role; the portal link is a
        # dynamic URL button (client_portal_deep_link) that opens Candidate Review
        # directly for this requirement, not just the portal root.
        template = os.environ.get("CLIENT_PENDING_REVIEW_TEMPLATE", "hl_client_pending_review_v4").strip()
        if template:
            components = [
                {"type": "body", "parameters": [
                    {"type": "text", "text": poc_name},
                    {"type": "text", "text": str(pending)},
                    {"type": "text", "text": role},
                ]},
                {"type": "button", "sub_type": "url", "index": "0", "parameters": [
                    {"type": "text", "text": client_portal_deep_link(client_id, "review")},
                ]},
            ]
            await msg91.send_template(client["poc_phone"], template, "en", components, sender="client")
        else:
            await msg91.send_text(
                client["poc_phone"],
                f"Hi {poc_name}, {pending} candidate{plural} for {role} {verb} pending your review.\n\n"
                f"Review now: {portal_url}/{client_portal_deep_link(client_id, 'review')}",
                sender="client",
            )
    except Exception as e:
        print(f"[notify_client_pending_review] failed for {client_id}: {e}")


def candidate_portal_deep_link(mapping_id: uuid.UUID, view: str = "documents") -> str:
    """Build the URL-button suffix for a candidate-portal deep link.

    Candidate-portal is a single-session app (no id in the path) — the deep
    link is a query string appended to the root: "?view=documents&mapping=<id>".
    Used as the dynamic {{1}} parameter on a WhatsApp URL-button template,
    mirroring client_portal_deep_link's role on the client side.
    """
    return f"?view={view}&mapping={mapping_id}"


async def notify_client_update(
    client_id: uuid.UUID, message: str, tab: str, conn: asyncpg.Connection
) -> None:
    """Generic reminder-only ping to a client's POC with a deep link into the
    portal — used for offer accepted/declined/expired and documents-received
    updates. Never sends the decision-making UI over WhatsApp; the message
    only says something happened and where to look."""
    try:
        client = await conn.fetchrow("SELECT poc_name, poc_phone FROM clients WHERE id = $1", client_id)
        if not client or not client["poc_phone"]:
            return
        poc_name = client["poc_name"] or "there"

        import services.msg91_service as msg91
        # v1 required 3 non-empty body params (name/subject/role) but this
        # function only ever has a name + a free-text message — sending "" for
        # the 3rd param gets silently rejected by Meta at delivery time (MSG91's
        # API still returns 200 for the queueing call, so the failure was
        # invisible). v2 is a 2-param "Hi {name}, {message}" template built to
        # match how this function actually gets called.
        template = os.environ.get("CLIENT_OFFER_UPDATE_TEMPLATE", "hl_client_offer_update_v2").strip()
        portal_url = os.environ.get("CLIENT_PORTAL_URL", "https://employer.justaccountants.in").rstrip("/")
        if template:
            components = [
                {"type": "body", "parameters": [
                    {"type": "text", "text": poc_name},
                    {"type": "text", "text": message},
                ]},
                {"type": "button", "sub_type": "url", "index": "0", "parameters": [
                    {"type": "text", "text": client_portal_deep_link(client_id, tab)},
                ]},
            ]
            await msg91.send_template(client["poc_phone"], template, "en", components, sender="client")
        else:
            await msg91.send_text(
                client["poc_phone"],
                f"Hi {poc_name}, {message}\n\nView in your portal: {portal_url}/{client_portal_deep_link(client_id, tab)}",
                sender="client",
            )
    except Exception as e:
        print(f"[notify_client_update] failed for {client_id}: {e}")


async def notify_rm_alert(client_id: uuid.UUID, message: str, conn: asyncpg.Connection) -> None:
    """Ping the client's assigned RM directly on WhatsApp for something that
    needs a human follow-up call (documents ready to close, an 'other reason'
    cancellation). Falls back to the internal Google Chat ops alert if no RM
    is assigned or the WhatsApp send fails — never fails silently."""
    client = await conn.fetchrow(
        "SELECT company_name, job_title, rm_id FROM clients WHERE id = $1", client_id
    )
    if not client:
        return
    context = {"client_id": str(client_id), "company": client["company_name"], "message": message}

    rm = None
    if client["rm_id"]:
        rm = await conn.fetchrow("SELECT name, phone FROM rms WHERE id = $1", client["rm_id"])

    if not rm or not rm["phone"]:
        try:
            from services.google_chat_service import send_alert
            await send_alert("RM alert — no RM assigned", message, severity="WARNING", context=context)
        except Exception:
            pass
        return

    try:
        import services.msg91_service as msg91
        template = os.environ.get("RM_ALERT_TEMPLATE", "hl_rm_client_alert_v1").strip()
        role_label = f"{client['company_name']} ({client['job_title']})" if client["job_title"] else client["company_name"]
        if template:
            components = [{"type": "body", "parameters": [
                {"type": "text", "text": rm["name"] or "there"},
                {"type": "text", "text": role_label or ""},
                {"type": "text", "text": message},
            ]}]
            await msg91.send_template(rm["phone"], template, "en", components, sender="client")
        else:
            await msg91.send_text(rm["phone"], f"Hi {rm['name'] or 'there'}, {role_label}: {message}", sender="client")
    except Exception as e:
        print(f"[notify_rm_alert] WhatsApp to RM failed for {client_id}: {e}")
        try:
            from services.google_chat_service import send_alert
            await send_alert("RM alert — WhatsApp send failed", f"{message}\n\nError: {e}", severity="ERROR", context=context)
        except Exception:
            pass
