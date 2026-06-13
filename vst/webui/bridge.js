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
const DEL  = (p)    => api("DELETE", p, null);

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
const lfoPanel   = document.getElementById("lfo-panel");
const lfoNameEl  = document.getElementById("lfo-name");
const lfoShapeEl = document.getElementById("lfo-shape");
const lfoDestEl  = document.getElementById("lfo-dest");
const lfoRateEl  = document.getElementById("lfo-rate");
const lfoRateV   = document.getElementById("lfo-rate-v");
const lfoDepthEl = document.getElementById("lfo-depth");
const lfoDepthV  = document.getElementById("lfo-depth-v");
const lfoSyncEl  = document.getElementById("lfo-sync");
const projectsBtn = document.getElementById("projects-btn");
const projModal  = document.getElementById("projects-modal");
const projName   = document.getElementById("proj-name");
const projSaveBtn = document.getElementById("proj-save");
const projCloseEl = document.getElementById("proj-close");
const projListEl = document.getElementById("proj-list");

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

// A labeled channel-strip control cell (tiny uppercase caption above the slider).
function mkCtl(labelText, input, purple) {
  const c = document.createElement("div");
  c.className = "ctl";
  const lab = document.createElement("span");
  lab.textContent = labelText;
  if (purple) lab.className = "v2";
  c.appendChild(lab);
  c.appendChild(input);
  return c;
}

// A step carries non-default params worth flagging with a dot.
function stepIsLocked(s) {
  const pl = s.p_locks || {};
  return s.probability < 100 || (s.trig_condition && s.trig_condition !== "always") ||
         (s.micro_timing || 0) !== 0 || pl.start !== undefined || pl.end !== undefined ||
         pl.cutoff !== undefined || pl.resonance !== undefined ||
         pl.lfo_depth !== undefined || pl.lfo_rate !== undefined || pl.filter_mode !== undefined ||
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
    const del = document.createElement("button");
    del.className = "del";
    del.textContent = "×";
    del.title = "delete track";
    let delTimer = null;
    del.onclick = () => {
      if (del.classList.contains("armed")) {           // confirmed
        clearTimeout(delTimer);
        deleteTrack(track.id);
        return;
      }
      del.classList.add("armed");                       // arm — second click confirms
      del.title = "click again to delete";
      clearTimeout(delTimer);
      delTimer = setTimeout(() => { del.classList.remove("armed"); del.title = "delete track"; }, 3000);
    };
    ms.appendChild(mute);
    ms.appendChild(solo);
    ms.appendChild(del);

    const name = document.createElement("div");
    name.className = "track-name";
    name.textContent = track.name;
    name.title = "click for track options · double-click to rename";
    name.onclick = () => selectTrack(track.id);
    name.ondblclick = () => startRename(track.id, name);

    const slot = document.createElement("div");
    slot.className = "sample-slot" + (track.samples && track.samples.length ? " loaded" : "");
    slot.textContent = sampleLabel(track);
    slot.title = (track.samples && track.samples[0]) ? track.samples[0].path : "no sample — click to assign";
    slot.onclick = (e) => { e.stopPropagation(); openLibrary(track.id, track.name); };

    const mix = document.createElement("div");
    mix.className = "track-mix";
    const vol = document.createElement("input");
    vol.type = "range"; vol.className = "vol"; vol.min = 0; vol.max = 100; vol.title = "volume";
    vol.value = Math.round((track.volume ?? 1) * 100);
    vol.addEventListener("input", () => { track.volume = vol.value / 100; });
    vol.addEventListener("change", () => PUT(`/tracks/${track.id}/volume`, { volume: track.volume }));
    const cut = document.createElement("input");
    cut.type = "range"; cut.className = "cut"; cut.min = 0; cut.max = 100; cut.title = "filter cutoff";
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
    const fmode = document.createElement("select");
    fmode.className = "fmode"; fmode.title = "filter mode";
    [["lowpass", "LP"], ["highpass", "HP"], ["bandpass", "BP"]].forEach(([v, t]) => {
      const o = document.createElement("option"); o.value = v; o.textContent = t; fmode.appendChild(o);
    });
    fmode.value = track.filter_mode || "lowpass";
    fmode.addEventListener("change", () => { track.filter_mode = fmode.value; PUT(`/tracks/${track.id}/filter_mode`, { mode: fmode.value }); });
    // grid order: row1 = Vol, Cut ; row2 = Pan, Res ; row3 = filter mode (spans)
    mix.appendChild(mkCtl("Vol", vol));
    mix.appendChild(mkCtl("Cut", cut));
    mix.appendChild(mkCtl("Pan", pan, true));
    mix.appendChild(mkCtl("Res", res, true));
    mix.appendChild(fmode);

    const grid = document.createElement("div");
    grid.className = "step-grid";
    track.steps.forEach((step, idx) => {
      const cell = document.createElement("div");
      cell.dataset.stepIdx = idx;
      paintCell(cell, track.id, idx, step);
      cell.onclick = () => { toggleStep(track.id, idx); selectStep(track.id, idx); };
      cell.oncontextmenu = (e) => { e.preventDefault(); resetStep(track.id, idx); };
      grid.appendChild(cell);
    });

    row.appendChild(ms);
    row.appendChild(name);
    row.appendChild(slot);
    row.appendChild(mix);
    row.appendChild(grid);
    tracksEl.appendChild(row);
  }
  const addBtn = document.getElementById("add-track");
  if (addBtn) addBtn.disabled = project.tracks.length >= 8;
}

