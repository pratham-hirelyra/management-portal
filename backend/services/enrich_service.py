"""
Client enrichment and classification services.

is_obvious_recruiter      — free name-based keyword check (no API call)
classify_and_find_phones  — single web search: classify recruiter/company + find phones
classify_company          — legacy: gpt-5.4-mini classify only (kept for reference)
extract_phone_from_website — httpx fetch → gpt-5-mini phone extraction
"""
import asyncio
import json
import os
import re

import httpx

_OPENAI_URL    = "https://api.openai.com/v1/chat/completions"
_RESPONSES_URL = "https://api.openai.com/v1/responses"
_WEBSITE_MODEL = "gpt-5-mini"
_PHONE_AGENT_MODEL = "gpt-5.4-mini"

_RETRY_MAX     = 4       # max attempts before giving up
_RETRY_BASE    = 1.0     # base backoff seconds (doubles each attempt)

# ── Name-based recruiter pre-filter (free, no API call) ───────────────────────

_RECRUITER_NAME_KEYWORDS = frozenset([
    "staffing",
    "recruitment",
    "recruiter",
    "manpower",
    "hr solutions",
    "hr services",
    "hr consultancy",
    "hr consulting",
    "hr associates",
    "placement services",
    "placement agency",
    "placement bureau",
    "placement consultant",
    "executive search",
    "headhunting",
    "headhunter",
    "talent hunt",
    "job consultancy",
    "hiring solutions",
    "workforce solutions",
    "payroll services",
])


def is_obvious_recruiter(company_name: str) -> bool:
    """
    Free keyword check — returns True if the name contains a known recruiter phrase.
    Catches the obvious cases before spending any API budget.
    """
    lower = company_name.lower()
    return any(kw in lower for kw in _RECRUITER_NAME_KEYWORDS)

# Pages to try for contact info (relative paths)
_CONTACT_PATHS = ["/", "/contact-us", "/contact", "/about-us", "/about"]


def _api_key() -> str:
    k = os.environ.get("OPENAI_API_KEY", "")
    if not k:
        raise RuntimeError("OPENAI_API_KEY not set")
    return k


async def _openai_post_with_retry(url: str, headers: dict, payload: dict, timeout: float) -> httpx.Response:
    """
    POST to an OpenAI endpoint with exponential backoff on 429 rate-limit errors.
    Respects the Retry-After header when present.
    Raises the last httpx response (or exception) after _RETRY_MAX attempts.
    """
    delay = _RETRY_BASE
    last_resp: httpx.Response | None = None
    for attempt in range(1, _RETRY_MAX + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                resp = await http.post(url, headers=headers, json=payload)
        except Exception:
            raise

        if resp.status_code != 429:
            return resp

        last_resp = resp
        if attempt == _RETRY_MAX:
            break

        # honour Retry-After if the API sends it, else use exponential backoff
        retry_after = resp.headers.get("retry-after") or resp.headers.get("x-ratelimit-reset-requests")
        try:
            wait = float(retry_after) if retry_after else delay
        except ValueError:
            wait = delay

        wait = max(wait, 0.3)  # never sleep less than 300ms
        print(f"[openai] 429 rate limit — waiting {wait:.1f}s (attempt {attempt}/{_RETRY_MAX})")
        await asyncio.sleep(wait)
        delay *= 2

    return last_resp  # type: ignore[return-value]


def normalise_phone(raw: str | None) -> str | None:
    """
    Clean a phone string to a 10-digit Indian number.
    Strips +91 / 0 prefix, removes spaces/dashes/dots.
    Returns None if result is not a plausible Indian number.
    """
    if not raw:
        return None
    # Remove country code variants
    cleaned = re.sub(r'(\+91|0091)\s*', '', raw.strip())
    # Strip leading 0 (STD code prefix used for landlines)
    cleaned = re.sub(r'^0+', '', cleaned)
    # Keep only digits
    digits = re.sub(r'\D', '', cleaned)
    if len(digits) == 10:
        return digits
    return None


def extract_best_phone(raw: str | None) -> str | None:
    """
    From a raw string (possibly containing multiple comma-separated numbers),
    return the first valid 10-digit Indian mobile number (starts with 6-9).
    Landlines are discarded.
    """
    if not raw:
        return None
    candidates = re.split(r'[,;/\n]+', raw)
    for c in candidates:
        phone = normalise_phone(c.strip())
        if phone and phone[0] in '6789':
            return phone
    return None


async def _fetch_page_text(url: str) -> str:
    """GET a URL and return stripped plain text (max 4 000 chars)."""
    try:
        async with httpx.AsyncClient(
            timeout=10,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; HireLyra-Enricher/1.0)"},
        ) as http:
            resp = await http.get(url)
            if resp.status_code != 200:
                return ""
            text = re.sub(r'<[^>]+>', ' ', resp.text)
            text = re.sub(r'&[a-z]+;', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:4000]
    except Exception:
        return ""


