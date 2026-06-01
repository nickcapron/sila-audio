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
  document.getElementById("bpm-input").value = project.bpm || 120;
  const swingPct = Math.round((project.swing || 0) * 100);
  document.getElementById("swing-input").value = swingPct;
  document.getElementById("swing-pct").textContent = swingPct + "%";
  document.getElementById("swing-input").addEventListener("input", function () {
    const val = parseInt(this.value);
    document.getElementById("swing-pct").textContent = val + "%";
  });
  document.getElementById("swing-input").addEventListener("change", async function () {
    const swing = parseInt(this.value) / 100;
    try { await PUT("/project/swing", { swing }); } catch { /* ignore */ }
  });

  document.getElementById("step-start").addEventListener("change", function () {
    _saveStepPlocks("start", parseInt(this.value) / 100);
  });
  document.getElementById("step-end").addEventListener("change", function () {
    _saveStepPlocks("end", parseInt(this.value) / 100);
  });

  document.getElementById("lfo-shape").addEventListener("change", function () { _saveLfo("shape", this.value); });
  document.getElementById("lfo-rate").addEventListener("input",  function () { _saveLfo("rate",  parseInt(this.value) / 10); });
  document.getElementById("lfo-depth").addEventListener("input", function () { _saveLfo("depth", parseInt(this.value) / 100); });
  document.getElementById("lfo-dest").addEventListener("change", function () { _saveLfo("destination", this.value); });

  document.getElementById("fx-volume").addEventListener("input",    function () { _saveFx("volume",           parseInt(this.value) / 100); });
  document.getElementById("fx-pan").addEventListener("input",       function () { _saveFx("pan",              parseInt(this.value) / 100); });
  document.getElementById("fx-cutoff").addEventListener("input",    function () { _saveFx("filter_cutoff",    parseInt(this.value) / 100); });
  document.getElementById("fx-resonance").addEventListener("input", function () { _saveFx("filter_resonance", parseInt(this.value) / 100); });

  document.getElementById("step-length").addEventListener("change", async function () {
    if (selectedTrackId === null || selectedStepIdx === null) return;
    const track = project.tracks.find(t => t.id === selectedTrackId);
    if (!track) return;
    const step = track.steps[selectedStepIdx];
    if (!step) return;
    step.length = parseFloat(this.value);
    await PUT(`/tracks/${selectedTrackId}/steps/${selectedStepIdx}`, { step });
  });
  renderTracks();
  await syncPlayState();
  await initSongBar();
  _startMidiPoll();
  status("Ready");

  // Live BPM: send change to server on every committed value (blur / Enter).
  document.getElementById("bpm-input").addEventListener("change", async function () {
    const bpm = parseFloat(this.value);
    if (isNaN(bpm) || bpm <= 0 || bpm > 300) return;
    try {
      await PUT("/project/bpm", { bpm });
      if (playing) _intervalMs = (60 / bpm / 4) * 1000;
    } catch { /* ignore */ }
  });
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
  if (track.color) row.style.setProperty("--track-col", track.color);
  row.oncontextmenu = (e) => { e.preventDefault(); _showTrackMenu(track.id, e); };

  const muteBtn = document.createElement("button");
  muteBtn.className = "mute-btn" + (track.muted ? " muted" : "");
  muteBtn.textContent = "M";
  muteBtn.title = "Mute";
  muteBtn.onclick = () => toggleMute(track.id);

  const soloBtn = document.createElement("button");
  soloBtn.className = "solo-btn" + (track.solo ? " soloed" : "");
  soloBtn.textContent = "S";
  soloBtn.title = "Solo";
  soloBtn.onclick = () => toggleSolo(track.id);

  const nameEl = document.createElement("div");
  nameEl.className = "track-name";
  nameEl.textContent = track.name;
  nameEl.title = "Click to inspect · Double-click to rename";
  nameEl.onclick    = () => selectTrack(track.id);
  nameEl.ondblclick = () => startRenameTrack(track.id, nameEl);

  const grid = document.createElement("div");
  grid.className = "step-grid";
  track.steps.forEach((step, idx) => {
    const cell = document.createElement("div");
    cell.className = "step" + (step.active ? " on" : "");
    cell.dataset.stepIdx = idx;
    cell.onclick = () => {
      toggleStep(track.id, idx);
      const fresh = project.tracks.find(t => t.id === track.id)?.steps[idx];
      if (fresh) selectStep(track.id, idx, fresh);
    };
    cell.oncontextmenu = (e) => { e.preventDefault(); selectStep(track.id, idx, step); };
    grid.appendChild(cell);
  });

  const sampleName = track.samples && track.samples.length ? track.samples[0].path : null;
  const sampleSlot = document.createElement("div");
  sampleSlot.className = "sample-slot" + (sampleName ? " loaded" : "");
  sampleSlot.textContent = sampleName ? sampleName.replace(/\.[^.]+$/, "") : "no sample";
  sampleSlot.title = sampleName || "Click to assign a sample";
  sampleSlot.onclick = (e) => { e.stopPropagation(); openSamplePicker(track.id, sampleSlot); };

  const euclidBtn = document.createElement("button");
  euclidBtn.className = "euclid-btn";
  euclidBtn.textContent = "E";
  euclidBtn.title = "Euclidean rhythm (click to enter hits/steps)";
  euclidBtn.onclick = () => applyEuclidean(track.id, track.steps.length);

  const diceBtn = document.createElement("button");
  diceBtn.className = "dice-btn";
  diceBtn.textContent = "⚄";
  diceBtn.title = "Randomize steps (right-click for density)";
  diceBtn.onclick = () => randomizeTrack(track.id, 0.5);
  diceBtn.oncontextmenu = (e) => {
    e.preventDefault();
    const d = parseFloat(prompt("Density (0=sparse, 0.5=medium, 1=dense):", "0.5") || "0.5");
    if (!isNaN(d)) randomizeTrack(track.id, Math.max(0, Math.min(1, d)));
  };

  const stepCountSel = document.createElement("select");
  stepCountSel.className = "step-count-sel";
  stepCountSel.title = "Step count";
  [16, 32, 64, 128].forEach(n => {
    const opt = document.createElement("option");
    opt.value = n;
    opt.textContent = n;
    if (track.steps.length === n) opt.selected = true;
    stepCountSel.appendChild(opt);
  });
  stepCountSel.onchange = () => changeStepCount(track.id, parseInt(stepCountSel.value));

  row.appendChild(muteBtn);
  row.appendChild(soloBtn);
  row.appendChild(euclidBtn);
  row.appendChild(diceBtn);
  row.appendChild(nameEl);
  row.appendChild(sampleSlot);
  row.appendChild(stepCountSel);
  row.appendChild(grid);
  return row;
}

