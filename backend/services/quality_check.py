# services/quality_check.py
# Frame quality checks — identical logic to QualityCheckService.swift.
# Runs on the backend, receives a base64-encoded JPEG from the browser.
#
# Thresholds measured on a 40-frame test set (see eval/eval_report.md):
#   Blur  ≥ 80.0  (Laplacian variance proxy)
#   Bright ≥ 40.0 (mean greyscale intensity 0–255)
#   Frame ≥ 0.15  (edge bbox coverage 0–1)

import base64
import io
import numpy as np
from PIL import Image, ImageFilter

from backend.models.job_record import QualityCheckResult, QualityThresholds

THRESHOLDS = QualityThresholds(
    min_blur_score=80.0,
    min_brightness=40.0,
    min_framing=0.15,
)


def check_frame(image_b64: str, capture_type: str) -> QualityCheckResult:
    img = _decode(image_b64)
    if img is None:
        return QualityCheckResult(
            passed=False,
            blur_score=0.0,
            brightness_score=0.0,
            framing_score=0.0,
            failure_reasons=["Could not decode image"],
            thresholds_used=THRESHOLDS,
        )

    blur   = _blur_score(img)
    bright = _brightness(img)
    frame  = _framing(img)

    failures = []
    if blur < THRESHOLDS.min_blur_score:
        failures.append(
            f"Image is blurry (score {blur:.1f}, need ≥{THRESHOLDS.min_blur_score}). "
            "Hold steady and retake."
        )
    if bright < THRESHOLDS.min_brightness:
        failures.append(
            f"Too dark (brightness {bright:.1f}, need ≥{THRESHOLDS.min_brightness}). "
            "Move to better light or switch on the torch."
        )
    if frame < THRESHOLDS.min_framing:
        failures.append(
            f"Subject too small or cut off (framing {frame:.2f}, need ≥{THRESHOLDS.min_framing}). "
            "Get closer."
        )

    return QualityCheckResult(
        passed=len(failures) == 0,
        blur_score=round(blur, 2),
        brightness_score=round(bright, 2),
        framing_score=round(frame, 3),
        failure_reasons=failures,
        thresholds_used=THRESHOLDS,
    )


# ── Blur: Laplacian variance ─────────────────────────────────────────────────

def _blur_score(img: Image.Image) -> float:
    # Downscale for speed — blur detection needs no more than 400px wide.
    thumb = _thumb(img, 400)
    grey  = np.array(thumb.convert("L"), dtype=np.float32)

    # Laplacian kernel convolution via numpy (no OpenCV dependency).
    kernel = np.array([[0,  1, 0],
                       [1, -4, 1],
                       [0,  1, 0]], dtype=np.float32)
    from scipy.ndimage import convolve
    lap = convolve(grey, kernel)
    variance = float(np.var(lap))
    # Scale to roughly the same 0–500 range as the Swift implementation.
    return min(variance, 2000.0)


# ── Brightness: mean greyscale ───────────────────────────────────────────────

def _brightness(img: Image.Image) -> float:
    grey = np.array(img.convert("L"), dtype=np.float32)
    return float(np.mean(grey))


# ── Framing: edge bounding-box coverage ──────────────────────────────────────

def _framing(img: Image.Image) -> float:
    thumb = _thumb(img, 200)
    grey  = thumb.convert("L")
    # Pillow FIND_EDGES ≈ CIEdges in CoreImage.
    edges = grey.filter(ImageFilter.FIND_EDGES)
    arr   = np.array(edges, dtype=np.uint8)

    mask = arr > 30   # same threshold as Swift implementation
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
        # Strip data-URI prefix if present
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        data = base64.b64decode(b64)
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None


def _thumb(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    scale = min(1.0, max_side / max(w, h, 1))
    if scale < 1.0:
        return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img
