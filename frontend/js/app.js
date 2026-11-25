// app.js — FieldCapture Web
// All front-end logic: camera, voice commands, quality check (client-side preview),
// API calls to the Python backend, and job record rendering.
//
// Runs in Android Chrome. No build step — plain ES2022.
// Voice recognition: Web Speech API (SpeechRecognition).
// Camera: getUserMedia with environment-facing camera (rear).
// Quality check: Canvas-based blur + brightness before sending to backend.

'use strict';

// ══ Config ════════════════════════════════════════════════════════════════════
// API base URL — auto-detects the server host so the same code works from
// both the laptop (localhost:8000) and the Android phone (192.168.x.x:8000).
const API = `${location.protocol}//${location.hostname}:8000`;

// ══ State ═════════════════════════════════════════════════════════════════════
let sessionId       = null;
let isListening     = false;
let pendingCommand  = null;    // { kind, room, surface, text }
let captureCount    = 0;
let jobRecord       = null;
let mediaStream     = null;
let recognition     = null;

// ══ DOM refs ══════════════════════════════════════════════════════════════════
const screenIdle    = document.getElementById('screen-idle');
const screenCapture = document.getElementById('screen-capture');
const screenRecord  = document.getElementById('screen-record');

const camera        = document.getElementById('camera');
const canvas        = document.getElementById('canvas');

const btnStart      = document.getElementById('btn-start');
const btnStop       = document.getElementById('btn-stop');
const btnMic        = document.getElementById('btn-mic');
const btnManual     = document.getElementById('btn-manual');
const btnSendManual = document.getElementById('btn-send-manual');
const btnNewSession = document.getElementById('btn-new-session');
const btnCopyJson   = document.getElementById('btn-copy-json');

const manualPanel   = document.getElementById('manual-panel');
const manualInput   = document.getElementById('manual-input');
const retakeBanner  = document.getElementById('retake-banner');
const retakeReasons = document.getElementById('retake-reasons');

const dotState      = document.getElementById('dot-state');
const labelState    = document.getElementById('label-state');
const captureCountEl= document.getElementById('capture-count');
const lastCommand   = document.getElementById('last-command');
const pillIcon      = document.getElementById('pill-icon');
const pillText      = document.getElementById('pill-text');
const statusPill    = document.getElementById('status-pill');
const queueLabel    = document.getElementById('queue-label');

// ══ Screen helpers ════════════════════════════════════════════════════════════
function showScreen(name) {
  [screenIdle, screenCapture, screenRecord].forEach(s => s.classList.remove('active'));
  document.getElementById(`screen-${name}`).classList.add('active');
}

// ══ Session ═══════════════════════════════════════════════════════════════════
btnStart.addEventListener('click', startSession);
btnStop.addEventListener('click',  stopSession);
btnNewSession.addEventListener('click', () => showScreen('idle'));