// ---------------------------------------------------------------------------
// Copy / paste pattern
// ---------------------------------------------------------------------------

let _copiedPattern = null;

function _showTrackMenu(trackId, e) {
  document.getElementById("track-ctx-menu")?.remove();
  const menu = document.createElement("div");
  menu.id = "track-ctx-menu";
  menu.style.cssText = `position:fixed;left:${e.clientX}px;top:${e.clientY}px;
    background:#1e1e1e;border:1px solid #3a3a3a;border-radius:4px;
    z-index:500;min-width:140px;box-shadow:0 4px 12px rgba(0,0,0,0.5)`;
  const addItem = (label, fn) => {
    const div = document.createElement("div");
    div.textContent = label;
    div.style.cssText = "padding:6px 12px;font-size:12px;cursor:pointer;color:#d4d4d4";
    div.onmouseenter = () => div.style.background = "#2a2a2a";
    div.onmouseleave = () => div.style.background = "";
    div.onclick = () => { menu.remove(); fn(); };
    menu.appendChild(div);
  };
  addItem("Copy pattern", () => {
    const t = project.tracks.find(t => t.id === trackId);
    if (t) { _copiedPattern = JSON.parse(JSON.stringify(t.steps)); status("Pattern copied"); }
  });
  if (_copiedPattern) {
    addItem("Paste pattern", () => pastePattern(trackId));
  }
  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener("click", () => menu.remove(), { once: true }), 0);
}

async function pastePattern(trackId) {
  if (!_copiedPattern) return;
  try {
    await PUT(`/tracks/${trackId}/pattern`, { steps: _copiedPattern });
    const track = project.tracks.find(t => t.id === trackId);
    if (track) {
      track.steps = JSON.parse(JSON.stringify(_copiedPattern));
      track.step_count = track.steps.length;
    }
    renderTracks();
    status("Pattern pasted");
  } catch { status("Paste failed"); }
}

