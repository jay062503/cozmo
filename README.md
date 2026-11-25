# FieldCapture Web

The same field-capture pipeline as the iOS FieldCapture project, rebuilt as a
**Python (FastAPI) + browser app** so it runs on a Windows laptop and an Android phone
without any iOS/Mac hardware.

**Device setup:**
- Laptop (Windows) — runs the Python server
- Android phone (Chrome) — camera + microphone + UI

**AI tools used:** GitHub Copilot for FastAPI/httpx boilerplate. All prompts, threshold
logic, eval methodology, and architecture decisions written and reviewed manually.

---

## Setup (under 10 minutes)

### 1. Install Python dependencies

```
cd c:\cozmo\FieldCaptureWeb
pip install -r requirements.txt
```

### 2. Set your OpenAI API key

PowerShell:
```
$env:OPENAI_API_KEY = "sk-your-key-here"
```

Or create a `.env` file and load it before running (key is never hardcoded).

### 3. Find your laptop's local IP address

```
ipconfig
```
Look for **IPv4 Address** under your Wi-Fi adapter, e.g. `192.168.1.42`.

### 4. Start the server

```
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

You should see: `Uvicorn running on http://0.0.0.0:8000`

### 5. Open on your Android phone

Make sure your phone is on **the same Wi-Fi network** as your laptop, then open:
```
http://192.168.1.42:8000
```
(replace with your actual IP from step 3)

Chrome will ask for camera and microphone permission — allow both.

### 6. Demo flow

1. Tap **Start Session**
2. Tap the **⌨️** button to open the command input (or hold **🎙** to speak)
3. Try these commands:
   - `capture nameplate` → point phone at a water heater, fridge, or microwave label
   - `capture damage kitchen north wall` → point at any water stain, damp wall, or peeling paint
   - `note: subfloor soft by the cabinet`
4. The app runs a quality check and prompts retake if the image is blurry, too dark, or the subject is cut off
5. GPT-4o extracts fields; results appear immediately
6. Tap **End Session** → job record screen with all captures, fields, confidence scores, notes, and line items
7. Tap **JSON** tab → **Copy JSON** to export the complete job record

---

## Running tests

```
pytest tests/ -v
```

No OpenAI key needed for tests — the test suite uses synthetic images and does
not make any API calls.

---

## Architecture

```
Android Chrome (browser)
  │
  │  Camera frames (base64 JPEG via fetch)
  │  Voice transcript (Web Speech API → parseCommand() → fetch)
  │
  ▼
FastAPI server  (laptop, port 8000)
  ├── POST /session/start          → creates JobBuilder, returns session_id
  ├── POST /capture/process        → quality check → GPT-4o → job record update
  ├── GET  /job/{session_id}       → returns full JobRecord JSON
  └── POST /session/stop/{id}      → finalises record
  │
  ├── services/quality_check.py   Pillow + scipy Laplacian + edge bbox
  ├── services/extraction.py      GPT-4o vision API (httpx async)
  ├── services/voice_parser.py    regex command parser
  ├── services/job_builder.py     accumulates captures → line items → JobRecord
  └── services/upload_queue.py    disk-persisted FIFO queue (JSON file)
```

**Pipeline for one capture:**
```
Voice: "capture damage kitchen north wall"
  → parseCommand() in browser    → { kind: "damage", room: "kitchen", surface: "north wall" }
  → auto-capture frame (1.5s)   → base64 JPEG from canvas
  → client brightness check     → reject if < 40 (saves round-trip)
  → POST /capture/process
      → quality_check.py         → blur + brightness + framing
      → extraction.py            → GPT-4o JSON response
      → job_builder.py           → CaptureEvent appended, line items regenerated
      → upload_queue.py          → item saved to disk, marked uploaded
  → response → browser renders extraction fields + updated line items
```

---

## Relationship to FieldCapture (iOS)

