"""
RinggAI integration + GPT transcript evaluation.

Env vars:
  RINGG_API_KEY           — RinggAI API key
  RINGG_API_URL           — outbound call endpoint (default: prod-api.ringg.ai)
  RINGG_AGENT_ID          — agent/campaign ID to use for the call
  OPENAI_API_KEY          — for transcript evaluation
  OPENAI_EVAL_MODEL       — defaults to gpt-4o-mini
"""

import os
import json
import httpx

RINGG_URL = os.environ.get(
    "RINGG_API_URL",
    "https://prod-api.ringg.ai/ca/api/v0/calling/outbound/individual",
)

_EVAL_SYSTEM = """## **Role**

You are a Senior Technical Auditor for the Indian Accounting Industry. Your objective is to evaluate a candidate's interview transcript against a set of "Right Answers."

You must be strict, objective, analytical, and data-driven.
Do not assume knowledge beyond what is explicitly stated in the transcript.

⚠️ **The entire output must be written in pure English only. No Hindi, Hinglish, regional language words, or mixed-language expressions are allowed under any circumstances.**

---

## **Evaluation Inputs**

1. **Candidate Response** – Transcript of the candidate's answers.

---

## **Scoring System (Strict Adherence Required)**

* Each question carries **5 marks**
* Total questions: **10**
* **Total Marks: 50**

### **Per Question Evaluation Criteria**

**5/5 (Perfect):**
Complete, accurate, and conceptually correct answer.

**3/5 (Partially Correct):**
Answer contains partial correctness but misses a key accounting, compliance, or conceptual element.

**0/5 (Poor):**
Answer is incorrect, irrelevant, or conceptually flawed.

---

### **Mandatory Rules**

* No assumptions beyond transcript content.
* Strict comparison with the provided Right Answers.
* Justifications must clearly explain correctness or specific conceptual gaps.
* Do not rewrite or simplify the interviewer's question. The question must be captured exactly as asked in the transcript.
* Be open if the candidate changes the answer later on after realizing he was wrong earlier.
---




## **Knowledge Base**

---

## **Q1.**

We have received a Professional Consultancy bill of ₹1 lakh from Tata Consultancy Services Private Limited, on which 18% GST has been charged. While entering this expense, will you debit or credit the Input GST account?

**Answer:** Debit

---

## **Q2.**

During the same entry, will the Tata Consultancy Services Private Limited account be debited or credited?

**Answer:** Credit

---

## **Q3.**

Why did you say that the Tata Consultancy Services Private Limited account should be debited/credited?

**Answer:**
Because Tata Consultancy is a vendor or creditor and hence must be credited.
Or
Because we have to pay to Tata Consultancy and hence must be credited.
Or
Because Tata Consultancy A/c will be shown as a liability and hence must be credited.

---

## **Q4.**

What is Input GST actually? Is it an asset, a liability, an income, or an expense?

**Answer:** Asset

---

## **Q5.**

When you deduct TDS while making an expense entry, what will you do to the TDS Payable account—debit or credit?

**Answer:** Credit

---

## **Q6.**

When you make the payment of this TDS to the government, what entry will you pass?

**Answer:** TDS or TDS Payable A/c Debited and Bank or Cash A/c Credited.

---

## **Q7.**

Out of the sales of ₹50,000 in October, goods worth ₹10,000 are returned in November. Will you modify the October entry or pass a new entry in November?

**Answer:**
Pass a new entry in November.
Or
Will modify the entry in October if the GST return is not filed yet, else pass a new entry in November.

---

## **Q8.**

But since the sales belonged to October, why shouldn't we modify it in October itself?

**Answer:**
Because GST returns would have been filed already / October month books would have been closed.(Full Marks)
Or
If the GST return is not yet filed, we can modify the original entry; else we will have to pass a new entry.(Full Marks)
Or
Month-end would have happened and books would be closed, hence it is good practice to pass a new entry in November through issuing a credit note. (Full Marks)
Or
We should not modify the entry, rather create a credit note in November.(Partial Marks)

---

## **Q9.**

You have received a bill of ₹1,00,000 + 18% GST (₹1,18,000 total) from Infosys Pvt Ltd. You have to deduct TDS at 10 percent on this. What will be the TDS amount?

**Answer:** 10,000/-

---

## **Q10.**

आप एक Contractor को ₹6 लाख की Payment कर रहे हो। अगर यह Contractor एक Private Limited Company है, तो इस Transaction पर क्या Rate पर TDS कटेगा?

**Answer:**
2%
(1% – partial credit)

---


---

## **Final Output Format (Strict JSON Only)**

```json
{
  "completed": true,
  "output": {
    "overview_summary": "Provide a single consolidated summary covering: (1) overall conceptual accounting strength without referring to specific questions, (2) tools/software knowledge, and (3) depth of GST/TDS involvement — whether only preparation or actual filing. Include overall professional stability and competency impression.",

    "technical_scoring": {
      "question_1": {
        "question": "Exact question as mentioned in the knowledge base in english",
        "score": 0,
        "correct_answer": "As per Knowledge Base",
        "candidate_response": "",
        "justification": ""
      },
      "question_2": {
        "question": "Exact question as mentioned in the knowledge base in english",
        "score": 0,
        "correct_answer": "As per Knowledge Base",
        "candidate_response": "",
        "justification": ""
      },
      "question_3": {
        "question": "Exact question as mentioned in the knowledge base in english",
        "score": 0,
        "correct_answer": "As per Knowledge Base",
        "candidate_response": "",
        "justification": ""
      },
      "question_4": {
        "question": "Exact question as mentioned in the knowledge base in english",
        "score": 0,
        "correct_answer": "As per Knowledge Base",
        "candidate_response": "",
        "justification": ""
      },
      "question_5": {
        "question": "Exact question as mentioned in the knowledge base in english",
        "score": 0,
        "correct_answer": "As per Knowledge Base",
        "candidate_response": "",
        "justification": ""
      },
      "question_6": {
        "question": "Exact question as mentioned in the knowledge base in english",
        "score": 0,
        "correct_answer": "As per Knowledge Base",
        "candidate_response": "",
        "justification": ""
      },
      "question_7": {
        "question": "Exact question as mentioned in the knowledge base in english",
        "score": 0,
        "correct_answer": "As per Knowledge Base",
        "candidate_response": "",
        "justification": ""
      },
      "question_8": {
        "question": "Exact question as mentioned in the knowledge base in english",
        "score": 0,
        "correct_answer": "As per Knowledge Base",
        "candidate_response": "",
        "justification": ""
      },
      "question_9": {
        "question": "Exact question as mentioned in the knowledge base in english",
        "score": 0,
        "correct_answer": "As per Knowledge Base",
        "candidate_response": "",
        "justification": ""
      },
      "question_10": {
        "question": "Exact question as mentioned in the knowledge base in english",
        "score": 0,
        "correct_answer": "As per Knowledge Base",
        "candidate_response": "",
        "justification": ""
      }
    }
  }
}
```"""