async function applyEuclidean(trackId, currentSteps) {
  const input = prompt(`Euclidean rhythm\nHits (pulses): e.g. 3\nSteps: e.g. 8\n\nEnter as "hits steps"`, `3 ${currentSteps}`);
  if (!input) return;
  const parts = input.trim().split(/\s+/);
  const hits  = parseInt(parts[0]);
  const steps = parseInt(parts[1] || currentSteps);
  if (isNaN(hits) || isNaN(steps)) return;
  try {
    const res = await POST(`/tracks/${trackId}/euclidean`, { hits, steps });
    const track = project.tracks.find(t => t.id === trackId);
    if (track && res.steps) {
      while (track.steps.length < res.steps.length)
        track.steps.push({ active: false, velocity: 100, pitch_offset: 0, probability: 100, trig_condition: "always", length: 1.0, p_locks: {} });
      track.steps.forEach((s, i) => { if (res.steps[i] !== undefined) s.active = res.steps[i].active; });
      track.step_count = res.steps.length;
      track.steps = track.steps.slice(0, res.steps.length);
    }
    renderTracks();
    status(`Euclidean E(${hits},${steps}) applied`);
  } catch { status("Euclidean failed"); }
}

async function randomizeTrack(trackId, density) {
  try {
    const res = await POST(`/tracks/${trackId}/randomize`, { density });
    const track = project.tracks.find(t => t.id === trackId);
    if (track && res.steps) {
      res.steps.forEach((s, i) => { if (track.steps[i]) track.steps[i].active = s.active; });
    }
    renderTracks();
  } catch { status("Randomize failed"); }
}

async function changeStepCount(trackId, stepCount) {
  try {
    await PUT(`/tracks/${trackId}/step_count`, { step_count: stepCount });
    const track = project.tracks.find(t => t.id === trackId);
    if (track) {
      track.step_count = stepCount;
      // Pad or trim local steps array to match
      while (track.steps.length < stepCount) track.steps.push({ active: false, velocity: 100, pitch_offset: 0, probability: 100, trig_condition: "always", p_locks: {} });
      if (track.steps.length > stepCount) track.steps = track.steps.slice(0, stepCount);
    }
    renderTracks();
  } catch { status("Failed to change step count"); }
}

// ---------------------------------------------------------------------------
// Inspector panel management
// ---------------------------------------------------------------------------

function _inspectorSetMode(mode, trackName, stepLabel) {
  const modeEl = document.getElementById("insp-mode");
  const subEl  = document.getElementById("insp-sub");
  const empty  = document.getElementById("insp-empty");
  const stepP  = document.getElementById("insp-step");
  const trackP = document.getElementById("insp-track");

  if (mode === "step") {
    modeEl.textContent = stepLabel || "Step";
    subEl.textContent  = trackName ? `Track: ${trackName}` : "";
    empty.style.display = "none";
    stepP.style.display  = "";
    trackP.style.display = "none";
  } else if (mode === "track") {
    modeEl.textContent = "Track";
    subEl.textContent  = trackName || "";
    empty.style.display = "none";
    stepP.style.display  = "none";
    trackP.style.display = "";
  } else {
    modeEl.textContent = "Nothing selected";
    subEl.textContent  = "";
    empty.style.display = "";
    stepP.style.display  = "none";
    trackP.style.display = "none";
  }
}

function selectTrack(trackId) {
  try {
    _doSelectTrack(trackId);
  } catch (e) {
    // Inspector DOM access failure must not propagate and break button clicks.
    selectedTrackId = trackId;
  }
}

function _doSelectTrack(trackId) {
  selectedTrackId = trackId;
  selectedStepIdx = null;
  const track = project && project.tracks.find(t => t.id === trackId);
  if (!track) { _inspectorSetMode("none"); return; }

  _inspectorSetMode("track", track.name);

  document.getElementById("track-notes").value    = track.notes || "";
  document.getElementById("track-humanize").value = Math.round((track.humanize || 0) * 100);

  const fx = track.fx || {};
  document.getElementById("fx-volume").value    = Math.round((fx.volume          ?? 1.0) * 100);
  document.getElementById("fx-pan").value       = Math.round((fx.pan            ?? 0.0) * 100);
  document.getElementById("fx-cutoff").value    = Math.round((fx.filter_cutoff  ?? 1.0) * 100);
  document.getElementById("fx-resonance").value = Math.round((fx.filter_resonance ?? 0.0) * 100);

  const lfo = track.lfo || {};
  document.getElementById("lfo-shape").value = lfo.shape  || "sine";
  document.getElementById("lfo-rate").value  = Math.round((lfo.rate  || 1.0) * 10);
  document.getElementById("lfo-depth").value = Math.round((lfo.depth || 0.5) * 100);
  document.getElementById("lfo-dest").value  = lfo.destination || "volume";

  if (track.samples && track.samples.length) {
    loadTrimmer(trackId);
  } else {
    document.getElementById("trimmer-section").style.display = "none";
  }
}

