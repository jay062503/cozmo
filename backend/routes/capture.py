# routes/capture.py
# API route for processing a single capture:
#   1. Quality check the frame
#   2. Extract fields via GPT-4o
#   3. Add to job record
#   4. Enqueue upload
#   Returns the full capture event so the browser can display results immediately.

import uuid
import base64
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from backend.services.quality_check import check_frame
from backend.services.extraction    import extract
from backend.services.upload_queue  import queue as upload_queue
from backend.routes.session         import get_builder

UPLOAD_DIR = Path(__file__).parent.parent.parent / "uploads"
router = APIRouter(prefix="/capture", tags=["capture"])


class CaptureRequest(BaseModel):
    session_id:   str
    capture_type: str            # "nameplate" | "damage" | "note"
    image_b64:    str            # data-URI or raw base64 JPEG
    voice_hint:   Optional[str] = None   # e.g. "room: kitchen, surface: north wall"
    skip_quality: bool = False   # allow override for note captures (no image)


class CaptureResponse(BaseModel):
    capture_id:  str
    quality_passed: bool
    failure_reasons: list[str]
    extraction:  dict
    line_items:  list
    upload_status: str


@router.post("/process", response_model=CaptureResponse)
async def process_capture(req: CaptureRequest):

    # ── 1. Quality check ───────────────────────────────────────────────────
    if req.skip_quality or req.capture_type == "note":
        from backend.models.job_record import QualityCheckResult, QualityThresholds
        quality = QualityCheckResult(
            passed=True, blur_score=200.0, brightness_score=128.0,
            framing_score=0.5, failure_reasons=[],
            thresholds_used=QualityThresholds(),
        )
    else:
        quality = check_frame(req.image_b64, req.capture_type)

    if not quality.passed:
        # Return early — don't waste an API call on a bad frame
        return CaptureResponse(
            capture_id="",
            quality_passed=False,
            failure_reasons=quality.failure_reasons,
            extraction={},
            line_items=[],
            upload_status="rejected",
        )

    # ── 2. Extraction ──────────────────────────────────────────────────────
    try:
        extraction = await extract(req.image_b64, req.capture_type, req.voice_hint)
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"[Extraction] FAILED: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail=f"Extraction failed: {type(e).__name__}: {e}")

    # ── 3. Save image to disk ──────────────────────────────────────────────
    capture_id = "cap-" + str(uuid.uuid4())[:8]
    image_path = None

    if req.capture_type != "note" and req.image_b64:
        image_path = _save_image(req.image_b64, capture_id)

    # ── 4. Add to job record ───────────────────────────────────────────────
    builder = get_builder(req.session_id)

    if req.capture_type == "note":
        from backend.models.job_record import NoteExtraction
        note_payload: NoteExtraction = extraction.payload
        builder.add_note(req.voice_hint or "", note_payload)
    else:
        builder.add_capture(
            capture_type=req.capture_type,
            frame_id=capture_id,
            image_path=image_path,
            quality=quality,
            extraction=extraction,
        )

    # ── 5. Enqueue upload ──────────────────────────────────────────────────
    upload_queue.enqueue(
        capture_id=capture_id,
        job_id=builder.job_id,
        image_path=image_path or "",
        capture_type=req.capture_type,
    )
    upload_queue.mark_uploaded(capture_id)   # Simulated immediate upload on LAN

    # ── 6. Return result ───────────────────────────────────────────────────
    record     = builder.current()
    line_items = [li.model_dump() for li in record.line_items] if record else []

    return CaptureResponse(
        capture_id=capture_id,
        quality_passed=True,
        failure_reasons=[],
        extraction=extraction.model_dump(),
        line_items=line_items,
        upload_status="uploaded",
    )


def _save_image(b64: str, capture_id: str) -> str:
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{capture_id}.jpg"
    path.write_bytes(base64.b64decode(b64))
    return str(path)