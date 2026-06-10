// SILA WebView bridge — Phase 4, Step 2b.
//
// Editable step grid + per-step inspector over JUCE 8 native integration:
//   GET  /project                       -> render the grid from the snapshot
//   PUT  /tracks/{id}/steps/{idx}        -> edit a step (active + all params),
//                                           publishes a new immutable snapshot
//   PUT  /tracks/{id}/mute | /solo       -> track gating
//   PUT  /project/swing                  -> drives the swing APVTS param
//   GET  /sequencer/status               -> initial transport status on boot
//   event "playhead"                     -> highlight the playing column
//   event "status"                       -> playing / bpm / active song slot
//   toggle "songModeToggle"              -> bound to the songMode APVTS param
//
// Audible step params: velocity, probability, trig_condition, micro_timing,
// p_locks(start/end). pitch_offset + length are carried but silent until Phase 5.

import { getToggleState, getNativeFunction } from "./js/juce/index.js";

const backendCall = getNativeFunction("backendCall");
const api  = (method, path, body) => backendCall(method, path, body ?? null);
const GET  = (p)    => api("GET", p);
const PUT  = (p, b) => api("PUT", p, b);

const tracksEl   = document.getElementById("tracks");
const ppqEl      = document.getElementById("ppq");
const barBeatEl  = document.getElementById("barbeat");
const swingEl    = document.getElementById("swing");
const swingPct   = document.getElementById("swing-pct");
const songEl     = document.getElementById("songMode");
const statusEl   = document.getElementById("status");
const transportEl = document.getElementById("transport");
const playStateEl = document.getElementById("play-state");
const bpmEl      = document.getElementById("bpm");
const activePatEl = document.getElementById("active-pattern");

let project = null;
let sel = { trackId: null, idx: null };   // selected step

// Suppress WebView2's native right-click menu so our oncontextmenu handlers
// (inspect-without-toggle) are what the user gets.
document.addEventListener("contextmenu", (e) => e.preventDefault());

function setStatus(msg, ok) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("ok", !!ok);
}

const findTrack = (id) => project.tracks.find(t => t.id === id);

// A step carries non-default params worth flagging with a dot.
function stepIsLocked(s) {
  const pl = s.p_locks || {};
  return s.probability < 100 || (s.trig_condition && s.trig_condition !== "always") ||
         (s.micro_timing || 0) !== 0 || pl.start !== undefined || pl.end !== undefined ||
         (s.velocity !== undefined && s.velocity !== 100);
}

// ── Render ────────────────────────────────────────────────────────────────
function renderTracks() {
  tracksEl.innerHTML = "";
  for (const track of project.tracks) {
    const row = document.createElement("div");
    row.className = "track-row";
    row.dataset.trackId = track.id;

    const ms = document.createElement("div");
    ms.className = "ms";
    const mute = document.createElement("button");
    mute.className = "mute" + (track.muted ? " on" : "");
    mute.textContent = "M";
    mute.onclick = () => toggleMute(track.id);
    const solo = document.createElement("button");
    solo.className = "solo" + (track.solo ? " on" : "");
    solo.textContent = "S";
    solo.onclick = () => toggleSolo(track.id);
    ms.appendChild(mute);
    ms.appendChild(solo);

    const name = document.createElement("div");
    name.className = "track-name";
    name.textContent = track.name;

    const grid = document.createElement("div");
    grid.className = "step-grid";
    track.steps.forEach((step, idx) => {
      const cell = document.createElement("div");
      cell.dataset.stepIdx = idx;
      paintCell(cell, track.id, idx, step);
      cell.onclick = () => { toggleStep(track.id, idx); selectStep(track.id, idx); };
      cell.oncontextmenu = (e) => { e.preventDefault(); selectStep(track.id, idx); };
      grid.appendChild(cell);
    });

    row.appendChild(ms);
    row.appendChild(name);
    row.appendChild(grid);
    tracksEl.appendChild(row);
  }
}

function paintCell(cell, trackId, idx, step) {
  cell.className = "step"
    + (step.active ? " on" : "")
    + (idx % 4 === 0 ? " beat" : "")
    + (stepIsLocked(step) ? " locked" : "")
    + (sel.trackId === trackId && sel.idx === idx ? " selected" : "");
}