function selectStep(trackId, idx, step) {
  selectedTrackId = trackId;
  selectedStepIdx = idx;
  const track = project.tracks.find(t => t.id === trackId);
  const trackName = track ? track.name : "";
  _inspectorSetMode("step", trackName, `Step ${idx + 1}`);

  document.getElementById("step-vel").value   = step.velocity;
  document.getElementById("step-pitch").value = step.pitch_offset;
  document.getElementById("step-prob").value  = step.probability;
  document.getElementById("step-trig").value  = step.trig_condition;
  document.getElementById("step-length").value = String(step.length ?? 1.0);

  const pl = step.p_locks || {};
  document.getElementById("step-start").value = Math.round((pl.start ?? 0)   * 100);
  document.getElementById("step-end").value   = Math.round((pl.end   ?? 1.0) * 100);

  // P-lock indicators: lit when a custom value overrides the track default
  document.getElementById("plk-start").classList.toggle("on", pl.start !== undefined);
  document.getElementById("plk-end").classList.toggle("on",   pl.end   !== undefined);
}

async function _saveLfo(field, value) {
  if (!selectedTrackId) return;
  const track = project.tracks.find(t => t.id === selectedTrackId);
  if (!track) return;
  if (!track.lfo) track.lfo = {};
  track.lfo[field] = value;
  try { await PUT(`/tracks/${selectedTrackId}/lfo`, { [field]: value }); } catch { /* ignore */ }
}

async function _saveFx(field, value) {
  if (!selectedTrackId) return;
  const track = project.tracks.find(t => t.id === selectedTrackId);
  if (!track) return;
  if (!track.fx) track.fx = {};
  track.fx[field] = value;
  try { await PUT(`/tracks/${selectedTrackId}/fx`, { [field]: value }); } catch { /* ignore */ }
}

document.addEventListener("change", async (e) => {
  if (e.target.id === "track-humanize" && selectedTrackId) {
    const amount = parseInt(e.target.value) / 100;
    try {
      await PUT(`/tracks/${selectedTrackId}/humanize`, { amount });
      const t = project.tracks.find(t => t.id === selectedTrackId);
      if (t) t.humanize = amount;
    } catch { /* ignore */ }
  }
});

function startRenameTrack(trackId, nameEl) {
  if (nameEl.querySelector("input")) return; // already editing
  const track = project.tracks.find(t => t.id === trackId);
  if (!track) return;
  selectTrack(trackId);

  const input = document.createElement("input");
  input.type = "text";
  input.className = "track-name-input";
  input.value = track.name;
  nameEl.textContent = "";
  nameEl.appendChild(input);
  input.focus();
  input.select();

  const commit = async () => {
    const newName = input.value.trim() || track.name;
    nameEl.textContent = newName;
    if (newName !== track.name) {
      try {
        await PUT(`/tracks/${trackId}/name`, { name: newName });
        track.name = newName;
      } catch { nameEl.textContent = track.name; }
    }
  };
  input.onblur = commit;
  input.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); input.blur(); }
    if (e.key === "Escape") { nameEl.textContent = track.name; }
  };
}

function selectStep(trackId, idx, step) {
  selectedTrackId = trackId;
  selectedStepIdx = idx;
  document.getElementById("step-vel").value   = step.velocity;
  document.getElementById("step-pitch").value = step.pitch_offset;
  document.getElementById("step-prob").value  = step.probability;
  document.getElementById("step-trig").value  = step.trig_condition;
  const pl = step.p_locks || {};
  document.getElementById("step-start").value  = Math.round((pl.start ?? 0)   * 100);
  document.getElementById("step-end").value    = Math.round((pl.end   ?? 1.0) * 100);
  document.getElementById("step-length").value = String(step.length ?? 1.0);
}

