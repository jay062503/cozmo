# services/quality_check.py
# Frame quality checks.
#
# Design principle: get output at all costs.
# A bad frame sent to GPT-4o gives a low-confidence result.
# A rejected frame gives nothing. So we reject as little as possible.
#
# Hard blocks (returned as passed=False):
#   - Image completely blank (brightness < 8) — camera not streaming
#   - Image could not be decoded
#
# Everything else passes. Blur and framing produce warnings in
# failure_reasons but do NOT set passed=False. The UI shows them
# as advisory ⚠️ banners that auto-dismiss — the extraction still runs.
#
# Before quality-scoring, the image is pre-processed:
#   1. Upscale to at least 600px short-side (webcams often send tiny frames)
#   2. Auto-levels: stretch histogram to 0-255 (fixes washed-out / dim shots)
#   3. Brightness boost for genuinely dark images
#   4. Contrast +35% and mild unsharp mask (makes text edges readable)
# The pre-processed image is also what gets sent to GPT-4o.

import base64
import io
import numpy as np
from PIL import Image, ImageFilter, ImageEnhance

from backend.models.job_record import QualityCheckResult, QualityThresholds

# Thresholds used only for advisory warnings — not for hard-blocking.
THRESHOLDS = QualityThresholds(
    min_blur_score=30.0,
    min_brightness=20.0,
    min_framing=0.05,
)

# Hard-block only below this raw brightness (image is completely black).
_HARD_BLOCK_BRIGHTNESS = 8.0


def check_frame(image_b64: str, capture_type: str) -> QualityCheckResult:
    """
    Decode, pre-process, and quality-check the image.
    Returns passed=True for anything a human could plausibly read.
    Only returns passed=False for a blank / undecodable image.
    """
    img = _decode(image_b64)
    if img is None:
        return QualityCheckResult(
            passed=False,
            blur_score=0.0,
            brightness_score=0.0,
            framing_score=0.0,
            failure_reasons=["Could not decode image — make sure it is a valid JPG or PNG."],
            thresholds_used=THRESHOLDS,
        )

    # Check raw brightness BEFORE pre-processing — if the camera isn't
    # streaming at all, pre-processing can't save it.
    raw_bright = _brightness(img)
    if raw_bright < _HARD_BLOCK_BRIGHTNESS:
        return QualityCheckResult(
            passed=False,
            blur_score=0.0,
            brightness_score=round(raw_bright, 2),
            framing_score=0.0,
            failure_reasons=[
                f"Frame is completely black (brightness {raw_bright:.1f}). "
                "The camera stream may not be active — stop and restart the session."
            ],
            thresholds_used=THRESHOLDS,
        )

    # Pre-process to maximise extraction accuracy.
    img = _preprocess(img)

    blur   = _blur_score(img)
    bright = _brightness(img)
    frame  = _framing(img)

    # Build advisory warnings — none of these block the capture.
    warnings = []
    if blur < THRESHOLDS.min_blur_score:
        warnings.append(
            f"Image is blurry (score {blur:.1f}) — confidence scores will be lower."
        )
    if bright < THRESHOLDS.min_brightness:
        warnings.append(
            f"Image is dark (brightness {bright:.1f}) — try better lighting."
        )
    if frame < THRESHOLDS.min_framing:
        warnings.append(
            f"Subject may be small or cut off (framing {frame:.2f}) — get closer if possible."
        )

    return QualityCheckResult(
        passed=True,              # always pass — warnings don't block
        blur_score=round(blur, 2),
        brightness_score=round(bright, 2),
        framing_score=round(frame, 3),
        failure_reasons=warnings, # warnings surface as ⚠️ in the UI
        thresholds_used=THRESHOLDS,
    )


def preprocess_for_extraction(image_b64: str) -> str:
    """
    Enhance the image before sending to GPT-4o.
    Returns a new base64 JPEG string (with data-URI prefix).
    If decoding fails, returns the original unchanged.
    """
    img = _decode(image_b64)
    if img is None:
        return image_b64
    img = _preprocess(img)
    return _encode(img)


# ── Pre-processing pipeline ───────────────────────────────────────────────────

def _preprocess(img: Image.Image) -> Image.Image:
    # 1. Upscale small frames — tiny webcam frames lose OCR accuracy.
    img = _upscale_if_small(img, min_short_side=600)

    # 2. Auto-levels — stretch histogram to full 0-255 range.
    img = _auto_levels(img)

    # 3. Brightness boost for still-dim images after auto-levels.
    grey_mean = float(np.mean(np.array(img.convert("L"), dtype=np.float32)))
    if grey_mean < 90:
        factor = min(2.8, 110.0 / max(grey_mean, 1))
        img = ImageEnhance.Brightness(img).enhance(factor)

    # 4. Contrast boost — makes text edges pop.
    img = ImageEnhance.Contrast(img).enhance(1.35)

    # 5. Mild unsharp mask — recovers slight motion blur.
    img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=110, threshold=3))

    return img


def _upscale_if_small(img: Image.Image, min_short_side: int) -> Image.Image:
    w, h = img.size
    short = min(w, h)
    if short < min_short_side:
        scale = min_short_side / short
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def _auto_levels(img: Image.Image) -> Image.Image:
    arr = np.array(img, dtype=np.float32)
    for c in range(3):
        ch = arr[:, :, c]
        lo, hi = ch.min(), ch.max()
        if hi > lo:
            arr[:, :, c] = (ch - lo) / (hi - lo) * 255.0
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8))


# ── Quality metrics ───────────────────────────────────────────────────────────

def _blur_score(img: Image.Image) -> float:
    thumb = _thumb(img, 400)
    grey  = np.array(thumb.convert("L"), dtype=np.float32)
    kernel = np.array([[0,  1, 0],
                       [1, -4, 1],
                       [0,  1, 0]], dtype=np.float32)
    from scipy.ndimage import convolve
    lap = convolve(grey, kernel)
    return min(float(np.var(lap)), 2000.0)


def _brightness(img: Image.Image) -> float:
    grey = np.array(img.convert("L"), dtype=np.float32)
    return float(np.mean(grey))


def _framing(img: Image.Image) -> float:
    thumb = _thumb(img, 200)
    edges = thumb.convert("L").filter(ImageFilter.FIND_EDGES)
    arr   = np.array(edges, dtype=np.uint8)
    # Use a low edge threshold so webcam/compressed images still get coverage.
    mask  = arr > 15
    if not mask.any():
        return 0.0
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]
    subject_area = float((rmax - rmin) * (cmax - cmin))
    total_area   = float(arr.shape[0] * arr.shape[1])
    return min(subject_area / total_area, 1.0) if total_area > 0 else 0.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _decode(b64: str) -> Image.Image | None:
    try:
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        data = base64.b64decode(b64)
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None


def _encode(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _thumb(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    scale = min(1.0, max_side / max(w, h, 1))
    if scale < 1.0:
        return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img
