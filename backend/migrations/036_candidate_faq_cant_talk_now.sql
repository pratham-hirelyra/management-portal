-- Candidate proactively says they can't talk right now / won't be able to
-- attend the upcoming AI screening call. Distinct from the "already missed
-- the call, asking for a callback" case (services/ai_rm_service.py's
-- CANDIDATE SCENARIOS section — that one has no reschedule tool and
-- escalates). This one is purely informational: tell them to still pick up
-- when it rings and negotiate a time live with the call itself, no WhatsApp-
-- side rescheduling action needed.

INSERT INTO candidate_faqs (topic, question_patterns, answer, applies_to_stage) VALUES
  ('cant_talk_right_now',
   ARRAY['cant talk right now', 'busy right now', 'not available for the call', 'call me later', 'can i call you in evening', 'i am busy right now'],
   'No worries! Please pick up the call when it comes and let our AI agent know what time works best for you — we''ll call you back at that time.',
   NULL)
ON CONFLICT DO NOTHING;
