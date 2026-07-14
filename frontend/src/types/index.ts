export type ClientStage =
  | 'lead' | 'scraping' | 'scraped' | 'reachout_sent'
  | 'interested' | 'agreement_sent' | 'onboarded' | 'disqualified' | 'churned' | 'deal_won'

export type MappingStage =
  | 'matched' | 'intent_ask' | 'interested' | 'not_interested'
  | 'client_approval_pending'
  | 'slot_booked' | 'interview_done' | 'placed' | 'rejected'
  | 'waitlisted' | 'invited_for_interview' | 'declined_for_interview'

export interface Client {
  id: string
  company_name: string
  location: string
  job_location: string
  location_lat: number | null
  location_lng: number | null
  poc_name: string
  poc_position: string
  poc_phone: string
  stage: ClientStage
  job_title: string
  industry: string
  num_employees: number | null
  min_salary: number | null
  max_salary: number | null
  key_skills: string[]
  job_timings: string
  gst_tds: { gst: string; tds: string } | null
  other_details: string
  age_requirements: string
  gender_requirements: string
  tools_requirements: string
  religion_requirement: string
  payment_terms: string
  salary_deductions: string
  job_type: string
  fee_amount: number | null
  replacement_period: string
  payment_due_days: number | null
  backdoor_period: string | null
  agreement_url: string
  source: string
  phone_numbers: Array<{ source: string; number: string; agreed?: boolean }> | null
  agreed_phone: string | null
  company_website: string | null
  company_description: string | null
  source_job_id: string | null
  female_employee_pct: number | null
  diwali_bonus: string | null
  business_type: string | null
  office_type: string | null
  is_ca_firm: boolean
  segment: 'ca_network' | 'active_job_post' | 'employer_lead' | null
  is_dnd: boolean
  stop_sourcing: boolean
  is_bot: boolean
  is_recruiter: boolean
  open_positions: number | null
  travel_radius: number | null
  recruiter_slots: Array<{ id: string; label: string; iso: string }> | null
  available_slots: Array<{
    label: string
    iso?: string
    received_at?: string
    slot_id?: string
    client_name?: string
    booked_at?: string
  }> | null
  sourcing_needed: boolean
  sourcing_count_needed: number | null
  labels: string[]
  feedback_token: string | null
  created_at: string
  updated_at: string
}

export interface EvaluationSummary {
  overview_summary: string
  technical_scoring: Record<string, unknown>
  hiring_decision: { status: string; reason: string }
}

export interface Candidate {
  id: string
  name: string
  age_years: number | null
  gender: string
  religion: string
  phone: string
  email: string | null
  experience: string
  cv_url: string
  source: string
  mentioned_location: string | null
  state: string | null
  current_location: string
  location_lat: number | null
  location_lng: number | null
  working_radius: number | null
  current_salary: number | null
  job_type: string
  industry: string
  call_status: string | null
  category: string | null
  evaluation_status: string
  technical_evaluation_score: number | null
  total_score: number | null
  evaluation_pdf_url: string | null
  evaluation_summary: EvaluationSummary | null
  hiring_decision_reason: string | null
  voice_recording_url: string
  work_preference: string
  other_details: Record<string, string> | null
  final_evaluation_report_url: string
  interview_count: number
  created_at: string
  updated_at: string
  is_active: boolean
  dnd_until: string | null
  recruiter_notes: string | null
  labels: string[]
  photo_url: string | null
  notice_period: string | null
  // lock status (joined from candidate_locks)
  is_locked: boolean
  lock_expires_at: string | null
  locked_for_company: string | null
  locked_companies: string[] | null
  // upcoming interview (joined from client_candidate_mappings)
  interview_slot: string | null
  interview_with: string | null
  // active mappings (joined from client_candidate_mappings + clients)
  active_mappings: Array<{ company_name: string; stage: string; match_score: number | null }> | null
}

export interface Mapping {
  id: string
  client_id: string
  candidate_id: string
  stage: MappingStage
  match_score: number
  wa_sent_at: string | null
  intent_received_at: string | null
  resume_sent_at: string | null
  slot_requested_at: string | null
  available_slots: { label: string }[] | null
  slot_sent_at: string | null
  interview_slot: string | null
  interview_reminder_sent: boolean
  interview_done: boolean
  interview_done_at: string | null
  placement_confirmed: boolean
  decline_reason: 'not_interested' | 'not_interested_role' | 'not_looking_for_job' | 'evaluation_failed' | null
  rejection_reason: 'client_disliked' | 'filters_not_matched' | 'slot_expired' | 'position_filled' | null
  notes: string
  feedback_client: 'liked' | 'disliked' | null
  feedback_client_reason: string | null
  is_locked: boolean
  locked_by_other: boolean
  locked_by_company: string | null
  created_at: string
  updated_at: string
  candidate?: Candidate
}

