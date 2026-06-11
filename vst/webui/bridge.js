// SILA WebView bridge — Phase 4, Step 2b.
//
// Editable step grid + per-step inspector over JUCE 8 native integration:
//   GET  /project                       -> render the grid from the snapshot
//   PUT  /tracks/{id}/steps/{idx}        -> edit a step (active + all params),
//                                           publishes a new immutable snapshot
//   PUT  /tracks/{id}/mute | /solo       -> track gating
//   PUT  /project/swing                  -> drives the swing APVTS param
//   GET  /sequencer/status               -> initial transport status on boot
//   GET  /library                        -> ~/SILA/library pack/category tree
//   PUT  /tracks/{id}/samples            -> assign sample layers (rebuilds the
//                                           track's sampler over the RCU seam);
//                                           also commits trimmer start/end edits
//   GET  /tracks/{id}/waveform?points=N  -> downsampled peaks + start/end (trim)
//   POST /export/digitakt                -> native folder picker + transcode;
//                                           result arrives via the "export" event
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
const POST = (p, b) => api("POST", p, b ?? null);

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
const libModal   = document.getElementById("library-modal");
const libTreeEl  = document.getElementById("lib-tree");
const libSearch  = document.getElementById("lib-search");
const libCloseEl = document.getElementById("lib-close");
const libTargetEl = document.getElementById("lib-target");
const trimmerEl  = document.getElementById("trimmer");
const trimWrap   = document.getElementById("trim-wrap");
const trimCanvas = document.getElementById("trim-canvas");
const trimRegion = document.getElementById("trim-region");
const trimStartH = document.getElementById("trim-start");
const trimEndH   = document.getElementById("trim-end");
const trimNameEl = document.getElementById("trim-name");
const trimStartV = document.getElementById("trim-start-v");
const trimEndV   = document.getElementById("trim-end-v");
const exportBtn  = document.getElementById("export-btn");

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
         pl.cutoff !== undefined || pl.resonance !== undefined ||
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

    const slot = document.createElement("div");
    slot.className = "sample-slot" + (track.samples && track.samples.length ? " loaded" : "");
    slot.textContent = sampleLabel(track);
    slot.title = (track.samples && track.samples[0]) ? track.samples[0].path : "no sample — click to assign";
    slot.onclick = (e) => { e.stopPropagation(); openLibrary(track.id, track.name); };

    const mix = document.createElement("div");
    mix.className = "track-mix";
    const vol = document.createElement("input");
    vol.type = "range"; vol.min = 0; vol.max = 100; vol.title = "volume";
    vol.value = Math.round((track.volume ?? 1) * 100);
    vol.addEventListener("input", () => { track.volume = vol.value / 100; });
    vol.addEventListener("change", () => PUT(`/tracks/${track.id}/volume`, { volume: track.volume }));
    const cut = document.createElement("input");
    cut.type = "range"; cut.min = 0; cut.max = 100; cut.title = "filter cutoff";
    cut.value = Math.round((track.cutoff ?? 1) * 100);
    cut.addEventListener("input", () => { track.cutoff = cut.value / 100; });
    cut.addEventListener("change", () => PUT(`/tracks/${track.id}/cutoff`, { cutoff: track.cutoff }));
    const pan = document.createElement("input");
    pan.type = "range"; pan.className = "pan"; pan.min = -100; pan.max = 100; pan.title = "pan (L–R)";
    pan.value = Math.round((track.pan ?? 0) * 100);
    pan.addEventListener("input", () => { track.pan = pan.value / 100; });
    pan.addEventListener("change", () => PUT(`/tracks/${track.id}/pan`, { pan: track.pan }));
    const res = document.createElement("input");
    res.type = "range"; res.className = "res"; res.min = 0; res.max = 100; res.title = "filter resonance";
    res.value = Math.round((track.resonance ?? 0) * 100);
    res.addEventListener("input", () => { track.resonance = res.value / 100; });
    res.addEventListener("change", () => PUT(`/tracks/${track.id}/resonance`, { resonance: track.resonance }));
    // grid order: row1 = vol, cutoff ; row2 = pan, resonance
    mix.appendChild(vol);
    mix.appendChild(cut);
    mix.appendChild(pan);
    mix.appendChild(res);

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
    row.appendChild(slot);
    row.appendChild(mix);
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
  $("i-cutoff").value = Math.round((pl.cutoff ?? track.cutoff ?? 1) * 100);   $("iv-cutoff").textContent = $("i-cutoff").value + "%";
  $("i-res").value    = Math.round((pl.resonance ?? track.resonance ?? 0) * 100); $("iv-res").textContent = $("i-res").value + "%";
  $("i-start").value = Math.round((pl.start ?? 0) * 100);   $("iv-start").textContent = $("i-start").value + "%";
  $("i-end").value   = Math.round((pl.end ?? 1) * 100);     $("iv-end").textContent   = $("i-end").value + "%";
  $("i-pitch").value = step.pitch_offset ?? 0;   $("iv-pitch").textContent = fmtSigned($("i-pitch").value);
  $("i-length").value = String(step.length ?? 0);   // 0 = ∞ one-shot (default)

  showTrimmer(trackId);   // trimmer follows the selected track's sample
}

