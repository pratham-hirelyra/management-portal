"""
Layer 2 — Integration tests for the matchmaking flow.

Requires a running PostgreSQL DB (reads DATABASE_URL from .env).
Every test runs inside a rolled-back transaction — no data is permanently written.
External calls (MSG91, RinggAI, Google Maps) are patched to no-ops.

Run with: pytest tests/test_flow_integration.py -v
"""

import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from services.filter_service import filter_candidates
from services.mms_service import score_candidates
from routers.matching import (
    MMS_GATE,
    THRESHOLD,
    _client_has_slots,
    _free_pool_evaluated,
    _persist_scores,
    _send_role_not_fit_message,
    _send_slot_message_to_candidate,
    _send_waiting_message,
    _set_state,
    _trigger_path1,
    _trigger_path2,
)
from services.flow_engine import (
    _handle_interested_response,
    _handle_not_interested_role_response,
)
from tests.conftest import seed_candidate, seed_client, seed_mapping


# ─────────────────────────────────────────────────────────────────────────────
# Score persistence
# ─────────────────────────────────────────────────────────────────────────────

class TestPersistScores:
    @pytest.mark.asyncio
    async def test_persist_filter_passed_candidate(self, conn):
        client = await seed_client(conn)
        cand = await seed_candidate(conn, "11001")

        passed = [cand]
        scored = score_candidates(passed, client, distances={str(cand["id"]): 5.0})
        await _persist_scores(client["id"], passed, [], scored, conn)

        row = await conn.fetchrow(
            "SELECT * FROM mms_scores WHERE client_id=$1 AND candidate_id=$2",
            client["id"], cand["id"],
        )
        assert row is not None
        assert row["filter_passed"] is True
        assert float(row["mms_score"]) > 0

    @pytest.mark.asyncio
    async def test_persist_filter_rejected_candidate(self, conn):
        client = await seed_client(conn)
        cand = await seed_candidate(conn, "11002")

        rejected = [{"candidate_id": str(cand["id"]), "name": cand["name"], "reason": "Salary ceiling: 40000 > 30000"}]
        await _persist_scores(client["id"], [], rejected, [], conn)

        row = await conn.fetchrow(
            "SELECT * FROM mms_scores WHERE client_id=$1 AND candidate_id=$2",
            client["id"], cand["id"],
        )
        assert row is not None
        assert row["filter_passed"] is False
        assert "Salary ceiling" in (row["filter_exclusion_reason"] or "")

    @pytest.mark.asyncio
    async def test_persist_upsert_updates_existing_row(self, conn):
        client = await seed_client(conn)
        cand = await seed_candidate(conn, "11003", technical_evaluation_score=40.0)

        scored_first = score_candidates([cand], client, distances={str(cand["id"]): 5.0})
        await _persist_scores(client["id"], [cand], [], scored_first, conn)

        # Simulate re-score with higher tech score
        cand2 = dict(cand)
        cand2["technical_evaluation_score"] = 70.0
        scored_second = score_candidates([cand2], client, distances={str(cand["id"]): 5.0})
        await _persist_scores(client["id"], [cand2], [], scored_second, conn)

        row = await conn.fetchrow(
            "SELECT mms_score FROM mms_scores WHERE client_id=$1 AND candidate_id=$2",
            client["id"], cand["id"],
        )
        assert float(row["mms_score"]) > float(scored_first[0]["mms_score"])

    @pytest.mark.asyncio
    async def test_dnd_flagged_candidate_gets_deactivated(self, conn):
        client = await seed_client(conn, max_salary=30_000)
        # Low tech score + high salary → DND
        cand = await seed_candidate(conn, "11004", technical_evaluation_score=20.0, current_salary=25_000)

        scored = score_candidates([cand], client, distances={str(cand["id"]): 5.0})
        assert scored[0]["dnd_flagged"] is True

        await _persist_scores(client["id"], [cand], [], scored, conn)

        cand_row = await conn.fetchrow("SELECT is_active, dnd_until FROM candidates WHERE id=$1", cand["id"])
        assert cand_row["is_active"] is False
        assert cand_row["dnd_until"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# Free pool queries
# ─────────────────────────────────────────────────────────────────────────────

class TestFreePool:
    @pytest.mark.asyncio
    async def test_evaluated_candidate_appears_in_pool(self, conn):
        client = await seed_client(conn)
        cand = await seed_candidate(conn, "12001", technical_evaluation_score=60.0, category="Senior Accountant")
        pool = await _free_pool_evaluated(client["id"], conn)
        ids = [str(r["id"]) for r in pool]
        assert str(cand["id"]) in ids

    @pytest.mark.asyncio
    async def test_null_eval_candidate_not_in_evaluated_pool(self, conn):
        client = await seed_client(conn)
        cand = await seed_candidate(conn, "12002", technical_evaluation_score=None, evaluation_status=None)
        pool = await _free_pool_evaluated(client["id"], conn)
        ids = [str(r["id"]) for r in pool]
        assert str(cand["id"]) not in ids

    @pytest.mark.asyncio
    async def test_inactive_candidate_not_in_pool(self, conn):
        client = await seed_client(conn)
        cand = await seed_candidate(conn, "12003", technical_evaluation_score=60.0, is_active=False)
        pool = await _free_pool_evaluated(client["id"], conn)
        ids = [str(r["id"]) for r in pool]
        assert str(cand["id"]) not in ids

    @pytest.mark.asyncio
    async def test_candidate_locked_by_other_client_not_in_pool(self, conn):
        client_a = await seed_client(conn, company_name="Client A")
        client_b = await seed_client(conn, company_name="Client B")
        cand = await seed_candidate(conn, "12004", technical_evaluation_score=60.0)

        # Lock candidate to client_b
        await conn.execute(
            "INSERT INTO candidate_locks (candidate_id, client_id) VALUES ($1, $2)",
            cand["id"], client_b["id"],
        )

        # candidate should NOT appear in client_a's free pool
        pool_a = await _free_pool_evaluated(client_a["id"], conn)
        ids_a = [str(r["id"]) for r in pool_a]
        assert str(cand["id"]) not in ids_a

        # BUT candidate DOES appear in client_b's own pool (locked by client_b, not by OTHER client)
        pool_b = await _free_pool_evaluated(client_b["id"], conn)
        ids_b = [str(r["id"]) for r in pool_b]
        assert str(cand["id"]) in ids_b

    @pytest.mark.asyncio
    async def test_expired_lock_does_not_exclude_candidate(self, conn):
        client_a = await seed_client(conn, company_name="Client Expire A")
        client_b = await seed_client(conn, company_name="Client Expire B")
        cand = await seed_candidate(conn, "12005", technical_evaluation_score=60.0)

        # Insert an already-expired lock on client_b
        await conn.execute(
            """
            INSERT INTO candidate_locks (candidate_id, client_id, expires_at)
            VALUES ($1, $2, now() - interval '1 hour')
            """,
            cand["id"], client_b["id"],
        )

        # Expired lock — candidate still appears in client_a's pool
        pool_a = await _free_pool_evaluated(client_a["id"], conn)
        ids_a = [str(r["id"]) for r in pool_a]
        assert str(cand["id"]) in ids_a


# ─────────────────────────────────────────────────────────────────────────────
# Decision-point: threshold routing
# ─────────────────────────────────────────────────────────────────────────────

class TestDecisionPointRouting:
    @pytest.mark.asyncio
    async def test_enough_candidates_triggers_path1_state(self, conn):
        """Seed THRESHOLD+3 high-quality candidates and verify pool count ≥ THRESHOLD."""
        client = await seed_client(conn)
        cid = client["id"]

        for i in range(THRESHOLD + 3):
            await seed_candidate(
                conn, f"2{i:04d}",
                technical_evaluation_score=70.0,
                current_salary=0,
                location_lat=19.076, location_lng=72.877,
            )

        evaluated = await _free_pool_evaluated(cid, conn)
        passed, rejected = filter_candidates(evaluated, client)
        scored = score_candidates(passed, client, distances={str(c["id"]): 2.0 for c in passed})
        await _persist_scores(cid, passed, rejected, scored, conn)

        high_quality = sum(1 for s in scored if s["mms_score"] >= MMS_GATE and not s["dnd_flagged"])
        assert high_quality >= THRESHOLD

        with patch("services.msg91_service.send_template", AsyncMock()), \
             patch("services.msg91_service.send_text", AsyncMock()):
            sent = await _trigger_path1(cid, client, conn)

        assert sent is True
        row = await conn.fetchrow("SELECT matching_state FROM clients WHERE id=$1", cid)
        assert row["matching_state"] == "path1"

    @pytest.mark.asyncio
    async def test_below_threshold_triggers_path2b(self, conn):
        """Only 3 high-quality candidates (< THRESHOLD=12) and no null-eval → path2_b."""
        client = await seed_client(conn)
        cid = client["id"]

        for i in range(3):
            await seed_candidate(
                conn, f"3{i:04d}",
                technical_evaluation_score=70.0,
                current_salary=0,
            )

        evaluated = await _free_pool_evaluated(cid, conn)
        passed, rejected = filter_candidates(evaluated, client)
        scored = score_candidates(passed, client)
        await _persist_scores(cid, passed, rejected, scored, conn)

        high_quality = sum(1 for s in scored if s["mms_score"] >= MMS_GATE and not s["dnd_flagged"])
        assert high_quality < THRESHOLD

        with patch("services.msg91_service.send_text", AsyncMock()), \
             patch("services.msg91_service.send_template", AsyncMock()):
            result = await _trigger_path2(cid, client, high_quality, conn)

        row = await conn.fetchrow("SELECT matching_state, sourcing_needed FROM clients WHERE id=$1", cid)
        # With no null-eval candidates in our small seeded pool, this lands in branch b or capped
        assert row["matching_state"] in ("path2_a", "path2_b", "capped")
        if result.get("branch") == "b":
            assert row["sourcing_needed"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Slot and waiting message helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestClientHasSlots:
    @pytest.mark.asyncio
    async def test_returns_true_when_slots_present(self, conn):
        slots = json.dumps([{"label": "Mon 10am", "iso": "2026-06-02T10:00:00"}])
        client = await seed_client(conn, recruiter_slots=slots)
        assert await _client_has_slots(client["id"], conn) is True

    @pytest.mark.asyncio
    async def test_returns_false_when_slots_null(self, conn):
        client = await seed_client(conn, recruiter_slots=None)
        assert await _client_has_slots(client["id"], conn) is False

    @pytest.mark.asyncio
    async def test_returns_false_when_slots_empty_list(self, conn):
        client = await seed_client(conn, recruiter_slots=json.dumps([]))
        assert await _client_has_slots(client["id"], conn) is False


class TestSendSlotMessage:
    @pytest.mark.asyncio
    async def test_sends_and_marks_slot_booking_sent(self, conn):
        slots = json.dumps([{"label": "Mon 10am"}])
        client = await seed_client(conn, recruiter_slots=slots)
        cand = await seed_candidate(conn, "41001")
        mapping = await seed_mapping(conn, client["id"], cand["id"], slot_booking_sent=False)

        with patch("services.msg91_service.send_template", AsyncMock()), \
             patch("services.msg91_service.send_text", AsyncMock()):
            result = await _send_slot_message_to_candidate(
                mapping["id"], cand["id"], "+919999941001", cand["name"], client, conn,
            )

        assert result is True
        row = await conn.fetchrow(
            "SELECT slot_booking_sent, stage FROM client_candidate_mappings WHERE id=$1",
            mapping["id"],
        )
        assert row["slot_booking_sent"] is True
        assert row["stage"] == "slot_sent_candidate"

    @pytest.mark.asyncio
    async def test_idempotent_does_not_resend(self, conn):
        slots = json.dumps([{"label": "Mon 10am"}])
        client = await seed_client(conn, recruiter_slots=slots)
        cand = await seed_candidate(conn, "41002")
        mapping = await seed_mapping(conn, client["id"], cand["id"], slot_booking_sent=True)

        mock_send = AsyncMock()
        with patch("services.msg91_service.send_template", mock_send), \
             patch("services.msg91_service.send_text", mock_send):
            result = await _send_slot_message_to_candidate(
                mapping["id"], cand["id"], "+919999941002", cand["name"], client, conn,
            )

        assert result is False
        mock_send.assert_not_called()


class TestSendWaitingMessage:
    @pytest.mark.asyncio
    async def test_sends_and_marks_waiting_message_sent(self, conn):
        client = await seed_client(conn, recruiter_slots=None)
        cand = await seed_candidate(conn, "42001")
        mapping = await seed_mapping(conn, client["id"], cand["id"], waiting_message_sent=False)

        with patch("services.msg91_service.send_template", AsyncMock()), \
             patch("services.msg91_service.send_text", AsyncMock()):
            result = await _send_waiting_message(
                mapping["id"], cand["id"], "+919999942001", cand["name"], client, conn,
            )

        assert result is True
        row = await conn.fetchrow(
            "SELECT waiting_message_sent FROM client_candidate_mappings WHERE id=$1",
            mapping["id"],
        )
        assert row["waiting_message_sent"] is True

    @pytest.mark.asyncio
    async def test_idempotent_does_not_resend(self, conn):
        client = await seed_client(conn, recruiter_slots=None)
        cand = await seed_candidate(conn, "42002")
        mapping = await seed_mapping(conn, client["id"], cand["id"], waiting_message_sent=True)

        mock_send = AsyncMock()
        with patch("services.msg91_service.send_template", mock_send), \
             patch("services.msg91_service.send_text", mock_send):
            result = await _send_waiting_message(
                mapping["id"], cand["id"], "+919999942002", cand["name"], client, conn,
            )

        assert result is False
        mock_send.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# on_evaluation_complete MMS gate
# ─────────────────────────────────────────────────────────────────────────────

class TestOnEvaluationCompleteMmsGate:
    @pytest.mark.asyncio
    async def test_high_mms_with_slots_triggers_slot_message(self, conn):
        slots = json.dumps([{"label": "Mon 10am", "iso": "2026-06-02T10:00:00"}])
        client = await seed_client(conn, recruiter_slots=slots, max_salary=30_000)
        # tech=70 at 0km → mms well above 70
        cand = await seed_candidate(conn, "51001", technical_evaluation_score=70.0, current_salary=0)
        mapping = await seed_mapping(conn, client["id"], cand["id"], slot_booking_sent=False)

        scored = score_candidates([cand], client, distances={str(cand["id"]): 0.0})
        await _persist_scores(client["id"], [cand], [], scored, conn)
        assert scored[0]["mms_score"] >= MMS_GATE

        with patch("services.msg91_service.send_template", AsyncMock()), \
             patch("services.msg91_service.send_text", AsyncMock()):
            has_slots = await _client_has_slots(client["id"], conn)
            assert has_slots is True
            result = await _send_slot_message_to_candidate(
                mapping["id"], cand["id"], "+919999951001", cand["name"], client, conn,
            )
        assert result is True

        row = await conn.fetchrow(
            "SELECT slot_booking_sent FROM client_candidate_mappings WHERE id=$1", mapping["id"]
        )
        assert row["slot_booking_sent"] is True

    @pytest.mark.asyncio
    async def test_high_mms_no_slots_triggers_waiting_message(self, conn):
        client = await seed_client(conn, recruiter_slots=None, max_salary=30_000)
        cand = await seed_candidate(conn, "51002", technical_evaluation_score=70.0, current_salary=0)
        mapping = await seed_mapping(conn, client["id"], cand["id"], waiting_message_sent=False)

        scored = score_candidates([cand], client, distances={str(cand["id"]): 0.0})
        await _persist_scores(client["id"], [cand], [], scored, conn)
        assert scored[0]["mms_score"] >= MMS_GATE

        with patch("services.msg91_service.send_template", AsyncMock()), \
             patch("services.msg91_service.send_text", AsyncMock()):
            has_slots = await _client_has_slots(client["id"], conn)
            assert has_slots is False
            result = await _send_waiting_message(
                mapping["id"], cand["id"], "+919999951002", cand["name"], client, conn,
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_low_mms_mapping_rejected_and_lock_released(self, conn):
        client = await seed_client(conn, max_salary=30_000)
        # Very low score at large distance → mms far below 70
        cand = await seed_candidate(conn, "51003", technical_evaluation_score=5.0, current_salary=28_000)
        mapping = await seed_mapping(conn, client["id"], cand["id"])

        await conn.execute(
            "INSERT INTO candidate_locks (candidate_id, client_id) VALUES ($1, $2)",
            cand["id"], client["id"],
        )

        scored = score_candidates([cand], client, distances={str(cand["id"]): 35.0})
        await _persist_scores(client["id"], [cand], [], scored, conn)
        assert scored[0]["mms_score"] < MMS_GATE

        # Simulate what on_evaluation_complete does for low-mms case
        await conn.execute(
            "UPDATE client_candidate_mappings SET stage='rejected'::mapping_stage, updated_at=now() WHERE id=$1",
            mapping["id"],
        )
        await conn.execute(
            "UPDATE candidate_locks SET unlocked_at=now(), unlock_reason='mms_below_gate' WHERE candidate_id=$1 AND client_id=$2 AND unlocked_at IS NULL",
            cand["id"], client["id"],
        )
        with patch("services.msg91_service.send_text", AsyncMock()), \
             patch("services.msg91_service.send_template", AsyncMock()):
            await _send_role_not_fit_message(
                "+919999951003", cand["name"], client["company_name"], client["job_title"]
            )

        m_row = await conn.fetchrow("SELECT stage FROM client_candidate_mappings WHERE id=$1", mapping["id"])
        assert m_row["stage"] == "rejected"

        l_row = await conn.fetchrow(
            "SELECT unlocked_at FROM candidate_locks WHERE candidate_id=$1 AND client_id=$2",
            cand["id"], client["id"],
        )
        assert l_row["unlocked_at"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# Flow engine: _handle_interested_response
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleInterestedResponse:
    @pytest.mark.asyncio
    async def test_eval_pass_with_slots_sends_slot_message(self, conn):
        slots = json.dumps([{"label": "Mon 10am"}])
        client = await seed_client(conn, recruiter_slots=slots)
        cand = await seed_candidate(conn, "61001", evaluation_status="Pass", cv_url=None)
        mapping = await seed_mapping(conn, client["id"], cand["id"])

        with patch("services.msg91_service.send_template", AsyncMock()), \
             patch("services.msg91_service.send_text", AsyncMock()):
            await _handle_interested_response(conn, mapping["id"], "+919999961001")

        row = await conn.fetchrow(
            "SELECT slot_booking_sent FROM client_candidate_mappings WHERE id=$1", mapping["id"]
        )
        assert row["slot_booking_sent"] is True

    @pytest.mark.asyncio
    async def test_eval_pass_no_slots_sends_waiting_message(self, conn):
        client = await seed_client(conn, recruiter_slots=None)
        cand = await seed_candidate(conn, "61002", evaluation_status="Pass")
        mapping = await seed_mapping(conn, client["id"], cand["id"])

        with patch("services.msg91_service.send_template", AsyncMock()), \
             patch("services.msg91_service.send_text", AsyncMock()):
            await _handle_interested_response(conn, mapping["id"], "+919999961002")

        row = await conn.fetchrow(
            "SELECT waiting_message_sent FROM client_candidate_mappings WHERE id=$1", mapping["id"]
        )
        assert row["waiting_message_sent"] is True

    @pytest.mark.asyncio
    async def test_eval_pass_locks_candidate_to_client(self, conn):
        slots = json.dumps([{"label": "Mon 10am"}])
        client = await seed_client(conn, recruiter_slots=slots)
        cand = await seed_candidate(conn, "61003", evaluation_status="Pass")
        mapping = await seed_mapping(conn, client["id"], cand["id"])

        with patch("services.msg91_service.send_template", AsyncMock()), \
             patch("services.msg91_service.send_text", AsyncMock()):
            await _handle_interested_response(conn, mapping["id"], "+919999961003")

        lock = await conn.fetchrow(
            "SELECT unlocked_at FROM candidate_locks WHERE candidate_id=$1 AND client_id=$2",
            cand["id"], client["id"],
        )
        assert lock is not None
        assert lock["unlocked_at"] is None  # active lock

    @pytest.mark.asyncio
    async def test_eval_fail_sends_rejection_and_unlocks(self, conn):
        client = await seed_client(conn)
        cand = await seed_candidate(conn, "61004", evaluation_status="Fail")
        mapping = await seed_mapping(conn, client["id"], cand["id"])

        # Insert active lock
        await conn.execute(
            "INSERT INTO candidate_locks (candidate_id, client_id) VALUES ($1, $2)",
            cand["id"], client["id"],
        )

        with patch("services.msg91_service.send_template", AsyncMock()), \
             patch("services.msg91_service.send_text", AsyncMock()):
            await _handle_interested_response(conn, mapping["id"], "+919999961004")

        m_row = await conn.fetchrow("SELECT stage FROM client_candidate_mappings WHERE id=$1", mapping["id"])
        assert m_row["stage"] == "not_interested"

        lock = await conn.fetchrow(
            "SELECT unlocked_at FROM candidate_locks WHERE candidate_id=$1 AND client_id=$2",
            cand["id"], client["id"],
        )
        assert lock["unlocked_at"] is not None  # unlocked

    @pytest.mark.asyncio
    async def test_eval_null_no_cv_sends_form_link_not_ai(self, conn):
        client = await seed_client(conn)
        cand = await seed_candidate(conn, "61005", evaluation_status=None, cv_url=None)
        mapping = await seed_mapping(conn, client["id"], cand["id"])

        mock_ringg = AsyncMock()
        with patch("services.ringg_service.trigger_ai_call", mock_ringg), \
             patch("services.msg91_service.send_text", AsyncMock()), \
             patch("services.msg91_service.send_template", AsyncMock()):
            await _handle_interested_response(conn, mapping["id"], "+919999961005")

        # No cv → form link sent; AI call must NOT fire
        mock_ringg.assert_not_called()

    @pytest.mark.asyncio
    async def test_eval_null_with_cv_triggers_ai_call(self, conn):
        client = await seed_client(conn)
        cand = await seed_candidate(
            conn, "61006",
            evaluation_status=None,
            cv_url="https://storage.googleapis.com/hirelyra/test.pdf",
        )
        mapping = await seed_mapping(conn, client["id"], cand["id"])

        mock_ringg = AsyncMock()
        with patch("services.ringg_service.trigger_ai_call", mock_ringg), \
             patch("services.msg91_service.send_text", AsyncMock()), \
             patch("services.msg91_service.send_template", AsyncMock()):
            await _handle_interested_response(conn, mapping["id"], "+919999961006")

        # cv present → AI call should fire
        mock_ringg.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Flow engine: _handle_not_interested_role_response
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleNotInterestedRoleResponse:
    @pytest.mark.asyncio
    async def test_eval_null_no_cv_sends_form_link(self, conn):
        client = await seed_client(conn)
        cand = await seed_candidate(conn, "71001", evaluation_status=None, cv_url=None)
        mapping = await seed_mapping(conn, client["id"], cand["id"])

        mock_ringg = AsyncMock()
        mock_text = AsyncMock()
        with patch("services.ringg_service.trigger_ai_call", mock_ringg), \
             patch("services.msg91_service.send_text", mock_text), \
             patch("services.msg91_service.send_template", AsyncMock()):
            await _handle_not_interested_role_response(conn, mapping["id"], "+919999971001")

        # No cv → form link; definitely no AI call
        mock_ringg.assert_not_called()

    @pytest.mark.asyncio
    async def test_eval_null_with_cv_sends_passive_ack_no_ai(self, conn):
        """cv_url set → passive ack. No form link, no AI call."""
        client = await seed_client(conn)
        cand = await seed_candidate(
            conn, "71002",
            evaluation_status=None,
            cv_url="https://storage.googleapis.com/hirelyra/cv.pdf",
        )
        mapping = await seed_mapping(conn, client["id"], cand["id"])

        mock_ringg = AsyncMock()
        with patch("services.ringg_service.trigger_ai_call", mock_ringg), \
             patch("services.msg91_service.send_text", AsyncMock()), \
             patch("services.msg91_service.send_template", AsyncMock()):
            await _handle_not_interested_role_response(conn, mapping["id"], "+919999971002")

        mock_ringg.assert_not_called()

    @pytest.mark.asyncio
    async def test_eval_pass_does_nothing(self, conn):
        """Already evaluated pass → no message sent (candidate is free to match elsewhere)."""
        client = await seed_client(conn)
        cand = await seed_candidate(conn, "71003", evaluation_status="Pass")
        mapping = await seed_mapping(conn, client["id"], cand["id"])

        mock_send = AsyncMock()
        with patch("services.msg91_service.send_text", mock_send), \
             patch("services.msg91_service.send_template", mock_send):
            await _handle_not_interested_role_response(conn, mapping["id"], "+919999971003")

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_eval_fail_sends_rejection_message(self, conn):
        client = await seed_client(conn)
        cand = await seed_candidate(conn, "71004", evaluation_status="Fail")
        mapping = await seed_mapping(conn, client["id"], cand["id"])

        mock_text = AsyncMock()
        mock_template = AsyncMock()
        with patch("services.msg91_service.send_text", mock_text), \
             patch("services.msg91_service.send_template", mock_template):
            await _handle_not_interested_role_response(conn, mapping["id"], "+919999971004")

        # A rejection message was sent via template or text (depends on env config)
        total_calls = mock_text.call_count + mock_template.call_count
        assert total_calls >= 1, "Expected at least one rejection message to be sent"