async def _gather_website_text(base_url: str) -> str:
    """Fetch homepage and top contact pages; return combined text up to 6 000 chars."""
    base = base_url.rstrip("/")
    texts = []
    # Try homepage first, then contact pages; stop once we have enough text
    for path in _CONTACT_PATHS:
        url = base if path == "/" else base + path
        chunk = await _fetch_page_text(url)
        if chunk:
            texts.append(chunk)
        if sum(len(t) for t in texts) >= 6000:
            break
    return "\n\n---\n\n".join(texts)[:6000]


async def extract_phone_from_website(website_url: str) -> str | None:
    """
    Fetch company website pages and ask gpt-5-mini to extract phone numbers.
    Returns a clean 10-digit Indian phone or None.
    """
    text = await _gather_website_text(website_url)
    if not text:
        return None

    system_prompt = (
        "You are a Company Contact Extraction Agent.\n"
        "Extract official phone numbers from the company website text provided.\n"
        "Look in Contact Us, footer, header, About sections.\n"
        "Only extract numbers clearly associated with the company.\n"
        "If multiple numbers found, return all separated by commas.\n"
        "If no phone number found, return empty string.\n"
        'Return ONLY valid JSON: {"phone_number": "<phones_or_empty>"}'
    )
    user_content = f"Website: {website_url}\n\nPage content:\n{text}"

    try:
        resp = await _openai_post_with_retry(
            _OPENAI_URL,
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Content-Type": "application/json",
            },
            payload={
                "model": _WEBSITE_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_content},
                ],
                "response_format": {"type": "json_object"},
                "max_completion_tokens": 100,
            },
            timeout=30,
        )
        if not resp.is_success:
            print(f"[enrich/website] OpenAI error {resp.status_code}: {resp.text[:200]}")
            return None
        data = json.loads(resp.json()["choices"][0]["message"]["content"] or "{}")
        return extract_best_phone(data.get("phone_number", ""))
    except Exception as e:
        print(f"[enrich/website] {website_url}: {e}")
        return None


# ── AI phone-finding agent ────────────────────────────────────────────────────

_PHONE_AGENT_INSTRUCTIONS = """\
You are an expert business contact researcher.

Input:
* Company Name: provided by user
* Location: provided by user

Goal:
Find ALL publicly available MOBILE phone numbers associated with the company.

Search these sources:
1. Official company website
2. Contact page
3. About page
4. Careers page
5. Google Business Profile
6. Justdial
7. TradeIndia
8. LinkedIn company page
9. Crunchbase
10. MCA/company registry records
11. Startup directories
12. Public social media profiles
13. Press releases and news articles

Instructions:
* Collect every publicly available MOBILE phone number.
* Exclude landline numbers.
* Exclude toll-free numbers.
* Exclude IVR/switchboard numbers when possible.
* Prioritize mobile numbers belonging to founders, directors, recruiters, HR personnel, \
sales representatives, business development executives, or publicly listed company representatives.
* Deduplicate numbers.
* Normalize numbers to international format when possible.
* Include the source URL where each number was found.
* Include a confidence score.
* Prefer official sources over directories.
* Prioritize sources in this order:
  1. Official website
  2. Google Business Profile
  3. Justdial
  4. LinkedIn
  5. TradeIndia
  6. Company registries

Validation Rules:
* Return only publicly available numbers.
* Do not invent, infer, generate, or guess numbers.
* Do not derive phone numbers from email addresses.
* Do not return private, hidden, leaked, or non-public information.
* If a number cannot be verified from a public source, exclude it.
* If only landline numbers are found, return an empty list.

Output JSON (strict, no markdown):
{
  "company_name": "...",
  "location": "...",
  "mobile_numbers": [
    {
      "number": "+91XXXXXXXXXX",
      "owner_name": "...",
      "designation": "...",
      "source": "...",
      "source_url": "https://...",
      "confidence": "high"
    }
  ]
}

Important:
* Return EVERY publicly available mobile number discovered.
* If the same number appears in multiple places, merge into one record.
* Return only verified mobile numbers.
* If no verified public mobile number exists, return an empty array.\
"""