function repaintCell(trackId, idx) {
  const step = findTrack(trackId)?.steps[idx];
  const cell = document.querySelector(`[data-track-id="${trackId}"] .step[data-step-idx="${idx}"]`);
  if (step && cell) paintCell(cell, trackId, idx, step);
}

// ── Edits (UI -> C++ over the RCU seam) ─────────────────────────────────────
async function toggleStep(trackId, idx) {
  const step = findTrack(trackId).steps[idx];
  step.active = !step.active;
  repaintCell(trackId, idx);
  await PUT(`/tracks/${trackId}/steps/${idx}`, { step });
}

// Persist the full selected step (all params) after an inspector edit.
async function saveSelectedStep() {
  if (sel.trackId === null || sel.idx === null) return;
  const step = findTrack(sel.trackId)?.steps[sel.idx];
  if (!step) return;
  repaintCell(sel.trackId, sel.idx);
  await PUT(`/tracks/${sel.trackId}/steps/${sel.idx}`, { step });
}

async function toggleMute(trackId) {
  const res = await PUT(`/tracks/${trackId}/mute`, {});
  const track = findTrack(trackId);
  if (track) track.muted = !!res.muted;
  renderTracks();
}

async function toggleSolo(trackId) {
  const res = await PUT(`/tracks/${trackId}/solo`, {});
  project.tracks.forEach(t => { t.solo = (t.id === trackId) ? !!res.solo : t.solo; });
  if (!res.any_solo) project.tracks.forEach(t => { t.solo = false; });
  renderTracks();
}

// ── Inspector ───────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

function selectStep(trackId, idx) {
  const prev = sel;
  sel = { trackId, idx };
  if (prev.trackId !== null) repaintCell(prev.trackId, prev.idx);
  repaintCell(trackId, idx);

  const track = findTrack(trackId);
  const step = track.steps[idx];
  const pl = step.p_locks || {};

  $("insp-title").textContent = `STEP ${idx + 1}`;
  $("insp-sub").textContent = `Track: ${track.name}`;
  $("insp-empty").style.display = "none";
  $("insp-fields").style.display = "block";   // explicit: "" would revert to the CSS display:none

  $("i-vel").value   = step.velocity ?? 100;     $("iv-vel").textContent  = $("i-vel").value;
  $("i-prob").value  = step.probability ?? 100;  $("iv-prob").textContent = $("i-prob").value + "%";
  $("i-trig").value  = step.trig_condition || "always";
  $("i-mt").value    = step.micro_timing ?? 0;   $("iv-mt").textContent   = fmtSigned($("i-mt").value);
  $("i-start").value = Math.round((pl.start ?? 0) * 100);   $("iv-start").textContent = $("i-start").value + "%";
  $("i-end").value   = Math.round((pl.end ?? 1) * 100);     $("iv-end").textContent   = $("i-end").value + "%";
  $("i-pitch").value = step.pitch_offset ?? 0;   $("iv-pitch").textContent = fmtSigned($("i-pitch").value);
  $("i-length").value = String(step.length ?? 1);
}

const fmtSigned = (v) => (Number(v) > 0 ? "+" + v : String(v));

