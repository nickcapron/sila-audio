/**
 * SILA frontend.
 * Reads the session token from the URL hash (#token=...) or localStorage.
 * Every fetch goes through api() which attaches the token header.
 */

const TOKEN_KEY = "sila_token";
let TOKEN = "";

// Grab token from URL hash on first load, then store it.
(function initToken() {
  const hash = location.hash.slice(1);
  const params = new URLSearchParams(hash);
  if (params.has("token")) {
    TOKEN = params.get("token");
    localStorage.setItem(TOKEN_KEY, TOKEN);
    history.replaceState(null, "", location.pathname);
  } else {
    TOKEN = localStorage.getItem(TOKEN_KEY) || "";
  }
})();

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function api(method, path, body) {
  const opts = {
    method,
    headers: { "X-SILA-Token": TOKEN, "Content-Type": "application/json" },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch("/api" + path, opts);
  if (!res.ok) {
    const text = await res.text();
    status(`Error ${res.status}: ${text}`);
    throw new Error(text);
  }
  return res.json();
}

const GET  = (p)    => api("GET",    p);
const POST = (p, b) => api("POST",   p, b);
const PUT  = (p, b) => api("PUT",    p, b);
const DEL  = (p)    => api("DELETE", p);

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let project = null;
let selectedTrackId = null;
let selectedStepIdx = null;
let playing = false;
let fillActive = false;
let _startedAt = null;   // ms epoch when server clock started
let _intervalMs = null;  // 16th-note duration in ms
let _rafId = null;

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function boot() {
  try {
    project = await GET("/project");
  } catch {
    // No project loaded yet — create a default one.
    project = await POST("/project/new", { name: "Untitled" });
  }
  renderTracks();
  await syncPlayState();
  status("Ready");
}

// Fetch real sequencer state from the server and reconcile UI.
async function syncPlayState() {
  let s;
  try {
    s = await GET("/sequencer/status");
  } catch {
    return;
  }
  const btn = document.getElementById("btn-play");
  if (!s.playing && playing) {
    // Server stopped (stream error or restart) — reset UI to stopped state.
    playing = false;
    if (_rafId !== null) { cancelAnimationFrame(_rafId); _rafId = null; }
    _clearPlayhead();
    _startedAt = null;
    btn.textContent = "PLAY";
    btn.classList.remove("active");
    btn.classList.add("primary");
  }
  if (s.error) {
    status("Audio error: " + s.error + " — click PLAY to retry");
  }
}

let _statusPollId = null;

function _startStatusPoll() {
  _statusPollId = setInterval(syncPlayState, 2000);
}

function _stopStatusPoll() {
  if (_statusPollId !== null) { clearInterval(_statusPollId); _statusPollId = null; }
}

function _clearPlayhead() {
  document.querySelectorAll(".step.playing").forEach(c => c.classList.remove("playing"));
  _tickPos = {};
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderTracks() {
  const container = document.getElementById("tracks-container");
  container.innerHTML = "";
  for (const track of project.tracks) {
    container.appendChild(buildTrackRow(track));
  }
}

function buildTrackRow(track) {
  const row = document.createElement("div");
  row.className = "track-row";
  row.dataset.trackId = track.id;

  const muteBtn = document.createElement("button");
  muteBtn.className = "mute-btn" + (track.muted ? " muted" : "");
  muteBtn.textContent = "M";
  muteBtn.title = "Mute";
  muteBtn.onclick = () => toggleMute(track.id);

  const nameEl = document.createElement("div");
  nameEl.className = "track-name";
  nameEl.textContent = track.name;
  nameEl.title = track.notes || track.name;
  nameEl.onclick = () => selectTrack(track.id);

  const grid = document.createElement("div");
  grid.className = "step-grid";
  track.steps.forEach((step, idx) => {
    const cell = document.createElement("div");
    cell.className = "step" + (step.active ? " on" : "");
    cell.dataset.stepIdx = idx;
    cell.onclick = () => toggleStep(track.id, idx);
    cell.oncontextmenu = (e) => { e.preventDefault(); selectStep(track.id, idx, step); };
    grid.appendChild(cell);
  });

  const sampleName = track.samples && track.samples.length ? track.samples[0].path : null;
  const sampleSlot = document.createElement("div");
  sampleSlot.className = "sample-slot" + (sampleName ? " loaded" : "");
  sampleSlot.textContent = sampleName ? sampleName.replace(/\.[^.]+$/, "") : "no sample";
  sampleSlot.title = sampleName || "Click to assign a sample";
  sampleSlot.onclick = (e) => { e.stopPropagation(); openSamplePicker(track.id, sampleSlot); };

  row.appendChild(muteBtn);
  row.appendChild(nameEl);
  row.appendChild(sampleSlot);
  row.appendChild(grid);
  return row;
}

function selectTrack(trackId) {
  selectedTrackId = trackId;
  const track = project.tracks.find(t => t.id === trackId);
  if (track) document.getElementById("track-notes").value = track.notes || "";
}

function selectStep(trackId, idx, step) {
  selectedTrackId = trackId;
  selectedStepIdx = idx;
  document.getElementById("step-vel").value   = step.velocity;
  document.getElementById("step-pitch").value = step.pitch_offset;
  document.getElementById("step-prob").value  = step.probability;
  document.getElementById("step-trig").value  = step.trig_condition;
}

// ---------------------------------------------------------------------------
// Sample picker
// ---------------------------------------------------------------------------

async function openSamplePicker(trackId, anchorEl) {
  closeSamplePicker();

  let files;
  try {
    const res = await GET("/samples");
    files = res.files;
  } catch {
    status("Could not load samples list");
    return;
  }

  const picker = document.createElement("div");
  picker.id = "sample-picker";
  picker.className = "sample-picker";

  if (files.length === 0) {
    const msg = document.createElement("div");
    msg.className = "picker-empty";
    msg.textContent = "No WAV files in samples folder";
    picker.appendChild(msg);
  } else {
    for (const file of files) {
      const item = document.createElement("div");
      item.className = "picker-item";
      item.textContent = file;
      item.onclick = async () => { closeSamplePicker(); await assignSample(trackId, file); };
      picker.appendChild(item);
    }
  }

  const rect = anchorEl.getBoundingClientRect();
  picker.style.left = rect.left + "px";
  picker.style.top  = (rect.bottom + 4) + "px";
  document.body.appendChild(picker);

  // Close on next click outside
  setTimeout(() => document.addEventListener("click", closeSamplePicker, { once: true }), 0);
}

function closeSamplePicker() {
  document.getElementById("sample-picker")?.remove();
}

async function assignSample(trackId, filename) {
  const layer = { path: filename, velocity_min: 0, velocity_max: 127, start: 0.0, end: 1.0, loop: false, rr_group: 0 };
  try {
    await PUT(`/tracks/${trackId}/samples`, { samples: [layer] });
  } catch {
    status("Failed to assign sample");
    return;
  }
  // Update local state
  const track = project.tracks.find(t => t.id === trackId);
  if (track) track.samples = [layer];
  // Update the slot in-place (avoids re-rendering the whole row)
  const slot = document.querySelector(`[data-track-id="${trackId}"] .sample-slot`);
  if (slot) {
    slot.textContent = filename.replace(/\.[^.]+$/, "");
    slot.title = filename;
    slot.classList.add("loaded");
  }
  status(`Assigned: ${filename}`);
}

// ---------------------------------------------------------------------------
// Step interactions
// ---------------------------------------------------------------------------

async function toggleStep(trackId, idx) {
  const track = project.tracks.find(t => t.id === trackId);
  const step = track.steps[idx];
  step.active = !step.active;
  // Optimistic update.
  const cell = document.querySelector(`[data-track-id="${trackId}"] .step[data-step-idx="${idx}"]`);
  if (cell) cell.classList.toggle("on", step.active);
  await PUT(`/tracks/${trackId}/steps/${idx}`, { step });
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

async function togglePlay() {
  const btn = document.getElementById("btn-play");
  if (playing) {
    playing = false;
    _stopStatusPoll();
    if (_rafId !== null) { cancelAnimationFrame(_rafId); _rafId = null; }
    _clearPlayhead();
    _startedAt = null;
    btn.textContent = "PLAY";
    btn.classList.remove("active");
    btn.classList.add("primary");
    try { await POST("/sequencer/stop"); } catch { /* already stopped */ }
  } else {
    const bpm = parseFloat(document.getElementById("bpm-input").value) || 120;
    let res;
    try {
      res = await POST("/sequencer/start", { bpm });
    } catch {
      status("Audio device unavailable — check system audio settings");
      return;
    }
    playing = true;
    // Anchor the playhead to the server's clock start time so the visual
    // step position is computed from elapsed wall-clock time, not a JS timer.
    _startedAt = res.started_at * 1000;  // Python time.time() → ms
    _intervalMs = (60 / res.bpm / 4) * 1000;
    btn.textContent = "STOP";
    btn.classList.remove("primary");
    btn.classList.add("active");
    _rafId = requestAnimationFrame(tickUI);
    _startStatusPoll();
  }
}

let _tickPos = {}; // trackId → current displayed step index

function tickUI() {
  if (!playing || _startedAt === null) return;
  const elapsed = Date.now() - _startedAt;
  for (const track of project.tracks) {
    if (track.muted) continue;
    const stepCount = track.steps.length;
    if (!stepCount) continue;
    const stepIdx = Math.floor(elapsed / _intervalMs) % stepCount;
    if (_tickPos[track.id] !== stepIdx) {
      _tickPos[track.id] = stepIdx;
      const cells = document.querySelectorAll(`[data-track-id="${track.id}"] .step`);
      cells.forEach((c, i) => c.classList.toggle("playing", i === stepIdx));
    }
  }
  _rafId = requestAnimationFrame(tickUI);
}

async function toggleFill() {
  fillActive = !fillActive;
  const btn = document.getElementById("btn-fill");
  btn.classList.toggle("active", fillActive);
  try {
    await POST("/sequencer/fill?active=" + fillActive);
  } catch {
    fillActive = !fillActive;
    btn.classList.toggle("active", fillActive);
    status("Fill toggle failed");
  }
}

// ---------------------------------------------------------------------------
// Project actions
// ---------------------------------------------------------------------------

async function addTrack() {
  const name = `Track ${project.tracks.length + 1}`;
  const track = await POST("/tracks", { name, step_count: 16 });
  project.tracks.push(track);
  renderTracks();
}

async function saveProject() {
  const res = await POST("/project/save");
  status("Saved → " + res.saved);
}

async function undo() {
  try {
    const res = await POST("/project/undo");
    project = res.project;
    renderTracks();
    status("Undo");
  } catch { status("Nothing to undo"); }
}

async function redo() {
  try {
    const res = await POST("/project/redo");
    project = res.project;
    renderTracks();
    status("Redo");
  } catch { status("Nothing to redo"); }
}

async function toggleMute(trackId) {
  const res = await PUT(`/tracks/${trackId}/mute`);
  const track = project.tracks.find(t => t.id === trackId);
  if (track) track.muted = res.muted;
  renderTracks();
}

async function saveNotes() {
  if (!selectedTrackId) { status("Select a track first"); return; }
  const notes = document.getElementById("track-notes").value;
  await PUT(`/tracks/${selectedTrackId}/notes`, { notes });
  const track = project.tracks.find(t => t.id === selectedTrackId);
  if (track) track.notes = notes;
  status("Notes saved");
}

async function exportDigitakt() {
  const dir = prompt("Output folder path:");
  if (!dir) return;
  const res = await POST("/export/digitakt", { output_dir: dir });
  status(res.summary);
  alert(res.summary);
}

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------

function status(msg) {
  document.getElementById("status").textContent = msg;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

// Keep the server alive while the tab is open; server shuts down when pings stop.
setInterval(() => { POST("/ping").catch(() => {}); }, 5000);

boot();