async function startSession() {
  btnStart.disabled = true;
  btnStart.textContent = 'Starting…';

  // Start camera first so the user sees the preview quickly
  await startCamera();

  const res  = await fetch(`${API}/session/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  }).then(r => r.json());

  sessionId    = res.session_id;
  captureCount = 0;
  pendingCommand = null;

  showScreen('capture');
  setPill('idle', '🎙', 'Listening for commands…');
  startVoiceRecognition();
  btnStart.disabled = false;
  btnStart.textContent = 'Start Session';
}

async function stopSession() {
  stopVoiceRecognition();
  stopCamera();

  if (sessionId) {
    await fetch(`${API}/session/stop/${sessionId}`, { method: 'POST' });
    jobRecord = await fetch(`${API}/job/${sessionId}`).then(r => r.json());
  }

  renderJobRecord(jobRecord);
  showScreen('record');
}

// ══ Camera ════════════════════════════════════════════════════════════════════
async function startCamera() {
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' }, width: 1280, height: 720 },
      audio: false,
    });
    camera.srcObject = mediaStream;
    // Some Android browsers need an explicit play() call even with autoplay attr.
    await camera.play().catch(() => {});
  } catch (e) {
    alert('Camera permission denied or unavailable. Please allow camera access and reload.');
  }
}

function stopCamera() {
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
}

// Grab current camera frame as base64 JPEG.
// Waits until the video element has real dimensions before drawing —
// this fixes the blank-canvas problem on slower phones where videoWidth
// is still 0 when captureFrame() is called.
function captureFrame() {
  return new Promise((resolve, reject) => {
    // If video already has dimensions, capture immediately.
    if (camera.videoWidth > 0 && camera.readyState >= 2) {
      resolve(_drawFrame());
      return;
    }
    // Otherwise wait for the first real frame.
    const onFrame = () => {
      camera.removeEventListener('canplay', onFrame);
      resolve(_drawFrame());
    };
    camera.addEventListener('canplay', onFrame);
    // Safety timeout — 5 seconds.
    setTimeout(() => {
      camera.removeEventListener('canplay', onFrame);
      reject(new Error('Camera not ready after 5 seconds. Try tapping Start Session again.'));
    }, 5000);
  });
}

function _drawFrame() {
  const w = camera.videoWidth;
  const h = camera.videoHeight;
  canvas.width  = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(camera, 0, 0, w, h);
  return canvas.toDataURL('image/jpeg', 0.85);
}

// ══ Voice recognition ══════════════════════════════════════════════════════════
function startVoiceRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    console.warn('Web Speech API not available — use the keyboard button instead');
    return;
  }
  recognition = new SR();
  recognition.lang = 'en-US';
  recognition.continuous = true;
  recognition.interimResults = false;

  recognition.onresult = e => {
    const transcript = e.results[e.results.length - 1][0].transcript.trim();
    // Use the upload-aware wrapper if a file is loaded, plain handleTranscript otherwise.
    const handler = window._uploadAwareHandleTranscript || handleTranscript;
    handler(transcript);
  };

  recognition.onerror = err => {
    if (err.error === 'no-speech') return;   // silent, happens all the time
    console.warn('Speech error:', err.error);
  };

  // Auto-restart — recognition stops after a period of silence on Android
  recognition.onend = () => {
    if (isListening) recognition.start();
  };

  recognition.start();
  isListening = true;
  btnMic.classList.add('active');
}

function stopVoiceRecognition() {
  isListening = false;
  if (recognition) { recognition.stop(); recognition = null; }
  btnMic.classList.remove('active');
}

// ══ Manual command input ══════════════════════════════════════════════════════
btnManual.addEventListener('click', () => {
  manualPanel.classList.toggle('hidden');
  if (!manualPanel.classList.contains('hidden')) manualInput.focus();
});

btnSendManual.addEventListener('click', () => {
  const val = manualInput.value.trim();
  if (val) { handleTranscript(val); manualInput.value = ''; }
});

manualInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') btnSendManual.click();
});

document.querySelectorAll('.hint-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    handleTranscript(btn.dataset.cmd);
    manualPanel.classList.add('hidden');
  });
});

// ══ Command dispatch ═══════════════════════════════════════════════════════════
function handleTranscript(text) {
  lastCommand.textContent = `"${text}"`;
  const cmd = parseCommand(text);

  if (cmd.kind === 'unknown') {
    setPill('idle', '❓', `Unknown: "${text}"`);
    return;
  }

  if (cmd.kind === 'note') {
    processNote(cmd);
    return;
  }

  // nameplate or damage — set pending, wait for user to trigger capture
  pendingCommand = cmd;
  const label = cmd.kind === 'nameplate'
    ? 'Point at nameplate and tap 📸'
    : `Point at damage (${cmd.room}, ${cmd.surface}) and tap 📸`;
  setPill('waiting', '📷', label);
  hideRetakeBanner();

  // Auto-capture after 1.5 s so the demo flows smoothly
  setTimeout(() => {
    if (pendingCommand && pendingCommand.kind === cmd.kind) {
      processCapture(pendingCommand);
    }
  }, 1500);
}

// ══ Command parser (mirrors VoiceCommandParser.swift) ═════════════════════════
const FILLER = /\b(um|uh|er|like)\b/gi;
const ROOMS    = ['kitchen','bathroom','bedroom','living room','basement','garage','hallway','laundry','attic','crawl space'];
const SURFACES = ['north wall','south wall','east wall','west wall','ceiling','floor','subfloor','cabinet','door','window'];

function parseCommand(raw) {
  const clean = raw.replace(FILLER, '').trim().toLowerCase();

  if (clean.includes('capture nameplate') || clean.startsWith('nameplate')) {
    return { kind: 'nameplate', raw };
  }

  if (clean.includes('damage')) {
    const room    = ROOMS.find(r => clean.includes(r))    || 'unknown room';
    const surface = SURFACES.find(s => clean.includes(s)) || 'unknown surface';
    return { kind: 'damage', room, surface, raw };
  }

  const noteMatch = raw.match(/^note\s*:?\s*(.+)/i);
  if (noteMatch) {
    return { kind: 'note', text: noteMatch[1].trim(), raw };
  }

  // Extension point for live-session task:
  // const moistureMatch = clean.match(/capture\s+moisture\s+reading\s+([\d.]+%?)\s+(.+)/);
  // if (moistureMatch) return { kind: 'moisture', value: moistureMatch[1], location: moistureMatch[2] };

  return { kind: 'unknown', raw };
}


// POST to /capture/process and surface real server errors (502, 422, 500)
// instead of letting them look like "quality failed".
async function postCapture(payload) {
  const resp = await fetch(`${API}/capture/process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  let body = null;
  try { body = await resp.json(); } catch (_) { /* non-JSON error page */ }
  if (!resp.ok) {
    let detail = body && body.detail;
    if (Array.isArray(detail)) detail = detail.map(d => d.msg).join('; ');
    throw new Error(`Server ${resp.status}: ${detail || resp.statusText}`);
  }
  return body;
}

// ══ Capture pipeline ══════════════════════════════════════════════════════════
async function processCapture(cmd) {
  pendingCommand = null;
  setPill('processing', '⚙️', 'Checking frame quality…');
  hideRetakeBanner();

  // captureFrame() is now async — waits for the video to have real dimensions.
  let imageB64;
  try {
    imageB64 = await captureFrame();
  } catch (err) {
    showRetakeBanner([err.message]);
    setPill('waiting', '📷', 'Camera not ready — speak command again when ready');
    return;
  }

  const voiceHint = cmd.kind === 'damage'
    ? `room: ${cmd.room}, surface: ${cmd.surface}` : null;

  // Client-side brightness check — reads directly from the canvas that
  // captureFrame() just drew. No argument needed.
  const clientQuality = quickQualityCheck();
  if (!clientQuality.passed) {
    showRetakeBanner(clientQuality.reasons);
    setPill('waiting', '📷', 'Retake — then speak command again');
    return;
  }

  setPill('extracting', '✨', 'Extracting fields…');

  try {
    const res = await postCapture({
      session_id:   sessionId,
      capture_type: cmd.kind,
      image_b64:    imageB64,
      voice_hint:   voiceHint,
    });

    if (!res.quality_passed) {
      showRetakeBanner(res.failure_reasons);
      setPill('waiting', '📷', 'Retake — then speak command again');
      return;
    }

    // Show non-blocking quality warnings if any (e.g. blurry upload)
    if (res.failure_reasons && res.failure_reasons.length > 0) {
      showRetakeBanner(res.failure_reasons.map(r => '⚠️ ' + r));
      // Auto-dismiss warnings after 4 seconds — they don't block anything
      setTimeout(hideRetakeBanner, 4000);
    }

    captureCount++;
    captureCountEl.textContent = `${captureCount} capture${captureCount !== 1 ? 's' : ''}`;
    queueLabel.textContent = '';
    setPill('success', '✅', `${cmd.kind === 'nameplate' ? 'Nameplate' : 'Damage'} captured`);

    setTimeout(() => setPill('idle', '🎙', 'Listening for commands…'), 2500);

  } catch (err) {
    setPill('error', '❌', `Error: ${err.message}`);
    console.error(err);
  }
}

async function processNote(cmd) {
  setPill('extracting', '📝', 'Recording note…');

  try {
    await fetch(`${API}/capture/process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id:   sessionId,
        capture_type: 'note',
        image_b64:    '',
        voice_hint:   cmd.text,
        skip_quality: true,
      }),
    });

    captureCount++;
    captureCountEl.textContent = `${captureCount} capture${captureCount !== 1 ? 's' : ''}`;
    setPill('success', '📝', 'Note saved');
    setTimeout(() => setPill('idle', '🎙', 'Listening for commands…'), 1500);

  } catch (err) {
    setPill('error', '❌', `Error: ${err.message}`);
  }
}

// ══ Client-side quality check ════════════════════════════════════════════════
// Reads brightness directly from the canvas that captureFrame() just drew.
// Only hard-rejects completely black frames (blank canvas = camera not working).
// Everything else passes through — the backend pre-processes and tries its best.
function quickQualityCheck() {
  const ctx    = canvas.getContext('2d');
  const data   = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  let total    = 0;
  const pixels = data.length / 4;

  if (pixels === 0) {
    return { passed: false, reasons: ['Could not read frame — camera may not have permission or has not started.'] };
  }

  for (let i = 0; i < data.length; i += 4) {
    total += 0.299 * data[i] + 0.587 * data[i+1] + 0.114 * data[i+2];
  }
  const brightness = total / pixels;

  const reasons = [];
  // Only block if the frame is completely black — a blank canvas means
  // the camera stream isn't active yet, not a dark scene.
  if (brightness < 5) {
    reasons.push('Frame appears blank — camera stream may not be active. Stop and restart the session.');
  }
  // For genuinely dark images (5–25) pass them through with a warning —
  // the backend will auto-brighten before sending to GPT-4o.

  return { passed: reasons.length === 0, reasons, brightness };
}

// ══ UI helpers ════════════════════════════════════════════════════════════════
function setPill(state, icon, text) {
  statusPill.className = `status-pill pill-${state}`;
  pillIcon.textContent = icon;
  pillText.textContent = text;

  dotState.className  = `dot dot-${state === 'success' || state === 'idle' ? 'live' : state}`;
  labelState.textContent = state.toUpperCase();
}

function showRetakeBanner(reasons) {
  reasons = Array.isArray(reasons) ? reasons : [String(reasons || 'Unknown problem')];
  retakeReasons.innerHTML = reasons.map(r => `• ${r}`).join('<br>');
  retakeBanner.classList.remove('hidden');
}

function hideRetakeBanner() {
  retakeBanner.classList.add('hidden');
}

// ══ Job record rendering ══════════════════════════════════════════════════════
function renderJobRecord(record) {
  if (!record) return;

  // Summary cards
  const cards = [
    { label: 'Job ID',    value: record.job_id },
    { label: 'Captures', value: record.metadata?.total_captures ?? 0 },
    { label: 'Quality Fails', value: record.metadata?.failed_quality_checks ?? 0 },
    { label: 'Line Items', value: record.line_items?.length ?? 0 },
  ];
  document.getElementById('summary-cards').innerHTML = cards.map(c => `
    <div class="summary-card">
      <div class="card-label">${c.label}</div>
      <div class="card-value">${c.value}</div>
    </div>
  `).join('');

  // Captures tab
  const capHtml = (record.captures || []).map(cap => renderCaptureCard(cap)).join('');
  document.getElementById('tab-captures').innerHTML = capHtml || '<p style="padding:16px;color:var(--muted)">No captures</p>';

  // Notes tab
  const noteHtml = (record.notes || []).map(note => `
    <div class="note-row">
      <div class="note-text">💬 ${escHtml(note.raw_voice_text)}</div>
      <div class="note-meta">
        Urgency: ${note.extraction?.urgency?.value ?? '—'} ·
        Confidence: ${pct(note.extraction?.overall_confidence)}
      </div>
    </div>
  `).join('');
  document.getElementById('tab-notes').innerHTML = noteHtml || '<p style="padding:16px;color:var(--muted)">No notes</p>';

  // Line items tab
  const liHtml = (record.line_items || []).map(li => `
    <div class="line-item-row">
      <div class="li-code">${li.code}</div>
      <div class="li-desc">${li.description}</div>
      <div class="li-meta">
        <span>Unit: ${li.unit}</span>
        <span>Qty: ${li.estimated_quantity != null ? li.estimated_quantity.toFixed(1) : 'TBD'}</span>
        <span class="conf-badge ${confClass(li.confidence)}">${pct(li.confidence)}</span>
      </div>
    </div>
  `).join('');
  document.getElementById('tab-lineitems').innerHTML = liHtml || '<p style="padding:16px;color:var(--muted)">No line items generated</p>';

  // JSON tab
  document.getElementById('json-output').textContent = JSON.stringify(record, null, 2);

  // Tab switching
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById(`tab-${tab.dataset.tab}`).classList.add('active');
    });
  });
}

function renderCaptureCard(cap) {
  const p = cap.extraction?.payload || {};
  const type = cap.capture_type;

  let fieldsHtml = '';
  if (type === 'nameplate') {
    fieldsHtml = [
      { label: 'Make',          field: p.make },
      { label: 'Model',         field: p.model },
      { label: 'Serial Number', field: p.serial_number },
      { label: 'Mfg Date',      field: p.manufacture_date },
    ].map(f => fieldRow(f.label, f.field)).join('');
  } else if (type === 'damage') {
    fieldsHtml = [
      { label: 'Room',      field: p.room },
      { label: 'Surface',   field: p.surface },
      { label: 'Damage',    field: p.damage_type },
      { label: 'Size (m²)', field: p.rough_size_m2 },
      { label: 'Severity',  field: p.severity_level },
    ].map(f => fieldRow(f.label, f.field)).join('');
  } else {
    fieldsHtml = `<div style="font-size:13px;color:var(--muted)">${escHtml(p.raw_text || '')}</div>`;
  }

  const typeIcon  = type === 'nameplate' ? '🏷' : type === 'damage' ? '⚠️' : '📝';
  const overallConf = p.overall_confidence ?? 0;
  const qPassed  = cap.quality_check?.passed;

  return `
    <div class="capture-card">
      <div class="capture-card-header">
        <div class="capture-type-badge">${typeIcon} ${cap.capture_type.charAt(0).toUpperCase() + cap.capture_type.slice(1)}</div>
        <span class="conf-badge ${confClass(overallConf)}">${pct(overallConf)}</span>
      </div>
      <div class="capture-card-body">
        ${fieldsHtml}
        <div class="capture-meta">
          <div class="meta-item">
            <span class="meta-dot" style="background:${qPassed ? 'var(--green)' : 'var(--red)'}"></span>
            Quality ${qPassed ? 'passed' : 'failed'}
          </div>
          <div class="meta-item">
            <span class="meta-dot" style="background:${cap.upload_status === 'uploaded' ? 'var(--green)' : 'var(--orange)'}"></span>
            ${cap.upload_status || 'queued'}
          </div>
        </div>
      </div>
    </div>
  `;
}

function fieldRow(label, field) {
  const val  = field?.value ?? '—';
  const conf = field?.confidence ?? 0;
  return `
    <div class="field-row">
      <span class="field-label">${label}</span>
      <span class="field-value">${escHtml(val)}</span>
      <span class="conf-badge ${confClass(conf)}">${pct(conf)}</span>
    </div>
  `;
}

// ══ Copy JSON ══════════════════════════════════════════════════════════════════
btnCopyJson.addEventListener('click', () => {
  const text = document.getElementById('json-output').textContent;
  navigator.clipboard.writeText(text).then(() => {
    btnCopyJson.textContent = '✅ Copied!';
    setTimeout(() => { btnCopyJson.textContent = '📋 Copy JSON'; }, 2000);
  });
});

// ══ Utility ═══════════════════════════════════════════════════════════════════
function pct(v) { return v != null ? `${Math.round(v * 100)}%` : '—'; }

function confClass(v) {
  if (v >= 0.8) return 'conf-high';
  if (v >= 0.6) return 'conf-medium';
  return 'conf-low';
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ══ Photo / file upload input ════════════════════════════════════════════════
// Lets the user pick an image from gallery or file system instead of using
// the live camera feed. The image goes through the exact same pipeline:
//   file → canvas → quickQualityCheck() → processCapture() override.
//
// Flow:
//   1. User taps 📎 → file picker opens
//   2. File selected → thumbnail preview shown, dims displayed
//   3. User speaks / types a command (nameplate / damage) to set pendingCommand
//      OR if pendingCommand is already set, the "Use this image" button is active
//   4. Tap "Use this image" → runs captureFromFile() instead of captureFrame()

const btnUpload      = document.getElementById('btn-upload');
const fileInput      = document.getElementById('file-input');
const uploadPanel    = document.getElementById('upload-panel');
const uploadPreview  = document.getElementById('upload-preview');
const uploadFilename = document.getElementById('upload-filename');
const uploadDims     = document.getElementById('upload-dims');
const btnUseUpload   = document.getElementById('btn-use-upload');
const btnCancelUpload= document.getElementById('btn-cancel-upload');

// Holds the loaded image object while the panel is open.
let uploadedImage = null;

// ── Open file picker ──────────────────────────────────────────────────────────
btnUpload.addEventListener('click', () => {
  fileInput.value = '';          // reset so the same file can be re-picked
  fileInput.click();
});

// ── File selected ─────────────────────────────────────────────────────────────
fileInput.addEventListener('change', () => {
  const file = fileInput.files[0];
  if (!file) return;

  // Validate it's actually an image
  if (!file.type.startsWith('image/')) {
    showRetakeBanner([`"${file.name}" is not an image file. Pick a JPG, PNG, or HEIC photo.`]);
    return;
  }

  const reader = new FileReader();
  reader.onload = e => {
    const img = new Image();
    img.onload = () => {
      uploadedImage = img;

      // Show thumbnail
      uploadPreview.src = e.target.result;
      uploadFilename.textContent = file.name;
      uploadDims.textContent     = `${img.naturalWidth} × ${img.naturalHeight} px · ${(file.size / 1024).toFixed(0)} KB`;
      uploadPanel.classList.remove('hidden');

      // If a command is already pending, enable the use button immediately.
      _refreshUseButton();

      setPill(
        pendingCommand ? 'waiting' : 'idle',
        '📎',
        pendingCommand
          ? `Image loaded — tap "Use this image" to process`
          : 'Image loaded — now speak or type a command'
      );
    };
    img.src = e.target.result;
  };
  reader.readAsDataURL(file);
});

// ── Use uploaded image ────────────────────────────────────────────────────────
btnUseUpload.addEventListener('click', () => {
  if (!uploadedImage) return;

  if (!pendingCommand) {
    // Flash hint — user needs to issue a command first
    setPill('idle', '📎', 'Speak or type a command first (e.g. "capture nameplate")');
    uploadPanel.querySelector('.upload-hint').style.color = 'var(--orange)';
    setTimeout(() => {
      uploadPanel.querySelector('.upload-hint').style.color = '';
    }, 2000);
    return;
  }

  _processUploadedImage(pendingCommand);
});

// ── Cancel ────────────────────────────────────────────────────────────────────
btnCancelUpload.addEventListener('click', _clearUpload);

// ── Core: draw uploaded image to canvas and run the same pipeline ─────────────
async function _processUploadedImage(cmd) {
  _clearUpload();
  pendingCommand = null;
  setPill('processing', '⚙️', 'Checking uploaded image quality…');
  hideRetakeBanner();

  // Draw the uploaded image into the shared canvas (same as captureFrame does)
  canvas.width  = uploadedImage.naturalWidth;
  canvas.height = uploadedImage.naturalHeight;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(uploadedImage, 0, 0);

  // Client-side brightness check — reads from canvas just drawn
  const clientQuality = quickQualityCheck();
  if (!clientQuality.passed) {
    showRetakeBanner(clientQuality.reasons);
    setPill('idle', '📎', 'Upload a different image or adjust lighting');
    return;
  }

  // Get base64 from canvas
  const imageB64  = canvas.toDataURL('image/jpeg', 0.85);
  const voiceHint = cmd.kind === 'damage'
    ? `room: ${cmd.room}, surface: ${cmd.surface}` : null;

  setPill('extracting', '✨', 'Extracting fields from uploaded image…');

  try {
    const res = await postCapture({
      session_id:   sessionId,
      capture_type: cmd.kind,
      image_b64:    imageB64,
      voice_hint:   voiceHint,
      skip_quality: true,
    });

    if (!res.quality_passed) {
      showRetakeBanner(res.failure_reasons);
      setPill('idle', '📎', 'Upload a different image and try again');
      return;
    }

    // Show non-blocking quality warnings (e.g. blurry but processed)
    if (res.failure_reasons && res.failure_reasons.length > 0) {
      showRetakeBanner(res.failure_reasons.map(r => '⚠️ ' + r));
      setTimeout(hideRetakeBanner, 4000);
    }

    captureCount++;
    captureCountEl.textContent = `${captureCount} capture${captureCount !== 1 ? 's' : ''}`;
    setPill('success', '✅', `${cmd.kind === 'nameplate' ? 'Nameplate' : 'Damage'} captured from upload`);
    setTimeout(() => setPill('idle', '🎙', 'Listening for commands…'), 2500);

  } catch (err) {
    setPill('error', '❌', `Error: ${err.message}`);
    console.error(err);
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// Update the "Use this image" button label based on whether a command is pending.
function _refreshUseButton() {
  if (pendingCommand) {
    btnUseUpload.textContent = `Use this image for "${pendingCommand.kind}" ✓`;
    btnUseUpload.disabled    = false;
  } else {
    btnUseUpload.textContent = 'Use this image ✓';
    btnUseUpload.disabled    = false;   // still clickable — will show the hint
  }
}

function _clearUpload() {
  uploadedImage = null;
  uploadPanel.classList.add('hidden');
  uploadPreview.src = '';
  fileInput.value   = '';
}

// Hook into handleTranscript so that if a file is loaded and the user then
// issues a command, the button label updates automatically.
const _origHandleTranscript = handleTranscript;
// Wrap handleTranscript to refresh the upload button after any command.
// We reassign by wrapping since handleTranscript is a plain function.
// (This avoids touching the original function body.)
window._uploadAwareHandleTranscript = function(text) {
  _origHandleTranscript(text);
  if (uploadedImage) {
    // Small delay so pendingCommand has been set by handleTranscript
    setTimeout(_refreshUseButton, 50);
  }
};

// Patch the event listeners that call handleTranscript to use the wrapper.
// The simplest approach: re-bind the two inputs.
btnSendManual.removeEventListener('click', btnSendManual._handler);
btnSendManual.addEventListener('click', () => {
  const val = manualInput.value.trim();
  if (val) {
    window._uploadAwareHandleTranscript(val);
    manualInput.value = '';
  }
});

manualInput.removeEventListener('keydown', manualInput._handler);
manualInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const val = manualInput.value.trim();
    if (val) {
      window._uploadAwareHandleTranscript(val);
      manualInput.value = '';
    }
  }
});

document.querySelectorAll('.hint-btn').forEach(btn => {
  // Clone to remove old listener, then re-add
  const fresh = btn.cloneNode(true);
  btn.parentNode.replaceChild(fresh, btn);
  fresh.addEventListener('click', () => {
    window._uploadAwareHandleTranscript(fresh.dataset.cmd);
    manualPanel.classList.add('hidden');
  });
});

// Voice recognition also needs to trigger the wrapper
if (typeof recognition !== 'undefined' && recognition) {
  recognition.onresult = e => {
    const transcript = e.results[e.results.length - 1][0].transcript.trim();
    window._uploadAwareHandleTranscript(transcript);
  };
}
// (startVoiceRecognition patches recognition.onresult to use
//  _uploadAwareHandleTranscript automatically — see recognition.onresult above.)