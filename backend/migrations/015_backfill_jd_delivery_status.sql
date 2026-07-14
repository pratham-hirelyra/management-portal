-- Migration 015: backfill delivery status for stuck candidate_intent_ask messages
--
-- Until migration 014's fix to _process_msg91_event's phone matching (RIGHT(phone,10)
-- comparison), DELIVERED/READ webhooks for JD-share messages sent via flush-jd-queue
-- (phone stored as 91-prefixed 12-digit) never matched their whatsapp_messages row,
-- so ~5,000 rows are stuck at status='sent'.
--
-- We have no MSG91 message IDs to query actual receipts retroactively. But where
-- client_candidate_mappings.intent_received_at is set, the candidate tapped a button
-- on that JD message — direct proof it was delivered and read. Backfill those rows
-- (and only those) to status='read'. Rows with no response signal are left as 'sent'
-- since we have no evidence either way.
--
-- Scoped to today's batch only (sent_at IST date = today) — run again with
-- updated date filter (or remove it) to cover earlier batches later.
--
-- Run once: psql $DATABASE_URL -f migrations/015_backfill_jd_delivery_status.sql

UPDATE whatsapp_messages wm
SET status = 'read'
FROM client_candidate_mappings ccm
WHERE wm.mapping_id = ccm.id
  AND wm.message_type = 'candidate_intent_ask'
  AND wm.status = 'sent'
  AND ccm.intent_received_at IS NOT NULL
  AND DATE(wm.sent_at AT TIME ZONE 'Asia/Kolkata') = (now() AT TIME ZONE 'Asia/Kolkata')::date;