async def find_phones_ai_agent(company_name: str, location: str | None) -> list[str]:
    """
    Use OpenAI web-search agent to find all public mobile numbers for a company.
    Returns list of clean 10-digit Indian mobile numbers (all found, deduped).
    """
    user_input = f"Company Name: {company_name}"
    if location:
        user_input += f"\nLocation: {location}"

    try:
        resp = await _openai_post_with_retry(
            _RESPONSES_URL,
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Content-Type": "application/json",
            },
            payload={
                "model":        _PHONE_AGENT_MODEL,
                "instructions": _PHONE_AGENT_INSTRUCTIONS,
                "input":        user_input,
                "tools": [{"type": "web_search_preview", "search_context_size": "medium"}],
            },
            timeout=90,
        )

        if not resp.is_success:
            print(f"[enrich/phone_agent] OpenAI error {resp.status_code}: {resp.text[:300]}")
            return []

        output = resp.json().get("output", [])
        text   = _extract_response_text(output)
        text   = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            print(f"[enrich/phone_agent] No JSON for {company_name}: {text[:200]}")
            return []

        data   = json.loads(m.group())
        phones: list[str] = []
        for entry in data.get("mobile_numbers", []):
            raw    = entry.get("number", "") if isinstance(entry, dict) else str(entry)
            clean  = normalise_phone(raw)
            if clean and clean[0] in "6789" and clean not in phones:
                phones.append(clean)

        return phones

    except Exception as e:
        print(f"[enrich/phone_agent] {company_name}: {e}")
        return []


# ── Combined classify + phone (single web search call) ───────────────────────

_COMBINED_INSTRUCTIONS = """\
You are a business intelligence agent for a recruitment firm.

Given a company name and location, do the following in a single web search session:

STEP 1 — Classify the company:
- "recruiter" = primarily provides staffing, placement, or HR consulting services to other businesses
- "company" = regular business that hires employees directly (the type we want to contact)

STEP 2 — If it is a "company", also find ALL publicly available Indian mobile phone numbers:
- Search Google for the company name + location + "phone number"
- Read the phone number DIRECTLY from Google search result snippets and the Google Knowledge Panel — do NOT navigate to Justdial, IndiaMart, TradeIndia, or any third-party directory pages as they hide numbers behind login walls
- Also check: official company website, Google Maps / Google Business Profile, LinkedIn
- Only mobile numbers (starts with 6, 7, 8, or 9 in India) — exclude landlines and toll-free
- Do NOT invent or guess numbers — only return numbers verified from a public source

Return ONLY valid JSON (no markdown, no extra text):
{
  "is_recruiter": true or false,
  "client_type": "recruiter" or "company",
  "description": "<factual 1-2 sentence description>",
  "mobile_numbers": [
    {
      "number": "+91XXXXXXXXXX",
      "confidence": "high" or "medium"
    }
  ]
}

Rules:
- If is_recruiter is true, set mobile_numbers to [] (skip phone search).
- If no verified mobile numbers found, set mobile_numbers to [].
- Deduplicate numbers. Normalize to +91XXXXXXXXXX format.\
"""


