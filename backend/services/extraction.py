# services/extraction.py
# Sends a captured frame to GPT-4o and returns structured extraction.
# Identical prompts and field schema to ExtractionService.swift.
#
# Vision model : GPT-4o (gpt-4o-2024-11-20)
# Cost estimate: ~$0.007 per capture, ~$0.11 per job (15 captures avg)
# AI tools used: GitHub Copilot for httpx boilerplate; prompts written manually.

import os
import json
import httpx
from typing import Optional

from backend.models.job_record import (
    ExtractionResult, NameplateExtraction, DamageExtraction,
    NoteExtraction, ConfidenceField,
)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL      = "gpt-4o-2024-11-20"


def get_api_key() -> str:
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise EnvironmentError(
            "OPENAI_API_KEY environment variable is not set. "
            "See README for setup instructions."
        )
    return key


# ── Main entry point ─────────────────────────────────────────────────────────

async def extract(
    image_b64: str,
    capture_type: str,
    voice_hint: Optional[str] = None,
) -> ExtractionResult:

    if capture_type == "note":
        return ExtractionResult(
            type="note",
            payload=_parse_note_locally(voice_hint or ""),
        )

    if capture_type == "nameplate":
        payload = await _extract_nameplate(image_b64, voice_hint)
        return ExtractionResult(type="nameplate", payload=payload)

    # damage
    payload = await _extract_damage(image_b64, voice_hint)
    return ExtractionResult(type="damage", payload=payload)


# ── Nameplate ────────────────────────────────────────────────────────────────

async def _extract_nameplate(b64: str, hint: Optional[str]) -> NameplateExtraction:
    system = """You are a property claims field assistant looking at an appliance nameplate photo.
Extract these fields. For each field return value (string or null), confidence (0.0–1.0), source ("vision_api").

Confidence guide:
  0.9+  : text is clearly printed and unambiguous
  0.7–0.9: partially obscured but inferable
  0.5–0.7: inferred from context
  <0.5  : genuinely uncertain

Return ONLY valid JSON:
{
  "make":            {"value": string|null, "confidence": float, "source": "vision_api"},
  "model":           {"value": string|null, "confidence": float, "source": "vision_api"},
  "serial_number":   {"value": string|null, "confidence": float, "source": "vision_api"},
  "manufacture_date":{"value": string|null, "confidence": float, "source": "vision_api"},
  "raw_text": string,
  "overall_confidence": float
}"""

    user_text = "Extract all nameplate fields from this image."
    if hint:
        user_text += f" Voice hint: {hint}"

    raw = await _call_api(system, user_text, b64)
    j   = _safe_parse(raw)

    return NameplateExtraction(
        make=            _field(j.get("make")),
        model=           _field(j.get("model")),
        serial_number=   _field(j.get("serial_number")),
        manufacture_date=_field(j.get("manufacture_date")),
        raw_text=        j.get("raw_text", ""),
        overall_confidence=float(j.get("overall_confidence", 0.5)),
    )


# ── Damage ───────────────────────────────────────────────────────────────────

async def _extract_damage(b64: str, hint: Optional[str]) -> DamageExtraction:
    system = """You are a property claims field assistant looking at a water/structural damage photo.
Extract these fields with value, confidence (0.0–1.0), and source.

Fields:
- room         : kitchen, bathroom, bedroom, basement, etc.
- surface      : north wall, ceiling, floor, cabinet, etc.
- damage_type  : describe the damage (water stain, mold, buckle, structural, etc.)
- rough_size_m2: estimated area in square metres as a decimal string e.g. "0.5"
- severity_level: "low", "medium", or "high"

Size estimation: use visible reference objects (door frame ~2m tall, light switch ~1.1m from floor,
standard countertop ~0.9m) to estimate. Set size confidence below 0.6 — there is no depth sensor.

Return ONLY valid JSON:
{
  "room":          {"value": string|null, "confidence": float, "source": string},
  "surface":       {"value": string|null, "confidence": float, "source": string},
  "damage_type":   {"value": string|null, "confidence": float, "source": string},
  "rough_size_m2": {"value": string|null, "confidence": float, "source": string},
  "severity_level":{"value": string|null, "confidence": float, "source": string},
  "overall_confidence": float
}"""

    user_text = "Assess the damage in this image."
    if hint:
        user_text += f" Voice command hint: {hint}. Use this to fill room/surface when the image is ambiguous."

    raw = await _call_api(system, user_text, b64)
    j   = _safe_parse(raw)

    return DamageExtraction(
        room=          _field(j.get("room")),
        surface=       _field(j.get("surface")),
        damage_type=   _field(j.get("damage_type")),
        rough_size_m2= _field(j.get("rough_size_m2")),
        severity_level=_field(j.get("severity_level")),
        overall_confidence=float(j.get("overall_confidence", 0.5)),
    )


# ── Note (local, no API call) ─────────────────────────────────────────────────

LOCATION_KEYWORDS = [
    "kitchen", "bathroom", "bedroom", "basement", "hallway", "garage",
    "attic", "living room", "by the", "near the", "under the", "crawl space",
]

def _parse_note_locally(text: str) -> NoteExtraction:
    lower = text.lower()

    location = None
    for kw in LOCATION_KEYWORDS:
        if kw in lower:
            location = ConfidenceField(value=kw, confidence=0.75, source="keyword_match")
            break

    is_urgent = any(w in lower for w in ["urgent", "immediate", "asap", "right away"])
    urgency   = ConfidenceField(
        value="urgent" if is_urgent else "normal",
        confidence=0.88 if is_urgent else 0.9,
        source="keyword_match",
    )

    return NoteExtraction(
        raw_text=text,
        location=location,
        subject=ConfidenceField(value=text, confidence=0.9, source="voice_command"),
        urgency=urgency,
        overall_confidence=0.85,
    )


# ── OpenAI API call ──────────────────────────────────────────────────────────

async def _call_api(system: str, user_text: str, b64: str) -> str:
    if "," in b64:
        b64 = b64.split(",", 1)[1]

    headers = {
        "Authorization": f"Bearer {get_api_key()}",
        "Content-Type":  "application/json",
    }
    body = {
        "model":           MODEL,
        "max_tokens":      600,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {
                    "url":    f"data:image/jpeg;base64,{b64}",
                    "detail": "high",
                }},
            ]},
        ],
    }

    async with httpx.AsyncClient(timeout=40.0) as client:
        resp = await client.post(OPENAI_URL, headers=headers, json=body)

    if resp.status_code != 200:
        # Surface the actual OpenAI error message so the caller (and developer)
        # can see whether it was a bad key (401), quota (429), model error (400), etc.
        try:
            err_msg = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            err_msg = resp.text
        raise RuntimeError(
            f"OpenAI API returned HTTP {resp.status_code}: {err_msg}"
        )

    data = resp.json()
    return data["choices"][0]["message"]["content"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_parse(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        return {}


def _field(raw) -> ConfidenceField:
    if not isinstance(raw, dict):
        return ConfidenceField(value=None, confidence=0.0, source="parse_error")
    return ConfidenceField(
        value=      raw.get("value"),
        confidence= float(raw.get("confidence", 0.0)),
        source=     raw.get("source", "vision_api"),
    )
