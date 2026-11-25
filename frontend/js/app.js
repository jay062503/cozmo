// app.js — FieldCapture Web
// Runs entirely in the browser. No build step — plain ES2022.
//
// Input methods:
//   1. Live camera  — getUserMedia (rear camera on Android, webcam on laptop)
//   2. Voice        — Web Speech API (Chrome/Android); keyboard fallback for all browsers
//   3. File upload  — file picker, works on all platforms
//
// All three input methods feed the same pipeline:
//   command issued → frame acquired → quality check → GPT-4o extraction → job record

'use strict';

// ── Config ────────────────────────────────────────────────────────────────────
// Auto-detect server host so the same file works on localhost and over LAN.
const API = `${location.protocol}//${location.hostname}:8000`;

// ── State ─────────────────────────────────────────────────────────────────────
let sessionId      = null;
let isListening    = false;
let pendingCommand = null;   // { kind, room, surface } set by dispatch(), cleared on capture
let captureCount   = 0;
let mediaStream    = null;
let recognition    = null;
let uploadedImage  = null;   // Image object while upload panel is open

// ── DOM ───────────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

const screenIdle    = $('screen-idle');
const screenCapture = $('screen-capture');
const screenRecord  = $('screen-record');
const camera        = $('camera');
const canvas        = $('canvas');
const retakeBanner  = $('retake-banner');
const retakeReasons = $('retake-reasons');
const statusPill    = $('status-pill');
const manualPanel   = $('manual-panel');
const uploadPanel   = $('upload-panel');

// ── Screen switcher ───────────────────────────────────────────────────────────
function showScreen(name) {
  [screenIdle, screenCapture, screenRecord].forEach(s => s.classList.remove('active'));
  $(`screen-${name}`).classList.add('active');
}

// ═══════════════════════════════════════════════════════════════════════════════
// SESSION
// ═══════════════════════════════════════════════════════════════════════════════

$('btn-start').addEventListener('click', startSession);
$('btn-stop').addEventListener('click', stopSession);
$('btn-new-session').addEventListener('click', () => showScreen('idle'));

