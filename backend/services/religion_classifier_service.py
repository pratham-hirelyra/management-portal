"""
Name-based Muslim identifier using OpenAI.

Classifies Indian names as 'M' (Muslim), 'N' (not Muslim), or 'U' (ambiguous).
Designed for batch use — send up to 50 names per API call.
"""

import os
import json
from openai import AsyncOpenAI

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


_SYSTEM_PROMPT = """You are an expert at identifying whether an Indian name belongs to a Muslim person.
For each name given, output ONLY:
  'M'  — the name is clearly Muslim (Mohammed, Abdul, Fatima, Khan, Sheikh, Syed, Ansari, Qureshi,
          Shaikh, Mirza, Pathan, Siddiqui, Usman, Aisha, Zainab, Rizvi, Imran, Raza, Noor, etc.)
  'N'  — the name is clearly not Muslim (Hindu, Sikh, Christian, Jain, Parsi, etc.)
  'U'  — genuinely ambiguous; cannot determine from name alone

Rules:
- Be conservative. Only mark 'M' when the name is distinctly and unambiguously Muslim.
- If in doubt, use 'U' — do not guess.
- Do NOT sub-classify non-Muslims (no Hindu/Sikh/Christian labels — just 'N').

Respond with a JSON object mapping each name exactly as given to its label.
Example input: ["Ramesh Kumar", "Mohammed Salim Khan", "Alex D'Souza", "Priya Sharma", "Arjun"]
Example output: {"Ramesh Kumar": "N", "Mohammed Salim Khan": "M", "Alex D'Souza": "N", "Priya Sharma": "N", "Arjun": "N"}"""


async def classify_names(names: list[str]) -> dict[str, str]:
    """
    Classify a list of names. Returns dict mapping name → 'M' | 'N' | 'U'.
    'U' means ambiguous — caller should leave religion NULL for retry.
    Names not returned by the model default to 'U'.
    """
    if not names:
        return {}

    model = os.environ.get("OPENAI_EVAL_MODEL", "gpt-4o")
    ai = _get_client()

    resp = await ai.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(names)},
        ],
    )

    raw = resp.choices[0].message.content or "{}"
    try:
        result = json.loads(raw)
    except Exception:
        return {n: "U" for n in names}

    valid = {"M", "N", "U"}
    return {n: (result.get(n, "U") if result.get(n, "U") in valid else "U") for n in names}