async function _saveStepPlocks(field, value) {
  if (selectedTrackId === null || selectedStepIdx === null) return;
  const track = project.tracks.find(t => t.id === selectedTrackId);
  if (!track) return;
  const step = track.steps[selectedStepIdx];
  if (!step) return;
  step.p_locks = step.p_locks || {};
  step.p_locks[field] = value;
  await PUT(`/tracks/${selectedTrackId}/steps/${selectedStepIdx}`, { step });
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
  if (!project) { status("No project loaded — reload the page"); return; }
  try {
    const name = `Track ${project.tracks.length + 1}`;
    const track = await POST("/tracks", { name, step_count: 16 });
    project.tracks.push(track);
    renderTracks();
    status(`Added track: ${name}`);
  } catch (e) {
    status("Failed to add track: " + (e.message || String(e)));
  }
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

async function toggleSolo(trackId) {
  const res = await PUT(`/tracks/${trackId}/solo`);
  // Update all local solo states (toggling one solo affects all)
  project.tracks.forEach(t => { t.solo = (t.id === trackId) ? res.solo : t.solo; });
  if (!res.any_solo) project.tracks.forEach(t => { t.solo = false; });
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

let _perfMode = false;

function togglePerfMode() {
  _perfMode = !_perfMode;
  document.body.classList.toggle("perf-mode", _perfMode);
  status(_perfMode ? "Performance mode — press F to exit" : "Ready");
}

let _metronomeOn = false;

async function toggleMetronome() {
  _metronomeOn = !_metronomeOn;
  const btn = document.getElementById("btn-metro");
  btn.classList.toggle("active", _metronomeOn);
  try {
    await fetch("/api/sequencer/metronome?active=" + _metronomeOn, {
      method: "PUT", headers: { "X-SILA-Token": TOKEN },
    });
  } catch { /* ignore */ }
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

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------

document.addEventListener("keydown", async (e) => {
  // Never intercept when user is typing in an input/textarea/select
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

  if (e.key === " ") {
    e.preventDefault();
    togglePlay();
    return;
  }
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    e.preventDefault();
    const inp = document.getElementById("bpm-input");
    const delta = (e.shiftKey ? 10 : 1) * (e.key === "ArrowRight" ? 1 : -1);
    const bpm = Math.max(20, Math.min(300, parseFloat(inp.value || 120) + delta));
    inp.value = bpm;
    inp.dispatchEvent(new Event("change"));
    return;
  }
  if (e.key === "c" || e.key === "C") {
    if (!selectedTrackId) return;
    const track = project.tracks.find(t => t.id === selectedTrackId);
    if (!track) return;
    track.steps.forEach(s => { s.active = false; });
    // Bulk-clear via paste with all-inactive steps
    try {
      await PUT(`/tracks/${selectedTrackId}/pattern`, { steps: track.steps });
      renderTracks();
      status("Track cleared");
    } catch { /* ignore */ }
    return;
  }
  if (e.key === "r" || e.key === "R") {
    if (!selectedTrackId) return;
    randomizeTrack(selectedTrackId, 0.5);
    return;
  }
  if (e.key === "f" || e.key === "F") {
    togglePerfMode();
    return;
  }
  // 1-8: mute/unmute track N
  const n = parseInt(e.key);
  if (n >= 1 && n <= 8) {
    const track = project.tracks[n - 1];
    if (track) toggleMute(track.id);
  }
});

// ---------------------------------------------------------------------------
// Project switcher
// ---------------------------------------------------------------------------

async function toggleProjectMenu() {
  const dd = document.getElementById("proj-dropdown");
  if (dd.classList.contains("open")) {
    dd.classList.remove("open");
    return;
  }
  dd.innerHTML = "";
  let projects = [];
  try {
    const res = await GET("/projects");
    projects = res.projects || [];
  } catch { /* ignore */ }

  projects.forEach(name => {
    const item = document.createElement("div");
    item.className = "proj-item" + (project && name === project.name ? " active" : "");
    item.textContent = name;
    item.onclick = () => { dd.classList.remove("open"); loadProjectByName(name); };
    dd.appendChild(item);
  });

  const newItem = document.createElement("div");
  newItem.className = "proj-item new-proj";
  newItem.textContent = "+ New project…";
  newItem.onclick = () => { dd.classList.remove("open"); newProjectFromMenu(); };
  dd.appendChild(newItem);

  dd.classList.add("open");
  setTimeout(() => document.addEventListener("click", _closeProjectMenu, { once: true }), 0);
}

function _closeProjectMenu(e) {
  const dd = document.getElementById("proj-dropdown");
  if (dd && !dd.contains(e.target)) dd.classList.remove("open");
}

async function loadProjectByName(name) {
  try {
    const res = await fetch(`/api/projects/${encodeURIComponent(name)}/load`, {
      method: "PUT",
      headers: { "X-SILA-Token": TOKEN },
    });
    if (!res.ok) { status("Failed to load project"); return; }
    project = await res.json();
    renderTracks();
    document.getElementById("bpm-input").value = project.bpm;
    status(`Loaded: ${project.name}`);
  } catch { status("Failed to load project"); }
}

// ---------------------------------------------------------------------------
// Sample trimmer
// ---------------------------------------------------------------------------

let _trimmerTrackId = null;
let _trimStart = 0.0;
let _trimEnd   = 1.0;

async function loadTrimmer(trackId) {
  _trimmerTrackId = trackId;
  const section = document.getElementById("trimmer-section");
  try {
    const data = await GET(`/tracks/${trackId}/waveform?points=600`);
    if (!data.waveform || !data.waveform.length) { section.style.display = "none"; return; }
    _trimStart = data.start;
    _trimEnd   = data.end;
    section.style.display = "";
    _drawWaveform(data.waveform);
    _updateTrimHandles();
  } catch { section.style.display = "none"; }
}

function _drawWaveform(peaks) {
  const canvas = document.getElementById("trimmer-canvas");
  const wrap   = document.getElementById("trimmer-wrap");
  canvas.width  = wrap.clientWidth  || 200;
  canvas.height = wrap.clientHeight || 60;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#333";
  const mid = canvas.height / 2;
  const w   = canvas.width / peaks.length;
  for (let i = 0; i < peaks.length; i++) {
    const h = peaks[i] * mid;
    ctx.fillRect(i * w, mid - h, Math.max(1, w - 0.5), h * 2);
  }
}

function _updateTrimHandles() {
  document.getElementById("trimmer-start-handle").style.left = (_trimStart * 100) + "%";
  document.getElementById("trimmer-end-handle").style.left   = (_trimEnd   * 100) + "%";
  const region = document.getElementById("trimmer-region");
  region.style.left  = (_trimStart * 100) + "%";
  region.style.width = ((_trimEnd - _trimStart) * 100) + "%";
}

async function _saveTrim() {
  if (!_trimmerTrackId) return;
  const track = project.tracks.find(t => t.id === _trimmerTrackId);
  if (!track || !track.samples.length) return;
  const layer = { ...track.samples[0], start: _trimStart, end: _trimEnd };
  try {
    await PUT(`/tracks/${_trimmerTrackId}/samples`, { samples: [layer] });
    track.samples[0].start = _trimStart;
    track.samples[0].end   = _trimEnd;
  } catch { /* ignore */ }
}

// Drag handlers for trim handles
(function() {
  let _dragging = null;
  function _startDrag(e, which) {
    e.preventDefault(); _dragging = which;
    document.addEventListener("mousemove", _doDrag);
    document.addEventListener("mouseup", _endDrag);
  }
  function _doDrag(e) {
    if (!_dragging) return;
    const wrap = document.getElementById("trimmer-wrap");
    const rect = wrap.getBoundingClientRect();
    const pct  = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    if (_dragging === "start") {
      _trimStart = Math.min(pct, _trimEnd - 0.01);
    } else {
      _trimEnd = Math.max(pct, _trimStart + 0.01);
    }
    _updateTrimHandles();
  }
  function _endDrag() {
    if (_dragging) { _dragging = null; _saveTrim(); }
    document.removeEventListener("mousemove", _doDrag);
    document.removeEventListener("mouseup", _endDrag);
  }
  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("trimmer-start-handle")
      .addEventListener("mousedown", e => _startDrag(e, "start"));
    document.getElementById("trimmer-end-handle")
      .addEventListener("mousedown", e => _startDrag(e, "end"));
  });
})();