async function startSession() {
  const btn = $('btn-start');
  btn.disabled = true;
  btn.textContent = 'Starting…';

  await startCamera();

  try {
    const res = await apiPost('/session/start', {});
    sessionId    = res.session_id;
    captureCount = 0;
    pendingCommand = null;
    showScreen('capture');
    setPill('idle', '🎙', 'Listening for commands…');
    startVoice();
  } catch (err) {
    alert(`Could not start session: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Start Session';
  }
}

async function stopSession() {
  stopVoice();
  stopCamera();
  if (sessionId) {
    try {
      await apiPost(`/session/stop/${sessionId}`, null, 'POST-no-body');
      const record = await apiGet(`/job/${sessionId}`);
      renderJobRecord(record);
    } catch (err) {
      renderJobRecord(null);
      console.error('Stop session error:', err);
    }
  }
  showScreen('record');
}

// ═══════════════════════════════════════════════════════════════════════════════
// CAMERA
// ═══════════════════════════════════════════════════════════════════════════════

async function startCamera() {
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    camera.srcObject = mediaStream;
    await camera.play().catch(() => {});
  } catch (e) {
    // Camera unavailable — that's OK, the upload path still works
    console.warn('Camera unavailable:', e.message);
  }
}

function stopCamera() {
  if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
}

// Returns a Promise<string> (base64 JPEG data-URI).
// Waits until the video has real pixel data before drawing.
function captureFrame() {
  return new Promise((resolve, reject) => {
    if (!mediaStream) {
      reject(new Error('No camera stream. Use the upload button (📎) instead.'));
      return;
    }
    if (camera.videoWidth > 0 && camera.readyState >= 2) {
      resolve(drawCanvas());
      return;
    }
    const onReady = () => { camera.removeEventListener('canplay', onReady); resolve(drawCanvas()); };
    camera.addEventListener('canplay', onReady);
    setTimeout(() => {
      camera.removeEventListener('canplay', onReady);
      reject(new Error('Camera not ready. Try the upload button (📎) or restart the session.'));
    }, 6000);
  });
}

function drawCanvas() {
  canvas.width  = camera.videoWidth;
  canvas.height = camera.videoHeight;
  canvas.getContext('2d').drawImage(camera, 0, 0);
  return canvas.toDataURL('image/jpeg', 0.88);
}

// ═══════════════════════════════════════════════════════════════════════════════
// VOICE
// ═══════════════════════════════════════════════════════════════════════════════

$('btn-mic').addEventListener('click', () => {
  if (!isListening) startVoice(); else stopVoice();
});

function startVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { console.warn('Web Speech API unavailable — use ⌨️'); return; }

  recognition = new SR();
  recognition.lang = 'en-US';
  recognition.continuous = true;
  recognition.interimResults = false;

  recognition.onresult = e => {
    const text = e.results[e.results.length - 1][0].transcript.trim();
    dispatch(text);
  };
  recognition.onerror = e => { if (e.error !== 'no-speech') console.warn('Voice error:', e.error); };
  recognition.onend   = () => { if (isListening) recognition.start(); };   // auto-restart on Android

  recognition.start();
  isListening = true;
  $('btn-mic').classList.add('active');
}

function stopVoice() {
  isListening = false;
  if (recognition) { recognition.stop(); recognition = null; }
  $('btn-mic').classList.remove('active');
}

// ═══════════════════════════════════════════════════════════════════════════════
// MANUAL INPUT
// ═══════════════════════════════════════════════════════════════════════════════

$('btn-manual').addEventListener('click', () => {
  manualPanel.classList.toggle('hidden');
  if (!manualPanel.classList.contains('hidden')) $('manual-input').focus();
});

$('btn-send-manual').addEventListener('click', sendManual);
$('manual-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendManual(); });

function sendManual() {
  const val = $('manual-input').value.trim();
  if (!val) return;
  $('manual-input').value = '';
  dispatch(val);
}

document.querySelectorAll('.hint-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    dispatch(btn.dataset.cmd);
    manualPanel.classList.add('hidden');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// UPLOAD
// ═══════════════════════════════════════════════════════════════════════════════

$('btn-upload').addEventListener('click', () => {
  $('file-input').value = '';
  $('file-input').click();
});

$('file-input').addEventListener('change', () => {
  const file = $('file-input').files[0];
  if (!file) return;
  if (!file.type.startsWith('image/')) {
    showBanner([`"${file.name}" is not an image. Pick a JPG, PNG, or HEIC photo.`]);
    return;
  }
  const reader = new FileReader();
  reader.onload = e => {
    const img = new Image();
    img.onload = () => {
      uploadedImage = img;
      $('upload-preview').src = e.target.result;
      $('upload-filename').textContent = file.name;
      $('upload-dims').textContent =
        `${img.naturalWidth} × ${img.naturalHeight} px · ${(file.size / 1024).toFixed(0)} KB`;
      refreshUploadBtn();
      uploadPanel.classList.remove('hidden');
      setPill(
        pendingCommand ? 'waiting' : 'idle',
        '📎',
        pendingCommand ? 'Image ready — tap "Use this image"' : 'Image loaded — speak or type a command first'
      );
    };
    img.src = e.target.result;
  };
  reader.readAsDataURL(file);
});

$('btn-use-upload').addEventListener('click', () => {
  if (!uploadedImage) return;
  if (!pendingCommand) {
    // Briefly highlight the hint instead of doing nothing silently
    const hint = uploadPanel.querySelector('.upload-hint');
    hint.style.color = 'var(--orange)';
    setTimeout(() => { hint.style.color = ''; }, 2000);
    setPill('idle', '📎', 'Speak or type a command first (e.g. "capture nameplate")');
    return;
  }
  runUploadCapture(pendingCommand);
});

$('btn-cancel-upload').addEventListener('click', clearUpload);

function refreshUploadBtn() {
  $('btn-use-upload').textContent = pendingCommand
    ? `Use this image for "${pendingCommand.kind}" ✓`
    : 'Use this image ✓';
}

function clearUpload() {
  uploadedImage = null;
  uploadPanel.classList.add('hidden');
  $('upload-preview').src = '';
  $('file-input').value = '';
}

// ═══════════════════════════════════════════════════════════════════════════════
// COMMAND DISPATCH  (single entry point for all three input methods)
// ═══════════════════════════════════════════════════════════════════════════════

const FILLER   = /\b(um|uh|er|like)\b/gi;
const ROOMS    = ['kitchen','bathroom','bedroom','living room','basement',
                  'garage','hallway','laundry','attic','crawl space'];
const SURFACES = ['north wall','south wall','east wall','west wall',
                  'ceiling','floor','subfloor','cabinet','door','window'];

function parseCommand(raw) {
  const clean = raw.replace(FILLER, '').trim().toLowerCase();

  if (clean.includes('capture nameplate') || clean.startsWith('nameplate'))
    return { kind: 'nameplate' };

  if (clean.includes('damage')) {
    const room    = ROOMS.find(r => clean.includes(r))    || 'unknown room';
    const surface = SURFACES.find(s => clean.includes(s)) || 'unknown surface';
    return { kind: 'damage', room, surface };
  }

  const noteMatch = raw.match(/^note\s*:?\s*(.+)/i);
  if (noteMatch) return { kind: 'note', text: noteMatch[1].trim() };

  // Extension point — uncomment for the live-session task:
  // const m = clean.match(/capture\s+moisture\s+reading\s+([\d.]+%?)\s+(.+)/);
  // if (m) return { kind: 'moisture', value: m[1], location: m[2].trim() };

  return { kind: 'unknown' };
}

function dispatch(text) {
  $('last-command').textContent = `"${text}"`;
  const cmd = parseCommand(text);

  if (cmd.kind === 'unknown') {
    setPill('idle', '❓', `Unknown command: "${text}"`);
    return;
  }

  if (cmd.kind === 'note') {
    runNoteCapture(cmd);
    return;
  }

  // nameplate or damage — store pending, then capture
  pendingCommand = cmd;

  // If a file is already loaded, refresh the upload button label
  if (uploadedImage) { refreshUploadBtn(); }

  const label = cmd.kind === 'nameplate'
    ? 'Point at nameplate — capturing in 1.5 s'
    : `Damage: ${cmd.room} / ${cmd.surface} — capturing in 1.5 s`;
  setPill('waiting', '📷', label);
  hideBanner();

  // Auto-capture after a short pause — gives the tech time to frame the shot
  const snap = { ...cmd };
  setTimeout(() => {
    if (pendingCommand && pendingCommand.kind === snap.kind) {
      runCameraCapture(snap);
    }
  }, 1500);
}

// ═══════════════════════════════════════════════════════════════════════════════
// CAPTURE PIPELINES
// ═══════════════════════════════════════════════════════════════════════════════

// ── Camera capture ────────────────────────────────────────────────────────────
async function runCameraCapture(cmd) {
  pendingCommand = null;
  setPill('processing', '⚙️', 'Checking frame…');
  hideBanner();

  let imageB64;
  try {
    imageB64 = await captureFrame();
  } catch (err) {
    showBanner([err.message]);
    setPill('idle', '📎', 'Use the upload button (📎) if camera is unavailable');
    return;
  }

  const qc = clientBrightnessCheck();
  if (!qc.passed) { showBanner(qc.reasons); setPill('waiting', '📷', 'Retake — speak command again'); return; }

  await runExtraction(cmd, imageB64);
}

// ── Upload capture ────────────────────────────────────────────────────────────
async function runUploadCapture(cmd) {
  clearUpload();
  pendingCommand = null;
  setPill('processing', '⚙️', 'Checking uploaded image…');
  hideBanner();

  // Draw the uploaded image into the shared canvas
  canvas.width  = uploadedImage.naturalWidth;
  canvas.height = uploadedImage.naturalHeight;
  canvas.getContext('2d').drawImage(uploadedImage, 0, 0);

  const qc = clientBrightnessCheck();
  if (!qc.passed) { showBanner(qc.reasons); setPill('idle', '📎', 'Upload a different image'); return; }

  const imageB64 = canvas.toDataURL('image/jpeg', 0.88);
  await runExtraction(cmd, imageB64, true);
}

// ── Note capture (no image) ───────────────────────────────────────────────────
async function runNoteCapture(cmd) {
  setPill('extracting', '📝', 'Recording note…');
  try {
    await apiPost('/capture/process', {
      session_id:   sessionId,
      capture_type: 'note',
      image_b64:    '',
      voice_hint:   cmd.text,
      skip_quality: true,
    });
    captureCount++;
    updateCaptureCount();
    setPill('success', '📝', 'Note saved');
    setTimeout(() => setPill('idle', '🎙', 'Listening for commands…'), 1500);
  } catch (err) {
    setPill('error', '❌', err.message);
  }
}

// ── Shared extraction ─────────────────────────────────────────────────────────
async function runExtraction(cmd, imageB64, isUpload = false) {
  setPill('extracting', '✨', isUpload ? 'Extracting from upload…' : 'Extracting fields…');

  const voiceHint = cmd.kind === 'damage'
    ? `room: ${cmd.room}, surface: ${cmd.surface}` : null;

  try {
    const res = await apiPost('/capture/process', {
      session_id:   sessionId,
      capture_type: cmd.kind,
      image_b64:    imageB64,
      voice_hint:   voiceHint,
      skip_quality: isUpload,   // skip backend quality check for uploads (already checked client-side)
    });

    if (!res.quality_passed) {
      showBanner(res.failure_reasons);
      setPill('waiting', '📷', isUpload ? 'Upload a different image' : 'Retake — speak command again');
      return;
    }

    // Surface any non-blocking quality warnings (e.g. blurry but processed)
    if (res.failure_reasons && res.failure_reasons.length > 0) {
      showBanner(res.failure_reasons.map(r => '⚠️ ' + r));
      setTimeout(hideBanner, 4000);
    }

    captureCount++;
    updateCaptureCount();
    const label = cmd.kind === 'nameplate' ? 'Nameplate' : 'Damage';
    setPill('success', '✅', `${label} captured${isUpload ? ' (upload)' : ''}`);
    setTimeout(() => setPill('idle', '🎙', 'Listening for commands…'), 2500);

  } catch (err) {
    setPill('error', '❌', err.message);
    console.error('[Extraction]', err);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// CLIENT-SIDE QUALITY CHECK
// ═══════════════════════════════════════════════════════════════════════════════
// Reads the canvas that was just drawn — no new Image() (would read before load).
// Only rejects truly blank frames (camera not started) — everything else passes
// through to the backend which does the full blur/framing check.

function clientBrightnessCheck() {
  const data   = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
  const pixels = data.length / 4;
  if (pixels === 0)
    return { passed: false, reasons: ['Could not read frame — camera stream not active.'] };

  let total = 0;
  for (let i = 0; i < data.length; i += 4)
    total += 0.299 * data[i] + 0.587 * data[i+1] + 0.114 * data[i+2];
  const brightness = total / pixels;

  if (brightness < 5)
    return { passed: false, reasons: ['Frame is completely black — camera stream may not be active. Try stopping and restarting the session.'] };

  return { passed: true, reasons: [], brightness };
}

// ═══════════════════════════════════════════════════════════════════════════════
// API HELPERS
// ═══════════════════════════════════════════════════════════════════════════════

async function apiPost(path, body) {
  const resp = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body != null ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await resp.json(); } catch (_) {}
  if (!resp.ok) {
    let detail = data?.detail;
    if (Array.isArray(detail)) detail = detail.map(d => d.msg || d).join('; ');
    throw new Error(`Server ${resp.status}: ${detail || resp.statusText || 'Unknown error'}`);
  }
  return data;
}

async function apiGet(path) {
  const resp = await fetch(`${API}${path}`);
  if (!resp.ok) throw new Error(`Server ${resp.status}: ${resp.statusText}`);
  return resp.json();
}

// ═══════════════════════════════════════════════════════════════════════════════
// UI HELPERS
// ═══════════════════════════════════════════════════════════════════════════════

function setPill(state, icon, text) {
  statusPill.className     = `status-pill pill-${state}`;
  $('pill-icon').textContent = icon;
  $('pill-text').textContent = text;
  $('dot-state').className   = `dot dot-${(state === 'success' || state === 'idle') ? 'live' : state}`;
  $('label-state').textContent = state.toUpperCase();
}

function showBanner(reasons) {
  reasons = Array.isArray(reasons) ? reasons : [String(reasons)];
  retakeReasons.innerHTML = reasons.map(r => `• ${r}`).join('<br>');
  retakeBanner.classList.remove('hidden');
}

function hideBanner() { retakeBanner.classList.add('hidden'); }

function updateCaptureCount() {
  $('capture-count').textContent = `${captureCount} capture${captureCount !== 1 ? 's' : ''}`;
}

// ═══════════════════════════════════════════════════════════════════════════════
// JOB RECORD RENDERING
// ═══════════════════════════════════════════════════════════════════════════════

function renderJobRecord(record) {
  if (!record) {
    ['summary-cards','tab-captures','tab-notes','tab-lineitems','json-output']
      .forEach(id => { if ($(id)) $(id).textContent = ''; });
    return;
  }

  // Summary
  $('summary-cards').innerHTML = [
    { label: 'Job ID',        value: record.job_id },
    { label: 'Captures',      value: record.metadata?.total_captures ?? 0 },
    { label: 'Quality Fails', value: record.metadata?.failed_quality_checks ?? 0 },
    { label: 'Line Items',    value: record.line_items?.length ?? 0 },
  ].map(c => `
    <div class="summary-card">
      <div class="card-label">${c.label}</div>
      <div class="card-value">${esc(String(c.value))}</div>
    </div>`).join('');

  // Captures
  $('tab-captures').innerHTML = (record.captures || []).map(renderCapture).join('')
    || '<p style="padding:16px;color:var(--muted)">No captures</p>';

  // Notes
  $('tab-notes').innerHTML = (record.notes || []).map(n => `
    <div class="note-row">
      <div class="note-text">💬 ${esc(n.raw_voice_text)}</div>
      <div class="note-meta">
        Urgency: ${n.extraction?.urgency?.value ?? '—'} ·
        Confidence: ${pct(n.extraction?.overall_confidence)}
      </div>
    </div>`).join('')
    || '<p style="padding:16px;color:var(--muted)">No notes</p>';

  // Line items
  $('tab-lineitems').innerHTML = (record.line_items || []).map(li => `
    <div class="line-item-row">
      <div class="li-code">${esc(li.code)}</div>
      <div class="li-desc">${esc(li.description)}</div>
      <div class="li-meta">
        <span>Unit: ${esc(li.unit)}</span>
        <span>Qty: ${li.estimated_quantity != null ? li.estimated_quantity.toFixed(1) : 'TBD'}</span>
        <span class="conf-badge ${cc(li.confidence)}">${pct(li.confidence)}</span>
      </div>
    </div>`).join('')
    || '<p style="padding:16px;color:var(--muted)">No line items generated</p>';

  // JSON
  $('json-output').textContent = JSON.stringify(record, null, 2);

  // Tab switching
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      tab.classList.add('active');
      $(`tab-${tab.dataset.tab}`).classList.add('active');
    });
  });
}

function renderCapture(cap) {
  const p    = cap.extraction?.payload || {};
  const type = cap.capture_type;
  let fields = '';

  if (type === 'nameplate') {
    fields = [
      ['Make',          p.make],
      ['Model',         p.model],
      ['Serial Number', p.serial_number],
      ['Mfg Date',      p.manufacture_date],
    ].map(([l, f]) => fieldRow(l, f)).join('');
  } else if (type === 'damage') {
    fields = [
      ['Room',      p.room],
      ['Surface',   p.surface],
      ['Damage',    p.damage_type],
      ['Size (m²)', p.rough_size_m2],
      ['Severity',  p.severity_level],
    ].map(([l, f]) => fieldRow(l, f)).join('');
  } else {
    fields = `<div style="font-size:13px;color:var(--muted)">${esc(p.raw_text || '')}</div>`;
  }

  const icon  = type === 'nameplate' ? '🏷' : type === 'damage' ? '⚠️' : '📝';
  const conf  = p.overall_confidence ?? 0;
  const qOk   = cap.quality_check?.passed;
  const uOk   = cap.upload_status === 'uploaded';

  return `
    <div class="capture-card">
      <div class="capture-card-header">
        <div class="capture-type-badge">${icon} ${cap.capture_type.charAt(0).toUpperCase() + cap.capture_type.slice(1)}</div>
        <span class="conf-badge ${cc(conf)}">${pct(conf)}</span>
      </div>
      <div class="capture-card-body">
        ${fields}
        <div class="capture-meta">
          <div class="meta-item">
            <span class="meta-dot" style="background:${qOk ? 'var(--green)' : 'var(--red)'}"></span>
            Quality ${qOk ? 'passed' : 'failed'}
          </div>
          <div class="meta-item">
            <span class="meta-dot" style="background:${uOk ? 'var(--green)' : 'var(--orange)'}"></span>
            ${esc(cap.upload_status || 'queued')}
          </div>
        </div>
      </div>
    </div>`;
}

function fieldRow(label, field) {
  return `
    <div class="field-row">
      <span class="field-label">${esc(label)}</span>
      <span class="field-value">${esc(field?.value ?? '—')}</span>
      <span class="conf-badge ${cc(field?.confidence ?? 0)}">${pct(field?.confidence)}</span>
    </div>`;
}

$('btn-copy-json').addEventListener('click', () => {
  const text = $('json-output').textContent;
  navigator.clipboard.writeText(text).then(() => {
    $('btn-copy-json').textContent = '✅ Copied!';
    setTimeout(() => { $('btn-copy-json').textContent = '📋 Copy JSON'; }, 2000);
  });
});

// ── Tiny helpers ──────────────────────────────────────────────────────────────
function pct(v)  { return v != null ? `${Math.round(Number(v) * 100)}%` : '—'; }
function cc(v)   { return v >= 0.8 ? 'conf-high' : v >= 0.6 ? 'conf-medium' : 'conf-low'; }
function esc(s)  {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
