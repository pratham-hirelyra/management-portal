-- Two fixes surfaced by testing on 2026-07-21:
--
-- 1. AI call could be triggered twice in quick succession for the same
--    candidate — once from /apply submission (always triggers now), once
--    from tapping Interested on a JD moments later. ai_call_triggered_at
--    lets _trigger_ai_call_with_notify (services/flow_engine.py) detect and
--    skip a duplicate trigger while a call is still in flight.
--
-- 2. Candidates who never answer any AI call attempt stayed permanently
--    locked to that client (candidate_locks never released once retries
--    were exhausted in routers/cron.py:_retry_dropped_calls), blocking them
--    from ever being matched to a different opportunity. Fixed in that same
--    cron function — no schema change needed for this one, decline_reason
--    already supports arbitrary text values.

ALTER TABLE candidates
  ADD COLUMN IF NOT EXISTS ai_call_triggered_at timestamptz;
