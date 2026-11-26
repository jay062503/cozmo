# services/extraction.py
# Sends a pre-processed frame to Google Gemini 1.5 Flash and returns
# structured extraction results.
#
# Vision model : gemini-1.5-flash (free tier: 15 req/min, 1500 req/day)
# Why Gemini Flash:
#   - Free tier — no billing required
#   - Native multimodal (image + text in one call)
#   - JSON mode via responseMimeType = "application/json"
#   - Fast (~1-2 s per call)
#
# API key setup (run in the SAME terminal that starts the server):
#   $env:GEMINI_API_KEY = "your-key-here"
# Get a free key at: https://aistudio.google.com/app/apikey
#
# Image is pre-processed before the call (auto-levels, sharpen, upscale)
# via quality_check.preprocess_for_extraction(). Prompts are written to
# infer aggressively from low-quality / partial images.

import os
import json
import base64
import httpx
from typing import Optional

from backend.models.job_record import (
    ExtractionResult, NameplateExtraction, DamageExtraction,
    NoteExtraction, ConfidenceField,
)
from backend.services.quality_check import preprocess_for_extraction

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)


def get_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise EnvironmentError(
            "GEMINI_API_KEY environment variable is not set. "
            "Get a free key at https://aistudio.google.com/app/apikey "
            "then run: $env:GEMINI_API_KEY = 'your-key-here' "
            "in the same terminal that starts the server."
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

    # Pre-process before sending — biggest single accuracy improvement.
    enhanced = preprocess_for_extraction(image_b64)

    if capture_type == "nameplate":
        payload = await _extract_nameplate(enhanced, voice_hint)
        return ExtractionResult(type="nameplate", payload=payload)

    payload = await _extract_damage(enhanced, voice_hint)
    return ExtractionResult(type="damage", payload=payload)


# ── Nameplate ─────────────────────────────────────���───────────────────────────

async def _extract_nameplate(b64: str, hint: Optional[str]) -> NameplateExtraction:
    prompt = """You are an expert OCR assistant for property insurance claims.
You are reading an appliance nameplate photo. The photo may be dark, blurry,
taken at an angle, low resolution, or partially obscured — this is normal in field conditions.

YOUR JOB: Extract every field. Never return null without a genuine attempt.

Strategies for hard images:
- Partial text: reconstruct from visible characters (e.g. "GE" + partial -> "GEW_")
- Faded/embossed text: mentally increase contrast and read the shape
- Rotated or angled: mentally correct the perspective
- Serial number cut off: return whatever is visible with low confidence
- Manufacture date: often encoded in serial (first 4 digits = YYWW, or letter A=Jan)

Confidence scale:
  0.9+  clear and unambiguous
  0.7-0.9 mostly clear, minor uncertainty
  0.5-0.7 partial read or inferred
  0.3-0.5 best guess from context
  <0.3  barely readable but still include it

Return ONLY valid JSON (no markdown, no code fences):
{
  "make":             {"value": string_or_null, "confidence": float, "source": "vision_api"},
  "model":            {"value": string_or_null, "confidence": float, "source": "vision_api"},
  "serial_number":    {"value": string_or_null, "confidence": float, "source": "vision_api"},
  "manufacture_date": {"value": string_or_null, "confidence": float, "source": "vision_api"},
  "raw_text": "everything readable from the label verbatim",
  "overall_confidence": float
}"""

    if hint:
        prompt += f"\n\nAdditional context from voice command: {hint}"

    prompt += "\n\nExtract all nameplate fields from this image."

    raw = await _call_gemini(prompt, b64)
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
    prompt = """You are an expert property damage assessor for insurance claims.
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
- Never return null for damage_type or severity_level — always make a best guess.

Voice command context (if provided) is ground truth for room and surface.

Return ONLY valid JSON (no markdown, no code fences):
{
  "room":           {"value": string, "confidence": float, "source": string},
  "surface":        {"value": string, "confidence": float, "source": string},
  "damage_type":    {"value": string, "confidence": float, "source": string},
  "rough_size_m2":  {"value": string, "confidence": float, "source": string},
  "severity_level": {"value": "low or medium or high", "confidence": float, "source": string},
  "overall_confidence": float
}"""

    if hint:
        prompt += (
            f"\n\nVoice command context: {hint}. "
            "Use this as ground truth for room and surface."
        )

    prompt += "\n\nAssess the damage in this image. Provide your best estimate for every field."

    raw = await _call_gemini(prompt, b64)
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


# ── Gemini API call ───────────────────────────────────────────────────────────

async def _call_gemini(prompt: str, b64: str) -> str:
    # Strip data-URI prefix if present
    if "," in b64:
        b64 = b64.split(",", 1)[1]

    # Gemini expects base64 inline as an inlineData part
    body = {
        "contents": [{
            "parts": [
                {
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": b64,
                    }
                },
                {
                    "text": prompt
                },
            ]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,       # low temperature for factual extraction
            "maxOutputTokens": 700,
        },
    }

    url = f"{GEMINI_URL}?key={get_api_key()}"

    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(
            url,
            headers={"Content-Type": "application/json"},
            json=body,
        )

    if resp.status_code != 200:
        try:
            err = resp.json()
            err_msg = err.get("error", {}).get("message", resp.text)
        except Exception:
            err_msg = resp.text
        raise RuntimeError(f"Gemini API returned HTTP {resp.status_code}: {err_msg}")

    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Gemini response shape: {e} — {data}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_parse(text: str) -> dict:
    # Gemini sometimes wraps JSON in markdown code fences even with
    # responseMimeType set — strip them defensively.
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first line (```json or ```) and last line (```)
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
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