// ---------------------------------------------------------------------------
// MIDI
// ---------------------------------------------------------------------------

let _midiLearning = false;
let _midiPollId = null;

function toggleMidiLearn() {
  if (_midiLearning) {
    _midiLearning = false;
    document.getElementById("btn-midi-learn").classList.remove("active");
    POST("/midi/cancel_learn").catch(() => {});
    status("MIDI learn cancelled");
    return;
  }
  if (!selectedTrackId) {
    status("Select a track first, then click MIDI to learn");
    return;
  }
  _midiLearning = true;
  document.getElementById("btn-midi-learn").classList.add("active");
  POST(`/midi/learn/${selectedTrackId}`).catch(() => {});
  status("Press a key on your MIDI device to map it to this track…");
}

async function _pollMidi() {
  try {
    const s = await GET("/midi/status");
    const ind = document.getElementById("midi-indicator");
    ind.style.background = s.active ? "#5f5" : "#333";
    // If learn finished server-side, clear local state
    if (_midiLearning && !s.learning) {
      _midiLearning = false;
      document.getElementById("btn-midi-learn").classList.remove("active");
      status("MIDI mapped");
    }
  } catch { /* ignore */ }
}

function _startMidiPoll() {
  if (_midiPollId) return;
  _midiPollId = setInterval(_pollMidi, 150);
}

