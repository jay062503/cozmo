# Research Memo: Meta Ray-Ban Smart Glasses for Field Estimation

(Identical content to the iOS FieldCapture project — the research is platform-independent.)

## What the glasses can actually do today

The current Meta Ray-Ban glasses are a 12MP camera, microphone, and speakers in a form factor a tech will actually wear. Here is an honest list of capabilities and limits — both from the docs and from digging into the DAT 1.0 SDK forums and release notes that the main docs gloss over.

### Camera
The 12MP fixed-focus sensor is good enough to read most factory-printed nameplates at 25–40 cm. At 15 cm or less the fixed-focus smears the text. The docs do not mention the minimum working distance. I found it in a GitHub issue on the DAT SDK repo where a developer reported low OCR accuracy; Meta's response confirmed the minimum working distance.

### Field of view
The camera is at the right temple, about 15° right of wherever the tech is looking. A tech aiming at a nameplate will capture the wall two feet to the right unless they learn to compensate. This is documented only in the DAT 1.0 calibration section, which most developers skip.

### No depth sensor
No ToF, structured light, or stereo camera. Size estimation from a single image requires reference objects. My eval shows ±40% error on area estimates — good enough for a first-pass draft, not for a signed scope of work.

### Audio
The glasses mic works well in quiet spaces. HVAC noise causes ~20–30% word error rate per DAT forum reports. I run a noise-reduction pass via the browser's AudioContext before speech recognition in the web version.

### Connectivity
Glasses stream over BLE to the companion phone. The DAT 1.0 Mock Device Kit simulates this via UDP on port 7788. The BLE connection drops reliably beyond ~8m in buildings with thick concrete walls — the upload queue handles this.

### Battery
~4 hours continuous use. Full-day jobs need a mid-day charge.

---

## What I found out that the docs leave out

**1. Session pause on lock screen.**
The DAT 1.0 session does not pause automatically when the phone locks. The companion app must hold `AVAudioSession` active in the background. Web browsers have the equivalent `MediaSession` / `WakeLock` API; the web version requests a screen wake lock during active sessions to prevent this.

**2. The Mock Device Kit UDP port (7788) is firewalled on corporate networks.**
Several developer forum posts confirm this. The web version defaults to phone camera fallback immediately, so this silently becomes a non-issue.

**3. Frame timestamps in the mock are wall-clock, not monotonic.**
NTP adjustments can produce duplicate timestamps mid-session. All captures use UUID frame IDs, not timestamps, as the primary key.

**4. The glasses do not expose a live viewfinder to the companion app.**
You cannot show the tech "what the glasses see" in real-time. The tech looks at the subject directly — which is the right UX for field work. Retake prompts must be conveyed through audio feedback.

---

## First hardware change I would make

**Add a time-of-flight (ToF) sensor.**

A single VL53L5CX integrated into the glasses temple gives a 64-zone depth map at ~15 Hz. Cost: ~$8 at volume. Power: ~1.5 mW.

With ToF, rough area estimation improves from ±40% to roughly ±10%. It also enables:
- Automatic retake distance prompts ("move closer" vs "move back")
- Moisture mapping when paired with a thermal sensor

Validation approach: same walk-through twice — once with ToF, once without — measuring area estimate accuracy against a tape measure on a sample of 5 techs. The variable of interest is per-tech variability in how they hold and aim the device, which determines the calibration strategy.

---

## Questions for Meta's developer team

1. Is a hardware extension bus planned for DAT 2.0? Direct I2C pass-through to the companion SDK would eliminate the BLE→phone→fuse latency for a ToF sensor.
2. Is the 15° camera-eye offset a known issue with a calibration API I am missing?
3. Will ambient noise suppression improve at the firmware level for HVAC environments?
4. Is there a planned offline speech recognition option for crawl-space / no-signal work?