export interface WhatsAppMessage {
  id: string
  client_id: string | null
  candidate_id: string | null
  mapping_id: string | null
  message_type: string
  direction: string
  phone: string
  message_text: string
  status: string
  approved_by: string | null
  approved_at: string | null
  meta_msg_id: string | null
  sent_at: string | null
  created_at: string
}

export interface PipelineEvent {
  id: string
  client_id: string
  event_type: 'stage_change' | 'auto_match'
  // stage_change fields
  from_stage?: ClientStage | null
  to_stage?: ClientStage
  note?: string
  // auto_match fields
  details?: { filters?: { include_muslim?: boolean }; matched?: number }
  created_by: string
  created_at: string
}

export interface ClientComment {
  id: string
  client_id: string
  comment_text: string | null
  experience_criteria: string | null
  created_by: string
  created_at: string
}

export interface AIVoiceReachoutClient {
  client_id: string
  call_id: string
  status: string
  classification: string | null
  classification_reason: string | null
  extracted_info: {
    alt_poc_name?: string | null
    alt_poc_phone?: string | null
    callback_note?: string | null
    query_note?: string | null
  } | null
  recording_url: string | null
  triggered_at: string
  completed_at: string | null
  phone: string | null
  company_name: string
  stage: ClientStage
  poc_name: string | null
  poc_phone: string | null
  is_dnd: boolean
}

export interface ClientAICall {
  id: string
  client_id: string
  phone: string | null
  ringg_call_id: string | null
  status: string // queued | calling | completed | no_pickup | dropped | failed
  call_type: string // 'reachout' | 'funnel_stuck'
  transcript: string | null
  recording_url: string | null
  classification: string | null
  // reachout: interested | not_interested | wrong_number | wrong_poc | position_closed | callback_requested
  // funnel_stuck: qualified_lead | follow_up_needed | callback_requested | busy_or_unavailable |
  //               no_response_or_dropout | support_ticket_requested | onboarding_stuck_job_form |
  //               onboarding_stuck_contract_agreement
  classification_reason: string | null
  extracted_info: {
    alt_poc_name?: string | null
    alt_poc_phone?: string | null
    callback_note?: string | null
    query_note?: string | null
  } | null
  triggered_at: string
  completed_at: string | null
}

export interface WATemplateComponent {
  type: 'HEADER' | 'BODY' | 'FOOTER' | 'BUTTONS'
  format?: string
  text?: string
}

export interface WATemplate {
  name: string
  language: string
  status: string
  category: string
  components: WATemplateComponent[]
}

export interface ScrapingJob {
  id: string
  client_id: string
  scraper_type: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  result: Record<string, unknown> | null
  triggered_by: string
  triggered_at: string
  completed_at: string | null
  error_msg: string | null
}

// Badge helpers
export function clientStageBadge(stage: ClientStage): string {
  const map: Record<ClientStage, string> = {
    lead: 'bg-gray-100 text-gray-600',
    scraping: 'bg-blue-100 text-blue-700 animate-pulse',
    scraped: 'bg-blue-100 text-blue-700',
    reachout_sent: 'bg-amber-100 text-amber-700',
    interested: 'bg-orange-100 text-orange-700',
    agreement_sent: 'bg-purple-100 text-purple-700',
    onboarded: 'bg-green-100 text-green-700',
    disqualified: 'bg-red-100 text-red-600',
    churned: 'bg-slate-200 text-slate-600',
    deal_won: 'bg-emerald-100 text-emerald-700',
  }
  return map[stage] ?? 'bg-gray-100 text-gray-500'
}

export function mappingStageBadge(stage: MappingStage): string {
  const map: Record<MappingStage, string> = {
    matched: 'bg-gray-100 text-gray-600',
    intent_ask: 'bg-blue-100 text-blue-700',
    interested: 'bg-green-100 text-green-700',
    not_interested: 'bg-red-100 text-red-600',
    client_approval_pending: 'bg-indigo-100 text-indigo-700',
    slot_booked: 'bg-teal-100 text-teal-700',
    interview_done: 'bg-amber-100 text-amber-700',
    placed: 'bg-green-200 text-green-800 font-medium',
    rejected: 'bg-red-100 text-red-600',
    waitlisted:              'bg-amber-100 text-amber-700',
    invited_for_interview:   'bg-indigo-100 text-indigo-700',
    declined_for_interview:  'bg-red-100 text-red-600',
  }
  return map[stage] ?? 'bg-gray-100 text-gray-500'
}