// ---------------------------------------------------------------------------
// Song mode / pattern chain
// ---------------------------------------------------------------------------

let _songChain = [];
let _songMode = false;
let _savedSlots = new Set();

async function initSongBar() {
  try {
    const res = await GET("/patterns");
    _savedSlots = new Set(res.slots_used.map(Number));
    _songChain = res.chain || [];
    _songMode = res.song_mode || false;
  } catch { /* ignore */ }
  renderPatternSlots();
  document.getElementById("btn-song-mode").textContent = "SONG " + (_songMode ? "ON" : "OFF");
  if (_songMode) document.getElementById("btn-song-mode").classList.add("active");
}

function renderPatternSlots() {
  const wrap = document.getElementById("pattern-slots");
  wrap.innerHTML = "";
  for (let i = 0; i < 8; i++) {
    const slot = document.createElement("div");
    const label = String.fromCharCode(65 + i);  // A-H
    slot.className = "pattern-slot" +
      (_savedSlots.has(i) ? " saved" : "") +
      (_songChain.includes(i) ? " in-chain" : "");
    slot.title = `Right-click to save; click to add/remove from chain`;
    slot.textContent = label;
    slot.onclick = () => toggleChainSlot(i);
    slot.oncontextmenu = (e) => { e.preventDefault(); savePatternSlot(i); };
    wrap.appendChild(slot);
  }
}

async function savePatternSlot(slot) {
  try {
    await POST(`/patterns/${slot}/save`);
    _savedSlots.add(slot);
    renderPatternSlots();
    status(`Saved to slot ${String.fromCharCode(65 + slot)}`);
  } catch { status("Failed to save pattern"); }
}

async function toggleChainSlot(slot) {
  if (!_savedSlots.has(slot)) {
    // Auto-save first if empty
    await savePatternSlot(slot);
  }
  const idx = _songChain.indexOf(slot);
  if (idx >= 0) _songChain.splice(idx, 1);
  else _songChain.push(slot);
  try {
    await PUT("/song/chain", { chain: _songChain });
    renderPatternSlots();
  } catch { status("Failed to update chain"); }
}

async function toggleSongMode() {
  _songMode = !_songMode;
  const btn = document.getElementById("btn-song-mode");
  btn.textContent = "SONG " + (_songMode ? "ON" : "OFF");
  btn.classList.toggle("active", _songMode);
  try { await fetch("/api/song/mode?active=" + _songMode, { method: "PUT", headers: { "X-SILA-Token": TOKEN } }); } catch { /* ignore */ }
}

async function newProjectFromMenu() {
  const name = prompt("Project name:");
  if (!name || !name.trim()) return;
  try {
    const res = await POST("/projects", { name: name.trim() });
    project = res;
    renderTracks();
    document.getElementById("bpm-input").value = project.bpm;
    status(`Created: ${project.name}`);
  } catch { status("Failed to create project"); }
}

// ---------------------------------------------------------------------------
// Sample browser
// ---------------------------------------------------------------------------

let _libraryLoaded = false;

function toggleBrowser() {
  const panel = document.getElementById("lib-panel");
  const wasCollapsed = panel.classList.contains("collapsed");
  panel.classList.toggle("collapsed");
  if (wasCollapsed && !_libraryLoaded) {
    _libraryLoaded = true;
    loadLibrary();
  }
}

async function loadLibrary() {
  document.getElementById("lib-tree").innerHTML = '<div class="lib-empty">Loading…</div>';
  try {
    const data = await GET("/library");
    renderLibrary(data.packs);
  } catch {
    document.getElementById("lib-tree").innerHTML = '<div class="lib-empty">Failed to load library</div>';
  }
}

