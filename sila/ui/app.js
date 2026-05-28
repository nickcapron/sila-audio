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
let playInterval = null;

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
  status("Ready");
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

  row.appendChild(muteBtn);
  row.appendChild(nameEl);
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
    clearInterval(playInterval);
    _tickPos = {};
    btn.textContent = "PLAY";
    btn.classList.remove("active");
    btn.classList.add("primary");
    try { await POST("/sequencer/stop"); } catch { /* already stopped */ }
  } else {
    const bpm = parseFloat(document.getElementById("bpm-input").value) || 120;
    try {
      await POST("/sequencer/start", { bpm });
    } catch {
      status("Audio device unavailable — check system audio settings");
      return;
    }
    playing = true;
    btn.textContent = "STOP";
    btn.classList.remove("primary");
    btn.classList.add("active");
    const intervalMs = (60 / bpm / 4) * 1000;
    playInterval = setInterval(tickUI, intervalMs);
  }
}

let _tickPos = {}; // trackId → step index for playhead

function tickUI() {
  for (const track of project.tracks) {
    if (track.muted) continue;
    const stepCount = track.steps.length;
    if (!stepCount) continue;
    const prev = _tickPos[track.id] ?? -1;
    const next = (prev + 1) % stepCount;
    _tickPos[track.id] = next;
    // Highlight playhead.
    const cells = document.querySelectorAll(`[data-track-id="${track.id}"] .step`);
    cells.forEach((c, i) => c.classList.toggle("playing", i === next));
  }
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

boot();
