# services/extraction.py
# Sends a pre-processed frame to GPT-4o and returns structured extraction.
#
# Vision model : GPT-4o (gpt-4o-2024-11-20)
# Cost estimate: ~$0.007 per capture, ~$0.11 per job (15 captures avg)
#
# Key design choices for low-quality image tolerance:
#   - Image is pre-processed (auto-levels, sharpen, upscale) before the API call
#   - Prompts explicitly instruct the model to infer from partial / degraded images
#   - Model is told to never return null without a genuine attempt
#   - detail="high" so GPT-4o uses full resolution tile processing

import os
import json
import httpx
from typing import Optional

from backend.models.job_record import (
    ExtractionResult, NameplateExtraction, DamageExtraction,
    NoteExtraction, ConfidenceField,
)
from backend.services.quality_check import preprocess_for_extraction

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


# ── Main entry point ──────────────────────────────────────────────────────────

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

    # Enhance the image before sending — biggest single accuracy improvement.
    enhanced = preprocess_for_extraction(image_b64)

    if capture_type == "nameplate":
        payload = await _extract_nameplate(enhanced, voice_hint)
        return ExtractionResult(type="nameplate", payload=payload)

    payload = await _extract_damage(enhanced, voice_hint)
    return ExtractionResult(type="damage", payload=payload)


# ── Nameplate ─────────────────────────────────────────────────────────────────

async def _extract_nameplate(b64: str, hint: Optional[str]) -> NameplateExtraction:
    system = """You are an expert OCR assistant for property insurance claims.
You are reading an appliance nameplate photo. The photo may be dark, blurry,
taken at an angle, low resolution, or partially obscured — this is normal in field conditions.

YOUR JOB: Extract every field. Never return null without a genuine attempt.

Strategies for hard images:
- Partial text: reconstruct from visible characters (e.g. "GE" + partial → "GEW_")
- Faded/embossed text: mentally increase contrast and read the shape
- Rotated or angled: mentally correct the perspective
- Serial number cut off: return whatever is visible with low confidence
- Manufacture date: often encoded in serial (first 4 digits = YYWW, or letter codes A=Jan)

Confidence scale:
  0.9+  clear and unambiguous
  0.7-0.9 mostly clear, minor uncertainty
  0.5-0.7 partial read or inferred
  0.3-0.5 best guess from context
  <0.3  barely readable but still include it

Return ONLY valid JSON:
{
  "make":             {"value": string|null, "confidence": float, "source": "vision_api"},
  "model":            {"value": string|null, "confidence": float, "source": "vision_api"},
  "serial_number":    {"value": string|null, "confidence": float, "source": "vision_api"},
  "manufacture_date": {"value": string|null, "confidence": float, "source": "vision_api"},
  "raw_text": "everything readable from the label verbatim",
  "overall_confidence": float
}"""

    user_text = (
        "Extract all nameplate fields. The image may be low quality — "
        "do your best to infer every field rather than returning null."
    )
    if hint:
        user_text += f" Additional context: {hint}"

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


# ── Damage ────────────────────────────────────────────────────────────────────

async def _extract_damage(b64: str, hint: Optional[str]) -> DamageExtraction:
    system = """You are an expert property damage assessor for insurance claims.
You are looking at a photo of water or structural damage. The photo may be dark,
blurry, low resolution, or taken at an angle — this is normal in field conditions.

YOUR JOB: Extract every field. Never return null without a genuine attempt.

Strategies for hard images:
- Dark image: look for shape outlines, watermarks, discolouration patterns
- Blurry image: identify damage category from shapes and colours
- Partial view: estimate room from visible fixtures (toilet=bathroom, stove=kitchen)
- Size estimation: use ANY visible reference — door frame ~2m tall, light switch ~1.1m
  from floor, outlet ~0.3m wide, baseboard ~0.1m tall, tile ~0.3m square.
  Always attempt a size estimate even if uncertain. Set confidence below 0.5.
- Severity: even from a blurry image you can tell "small stain" vs "large buckle"
- Never return null for damage_type or severity_level — always make a best guess.

Voice command context (if provided) is ground truth for room and surface.

Return ONLY valid JSON:
{
  "room":           {"value": string, "confidence": float, "source": string},
  "surface":        {"value": string, "confidence": float, "source": string},
  "damage_type":    {"value": string, "confidence": float, "source": string},
  "rough_size_m2":  {"value": string, "confidence": float, "source": string},
  "severity_level": {"value": "low"|"medium"|"high", "confidence": float, "source": string},
  "overall_confidence": float
}"""

    user_text = (
        "Assess the damage in this image. "
        "Even if quality is poor, provide your best estimate for every field."
    )
    if hint:
        user_text += (
            f" Voice command context: {hint}. "
            "Use this as ground truth for room and surface."
        )

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
    return NoteExtraction(
        raw_text=text,
        location=location,
        subject=ConfidenceField(value=text, confidence=0.9, source="voice_command"),
        urgency=ConfidenceField(
            value="urgent" if is_urgent else "normal",
            confidence=0.88 if is_urgent else 0.9,
            source="keyword_match",
        ),
        overall_confidence=0.85,
    )


# ── OpenAI API call ───────────────────────────────────────────────────────────

async def _call_api(system: str, user_text: str, b64: str) -> str:
    if "," in b64:
        b64 = b64.split(",", 1)[1]

    headers = {
        "Authorization": f"Bearer {get_api_key()}",
        "Content-Type":  "application/json",
    }
    body = {
        "model":           MODEL,
        "max_tokens":      700,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {
                    "url":    f"data:image/jpeg;base64,{b64}",
                    "detail": "high",   # full tile processing — critical for OCR
                }},
            ]},
        ],
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(OPENAI_URL, headers=headers, json=body)

    if resp.status_code != 200:
        try:
            err_msg = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            err_msg = resp.text
        raise RuntimeError(f"OpenAI API returned HTTP {resp.status_code}: {err_msg}")

    return resp.json()["choices"][0]["message"]["content"]


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