// ── Track management (add / remove / rename) ─────────────────────────────────
// add/remove publish a new Project+bank via setProject -> projectEpoch bump, so
// the UI rebuilds through the "project" event. Rename is a snapshot edit only.
async function addTrack() {
  const res = await POST("/tracks", {});
  if (res && res.error) setStatus(res.error, false);
}

async function deleteTrack(id) {
  await DEL(`/tracks/${id}`);
}

function startRename(id, nameEl) {
  const input = document.createElement("input");
  input.className = "track-name-edit";
  input.value = nameEl.textContent;
  nameEl.replaceWith(input);
  input.focus(); input.select();
  let done = false;
  const finish = (save) => {
    if (done) return; done = true;
    const newName = (save && input.value.trim()) ? input.value.trim() : nameEl.textContent;
    if (save && newName !== nameEl.textContent) {
      const t = findTrack(id); if (t) t.name = newName;
      PUT(`/tracks/${id}/name`, { name: newName });
    }
    nameEl.textContent = newName;
    input.replaceWith(nameEl);
  };
  input.addEventListener("blur", () => finish(true));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); input.blur(); }
    else if (e.key === "Escape") finish(false);
  });
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

// Right-click a step: wipe its p-locks + per-step tweaks back to a plain step
// (keeps the on/off state), then select it so the inspector shows the defaults.
async function resetStep(trackId, idx) {
  const track = findTrack(trackId);
  const step = track && track.steps[idx];
  if (!step) return;
  step.velocity = 100;
  step.pitch_offset = 0;
  step.probability = 100;
  step.trig_condition = "always";
  step.length = 0;          // ∞ one-shot (default)
  step.micro_timing = 0;
  step.p_locks = {};        // clears start/end/cutoff/resonance/lfo/filter_mode
  repaintCell(trackId, idx);
  selectStep(trackId, idx); // select + refresh the inspector to the reset values
  await PUT(`/tracks/${trackId}/steps/${idx}`, { step });
  setStatus("step reset", true);
}

// ── Inspector ───────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