export function stageLabel(stage: string): string {
  return stage.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

export function aiCallStatusBadge(status: string): string {
  const map: Record<string, string> = {
    queued: 'bg-gray-100 text-gray-600',
    calling: 'bg-blue-100 text-blue-700 animate-pulse',
    completed: 'bg-gray-100 text-gray-600',
    no_pickup: 'bg-amber-100 text-amber-700',
    dropped: 'bg-amber-100 text-amber-700',
    failed: 'bg-red-100 text-red-600',
  }
  return map[status] ?? 'bg-gray-100 text-gray-500'
}

export function aiCallClassificationBadge(classification: string): string {
  const map: Record<string, string> = {
    interested: 'bg-green-100 text-green-700',
    not_interested: 'bg-red-100 text-red-600',
    wrong_number: 'bg-gray-100 text-gray-600',
    wrong_poc: 'bg-amber-100 text-amber-700',
    position_closed: 'bg-purple-100 text-purple-700',
    callback_requested: 'bg-teal-100 text-teal-700',
    // Funnel Stuck agent classifications
    acknowledged: 'bg-green-100 text-green-700',
    no_response_or_dropout: 'bg-gray-100 text-gray-500',
    support_query: 'bg-purple-100 text-purple-700',
  }
  return map[classification] ?? 'bg-gray-100 text-gray-500'
}

export function aiCallTypeBadge(callType: string): string {
  return callType === 'funnel_stuck'
    ? 'bg-indigo-100 text-indigo-700'
    : 'bg-sky-100 text-sky-700'
}

export function aiCallTypeLabel(callType: string): string {
  return callType === 'funnel_stuck' ? 'Funnel Stuck' : 'Reachout'
}

export function declineReasonLabel(reason: string | null): string {
  const map: Record<string, string> = {
    not_interested_role: 'Not Interested — Role',
    not_looking_for_job: 'Not Looking for Job',
    evaluation_failed: 'Failed Evaluation',
    not_interested: 'Not Interested',
  }
  return reason ? (map[reason] ?? stageLabel(reason)) : 'Not Interested'
}

export function declineReasonBadge(reason: string | null): string {
  const map: Record<string, string> = {
    not_interested_role: 'bg-orange-100 text-orange-700',
    not_looking_for_job: 'bg-rose-100 text-rose-700',
    evaluation_failed: 'bg-slate-100 text-slate-600',
    not_interested: 'bg-red-100 text-red-600',
  }
  return reason ? (map[reason] ?? 'bg-red-100 text-red-600') : 'bg-red-100 text-red-600'
}

export function rejectionReasonLabel(reason: string | null): string {
  const map: Record<string, string> = {
    client_disliked: 'Rejected by Client',
    filters_not_matched: 'Filters Not Matched',
    slot_expired: 'Slot Not Picked (Expired)',
    position_filled: 'Position Filled',
  }
  return reason ? (map[reason] ?? stageLabel(reason)) : 'Rejected'
}

export function rejectionReasonBadge(reason: string | null): string {
  const map: Record<string, string> = {
    client_disliked: 'bg-red-100 text-red-600',
    filters_not_matched: 'bg-amber-100 text-amber-700',
    slot_expired: 'bg-gray-100 text-gray-600',
    position_filled: 'bg-gray-100 text-gray-600',
  }
  return reason ? (map[reason] ?? 'bg-red-100 text-red-600') : 'bg-red-100 text-red-600'
}

// Derived "where is this candidate in the form → AI call → evaluation funnel" status.
// Reports the furthest stage reached so RMs can tell apart, e.g., a candidate who
// hasn't filled the form yet from one who's done everything but failed evaluation.
export interface PipelineProgress {
  key: string
  label: string
  badge: string
}

export function pipelineProgress(c: {
  form_submitted?: boolean | null
  call_status?: string | null
  evaluation_status?: string | null
}): PipelineProgress {
  const evalStatus = (c.evaluation_status || '').toLowerCase()
  if (evalStatus === 'pass' || evalStatus === 'passed') {
    return { key: 'evaluated_pass', label: 'Evaluated — Pass', badge: 'bg-green-100 text-green-700' }
  }
  if (evalStatus === 'fail' || evalStatus === 'failed' || evalStatus === 'reject') {
    return { key: 'evaluated_fail', label: 'Evaluated — Fail', badge: 'bg-red-100 text-red-600' }
  }
  const callStatus = (c.call_status || '').toUpperCase()
  if (callStatus === 'COMPLETED') {
    return { key: 'call_completed', label: 'AI Call Completed', badge: 'bg-blue-100 text-blue-700' }
  }
  if (callStatus === 'DROPPED') {
    return { key: 'call_dropped', label: 'AI Call Dropped', badge: 'bg-amber-100 text-amber-700' }
  }
  if (c.form_submitted) {
    return { key: 'form_filled', label: 'Form Filled — Awaiting Call', badge: 'bg-teal-100 text-teal-700' }
  }
  return { key: 'form_pending', label: 'Form Not Filled', badge: 'bg-gray-100 text-gray-500' }
}

export function scoreBadge(score: number): string {
  if (score >= 75) return 'bg-green-100 text-green-700'
  if (score >= 50) return 'bg-amber-100 text-amber-700'
  return 'bg-red-100 text-red-600'
}