def _format_phone(phone: str) -> str:
    phone = phone.strip().lstrip("+")
    if len(phone) == 10 and phone.isdigit():
        return "91" + phone
    return phone


def classify_result(ai_pct: float, salary: float | None) -> tuple[str, str]:
    """
    Returns (eval_status, category) from AI score % and current/expected salary.

    Sal ≤ 18K  : ≥60% → Senior Accountant (Pass) · 40-60% → Junior Accountant (Pass) · <40% → Fail
    18K-20K    : ≥60% → Senior Accountant (Pass) · else → Fail
    20K-30K    : threshold = 61% at 20,001 + 1% per ₹1K · ≥threshold → Senior Accountant (Pass) · else → Fail
    >30K       : ≥70% → Senior Accountant (Pass) · else → Fail
    Unknown    : ≥65% → Senior Accountant (Pass) · else → Fail
    """
    import math as _math

    if salary is None:
        if ai_pct >= 65.0:
            return "Pass", "Senior Accountant"
        return "Fail", ""

    s = float(salary)

    if s <= 18000:
        if ai_pct >= 60.0:
            return "Pass", "Senior Accountant"
        if ai_pct >= 40.0:
            return "Pass", "Junior Accountant"
        return "Fail", ""

    if s <= 20000:
        if ai_pct >= 60.0:
            return "Pass", "Senior Accountant"
        return "Fail", ""

    if s <= 30000:
        threshold = 60.0 + _math.ceil((s - 20000) / 1000)
        if ai_pct >= threshold:
            return "Pass", "Senior Accountant"
        return "Fail", ""

    # s > 30000
    if ai_pct >= 70.0:
        return "Pass", "Senior Accountant"
    return "Fail", ""