function wireInspector() {
  const cur = () => findTrack(sel.trackId)?.steps[sel.idx];

  $("i-vel").addEventListener("input", () => { const s = cur(); if (!s) return; s.velocity = parseInt($("i-vel").value); $("iv-vel").textContent = s.velocity; });
  $("i-prob").addEventListener("input", () => { const s = cur(); if (!s) return; s.probability = parseInt($("i-prob").value); $("iv-prob").textContent = s.probability + "%"; });
  $("i-mt").addEventListener("input", () => { const s = cur(); if (!s) return; s.micro_timing = parseInt($("i-mt").value); $("iv-mt").textContent = fmtSigned(s.micro_timing); });
  $("i-pitch").addEventListener("input", () => { const s = cur(); if (!s) return; s.pitch_offset = parseInt($("i-pitch").value); $("iv-pitch").textContent = fmtSigned(s.pitch_offset); });
  $("i-start").addEventListener("input", () => { const s = cur(); if (!s) return; (s.p_locks = s.p_locks || {}).start = parseInt($("i-start").value) / 100; $("iv-start").textContent = $("i-start").value + "%"; });
  $("i-end").addEventListener("input", () => { const s = cur(); if (!s) return; (s.p_locks = s.p_locks || {}).end = parseInt($("i-end").value) / 100; $("iv-end").textContent = $("i-end").value + "%"; });

  // Commit (PUT) on release / change so we don't spam the bridge per pixel.
  ["i-vel", "i-prob", "i-mt", "i-start", "i-end", "i-pitch"].forEach(id =>
    $(id).addEventListener("change", saveSelectedStep));
  $("i-trig").addEventListener("change", () => { const s = cur(); if (s) { s.trig_condition = $("i-trig").value; saveSelectedStep(); } });
  $("i-length").addEventListener("change", () => { const s = cur(); if (s) { s.length = parseFloat($("i-length").value); saveSelectedStep(); } });
}

// ── Playhead (C++ -> UI) ────────────────────────────────────────────────────
let _lastCol = {};
function onPlayhead(ppq) {
  const p = Number(ppq);
  ppqEl.textContent = p.toFixed(3);
  barBeatEl.textContent = `${Math.floor(p / 4) + 1} · ${(Math.floor(p) % 4) + 1}`;
  const globalStep = Math.floor(p * 4);
  if (!project) return;
  for (const track of project.tracks) {
    const n = track.steps.length;
    if (!n) continue;
    const col = ((globalStep % n) + n) % n;
    if (_lastCol[track.id] === col) continue;
    _lastCol[track.id] = col;
    const cells = document.querySelectorAll(`[data-track-id="${track.id}"] .step`);
    cells.forEach((c, i) => c.classList.toggle("playing", i === col));
  }
}

// ── Transport status (C++ -> UI) ────────────────────────────────────────────
// Pushed on change (and fetched once on boot) — replaces app.js's 2 s poll.
function onStatus(s) {
  const playing = !!s.playing;
  transportEl.classList.toggle("playing", playing);
  playStateEl.textContent = playing ? "PLAYING" : "STOPPED";

  bpmEl.textContent = s.bpm != null ? Number(s.bpm).toFixed(1) : "—";

  // Active song-mode pattern (null = off/stopped). Slot 0 -> "A", 1 -> "B"...
  const slot = s.current_song_slot;
  activePatEl.textContent = (slot != null) ? "PATTERN " + String.fromCharCode(65 + slot) : "";

  // When the transport stops, clear any lit playhead column.
  if (!playing) {
    document.querySelectorAll(".step.playing").forEach(c => c.classList.remove("playing"));
    _lastCol = {};
  }
}

// ── Boot ────────────────────────────────────────────────────────────────────
async function boot() {
  if (typeof window.__JUCE__ !== "undefined" && window.__JUCE__.backend) {
    window.__JUCE__.backend.addEventListener("playhead", onPlayhead);
    window.__JUCE__.backend.addEventListener("status", onStatus);
  }

  project = await GET("/project");
  renderTracks();
  wireInspector();

  // Initial transport status (live updates after this arrive via the event).
  try { onStatus(await GET("/sequencer/status")); } catch { /* ignore */ }

  const swing0 = Math.round((project.swing || 0) * 100);
  swingEl.value = swing0;
  swingPct.textContent = swing0 + "%";
  swingEl.addEventListener("input", () => { swingPct.textContent = swingEl.value + "%"; });
  swingEl.addEventListener("change", () => PUT("/project/swing", { swing: parseInt(swingEl.value) / 100 }));

  const songMode = getToggleState("songModeToggle");
  const reflect = () => { songEl.checked = songMode.getValue(); };
  songMode.valueChangedEvent.addListener(reflect);
  reflect();
  songEl.addEventListener("change", () => songMode.setValue(songEl.checked));

  setStatus(`connected — ${project.tracks.length} tracks · click a step, right-click to inspect`, true);
}

boot().catch(e => setStatus("bridge error: " + (e && e.message ? e.message : e)));
