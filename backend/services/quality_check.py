# services/quality_check.py
# Frame quality checks with relaxed thresholds suitable for real-world
# phone photos, gallery uploads, and low-light field conditions.
#
# Design change from original:
#   - Hard-reject only truly unusable images (blank, completely black)
#   - Warn on borderline quality but still pass the frame to extraction
#   - Pre-process (brighten, sharpen, upscale) before rejecting on blur/dark
#   - Framing check is advisory only — never blocks a capture
#
# New thresholds (after pre-processing):
#   Blur    ≥ 15.0   (was 80.0 — was rejecting motion-blurred but legible photos)
#   Bright  ≥ 15.0   (was 40.0 — was rejecting dim but readable shots)
#   Framing ≥ 0.05   (was 0.15 — advisory only now anyway)

import base64
import io
import numpy as np
from PIL import Image, ImageFilter, ImageEnhance, ImageOps

from backend.models.job_record import QualityCheckResult, QualityThresholds

# Thresholds after pre-processing has been applied.
THRESHOLDS = QualityThresholds(
    min_blur_score=15.0,
    min_brightness=15.0,
    min_framing=0.05,
)


def check_frame(image_b64: str, capture_type: str) -> QualityCheckResult:
    """
    Decode, pre-process, and quality-check the image.
    Returns a result where `passed=True` for anything a human could
    reasonably read, even if the original was dark or slightly blurry.
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

    # ── Pre-process to improve marginal images ──────────────────────────────
    img = _preprocess(img)

    blur   = _blur_score(img)
    bright = _brightness(img)
    frame  = _framing(img)

    failures = []
    warnings = []

    # Only hard-reject completely unusable frames.
    if bright < THRESHOLDS.min_brightness:
        failures.append(
            f"Image is too dark to read (brightness {bright:.1f} after auto-enhance). "
            "Try turning on a light or the phone torch."
        )

    if blur < THRESHOLDS.min_blur_score:
        # For uploads we warn but don't block — the image can't be retaken.
        # For live camera we block so the tech can steady the shot.
        if capture_type == "upload":
            warnings.append(
                f"Image is blurry (score {blur:.1f}) — accuracy may be lower."
            )
        else:
            failures.append(
                f"Image is too blurry (score {blur:.1f}). "
                "Hold steady and retake."
            )

    # Framing is advisory only — never hard-blocks a capture.
    if frame < THRESHOLDS.min_framing:
        warnings.append(
            f"Subject may be too small or cut off (framing {frame:.2f}). "
            "Get closer if possible."
        )

    # Treat warnings as a passed check with notes in failure_reasons
    all_messages = failures + warnings

    return QualityCheckResult(
        passed=len(failures) == 0,   # only hard failures block the capture
        blur_score=round(blur, 2),
        brightness_score=round(bright, 2),
        framing_score=round(frame, 3),
        failure_reasons=all_messages,
        thresholds_used=THRESHOLDS,
    )


def preprocess_for_extraction(image_b64: str) -> str:
    """
    Public entry point called by extraction.py before sending to GPT-4o.
    Returns a new base64 JPEG that has been enhanced for maximum readability.
    """
    img = _decode(image_b64)
    if img is None:
        return image_b64
    img = _preprocess(img)
    return _encode(img)


# ── Pre-processing pipeline ──────────────────────────────────────────────────

def _preprocess(img: Image.Image) -> Image.Image:
    """
    Apply a chain of enhancements so that dark, compressed, or small images
    become legible. Each step is conservative — we don't want to hallucinate
    text that wasn't there.
    """
    # 1. Upscale small images — GPT-4o reads detail tokens, tiny images waste them.
    img = _upscale_if_small(img, min_side=600)

    # 2. Auto-levels: stretch histogram to use full 0–255 range.
    #    This fixes washed-out and underexposed images in one step.
    img = _auto_levels(img)

    # 3. Brightness boost for genuinely dark images.
    grey_mean = float(np.mean(np.array(img.convert("L"), dtype=np.float32)))
    if grey_mean < 80:
        # Boost proportionally — darker images get more lift.
        factor = min(2.5, 110.0 / max(grey_mean, 1))
        img = ImageEnhance.Brightness(img).enhance(factor)

    # 4. Contrast enhancement — makes text edges pop.
    img = ImageEnhance.Contrast(img).enhance(1.4)

    # 5. Mild sharpening — recovers slight motion blur without hallucinating edges.
    img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=120, threshold=3))

    return img


def _upscale_if_small(img: Image.Image, min_side: int) -> Image.Image:
    w, h = img.size
    short = min(w, h)
    if short < min_side:
        scale = min_side / short
        new_w = int(w * scale)
        new_h = int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
    return img


def _auto_levels(img: Image.Image) -> Image.Image:
    """Stretch each channel's histogram to [0, 255]."""
    arr = np.array(img, dtype=np.float32)
    for c in range(3):
        ch = arr[:, :, c]
        lo, hi = ch.min(), ch.max()
        if hi > lo:
            arr[:, :, c] = (ch - lo) / (hi - lo) * 255.0
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8))


# ── Quality metrics ──────────────────────────────────────────────────────────

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
    mask  = arr > 20   # lowered from 30 — catches faint edges in low-contrast images
    if not mask.any():
        return 0.0
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]
    subject_area = float((rmax - rmin) * (cmax - cmin))
    total_area   = float(arr.shape[0] * arr.shape[1])
    return min(subject_area / total_area, 1.0) if total_area > 0 else 0.0


# ── Helpers ──────────────────────────────────────────────────────────────────

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