async def classify_and_find_phones(
    company_name: str,
    location: str | None,
    job_title: str | None = None,
) -> dict:
    """
    Single web-search call that both classifies the company AND finds phone numbers.
    Returns:
      {
        "is_recruiter": bool,
        "client_type": "recruiter"|"company",
        "description": str,
        "phones": list[str],   # clean 10-digit Indian mobiles
      }
    Falls back to {"is_recruiter": False, "client_type": "company", "description": "", "phones": []}
    on error (caller should treat this as an api_error).
    """
    user_input = f"Company Name: {company_name}"
    if location:
        user_input += f"\nLocation: {location}"
    if job_title:
        user_input += f"\nJob Title they are hiring for: {job_title}"

    try:
        resp = await _openai_post_with_retry(
            _RESPONSES_URL,
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Content-Type": "application/json",
            },
            payload={
                "model":        _PHONE_AGENT_MODEL,
                "instructions": _COMBINED_INSTRUCTIONS,
                "input":        user_input,
                "tools": [{"type": "web_search_preview", "search_context_size": "medium"}],
            },
            timeout=90,
        )

        if not resp.is_success:
            print(f"[enrich/combined] OpenAI error {resp.status_code}: {resp.text[:300]}")
            raise RuntimeError(f"OpenAI {resp.status_code}")

        output = resp.json().get("output", [])
        text   = _extract_response_text(output)
        text   = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            print(f"[enrich/combined] No JSON for {company_name}: {text[:200]}")
            return {"is_recruiter": False, "client_type": "company", "description": "", "phones": []}

        data = json.loads(m.group())
        is_recruiter = bool(data.get("is_recruiter", False))

        phones: list[str] = []
        if not is_recruiter:
            for entry in data.get("mobile_numbers", []):
                raw   = entry.get("number", "") if isinstance(entry, dict) else str(entry)
                clean = normalise_phone(raw)
                if clean and clean[0] in "6789" and clean not in phones:
                    phones.append(clean)

        return {
            "is_recruiter": is_recruiter,
            "client_type":  data.get("client_type", "recruiter" if is_recruiter else "company"),
            "description":  data.get("description", ""),
            "phones":       phones,
        }

    except Exception as e:
        print(f"[enrich/combined] {company_name}: {e}")
        raise  # let caller handle as api_error


# ── Recruiter classification (legacy — kept for direct use if needed) ─────────

_CLASSIFY_MODEL = "gpt-5.4-mini"
_RESPONSES_URL  = "https://api.openai.com/v1/responses"

_CLASSIFY_INSTRUCTIONS = """\
You are a company identification agent.

Your task:
- Search the web to determine whether the given company is a **recruitment \
consultancy / staffing agency (recruiter)** or a **regular company that hires directly**.

Guidelines:
- Classify as "recruiter" if the company primarily provides hiring, staffing, \
placement, or HR consulting services to other businesses.
- Otherwise classify as "company".
- Use the web search tool to verify — do not guess based on name alone.
- Keep the description factual and concise (1–2 sentences).

Return ONLY valid JSON (no markdown, no extra text):
{
  "company_name": "<name>",
  "client_type": "recruiter" or "company",
  "description": "<factual 1-2 sentence description>",
  "is_recruiter": true or false
}"""


def _extract_response_text(output: list) -> str:
    """Pull the final text content from an OpenAI Responses API output array."""
    for item in reversed(output):
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    return block.get("text", "")
    return ""


async def classify_company(
    company_name: str,
    location: str | None,
    job_title: str | None,
) -> dict:
    """
    Use gpt-5.4-mini with web_search_preview (low context) to determine whether
    a company is a recruiter or a real employer.
    Returns {"client_type": "recruiter"|"company", "description": str, "is_recruiter": bool}.
    Falls back to {"client_type": "company", "description": "", "is_recruiter": False} on error.
    """
    user_input = f"Company Name: {company_name}"
    if location:
        user_input += f"\nCompany Location: {location}"
    if job_title:
        user_input += f"\nJob Title they are hiring for: {job_title}"

    try:
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.post(
                _RESPONSES_URL,
                headers={
                    "Authorization": f"Bearer {_api_key()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model":        _CLASSIFY_MODEL,
                    "instructions": _CLASSIFY_INSTRUCTIONS,
                    "input":        user_input,
                    "tools": [
                        {
                            "type":                "web_search_preview",
                            "search_context_size": "low",
                        }
                    ],
                },
            )

        if not resp.is_success:
            print(f"[classify] OpenAI error {resp.status_code}: {resp.text[:300]}")
            return {"client_type": "company", "description": "", "is_recruiter": False}

        output = resp.json().get("output", [])
        text   = _extract_response_text(output)

        # Strip markdown code fences if present
        text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()

        # Extract JSON object from response text
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            print(f"[classify] No JSON in response for {company_name}: {text[:200]}")
            return {"client_type": "company", "description": "", "is_recruiter": False}

        data = json.loads(m.group())
        return {
            "client_type": data.get("client_type", "company"),
            "description":  data.get("description", ""),
            "is_recruiter": bool(data.get("is_recruiter", False)),
        }

    except Exception as e:
        print(f"[classify] {company_name}: {e}")
        return {"client_type": "company", "description": "", "is_recruiter": False}
