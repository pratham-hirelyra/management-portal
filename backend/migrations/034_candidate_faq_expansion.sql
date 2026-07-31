-- Expands candidate_faqs with the static, non-routing answers from the
-- candidate-side FAQ script (2026-07-20). Routing/action items from that same
-- script (button nudges, form-status handling, escalation triggers) live in
-- services/ai_rm_service.py's system prompt instead, since they depend on
-- conversation state, not a static lookup.

INSERT INTO candidate_faqs (topic, question_patterns, answer, applies_to_stage) VALUES
  ('not_ca_firm',
   ARRAY['is this a ca firm', 'are you a ca firm', 'chartered accountant firm'],
   'No, we are not a CA firm. JustAccountants is a dedicated recruitment consultancy firm that exclusively handles accounting roles. We act as a bridge to help companies find great accountants and help accountants secure excellent jobs.',
   NULL),
  ('no_wfh_part_time',
   ARRAY['work from home', 'wfh', 'part time', 'part-time'],
   'No, we do not have any part-time or work-from-home (WFH) jobs available as of now. All our active listings are full-time, on-site roles.',
   NULL),
  ('roles_not_offered',
   ARRAY['cashier', 'back office', 'back-office', 'ca role', 'chartered accountant role', 'foreign accounting', 'international accounting'],
   'No, we do not have cashier or general back-office roles. We also do not hire for Chartered Accountant (CA) positions or foreign accounting roles at this moment. JustAccountants focuses exclusively on domestic accounting roles to help accountants land the right fit.',
   NULL),
  ('fresher_eligibility',
   ARRAY['fresher', 'no experience', 'can i apply without experience'],
   'Yes, absolutely! We have roles suited for freshers as well. Please click the Interested button above and fill out the form so we can match you with the right entry-level opportunities.',
   NULL),
  ('senior_vs_junior',
   ARRAY['difference between senior and junior', 'what is senior accountant', 'what is junior accountant', 'am i senior or junior'],
   'A Senior Accountant is someone who knows how to pass journal entries, prepare data for GST and TDS filings, and coordinate directly with a client''s CA. A Junior Accountant primarily handles sales, purchase, or bank entries, including bank reconciliation. If you know GST & TDS data preparation work, you should apply for the senior accountant role!',
   NULL),
  ('age_no_issue',
   ARRAY['too old', 'age issue', 'i am 50 years', 'i am 60 years', 'age limit'],
   'Age is absolutely not an issue for us! Please click on the Interested button and fill up the job requirement form on our portal, and we will happily share relevant job opportunities with you.',
   NULL),
  ('location_field_help',
   ARRAY['location field not working', 'cant fill location', 'location field error', 'area not showing'],
   'If you are having trouble with the location field, please try typing in your specific area name (for example, Paldi) rather than typing out the whole city name.',
   NULL),
  ('post_interview_followup',
   ARRAY['interview result', 'interview feedback', 'did i get selected', 'any update on my interview'],
   'Thank you for reaching out! We are currently coordinating with the client regarding their feedback post interview and will update you on your status as soon as possible. Once you are selected by the client, we will reach out to get your formal feedback for this job. In the meantime, feel free to scan other opportunities we''ve shared to see if any catch your eye!',
   NULL),
  ('urgent_interview_request',
   ARRAY['set up my interview', 'urgently looking', 'need a job urgently', 'please schedule interview'],
   'Don''t worry, our process is already underway! We are actively tracking your profile. As soon as a role matches your profile, we will share the JD with you immediately.',
   NULL)
ON CONFLICT DO NOTHING;
