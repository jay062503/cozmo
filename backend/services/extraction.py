# services/extraction.py
# Sends a captured (and pre-processed) frame to GPT-4o.
# Two changes from the original:
#   1. Image is pre-processed before the API call (auto-levels, sharpen, upscale)
#   2. Prompts instruct the model to infer aggressively from partial/blurry images
#      and never give up on a field without a genuine attempt.

import os
import json
import asyncio
import httpx
from typing import Optional

from backend.models.job_record import (
    ExtractionResult, NameplateExtraction, DamageExtraction,
    NoteExtraction, ConfidenceField,
)
from backend.services.quality_check import preprocess_for_extraction

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL      = "gpt-4o-2024-11-20"

# Network behaviour. A vision call normally takes 3-15 s. We wait up to 60 s for the
# reply, and retry once on network trouble or a temporary OpenAI error.
TIMEOUT      = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)
MAX_ATTEMPTS = 2
RETRY_STATUS = (429, 500, 502, 503, 504)


PLACEHOLDER_HINTS = ("sk-your", "your-key", "your_key", "yourkey", "xxxx", "paste", "<", "...")


def _mask(key: str) -> str:
    """Show enough to recognise a key without revealing it."""
    if len(key) > 12:
        return f"{key[:7]}...{key[-4:]} ({len(key)} characters)"
    return f"'{key}' ({len(key)} characters)"


def get_api_key() -> str:
    key = os.getenv("OPENAI_API_KEY", "").strip().strip('"').strip("'")
    if not key:
        raise EnvironmentError(
            "OPENAI_API_KEY environment variable is not set. "
            "Set it in the SAME terminal that starts the server, then restart the server."
        )
    low = key.lower()
    if any(h in low for h in PLACEHOLDER_HINTS) or len(key) < 20 or not key.startswith("sk-"):
        raise EnvironmentError(
            f"The server sees OPENAI_API_KEY = {_mask(key)}, which does not look like a real key "
            "(real keys start with sk- and are 50+ characters). Set the real key in the terminal "
            "that starts the server, then restart the server."
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

    # Pre-process the image before sending — auto-levels, sharpen, upscale.
    # This is the single biggest accuracy improvement for low-quality inputs.
    enhanced_b64 = preprocess_for_extraction(image_b64)

    if capture_type == "nameplate":
        payload = await _extract_nameplate(enhanced_b64, voice_hint)
        return ExtractionResult(type="nameplate", payload=payload)

    payload = await _extract_damage(enhanced_b64, voice_hint)
    return ExtractionResult(type="damage", payload=payload)


# ── Nameplate ─────────────────────────────────────────────────────────────────

async def _extract_nameplate(b64: str, hint: Optional[str]) -> NameplateExtraction:
    system = """You are an expert OCR assistant for property insurance claims.
You are reading an appliance nameplate photo. The photo may be dark, blurry, 
taken at an angle, or partially obscured — this is normal in field conditions.

YOUR JOB: Extract every field you can. Never return null for a field without 
genuinely attempting inference. Use these strategies:

- Partial text: reconstruct from visible characters (e.g. "GE" + partial model → "GEW_")
- Faded ink: increase contrast mentally, read the embossed shape
- Obstructed: infer from surrounding context (brand fonts, label layout)  
- Rotated/angled: mentally correct the perspective and read
- If a serial number is partly cut off, return what is visible with low confidence
- Manufacture date: often encoded in serial number (first 4 digits = year+week, 
  or 2-letter month codes like "A"=Jan). Try to decode it.

For each field return:
  value: the best guess string, or null ONLY if zero information is available
  confidence: 0.0–1.0
    0.9+ clear and unambiguous
    0.7–0.9 mostly clear, minor uncertainty
    0.5–0.7 partial read or inferred
    0.3–0.5 best guess from context
    <0.3 barely readable, include anyway with low confidence
  source: "vision_api"

Return ONLY valid JSON:
{
  "make":             {"value": string|null, "confidence": float, "source": "vision_api"},
  "model":            {"value": string|null, "confidence": float, "source": "vision_api"},
  "serial_number":    {"value": string|null, "confidence": float, "source": "vision_api"},
  "manufacture_date": {"value": string|null, "confidence": float, "source": "vision_api"},
  "raw_text": "everything you can read from the label verbatim",
  "overall_confidence": float
}"""

    user_text = (
        "Read all text from this appliance nameplate. "
        "The image may be low quality — do your best to infer every field."
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
You are looking at a photo of water or structural damage. The photo may be 
dark, blurry, taken at an angle, or low resolution — this is normal in field work.

YOUR JOB: Extract every field you can. Use aggressive inference.

Strategies for low-quality images:
- Dark image: look for shape outlines, water marks, discolouration patterns
- Blurry image: identify the general damage category from overall shapes and colours
- Partial view: estimate room from visible fixtures (toilet=bathroom, stove=kitchen)
- Size estimation: use ANY visible reference object — door frame ~2m, light switch 
  ~1.1m from floor, outlet ~0.3m wide, baseboard ~0.1m tall, tile ~0.3m square.
  Always attempt a size estimate; never return null for size without trying.
- Severity: even from a blurry image you can tell "small stain" vs "large buckle"

For each field return value, confidence (0.0–1.0), and source.
Set confidence below 0.5 for heavily inferred values.
Never return null for damage_type or severity_level — always make a best guess.

Return ONLY valid JSON:
{
  "room":           {"value": string, "confidence": float, "source": string},
  "surface":        {"value": string, "confidence": float, "source": string},
  "damage_type":    {"value": string, "confidence": float, "source": string},
  "rough_size_m2":  {"value": string, "confidence": float, "source": string},
  "severity_level": {"value": "low"|"medium"|"high", "confidence": float, "source": string},
  "overall_confidence": float
}"""

    voice_note = ""
    if hint:
        voice_note = (
            f" Voice command provided this context: {hint}. "
            "Treat room and surface from voice as ground truth — only override "
            "if the image clearly contradicts them."
        )

    user_text = (
        "Assess the damage in this image. "
        "Even if the image quality is poor, provide your best estimate for every field."
        + voice_note
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
                    "detail": "high",   # always use high detail for field extraction
                }},
            ]},
        ],
    }

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(OPENAI_URL, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            print(f"[Extraction] attempt {attempt}/{MAX_ATTEMPTS} network error: {type(e).__name__}")
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(1.5)
                continue
            raise RuntimeError(
                f"No usable response from OpenAI ({type(e).__name__}) after {MAX_ATTEMPTS} attempts. "
                "This is a network problem between this computer and api.openai.com "
                "(slow connection, VPN, proxy, firewall/antivirus). Run check_openai.py."
            ) from e

        if resp.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
            print(f"[Extraction] attempt {attempt}/{MAX_ATTEMPTS} got HTTP {resp.status_code}, retrying")
            await asyncio.sleep(1.5)
            continue

        if resp.status_code != 200:
            try:
                msg = resp.json().get("error", {}).get("message", resp.text)
            except Exception:
                msg = resp.text
            raise RuntimeError(f"OpenAI returned HTTP {resp.status_code}: {msg}")

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