// Clicking a track name selects the track (no step): surface its LFO + trimmer
// panels without needing to click a step first.
function selectTrack(trackId) {
  const prev = sel;
  sel = { trackId, idx: null };
  if (prev.trackId !== null && prev.idx !== null) repaintCell(prev.trackId, prev.idx);
  showLfo(trackId);
  showTrimmer(trackId);
}

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
  $("i-fmode").value = pl.filter_mode ?? track.filter_mode ?? "lowpass";
  const _L = track.lfo || {};
  $("i-lfo-depth").value = Math.round((pl.lfo_depth ?? _L.depth ?? 0) * 100); $("iv-lfo-depth").textContent = $("i-lfo-depth").value + "%";
  const _lr = pl.lfo_rate ?? _L.rate ?? 1; $("i-lfo-rate").value = rateToSlider(_lr); $("iv-lfo-rate").textContent = fmtHz(_lr);
  $("i-start").value = Math.round((pl.start ?? 0) * 100);   $("iv-start").textContent = $("i-start").value + "%";
  $("i-end").value   = Math.round((pl.end ?? 1) * 100);     $("iv-end").textContent   = $("i-end").value + "%";
  $("i-pitch").value = step.pitch_offset ?? 0;   $("iv-pitch").textContent = fmtSigned($("i-pitch").value);
  $("i-length").value = String(step.length ?? 0);   // 0 = ∞ one-shot (default)

  showTrimmer(trackId);   // trimmer follows the selected track's sample
  showLfo(trackId);       // LFO panel follows the selected track
}

const fmtSigned = (v) => (Number(v) > 0 ? "+" + v : String(v));

