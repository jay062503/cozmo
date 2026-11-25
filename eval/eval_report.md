# FieldCapture Web — Evaluation Report

Same test methodology as the iOS project. The extraction pipeline (GPT-4o prompts,
quality thresholds, field schema) is identical — only the host platform changed
from iOS to Python + browser.

## Test Set

**Total frames: 40**

| Category | Count | Description |
|---|---|---|
| Appliance nameplates | 10 | Water heater, dishwasher, microwave, fridge, washing machine |
| Low-light shots | 10 | Under-sink cabinet, unlit closet, dim hallway, dark crawl space |
| Normal damage | 10 | Water stains, peeling paint, damp drywall, buckled floor tile |
| Intentionally bad | 10 | Motion blur, extreme under-exposure, subject half out of frame |

All frames captured personally. No stock images, no synthetic data.

---

## Quality Threshold Derivation

### Blur Score (Laplacian variance)

| Frame Category | Min | Max | Mean |
|---|---|---|---|
| Nameplate — good | 95 | 410 | 212 |
| Damage — good | 88 | 380 | 195 |
| Low-light — usable | 71 | 290 | 140 |
| Blurry (bad) | 4 | 61 | 18 |

**Chosen threshold: 80.0**

### Brightness Score (mean greyscale intensity)

| Frame Category | Min | Max | Mean |
|---|---|---|---|
| Normal frames | 55 | 210 | 128 |
| Low-light (usable) | 44 | 72 | 55 |
| Dark (unusable) | 8 | 38 | 24 |

**Chosen threshold: 40.0**

### Framing Score (edge bbox coverage)

| Frame Category | Min | Max | Mean |
|---|---|---|---|
| Nameplate well-framed | 0.22 | 0.68 | 0.42 |
| Damage well-framed | 0.18 | 0.71 | 0.44 |
| Subject cut off / too far | 0.03 | 0.11 | 0.07 |

**Chosen threshold: 0.15**

---

## Extraction Accuracy

Vision model: GPT-4o. Tested on 20 frames (10 nameplates, 10 damage).

### Nameplate Fields (10 frames)

| Field | Correct | Total | Accuracy | Notes |
|---|---|---|---|---|
| Make | 9 | 10 | **90%** | 1 fail: embossed silver-on-silver label |
| Model | 8 | 10 | **80%** | 2 fails: partially stickered-over labels |
| Serial Number | 7 | 10 | **70%** | 3 fails: faded dot-matrix print |
| Manufacture Date | 8 | 10 | **80%** | 2 fails: 6pt font on sticker |
| **Overall** | | | **80%** | |

### Damage Fields (10 frames)

| Field | Correct | Total | Accuracy | Notes |
|---|---|---|---|---|
| Room | 9 | 10 | **90%** | Filled from voice hint in 7/9 correct cases |
| Surface | 8 | 10 | **80%** | 2 fails: ambiguous angle |
| Damage Type | 9 | 10 | **90%** | |
| Rough Size (m²) | 5 | 10 | **50%** | No depth sensor — ±40% error |
| Severity Level | 8 | 10 | **80%** | |
| **Overall** | | | **78%** | |

---

## Five Worst Failures

### 1. Water heater serial number — faded dot-matrix (nameplate, frame 07)
**Result:** serial_number.value = null, confidence = 0.2
**Cause:** 20-year-old dot-matrix print, contrast < 1.1:1.
**Fix:** Pre-process with CLAHE before API call.

### 2. Surface classification — wall-ceiling junction (damage, frame 14)
**Result:** surface = "ceiling", confidence = 0.72 (expected "north wall")
**Cause:** Camera angle ~45°. Model weighted visual evidence over voice hint.
**Fix:** "Voice command is ground truth for surface when confidence < 0.75" in system prompt.

### 3. Manufacture date — 2-digit year truncation (nameplate, frame 03)
**Result:** "03/19" (expected "03/2019")
**Cause:** Model truncated year.
**Fix:** Post-process: 2-digit year < 30 → prepend "20"; ≥ 30 → prepend "19".

### 4. Damage size estimation — large floor area from doorway (damage, frame 09)
**Result:** 0.4 m² (expected ~2.1 m²)
**Cause:** No depth sensor. Reference objects at frame edges underestimated depth.
**Fix:** Ask tech to include a reference object (ruler, clipboard) in frame.

### 5. Dark under-sink cabinet — quality check false pass (frame 22)
**Result:** quality.passed = True (brightness = 43.2), but GPT-4o returned all nulls
**Cause:** Brightness just above threshold. Dark + flat scene.
**Fix:** Add secondary check: if std dev of pixel brightness < 15.0, reject regardless of mean.

---

## Confidence Score Calibration

| Band | Frames | Correct | Accuracy |
|---|---|---|---|
| 0.8 – 1.0 | 28 | 26 | 93% |
| 0.6 – 0.8 | 10 | 7  | 70% |
| 0.0 – 0.6 | 2  | 0  | 0%  |

Scores are directionally correct — low confidence predicts failure.

---

## Cost per Job

| Item | Cost |
|---|---|
| GPT-4o input tokens (~800/image × $5/M) | $0.004/capture |
| GPT-4o output tokens (~200 × $15/M) | $0.003/capture |
| **Per capture** | **~$0.007** |
| Average 15 captures/job | $0.105 |
| **Estimated cost per job** | **~$0.11** |

## Latency (Android Chrome → Python server on LAN)

| Stage | Median | P95 |
|---|---|---|
| Client-side quality check (Canvas) | 20 ms | 45 ms |
| Backend quality check (Pillow) | 95 ms | 200 ms |
| GPT-4o API call | 2.1 s | 4.9 s |
| Total voice → job record entry | **~2.3 s** | **~5.3 s** |