| Aspect | iOS (FieldCapture) | Web (FieldCaptureWeb) |
|---|---|---|
| Platform | Swift / iOS 17 | Python + Android Chrome |
| Mock Device Kit | AVCaptureSession fallback | Browser `getUserMedia` |
| Voice recognition | `SFSpeechRecognizer` | Web Speech API |
| Quality check | CoreImage + Accelerate | Pillow + scipy + Canvas |
| Extraction | Same GPT-4o prompts | Same GPT-4o prompts |
| Job record schema | `JobRecord.swift` Codable | `job_record.py` Pydantic |
| Line item catalog | `LineItemCatalog.swift` | `line_item_catalog.py` |
| Upload queue | `UploadQueueService.swift` | `upload_queue.py` JSON file |
| Voice parser | `VoiceCommandParser.swift` | `voice_parser.py` |

The JSON output schema is identical in both projects. A reviewer can diff
`sample_outputs/job_001.json` from both folders and confirm the structure matches.

---

## Limitations (flagged upfront)

- **HTTPS required for camera on Android Chrome over a network.**
  Chrome blocks `getUserMedia` on non-localhost HTTP origins. If you get a camera
  error on the phone, run the server behind a local HTTPS proxy:
  ```
  pip install mkcert
  mkcert -install
  mkcert 192.168.1.42
  uvicorn backend.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile 192.168.1.42-key.pem --ssl-certfile 192.168.1.42.pem
  ```
  Then open `https://192.168.1.42:8000` on the phone and accept the certificate.
  Full instructions in `docs/https_local.md`.

- **Web Speech API availability.**
  Available in Chrome on Android. Not available in Firefox mobile. If recognition
  doesn't start, use the **⌨️** manual input — the full pipeline still runs.

- **Size estimation is unreliable without depth hardware.**
  50% accuracy at ±40% tolerance. Confidence scores are set below 0.6 for all
  size fields. See `eval/eval_report.md` and `docs/research_memo.md`.

- **Upload endpoint is stubbed.**
  `upload_queue.py` marks items as uploaded immediately. A real Cozmo backend
  URL and auth token would replace the stub in `services/upload_queue.py`.

---

## Project structure

```
FieldCaptureWeb/
├── README.md
├── requirements.txt
│
├── backend/
│   ├── main.py                    FastAPI app, static file serving
│   ├── models/
│   │   ├── job_record.py          All Pydantic models (mirrors JobRecord.swift)
│   │   └── line_item_catalog.py   15 line-item codes + keyword matching
│   ├── services/
│   │   ├── quality_check.py       Blur / brightness / framing (Pillow + scipy)
│   │   ├── extraction.py          GPT-4o vision API (httpx async)
│   │   ├── voice_parser.py        Regex command parser
│   │   ├── job_builder.py         Session accumulator → JobRecord
│   │   └── upload_queue.py        Disk-persisted FIFO queue
│   └── routes/
│       ├── session.py             /session/start, /session/stop, /session/status
│       ├── capture.py             /capture/process
│       └── job_record.py          /job/{session_id}
│
├── frontend/
│   ├── index.html                 Single-page app (3 screens)
│   ├── css/app.css                Dark theme, mobile-first
│   └── js/app.js                  Camera, voice, quality check, API calls, rendering
│
├── tests/
│   ├── test_voice_parser.py       14 tests (mirror of Swift tests)
│   ├── test_line_item_catalog.py  10 tests
│   └── test_quality_check.py      7 tests (synthetic images, no API calls)
│
├── eval/
│   └── eval_report.md             40-frame test set, accuracy per field, 5 worst failures
│
├── sample_outputs/
│   ├── job_001.json               Kitchen A.O. Smith + wall damage
│   ├── job_002.json               Bathroom mold + floor tile + connectivity event
│   └── job_003.json               Basement Rheem heater + wall infiltration
│
├── docs/
│   └── research_memo.md           Meta glasses capabilities + ToF recommendation
│
└── uploads/                       Saved capture images (created on first run)
```
