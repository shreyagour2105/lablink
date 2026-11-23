"""Select an explanation focus from server-verified scheduling facts."""

import json
import re
from dataclasses import dataclass
from time import monotonic

import httpx
from django.conf import settings

from .scheduling import AGING_THRESHOLD

FACTS = {
    "requested_conflict": "The requested interval has a conflict listed on this request.",
    "equivalent_unit": "The recommended unit is in the requested lab and meets the required capabilities.",
    "different_time": "The recommended time is within the student's permitted alternative window.",
    "requested_slot": "The recommendation keeps the requested equipment and start time.",
    "waiting_priority": "The request has waited at least 48 hours and qualifies for aging priority.",
    "duration": "The interval fits the full requested duration during operating hours.",
    "deadline": "The interval ends by the project's deadline.",
    "no_overlap": "The interval avoids confirmed overlaps for both the unit and the student.",
}
API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_PROMPT_BYTES = 4096
MAX_RESPONSE_BYTES = 8192
MAX_ANSWER_CHARS = 256
RESPONSE_BUDGET_SECONDS = 4


@dataclass(frozen=True)
class Explanation:
    summary: str
    points: tuple[str, ...] = ()
    used_gemini: bool = False


def explain(entry, *, now):
    if not entry["options"]:
        return Explanation(
            "The scheduler currently has no feasible recommendation for this request."
        )
    first = entry["options"][0]
    item = entry["item"]
    facts = []
    if entry["conflicts"]:
        facts.append("requested_conflict")
    if first["resource_id"] != item["resource_id"]:
        facts.append("equivalent_unit")
    if first["starts_at"] != item["starts_at"]:
        facts.append("different_time")
    if not first["alternative"]:
        facts.append("requested_slot")
    if item["submitted_at"] <= now - AGING_THRESHOLD:
        facts.append("waiting_priority")
    facts.extend(("duration", "deadline", "no_overlap"))

    selection = select_highlights(facts)
    highlights = selection or facts[:2]
    summary = (
        "The first recommendation is an alternative allocation for this request."
        if first["alternative"]
        else "The first recommendation matches the requested slot."
    )
    return Explanation(
        summary, tuple(FACTS[key] for key in highlights), selection is not None
    )


def select_highlights(facts):
    api_key = settings.GEMINI_API_KEY
    model = settings.GEMINI_MODEL
    if not api_key or not re.fullmatch(r"gemini-[a-z0-9][a-z0-9.-]{0,78}", model):
        return None

    # Only static, allowlisted evidence is shared. Never forward an entry or user text.
    choices = {key: FACTS[key] for key in facts}
    # This string is sent to Gemini and is never used as a database query.
    prompt = (
        "For a faculty member reviewing a scheduling recommendation, select exactly two distinct "  # nosec B608
        "fact IDs from the evidence below. Put the most useful reason first. Prioritize what "
        "changed or why when an alternative was necessary; otherwise emphasize useful eligibility "
        "or timing evidence. Return only JSON with the highlights array.\n"
        + json.dumps(choices, separators=(",", ":"))
    )
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        return None

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 128,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "required": ["highlights"],
                "properties": {
                    "highlights": {
                        "type": "ARRAY",
                        "minItems": 2,
                        "maxItems": 2,
                        "items": {"type": "STRING", "enum": list(choices)},
                    }
                },
            },
        },
    }

    deadline = monotonic() + RESPONSE_BUDGET_SECONDS
    try:
        with httpx.stream(
            "POST",
            f"{API_ROOT}/{model}:generateContent",
            json=payload,
            headers={"x-goog-api-key": api_key, "Accept-Encoding": "identity"},
            timeout=httpx.Timeout(2.5, connect=1.5),
            follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                return None
            body = bytearray()
            for chunk in response.iter_bytes():
                if (
                    monotonic() >= deadline
                    or len(body) + len(chunk) > MAX_RESPONSE_BYTES
                ):
                    return None
                body.extend(chunk)
        return parse_highlights(body, choices)
    except (httpx.HTTPError, ValueError):
        return None


def parse_highlights(body, choices):
    try:
        (candidate,) = json.loads(body)["candidates"]
        if candidate["finishReason"] != "STOP":
            return None
        (part,) = candidate["content"]["parts"]
        answer = part["text"]
        if not isinstance(answer, str) or len(answer) > MAX_ANSWER_CHARS:
            return None
        result = json.loads(answer)
        if not isinstance(result, dict) or set(result) != {"highlights"}:
            return None
        highlights = result["highlights"]
        if not isinstance(highlights, list) or len(highlights) != 2:
            return None
        if any(not isinstance(key, str) or key not in choices for key in highlights):
            return None
        if highlights[0] == highlights[1]:
            return None
        return tuple(highlights)
    except (KeyError, RecursionError, TypeError, ValueError):
        return None