const fmtSigned = (v) => (Number(v) > 0 ? "+" + v : String(v));

function wireInspector() {
  const cur = () => findTrack(sel.trackId)?.steps[sel.idx];

  $("i-vel").addEventListener("input", () => { const s = cur(); if (!s) return; s.velocity = parseInt($("i-vel").value); $("iv-vel").textContent = s.velocity; });
  $("i-prob").addEventListener("input", () => { const s = cur(); if (!s) return; s.probability = parseInt($("i-prob").value); $("iv-prob").textContent = s.probability + "%"; });
  $("i-mt").addEventListener("input", () => { const s = cur(); if (!s) return; s.micro_timing = parseInt($("i-mt").value); $("iv-mt").textContent = fmtSigned(s.micro_timing); });
  $("i-cutoff").addEventListener("input", () => { const s = cur(); if (!s) return; (s.p_locks = s.p_locks || {}).cutoff = parseInt($("i-cutoff").value) / 100; $("iv-cutoff").textContent = $("i-cutoff").value + "%"; });
  $("i-res").addEventListener("input", () => { const s = cur(); if (!s) return; (s.p_locks = s.p_locks || {}).resonance = parseInt($("i-res").value) / 100; $("iv-res").textContent = $("i-res").value + "%"; });
  $("i-pitch").addEventListener("input", () => { const s = cur(); if (!s) return; s.pitch_offset = parseInt($("i-pitch").value); $("iv-pitch").textContent = fmtSigned(s.pitch_offset); });
  $("i-start").addEventListener("input", () => { const s = cur(); if (!s) return; (s.p_locks = s.p_locks || {}).start = parseInt($("i-start").value) / 100; $("iv-start").textContent = $("i-start").value + "%"; });
  $("i-end").addEventListener("input", () => { const s = cur(); if (!s) return; (s.p_locks = s.p_locks || {}).end = parseInt($("i-end").value) / 100; $("iv-end").textContent = $("i-end").value + "%"; });

  // Commit (PUT) on release / change so we don't spam the bridge per pixel.
  ["i-vel", "i-prob", "i-mt", "i-start", "i-end", "i-pitch", "i-cutoff", "i-res"].forEach(id =>
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

// ── Sample library browser ──────────────────────────────────────────────────
let libraryCache = null;   // GET /library result, fetched once
let libTrackId = null;     // track being assigned

const stem = (s) => String(s).replace(/\.[^.]+$/, "");
function sampleLabel(track) {
  const l = track.samples && track.samples[0];
  return l && l.path ? stem(l.path.split("/").pop()) : "—";
}

async function openLibrary(trackId, trackName) {
  libTrackId = trackId;
  libTargetEl.textContent = "assign to track: " + trackName;
  libSearch.value = "";
  libModal.classList.add("open");
  libSearch.focus();
  if (!libraryCache) {
    libTreeEl.innerHTML = '<div class="lib-empty">loading library…</div>';
    try { libraryCache = await GET("/library"); }
    catch { libTreeEl.innerHTML = '<div class="lib-empty">could not load ~/SILA/library</div>'; return; }
  }
  renderLibrary("");
}

function closeLibrary() { libModal.classList.remove("open"); libTrackId = null; }

function sampleRow(s, sub) {
  const el = document.createElement("div");
  el.className = "lib-sample";
  el.textContent = sub ? `${s.name}   ·   ${sub}` : s.name;
  el.title = s.path;
  el.onclick = () => assignSample(libTrackId, s.path, s.filename);
  return el;
}

function renderLibrary(filter) {
  const packs = (libraryCache && libraryCache.packs) || [];
  libTreeEl.innerHTML = "";
  const q = filter.trim().toLowerCase();

  // Search: flat filtered list across the whole library (capped).
  if (q) {
    let n = 0;
    for (const pack of packs)
      for (const cat of pack.categories)
        for (const s of cat.samples)
          if (s.filename.toLowerCase().includes(q)) {
            libTreeEl.appendChild(sampleRow(s, `${pack.name} / ${cat.name}`));
            if (++n >= 300) {
              const more = document.createElement("div");
              more.className = "lib-empty";
              more.textContent = "… refine your search to see more";
              libTreeEl.appendChild(more);
              return;
            }
          }
    if (n === 0) libTreeEl.innerHTML = '<div class="lib-empty">no matches</div>';
    return;
  }

  // Browse: collapsible packs → categories; sample rows built lazily on expand.
  if (packs.length === 0) { libTreeEl.innerHTML = '<div class="lib-empty">library is empty</div>'; return; }
  for (const pack of packs) {
    const pd = document.createElement("details");
    pd.className = "lib-pack";
    const ps = document.createElement("summary");
    ps.textContent = pack.name;
    pd.appendChild(ps);
    for (const cat of pack.categories) {
      const cd = document.createElement("details");
      cd.className = "lib-cat";
      const cs = document.createElement("summary");
      cs.innerHTML = `${cat.name}<span class="count">${cat.samples.length}</span>`;
      cd.appendChild(cs);
      cd.addEventListener("toggle", () => {
        if (cd.open && cd.dataset.built !== "1") {
          cd.dataset.built = "1";
          for (const s of cat.samples) cd.appendChild(sampleRow(s, null));
        }
      });
      pd.appendChild(cd);
    }
    libTreeEl.appendChild(pd);
  }
}

async function assignSample(trackId, path, filename) {
  if (!trackId) return;
  const layer = { path, velocity_min: 0, velocity_max: 127, start: 0.0, end: 1.0, rr_group: 0 };
  try {
    await PUT(`/tracks/${trackId}/samples`, { samples: [layer] });
  } catch {
    setStatus("failed to assign sample", false);
    return;
  }
  const track = findTrack(trackId);
  if (track) track.samples = [layer];
  const slot = document.querySelector(`[data-track-id="${trackId}"] .sample-slot`);
  if (slot) { slot.textContent = stem(filename); slot.title = path; slot.classList.add("loaded"); }
  closeLibrary();
  setStatus(`assigned ${filename}`, true);
  showTrimmer(trackId);   // surface the trimmer for the freshly-assigned sample
}

// ── Sample trimmer (layer start/end over the waveform) ──────────────────────
let trimTrackId = null;
let trimStart = 0, trimEnd = 1;
let trimPeaks = [];

async function showTrimmer(trackId) {
  const track = findTrack(trackId);
  if (!track || !track.samples || !track.samples.length) { hideTrimmer(); return; }
  let data;
  try { data = await GET(`/tracks/${trackId}/waveform?points=600`); }
  catch { hideTrimmer(); return; }
  if (!data.waveform || !data.waveform.length) { hideTrimmer(); return; }
  trimTrackId = trackId;
  trimPeaks = data.waveform;
  trimStart = data.start ?? 0;
  trimEnd   = data.end ?? 1;
  trimNameEl.textContent = sampleLabel(track);
  trimmerEl.classList.add("visible");
  updateTrimHandles();   // draws the waveform too
}

function hideTrimmer() { trimmerEl.classList.remove("visible"); trimTrackId = null; }

function drawWaveform() {
  const c = trimCanvas;
  c.width  = trimWrap.clientWidth  || 200;
  c.height = trimWrap.clientHeight || 92;
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  const mid = c.height / 2;
  const w   = c.width / trimPeaks.length;
  const startPx = Math.round(trimStart * c.width);
  const endPx   = Math.round(trimEnd   * c.width);
  for (let i = 0; i < trimPeaks.length; i++) {
    const x = i * w;
    const h = trimPeaks[i] * mid;
    const active = x >= startPx && x < endPx;
    ctx.fillStyle = active ? "#34e3c4" : "#243245";
    ctx.fillRect(x, mid - h, Math.max(1, w - 0.5), h * 2);
  }
}

function updateTrimHandles() {
  trimStartH.style.left = (trimStart * 100) + "%";
  trimEndH.style.left   = (trimEnd   * 100) + "%";
  trimRegion.style.left  = (trimStart * 100) + "%";
  trimRegion.style.width = ((trimEnd - trimStart) * 100) + "%";
  trimStartV.textContent = Math.round(trimStart * 100) + "%";
  trimEndV.textContent   = Math.round(trimEnd   * 100) + "%";
  if (trimPeaks.length) drawWaveform();   // re-shade active/muted as handles move
}

function startTrimDrag(e, edge) {
  e.preventDefault();
  const rect = trimWrap.getBoundingClientRect();
  const onMove = (ev) => {
    let frac = (ev.clientX - rect.left) / rect.width;
    frac = Math.max(0, Math.min(1, frac));
    if (edge === "start") trimStart = Math.min(frac, trimEnd - 0.01);
    else                  trimEnd   = Math.max(frac, trimStart + 0.01);
    updateTrimHandles();
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    commitTrim();   // PUT through the existing RCU seam on release
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

async function commitTrim() {
  if (!trimTrackId) return;
  const track = findTrack(trimTrackId);
  if (!track || !track.samples || !track.samples.length) return;
  const layer = { ...track.samples[0], start: trimStart, end: trimEnd };
  track.samples[0] = layer;
  try { await PUT(`/tracks/${trimTrackId}/samples`, { samples: [layer] }); }
  catch { setStatus("trim failed", false); return; }
  setStatus(`trimmed ${sampleLabel(track)} → ${Math.round(trimStart * 100)}–${Math.round(trimEnd * 100)}%`, true);
}

// ── Digitakt export (C++ owns the native folder dialog; result via event) ────
async function triggerExport() {
  setStatus("choose a folder to export Digitakt WAVs…", false);
  try { await POST("/export/digitakt"); }   // C++ opens the picker; summary arrives via "export"
  catch { setStatus("export failed to start", false); }
}

function onExport(res) {
  const line = (res && res.summary) ? res.summary.split("\n")[0] : "export done";
  setStatus(line, !!(res && res.exported));
  if (res && res.summary) console.log("[export] " + (res.dir ? res.dir + "\n" : "") + res.summary);
}

// ── Project reload (DAW state load swapped in a whole new Project) ────────────
async function onProjectReload() {
  try { project = await GET("/project"); } catch { return; }
  sel = { trackId: null, idx: null };
  renderTracks();
  hideTrimmer();
  const sw = Math.round((project.swing || 0) * 100);
  swingEl.value = sw; swingPct.textContent = sw + "%";   // header reflects restored swing
  setStatus(`project loaded — ${project.tracks.length} tracks`, true);
}

// ── Boot ────────────────────────────────────────────────────────────────────
async function boot() {
  if (typeof window.__JUCE__ !== "undefined" && window.__JUCE__.backend) {
    window.__JUCE__.backend.addEventListener("playhead", onPlayhead);
    window.__JUCE__.backend.addEventListener("status", onStatus);
    window.__JUCE__.backend.addEventListener("export", onExport);
    window.__JUCE__.backend.addEventListener("project", onProjectReload);
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

  // Library browser controls.
  libSearch.addEventListener("input", () => renderLibrary(libSearch.value));
  libCloseEl.addEventListener("click", closeLibrary);
  libModal.addEventListener("click", (e) => { if (e.target === libModal) closeLibrary(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeLibrary(); });

  // Trimmer drag handles.
  trimStartH.addEventListener("mousedown", (e) => startTrimDrag(e, "start"));
  trimEndH.addEventListener("mousedown", (e) => startTrimDrag(e, "end"));
  window.addEventListener("resize", () => { if (trimmerEl.classList.contains("visible")) updateTrimHandles(); });

  // Digitakt export.
  exportBtn.addEventListener("click", triggerExport);

  setStatus(`connected — ${project.tracks.length} tracks · click a step, right-click to inspect`, true);
}

boot().catch(e => setStatus("bridge error: " + (e && e.message ? e.message : e)));