function wireInspector() {
  const cur = () => findTrack(sel.trackId)?.steps[sel.idx];

  $("i-vel").addEventListener("input", () => { const s = cur(); if (!s) return; s.velocity = parseInt($("i-vel").value); $("iv-vel").textContent = s.velocity; });
  $("i-prob").addEventListener("input", () => { const s = cur(); if (!s) return; s.probability = parseInt($("i-prob").value); $("iv-prob").textContent = s.probability + "%"; });
  $("i-mt").addEventListener("input", () => { const s = cur(); if (!s) return; s.micro_timing = parseInt($("i-mt").value); $("iv-mt").textContent = fmtSigned(s.micro_timing); });
  $("i-cutoff").addEventListener("input", () => { const s = cur(); if (!s) return; (s.p_locks = s.p_locks || {}).cutoff = parseInt($("i-cutoff").value) / 100; $("iv-cutoff").textContent = $("i-cutoff").value + "%"; });
  $("i-res").addEventListener("input", () => { const s = cur(); if (!s) return; (s.p_locks = s.p_locks || {}).resonance = parseInt($("i-res").value) / 100; $("iv-res").textContent = $("i-res").value + "%"; });
  $("i-lfo-depth").addEventListener("input", () => { const s = cur(); if (!s) return; (s.p_locks = s.p_locks || {}).lfo_depth = parseInt($("i-lfo-depth").value) / 100; $("iv-lfo-depth").textContent = $("i-lfo-depth").value + "%"; });
  $("i-lfo-rate").addEventListener("input", () => { const s = cur(); if (!s) return; const hz = sliderToRate(+$("i-lfo-rate").value); (s.p_locks = s.p_locks || {}).lfo_rate = hz; $("iv-lfo-rate").textContent = fmtHz(hz); });
  $("i-pitch").addEventListener("input", () => { const s = cur(); if (!s) return; s.pitch_offset = parseInt($("i-pitch").value); $("iv-pitch").textContent = fmtSigned(s.pitch_offset); });
  $("i-start").addEventListener("input", () => { const s = cur(); if (!s) return; (s.p_locks = s.p_locks || {}).start = parseInt($("i-start").value) / 100; $("iv-start").textContent = $("i-start").value + "%"; });
  $("i-end").addEventListener("input", () => { const s = cur(); if (!s) return; (s.p_locks = s.p_locks || {}).end = parseInt($("i-end").value) / 100; $("iv-end").textContent = $("i-end").value + "%"; });

  // Commit (PUT) on release / change so we don't spam the bridge per pixel.
  ["i-vel", "i-prob", "i-mt", "i-start", "i-end", "i-pitch", "i-cutoff", "i-res", "i-lfo-depth", "i-lfo-rate"].forEach(id =>
    $(id).addEventListener("change", saveSelectedStep));
  $("i-trig").addEventListener("change", () => { const s = cur(); if (s) { s.trig_condition = $("i-trig").value; saveSelectedStep(); } });
  $("i-fmode").addEventListener("change", () => { const s = cur(); if (s) { (s.p_locks = s.p_locks || {}).filter_mode = $("i-fmode").value; saveSelectedStep(); } });
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

// ── Per-track param push (host automation / generic editor -> UI) ────────────
function onParams(changed) {
  if (!project || !Array.isArray(changed)) return;
  for (const c of changed) {
    const t = project.tracks[c.index];
    if (!t) continue;
    if (c.volume !== undefined) t.volume = c.volume;
    if (c.pan !== undefined) t.pan = c.pan;
    if (c.cutoff !== undefined) t.cutoff = c.cutoff;
    if (c.resonance !== undefined) t.resonance = c.resonance;
    if (c.filter_mode !== undefined) t.filter_mode = c.filter_mode;
    const row = document.querySelector(`[data-track-id="${t.id}"]`);
    if (!row) continue;
    // Don't fight a control the user is actively dragging.
    const set = (sel, val) => { const el = row.querySelector(sel); if (el && el !== document.activeElement && val !== undefined) el.value = val; };
    set(".vol", c.volume !== undefined ? Math.round(c.volume * 100) : undefined);
    set(".pan", c.pan !== undefined ? Math.round(c.pan * 100) : undefined);
    set(".cut", c.cutoff !== undefined ? Math.round(c.cutoff * 100) : undefined);
    set(".res", c.resonance !== undefined ? Math.round(c.resonance * 100) : undefined);
    set(".fmode", c.filter_mode);
  }
}

// ── Project reload (DAW state load swapped in a whole new Project) ────────────
async function onProjectReload() {
  try { project = await GET("/project"); } catch { return; }
  sel = { trackId: null, idx: null };
  renderTracks();
  hideTrimmer();
  lfoPanel.classList.remove("visible");
  lfoTrackId = null;
  const sw = Math.round((project.swing || 0) * 100);
  swingEl.value = sw; swingPct.textContent = sw + "%";   // header reflects restored swing
  setStatus(`project loaded — ${project.tracks.length} tracks`, true);
}

// ── Per-track LFO panel ──────────────────────────────────────────────────────
let lfoTrackId = null;

// Rate slider 0..100 <-> 0.1..20 Hz (exponential), shared by panel + inspector.
const sliderToRate = (v) => 0.1 * Math.pow(200, v / 100);
const rateToSlider = (hz) => Math.round(100 * Math.log(Math.max(0.1, hz) / 0.1) / Math.log(200));
const fmtHz = (hz) => (hz < 1 ? hz.toFixed(2) : hz.toFixed(1)) + " Hz";

function showLfo(trackId) {
  const t = findTrack(trackId);
  if (!t) { lfoPanel.classList.remove("visible"); return; }
  lfoTrackId = trackId;
  const L = t.lfo || {};
  lfoNameEl.textContent = t.name;
  lfoShapeEl.value = L.shape || "sine";
  lfoDestEl.value  = L.destination || "cutoff";
  const hz = L.rate ?? 1;
  lfoRateEl.value = rateToSlider(hz);  lfoRateV.textContent = fmtHz(hz);
  const d = Math.round((L.depth ?? 0) * 100);
  lfoDepthEl.value = d;  lfoDepthV.textContent = d + "%";
  lfoSyncEl.checked = L.sync !== false;
  lfoPanel.classList.add("visible");
}

function sendLfo(patch) {
  if (!lfoTrackId) return;
  const t = findTrack(lfoTrackId);
  if (t) t.lfo = Object.assign({}, t.lfo, patch);
  PUT(`/tracks/${lfoTrackId}/lfo`, patch);
}

// ── Projects (standalone Save / Load to ~/SILA/projects) ─────────────────────
async function openProjects() {
  projModal.classList.add("open");
  projName.focus();
  await refreshProjectList();
}
function closeProjects() { projModal.classList.remove("open"); }

async function refreshProjectList() {
  projListEl.innerHTML = '<div class="lib-empty">loading…</div>';
  let res;
  try { res = await GET("/projects"); } catch { projListEl.innerHTML = '<div class="lib-empty">could not list projects</div>'; return; }
  const names = (res && res.projects) || [];
  projListEl.innerHTML = "";
  if (!names.length) { projListEl.innerHTML = '<div class="lib-empty">no saved projects yet</div>'; return; }
  for (const name of names) {
    const row = document.createElement("div");
    row.className = "lib-sample proj-item";
    const label = document.createElement("span");
    label.textContent = name;
    label.onclick = () => loadProject(name);
    row.appendChild(label);
    projListEl.appendChild(row);
  }
}

async function saveProject() {
  const name = projName.value.trim();
  if (!name) { setStatus("enter a project name", false); return; }
  try {
    const res = await POST("/projects/save", { name });
    if (res && res.saved) { setStatus(`saved "${res.saved}"`, true); projName.value = ""; refreshProjectList(); }
    else setStatus("save failed: " + ((res && res.error) || "?"), false);
  } catch { setStatus("save failed", false); }
}

async function loadProject(name) {
  try {
    const res = await POST("/projects/load", { name });
    if (res && res.loaded) { closeProjects(); setStatus(`loaded "${res.loaded}"`, true); }
    else setStatus("load failed: " + ((res && res.error) || "?"), false);
  } catch { setStatus("load failed", false); }
  // grid refresh arrives via the "project" event (projectEpoch bump)
}

// ── Boot ────────────────────────────────────────────────────────────────────
async function boot() {
  if (typeof window.__JUCE__ !== "undefined" && window.__JUCE__.backend) {
    window.__JUCE__.backend.addEventListener("playhead", onPlayhead);
    window.__JUCE__.backend.addEventListener("status", onStatus);
    window.__JUCE__.backend.addEventListener("export", onExport);
    window.__JUCE__.backend.addEventListener("project", onProjectReload);
    window.__JUCE__.backend.addEventListener("params", onParams);
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

  // Track management.
  const addBtn = document.getElementById("add-track");
  if (addBtn) addBtn.addEventListener("click", addTrack);

  // Projects (save/load).
  projectsBtn.addEventListener("click", openProjects);
  projSaveBtn.addEventListener("click", saveProject);
  projCloseEl.addEventListener("click", closeProjects);
  projModal.addEventListener("click", (e) => { if (e.target === projModal) closeProjects(); });
  projName.addEventListener("keydown", (e) => { if (e.key === "Enter") saveProject(); });

  // LFO panel controls.
  lfoShapeEl.addEventListener("change", () => sendLfo({ shape: lfoShapeEl.value }));
  lfoDestEl.addEventListener("change", () => sendLfo({ destination: lfoDestEl.value }));
  lfoRateEl.addEventListener("input", () => { lfoRateV.textContent = fmtHz(sliderToRate(+lfoRateEl.value)); });
  lfoRateEl.addEventListener("change", () => sendLfo({ rate: sliderToRate(+lfoRateEl.value) }));
  lfoDepthEl.addEventListener("input", () => { lfoDepthV.textContent = lfoDepthEl.value + "%"; });
  lfoDepthEl.addEventListener("change", () => sendLfo({ depth: +lfoDepthEl.value / 100 }));
  lfoSyncEl.addEventListener("change", () => sendLfo({ sync: lfoSyncEl.checked }));

  setStatus(`connected — ${project.tracks.length} tracks · click a step, right-click to inspect`, true);
}

boot().catch(e => setStatus("bridge error: " + (e && e.message ? e.message : e)));