function _fmtSize(bytes) {
  if (bytes < 1024)            return `${bytes} B`;
  if (bytes < 1024 * 1024)     return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderLibrary(packs) {
  const tree = document.getElementById("lib-tree");
  tree.innerHTML = "";
  if (!packs || !packs.length) {
    tree.innerHTML = '<div class="lib-empty">Library is empty — add samples to ~/SILA/library/</div>';
    return;
  }
  for (const pack of packs) {
    tree.appendChild(_buildPackNode(pack));
  }
}

function _buildPackNode(pack) {
  const el   = document.createElement("div");
  el.className   = "lib-pack";
  el.dataset.pack = pack.name;

  const hdr   = document.createElement("div");
  hdr.className = "lib-pack-header";
  const caret = document.createElement("span");
  caret.className = "lib-caret";
  caret.textContent = "▶";
  hdr.appendChild(caret);
  hdr.appendChild(document.createTextNode(" " + pack.name));
  el.appendChild(hdr);

  const body  = document.createElement("div");
  body.className = "lib-pack-body";
  for (const cat of pack.categories) {
    body.appendChild(_buildCatNode(cat));
  }
  el.appendChild(body);

  hdr.onclick = () => {
    caret.classList.toggle("open");
    body.classList.toggle("open");
  };
  return el;
}

function _buildCatNode(cat) {
  const el   = document.createElement("div");
  el.className   = "lib-cat";
  el.dataset.cat = cat.name;

  const hdr   = document.createElement("div");
  hdr.className = "lib-cat-header";
  const caret = document.createElement("span");
  caret.className = "lib-caret";
  caret.textContent = "▶";
  hdr.appendChild(caret);
  hdr.appendChild(document.createTextNode(" " + cat.name));
  el.appendChild(hdr);

  const body  = document.createElement("div");
  body.className = "lib-cat-body";
  for (const s of cat.samples) {
    body.appendChild(_buildSampleNode(s));
  }
  el.appendChild(body);

  hdr.onclick = () => {
    caret.classList.toggle("open");
    body.classList.toggle("open");
  };
  return el;
}

function _buildSampleNode(s) {
  const el = document.createElement("div");
  el.className     = "lib-sample";
  el.dataset.name  = s.name.toLowerCase();
  el.dataset.path  = s.path;
  el.dataset.fname = s.filename;

  const name = document.createElement("span");
  name.className   = "lib-sample-name";
  name.textContent = s.name;
  name.title       = s.filename;
  el.appendChild(name);

  const sz = document.createElement("span");
  sz.className   = "lib-sample-size";
  sz.textContent = _fmtSize(s.size_bytes);
  el.appendChild(sz);

  const prev = document.createElement("button");
  prev.className   = "lib-btn-prev";
  prev.textContent = "▶";
  prev.title       = "Preview";
  prev.onclick = (e) => { e.stopPropagation(); previewSample(s.path); };
  el.appendChild(prev);

  const add = document.createElement("button");
  add.className   = "lib-btn-add";
  add.textContent = "+";
  add.title       = "Add to project";
  add.onclick = (e) => { e.stopPropagation(); addSample(s.path, s.filename); };
  el.appendChild(add);

  el.ondblclick = () => addSample(s.path, s.filename);
  return el;
}

function filterLibrary(q) {
  const query = q.trim().toLowerCase();

  // Reveal/hide individual samples.
  document.querySelectorAll(".lib-sample").forEach(el => {
    el.classList.toggle("hidden", query !== "" && !el.dataset.name.includes(query));
  });

  // Auto-expand packs/categories that contain visible matches; hide empty ones.
  document.querySelectorAll(".lib-cat").forEach(catEl => {
    const hasMatch = catEl.querySelectorAll(".lib-sample:not(.hidden)").length > 0;
    catEl.style.display = hasMatch ? "" : "none";
    if (query && hasMatch) {
      catEl.querySelector(".lib-caret").classList.add("open");
      catEl.querySelector(".lib-cat-body").classList.add("open");
    }
  });
  document.querySelectorAll(".lib-pack").forEach(packEl => {
    const hasMatch = packEl.querySelectorAll(".lib-sample:not(.hidden)").length > 0;
    packEl.style.display = hasMatch ? "" : "none";
    if (query && hasMatch) {
      packEl.querySelector(".lib-caret").classList.add("open");
      packEl.querySelector(".lib-pack-body").classList.add("open");
    }
  });
}

async function previewSample(path) {
  try {
    await POST("/library/preview", { path });
  } catch {
    status("Preview failed — check audio device");
  }
}

async function addSample(path, filename) {
  try {
    await POST("/library/add", { path });
    status(`Added to project: ${filename}`);
  } catch {
    status("Failed to add sample to project");
  }
}

boot();
