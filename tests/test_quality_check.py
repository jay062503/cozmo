# tests/test_quality_check.py
# Tests for quality check threshold logic.
# Creates synthetic bright/dark/blurry images using Pillow so no real
# camera frames are needed to run the test suite.

import sys, os, base64, io
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from PIL import Image, ImageFilter
import numpy as np
from backend.services.quality_check import check_frame, THRESHOLDS


def _make_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def _solid(brightness: int, size=(200, 200)) -> str:
    img = Image.fromarray(
        np.full((*size, 3), brightness, dtype=np.uint8)
    )
    return _make_b64(img)


def _sharp_image() -> str:
    """High-contrast checkerboard — high Laplacian variance."""
    arr = np.zeros((200, 200, 3), dtype=np.uint8)
    for y in range(200):
        for x in range(200):
            arr[y, x] = 255 if (x // 20 + y // 20) % 2 == 0 else 0
    return _make_b64(Image.fromarray(arr))


def _blurry_image() -> str:
    """Heavily blurred checkerboard — low Laplacian variance."""
    arr = np.zeros((200, 200, 3), dtype=np.uint8)
    for y in range(200):
        for x in range(200):
            arr[y, x] = 200 if (x // 20 + y // 20) % 2 == 0 else 50
    img = Image.fromarray(arr)
    for _ in range(10):
        img = img.filter(ImageFilter.BLUR)
    return _make_b64(img)


# ── Threshold sanity ─────────────────────────────────────────────────────────

def test_min_blur_threshold_reasonable():
    # Threshold is now 15.0 — relaxed to allow low-quality field photos
    assert 5.0 < THRESHOLDS.min_blur_score < 300.0

def test_min_brightness_threshold_reasonable():
    # Threshold is now 15.0 — backend auto-brightens before checking
    assert 5.0 < THRESHOLDS.min_brightness < 80.0

def test_min_framing_threshold_reasonable():
    # Threshold is now 0.05 — advisory only
    assert 0.01 < THRESHOLDS.min_framing < 0.5


# ── Brightness checks ────────────────────────────────────────────────────────

def test_bright_image_passes_brightness():
    result = check_frame(_solid(150), "damage")
    assert result.brightness_score > THRESHOLDS.min_brightness

def test_dark_image_fails_brightness():
    result = check_frame(_solid(10), "damage")
    assert result.brightness_score < THRESHOLDS.min_brightness
    assert not result.passed
    # Message wording changed — just check it mentions darkness
    assert any("dark" in r.lower() or "bright" in r.lower() for r in result.failure_reasons)


# ── Blur checks ──────────────────────────────────────────────────────────────

def test_sharp_image_has_higher_blur_score_than_blurry():
    sharp_result  = check_frame(_sharp_image(),  "nameplate")
    blurry_result = check_frame(_blurry_image(), "nameplate")
    assert sharp_result.blur_score > blurry_result.blur_score


# ── Result fields ────────────────────────────────────────────────────────────

def test_passed_result_has_no_failure_reasons():
    result = check_frame(_sharp_image(), "damage")
    # May or may not pass depending on framing, but failure_reasons must match passed
    if result.passed:
        assert result.failure_reasons == []

def test_thresholds_are_included_in_result():
    result = check_frame(_solid(150), "nameplate")
    assert result.thresholds_used.min_blur_score == THRESHOLDS.min_blur_score
    assert result.thresholds_used.min_brightness == THRESHOLDS.min_brightness


def test_dim_image_now_passes():
    # Brightness 35 used to hard-fail (old threshold was 40).
    # After auto-levels pre-processing it should pass.
    result = check_frame(_solid(35), "damage")
    # Either passes outright, or passes with a warning (failure_reasons may have warnings)
    # The key is quality_passed must be True for a dim-but-not-black image.
    assert result.passed, f"Dim image should pass after pre-processing, reasons: {result.failure_reasons}"


def test_blurry_upload_passes_with_warning():
    # Blurry uploads should pass (advisory-only) so the user doesn't get blocked.
    result = check_frame(_blurry_image(), "upload")
    assert result.passed, f"Blurry upload should not hard-block, reasons: {result.failure_reasons}"