async def trigger_ai_call(candidate_id, phone: str, name: str = "", retry_on_no_pickup: bool = True) -> dict:
    """
    Trigger an outbound AI interview call via RinggAI.
    Returns the API response dict (or empty dict if key not configured).
    """
    import time as _time
    import datetime as _dt

    api_key = os.environ.get("RINGG_API_KEY", "").strip()
    if not api_key:
        return {}

    agent_id = os.environ.get("RINGG_AGENT_ID", "bda70576-3184-4460-bbc8-03900936b822").strip()
    from_number_id = os.environ.get("RINGG_FROM_NUMBER_ID", "51d265bf-2ee1-4532-9460-ce2990f2b90a").strip()
    whatsapp_number = os.environ.get("MSG91_CANDIDATE_WHATSAPP_NUMBER", "9998910512").strip().lstrip("91").lstrip("+91")

    # Phone as +91XXXXXXXXXX
    digits = phone.strip().lstrip("+")
    if digits.startswith("91") and len(digits) == 12:
        mobile = f"+{digits}"
    elif len(digits) == 10:
        mobile = f"+91{digits}"
    else:
        mobile = f"+{digits}"

    # RinggAI treats naive datetimes as IST — send IST time (UTC+5:30)
    IST_OFFSET_SECS = 19800  # 5h30m
    scheduled_at = _dt.datetime.utcfromtimestamp(_time.time() + 120 + IST_OFFSET_SECS).strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[ringg] scheduled_at (IST): {scheduled_at}")

    payload = {
        "name": name or str(candidate_id),
        "from_number_id": from_number_id,
        "agent_id": agent_id,
        "custom_args_values": {
            "mobile_number": mobile,
            "callee_name": name or "",
            "job_role": "Accounting",
            "whatsapp_number": whatsapp_number,
        },
        "call_config": {
            "idle_timeout_warning": 10,
            "idle_timeout_end": 30,
            "max_call_length": 900,
            "call_time": {
                "scheduled_at": scheduled_at,
                "call_start_time": "09:30",
                "call_end_time": "21:30",
            },
            **({"call_retry_config": {
                "retry_count": 3,
                "retry_busy": 40,
                "retry_not_picked": 40,
                "retry_failed": 40,
            }} if retry_on_no_pickup else {}),
        },
        "mobile_number": mobile,
    }

    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(
            RINGG_URL,
            json=payload,
            headers={
                "accept": "application/json, text/plain, */*",
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            },
        )
    if not resp.is_success:
        print(f"[ringg] trigger_ai_call error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    return resp.json()


async def evaluate_transcript(
    transcript: str,
    expected_salary: float | None,
    current_salary: float | None = None,
) -> dict:
    """
    Send transcript to GPT for evaluation.
    Returns:
      {
        "scores": [int x10],          # per-question scores (0, 3, or 5)
        "total": int (0-50),
        "percentage": float (0-100),
        "cutoff": float,
        "passed": bool,
        "summary": str,               # overview_summary paragraph
        "technical_scoring": dict,    # full per-question breakdown
      }
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_EVAL_MODEL", "gpt-5.4")

    if not api_key:
        return {
            "scores": [0] * 10,
            "total": 0,
            "percentage": 0.0,
            "eval_status": "Fail",
            "category": "",
            "passed": False,
            "summary": "Evaluation skipped (OPENAI_API_KEY not set)",
            "technical_scoring": {},
        }

    user_message = f"Transcript:\n\n{transcript}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _EVAL_SYSTEM},
            {"role": "user", "content": user_message},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }

    async with httpx.AsyncClient(timeout=90.0) as http:
        resp = await http.post(
            "https://api.openai.com/v1/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
    resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"]
    result = json.loads(content)

    output = result.get("output", result)
    technical_scoring = output.get("technical_scoring", {})
    overview_summary = output.get("overview_summary", "")

    # Extract per-question scores in order
    scores = []
    for i in range(1, 11):
        q_data = technical_scoring.get(f"question_{i}", {})
        scores.append(int(q_data.get("score", 0)))

    raw_total = sum(scores)
    base_pct = (raw_total / 50) * 100
    final_pct = min(100.0, base_pct + 10.0)  # +10% bonus, capped at 100

    salary_for_band = current_salary or expected_salary
    eval_status, category = classify_result(final_pct, salary_for_band)

    return {
        "scores": scores,
        "total": raw_total,
        "percentage": round(final_pct, 2),
        "eval_status": eval_status,
        "category": category,
        "passed": eval_status == "Pass",
        "summary": overview_summary,
        "technical_scoring": technical_scoring,
    }


async def trigger_client_ai_call(
    client_id, phone: str, company_name: str = "", job_portal_name: str = "",
) -> dict:
    """
    Trigger an outbound AI voice reachout call to a client POC via RinggAI,
    using the dedicated client-reachout agent.
    Returns the API response dict (or empty dict if key not configured).
    """
    import time as _time
    import datetime as _dt

    api_key = os.environ.get("RINGG_API_KEY", "").strip()
    if not api_key:
        return {}

    agent_id = os.environ.get("RINGG_CLIENT_REACHOUT_AGENT_ID", "7d302d06-9515-4e0a-957e-56cdb34431d0").strip()
    from_number_id = os.environ.get("RINGG_FROM_NUMBER_ID", "51d265bf-2ee1-4532-9460-ce2990f2b90a").strip()

    # Phone as +91XXXXXXXXXX
    digits = phone.strip().lstrip("+")
    if digits.startswith("91") and len(digits) == 12:
        mobile = f"+{digits}"
    elif len(digits) == 10:
        mobile = f"+91{digits}"
    else:
        mobile = f"+{digits}"

    # RinggAI treats naive datetimes as IST — send IST time (UTC+5:30)
    IST_OFFSET_SECS = 19800  # 5h30m
    scheduled_at = _dt.datetime.utcfromtimestamp(_time.time() + 300 + IST_OFFSET_SECS).strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[ringg] client reachout scheduled_at (IST): {scheduled_at}")

    payload = {
        "name": company_name or str(client_id),
        "from_number_id": from_number_id,
        "agent_id": agent_id,
        "custom_args_values": {
            "mobile_number": mobile,
            "callee_company_name": company_name or "",
            "job_portal_name": job_portal_name or "the job portal",
        },
        "call_config": {
            "idle_timeout_warning": 10,
            "idle_timeout_end": 30,
            "max_call_length": 600,
            "call_time": {
                "scheduled_at": scheduled_at,
                "call_start_time": "10:00",
                "call_end_time": "19:00",
            },
            "call_retry_config": {
                "retry_count": 1,
                "retry_busy": 60,
                "retry_not_picked": 60,
                "retry_failed": 60,
            },
        },
        "mobile_number": mobile,
    }

    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(
            RINGG_URL,
            json=payload,
            headers={
                "accept": "application/json, text/plain, */*",
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            },
        )
    if not resp.is_success:
        print(f"[ringg] trigger_client_ai_call error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    return resp.json()


async def trigger_slot_reminder_call(
    mapping_id, phone: str, callee_name: str = "", company_name: str = "",
) -> dict:
    """
    Trigger a slot-selection reminder AI call to a candidate who has been sent
    interview slot options but hasn't booked yet.
    Agent: da73230d-d392-498e-86a9-8583bd8799c7 (slot reminder)
    Variables: mobile_number, callee_name, company_name
    Retry: 1 retry after 30 min on no-pickup/busy/failed.
    """
    import time as _time
    import datetime as _dt

    api_key = os.environ.get("RINGG_API_KEY", "").strip()
    if not api_key:
        return {}

    agent_id = os.environ.get("RINGG_SLOT_REMINDER_AGENT_ID", "da73230d-d392-498e-86a9-8583bd8799c7").strip()
    from_number_id = os.environ.get("RINGG_FROM_NUMBER_ID", "51d265bf-2ee1-4532-9460-ce2990f2b90a").strip()

    digits = phone.strip().lstrip("+")
    if digits.startswith("91") and len(digits) == 12:
        mobile = f"+{digits}"
    elif len(digits) == 10:
        mobile = f"+91{digits}"
    else:
        mobile = f"+{digits}"

    IST_OFFSET_SECS = 19800  # 5h30m
    scheduled_at = _dt.datetime.utcfromtimestamp(_time.time() + 300 + IST_OFFSET_SECS).strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[ringg] slot-reminder scheduled_at (IST): {scheduled_at}")

    payload = {
        "name": callee_name or str(mapping_id),
        "from_number_id": from_number_id,
        "agent_id": agent_id,
        "custom_args_values": {
            "mobile_number": mobile,
            "callee_name": callee_name or "",
            "company_name": company_name or "",
        },
        "call_config": {
            "idle_timeout_warning": 10,
            "idle_timeout_end": 30,
            "max_call_length": 600,
            "call_time": {
                "scheduled_at": scheduled_at,
                "call_start_time": "09:30",
                "call_end_time": "21:00",
            },
            "call_retry_config": {
                "retry_count": 1,
                "retry_busy": 30,
                "retry_not_picked": 30,
                "retry_failed": 30,
            },
        },
        "mobile_number": mobile,
    }

    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(
            RINGG_URL,
            json=payload,
            headers={
                "accept": "application/json, text/plain, */*",
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            },
        )
    if not resp.is_success:
        print(f"[ringg] trigger_slot_reminder_call error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    return resp.json()


async def trigger_funnel_stuck_call(
    client_id, phone: str, callee_name: str = "", client_stage_label: str = "",
) -> dict:
    """
    Trigger an outbound AI voice call to a client POC who is stuck at the
    'interested' or 'agreement_sent' stage, using the dedicated "Funnel Stuck" agent.
    Returns the API response dict (or empty dict if key not configured).
    """
    import time as _time
    import datetime as _dt

    api_key = os.environ.get("RINGG_API_KEY", "").strip()
    if not api_key:
        return {}

    agent_id = os.environ.get("RINGG_FUNNEL_STUCK_AGENT_ID", "59813662-0fee-47a7-8f3b-b33e6a7abb5e").strip()
    from_number_id = os.environ.get("RINGG_FROM_NUMBER_ID", "51d265bf-2ee1-4532-9460-ce2990f2b90a").strip()

    # Phone as +91XXXXXXXXXX
    digits = phone.strip().lstrip("+")
    if digits.startswith("91") and len(digits) == 12:
        mobile = f"+{digits}"
    elif len(digits) == 10:
        mobile = f"+91{digits}"
    else:
        mobile = f"+{digits}"

    # RinggAI treats naive datetimes as IST — send IST time (UTC+5:30)
    IST_OFFSET_SECS = 19800  # 5h30m
    scheduled_at = _dt.datetime.utcfromtimestamp(_time.time() + 300 + IST_OFFSET_SECS).strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[ringg] funnel-stuck scheduled_at (IST): {scheduled_at}")

    payload = {
        "name": callee_name or str(client_id),
        "from_number_id": from_number_id,
        "agent_id": agent_id,
        "custom_args_values": {
            "mobile_number": mobile,
            "callee_name": callee_name or "",
            "client_stage": client_stage_label,
        },
        "call_config": {
            "idle_timeout_warning": 10,
            "idle_timeout_end": 30,
            "max_call_length": 600,
            "call_time": {
                "scheduled_at": scheduled_at,
                "call_start_time": "10:00",
                "call_end_time": "19:00",
            },
        },
        "mobile_number": mobile,
    }

    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(
            RINGG_URL,
            json=payload,
            headers={
                "accept": "application/json, text/plain, */*",
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            },
        )
    if not resp.is_success:
        print(f"[ringg] trigger_funnel_stuck_call error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    return resp.json()


_FUNNEL_STUCK_CLASSIFY_SYSTEM = """You are analyzing the transcript of an outbound AI voice call made by "Riya Shah" \
on behalf of Just Accountants (a recruitment consultancy that places Accountants) to a client company that is stuck \
mid-funnel — either they marked themselves "Interested" but haven't filled in the Job Requirement Form yet, or they \
filled the form but haven't signed/agreed to the contract yet. The agreement-stage call is just a reminder to confirm
the agreement.

The transcript may be in Gujarati (written in Latin/Devanagari script), Hindi, English, or Hinglish — read it in the
original language(s) but respond only in English.

The call follows this script:

1. Opening (varies by stage):
   - Job Requirement Form stage: Riya reminds them they showed interest but haven't filled the job requirement form
     yet, and asks if she can help with anything.
   - Agreement Signing stage: Riya reminds them the form is filled but the contract/agreement hasn't been signed yet,
     and asks if she can help with anything.

2. Then ONE of these situations plays out:
   - Situation 1 (Wrong POC): The person says they aren't the right contact for this. Riya asks for the correct POC's
     name and phone number so she can guide them.
   - Situation 2 (Busy right now): The person is busy. Riya asks when she should call back.
   - Situation 3 (Correct POC, will complete it): The person confirms they are the right contact and will fill the
     form / sign the agreement themselves (now or later — "I'll do it", "I'm doing it now", etc.). Riya thanks them
     and ends the call positively.
   - Situation 4 (Mistaken click / not interested): The person says the "Interested" button was pressed by mistake,
     or that they are not actually interested / don't want to proceed. Riya says "no problem, let us know in future
     if you ever need an accountant" and ends the call.
   - Fallback (Query/issue raised): The person raises a question, complaint, or issue (about the form, the
     agreement, or anything else). Riya notes it and says an expert from the team will reach out to help.

Classify the call outcome into exactly ONE of these categories:

- "wrong_poc": Situation 1 — the person who answered is not the right point of contact.
- "callback_requested": Situation 2 — the person is busy and a callback time was discussed/requested.
- "acknowledged": Situation 3 — the right POC confirmed they will complete the pending action (form or agreement)
  themselves, with no DND and no further action needed from us right now.
- "not_interested": Situation 4 — the person says "Interested" was clicked by mistake, or they are no longer
  interested / don't want to proceed. This client should be marked Do Not Disturb.
- "support_query": Fallback — the person raised a question, complaint, or issue that needs human follow-up.
- "no_response_or_dropout": The call did not result in a meaningful conversation at all (no pickup signals in the
  transcript, call dropped immediately, or the transcript is empty/unintelligible).

Return strict JSON only, in this exact shape:
{
  "classification": "<one of: wrong_poc, callback_requested, acknowledged, not_interested, support_query, no_response_or_dropout>",
  "reason": "<1-3 sentence explanation in English summarizing what happened on the call and why you picked this category>",
  "extracted_info": {
    "alt_poc_name": "<name of an alternate contact person, if mentioned, else null>",
    "alt_poc_phone": "<an alternate phone number, if mentioned, else null>",
    "callback_note": "<any specific day/time mentioned for a callback, if any, else null>",
    "query_note": "<a short summary of the question/issue raised, if any, else null>"
  }
}"""


async def classify_funnel_stuck_call(transcript: str) -> dict:
    """
    Send a "Funnel Stuck" AI-voice call transcript to GPT for classification.
    Returns:
      {
        "classification": str,   # one of the categories in _FUNNEL_STUCK_CLASSIFY_SYSTEM
        "reason": str,
        "extracted_info": dict,
      }
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_EVAL_MODEL", "gpt-5.4")

    if not api_key:
        return {
            "classification": "acknowledged",
            "reason": "Classification skipped (OPENAI_API_KEY not set)",
            "extracted_info": {},
        }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _FUNNEL_STUCK_CLASSIFY_SYSTEM},
            {"role": "user", "content": f"Transcript:\n\n{transcript}"},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }

    async with httpx.AsyncClient(timeout=90.0) as http:
        resp = await http.post(
            "https://api.openai.com/v1/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
    resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"]
    result = json.loads(content)

    return {
        "classification": result.get("classification", "acknowledged"),
        "reason": result.get("reason", ""),
        "extracted_info": result.get("extracted_info") or {},
    }


_CLIENT_CALL_CLASSIFY_SYSTEM = """You are analyzing the transcript of an outbound AI voice call made by "Riya Shah" \
on behalf of Just Accountants (a recruitment consultancy that places Accountants) to a company that posted a job \
opening for an Accountant on a job portal.

The call follows this rough script:
1. Confirm we've reached the right company.
2. Ask if they're still looking to hire an Accountant.
   - If the position is already filled/closed, Riya pitches Just Accountants for *future* hiring needs and ends the call.
   - If the person who answered isn't the right point of contact for hiring, Riya asks for the right person's name/number.
   - If the prospect is busy, Riya offers to call back later.
   - If the position is still open and this is the right POC, Riya pitches Just Accountants: a flat fee of ₹7,000 paid
     only when a candidate joins, no advance payment, a 3-month replacement guarantee with 100% refund if no
     replacement is provided, and the ability to close the position within 48 hours.
3. If the prospect agrees to proceed, Riya says she'll send a WhatsApp form to fill in their job requirements.

The transcript may be in Gujarati (written in Latin/Devanagari script), Hindi, English, or Hinglish — read it in the
original language(s) but respond only in English.

Classify the call outcome into exactly ONE of these categories:

- "wrong_number": Whoever answered said this isn't the right company / wrong number — call ended immediately with no
  further conversation.
- "position_closed": The company confirmed the Accountant position is already filled / closed / no longer hiring,
  and Riya gave the "keep us in mind for the future" pitch.
- "wrong_poc": The person who answered is not the right point of contact for hiring (e.g. says "this isn't my area,
  talk to someone else"), regardless of whether they gave an alternate contact.
- "callback_requested": The prospect was busy, asked to be called back later, or said they'd "check internally and
  let us know" — no pitch was completed.
- "interested": The right POC heard the full pitch (fee, replacement guarantee, 48-hour timeline) and agreed to
  receive/fill the WhatsApp requirements form, or otherwise clearly wants to proceed.
- "not_interested": The right POC heard the pitch but declined, said no, or did not agree to receive the form.

Return strict JSON only, in this exact shape:
{
  "classification": "<one of: wrong_number, position_closed, wrong_poc, callback_requested, interested, not_interested>",
  "reason": "<1-3 sentence explanation in English summarizing what happened on the call and why you picked this category>",
  "extracted_info": {
    "alt_poc_name": "<name of an alternate contact person, if mentioned, else null>",
    "alt_poc_phone": "<an alternate phone number, if mentioned, else null>",
    "callback_note": "<any specific day/time mentioned for a callback, if any, else null>"
  }
}"""


async def classify_client_call(transcript: str) -> dict:
    """
    Send a client AI-voice-reachout call transcript to GPT for classification.
    Returns:
      {
        "classification": str,   # one of the categories in _CLIENT_CALL_CLASSIFY_SYSTEM
        "reason": str,
        "extracted_info": dict,
      }
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_EVAL_MODEL", "gpt-5.4")

    if not api_key:
        return {
            "classification": "callback_requested",
            "reason": "Classification skipped (OPENAI_API_KEY not set)",
            "extracted_info": {},
        }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _CLIENT_CALL_CLASSIFY_SYSTEM},
            {"role": "user", "content": f"Transcript:\n\n{transcript}"},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }

    async with httpx.AsyncClient(timeout=90.0) as http:
        resp = await http.post(
            "https://api.openai.com/v1/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
    resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"]
    result = json.loads(content)

    return {
        "classification": result.get("classification", "callback_requested"),
        "reason": result.get("reason", ""),
        "extracted_info": result.get("extracted_info") or {},
    }
