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

// ── Tooltips ─────────────────────────────────────────────────────────────────
// attachTip(el, html): a small themed tooltip after a short hover delay. `html`
// is trusted (our own strings), shown above the element. Used to ID a control
// and explain how it shapes the sound.
let _tipEl = null, _tipTimer = null;
function _hideTip() { clearTimeout(_tipTimer); if (_tipEl) _tipEl.classList.remove("show"); }
function _showTip(el, html) {
  if (!_tipEl) { _tipEl = document.createElement("div"); _tipEl.id = "tooltip"; document.body.appendChild(_tipEl); }
  _tipEl.innerHTML = html;
  const r = el.getBoundingClientRect();
  const tw = _tipEl.offsetWidth, th = _tipEl.offsetHeight, vw = window.innerWidth;
  // Centre on the control, but clamp horizontally so it never runs off an edge.
  const cx = Math.max(tw / 2 + 4, Math.min(vw - tw / 2 - 4, r.left + r.width / 2));
  _tipEl.style.left = Math.round(cx) + "px";
  // Above by default; flip below when there isn't room (e.g. top-row knobs).
  if (r.top - th - 10 > 4) {
    _tipEl.style.top = Math.round(r.top - 8) + "px";
    _tipEl.style.transform = "translate(-50%, -100%)";
  } else {
    _tipEl.style.top = Math.round(r.bottom + 8) + "px";
    _tipEl.style.transform = "translate(-50%, 0)";
  }
  _tipEl.classList.add("show");
}
function attachTip(el, html) {
  el.addEventListener("mouseenter", () => { _tipTimer = setTimeout(() => _showTip(el, html), 350); });
  el.addEventListener("mouseleave", _hideTip);
  el.addEventListener("mousedown", _hideTip);
}

const tracksEl   = document.getElementById("tracks");
const barBeatEl  = document.getElementById("barbeat");
const swingHostEl = document.getElementById("swing-host");
let   swingKnob   = null;   // header swing dial (built in boot)
const masterVolEl = document.getElementById("master-vol");
const masterPctEl = document.getElementById("master-pct");
const songEl     = document.getElementById("songMode");
const patternSelectEl = document.getElementById("pattern-select");
const patternLenEl = document.getElementById("pattern-len");
const pageBarEl    = document.getElementById("page-bar");
const statusEl   = document.getElementById("status");
const transportEl = document.getElementById("transport");
const playStateEl = document.getElementById("play-state");
const bpmEl      = document.getElementById("bpm");
const playBtn    = document.getElementById("play-btn");
const activePatEl = document.getElementById("active-pattern");
const libModal   = document.getElementById("library-modal");
const libTreeEl  = document.getElementById("lib-tree");
const libSearch  = document.getElementById("lib-search");
const libCloseEl = document.getElementById("lib-close");
const libTargetEl = document.getElementById("lib-target");
const partsModal  = document.getElementById("parts-modal");
const partsTree   = document.getElementById("parts-tree");
const partsSearch = document.getElementById("parts-search");
const partsCloseEl = document.getElementById("parts-close");
const partsTargetEl = document.getElementById("parts-target");
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
const lfoSyncEl  = document.getElementById("lfo-sync");
let   lfoRateKnob = null, lfoDepthKnob = null;   // LFO rate/depth dials (built in boot)
const projectsBtn = document.getElementById("projects-btn");
const projModal  = document.getElementById("projects-modal");
const projName   = document.getElementById("proj-name");
const projSaveBtn = document.getElementById("proj-save");
const projCloseEl = document.getElementById("proj-close");
const projListEl = document.getElementById("proj-list");

let project = null;
let sel = { trackId: null, idx: null };   // selected step
// Transport (UI internal): mirror of the published play state + a local tempo
// target so the BPM wheel feels instant and the status echo doesn't fight it.
let _playing = false, uiBpm = 120, _bpmWheelAt = 0, _bpmPutTimer = null;
let currentPage = 0;                        // active 16-step page (front-end view state)
const STEPS_PER_PAGE = 16;

// SILA-theme track palette. A new track auto-takes the first UNused colour (no
// duplicate by default); the picker lets the user override with any colour
// (duplicates allowed when chosen manually). The engine just stores the string.
const THEME_PALETTE = ["#34e3c4", "#8b6cf0", "#ffae57", "#ff5a5a", "#5fd0e0", "#6ee7a0", "#f08bd0", "#e3d534"];

// Global musical key (UI-only; the engine reads absolute pitch_offset semitones).
// Convention: pitch_offset 0 = the key root. A step's chromatic note = root +
// pitch_offset; in-scale = (note-root) mod 12 is in the scale's intervals.
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const SCALES = {
  chromatic:          [0,1,2,3,4,5,6,7,8,9,10,11],
  major:              [0,2,4,5,7,9,11],
  minor:              [0,2,3,5,7,8,10],
  dorian:             [0,2,3,5,7,9,10],
  phrygian:           [0,1,3,5,7,8,10],
  mixolydian:         [0,2,4,5,7,9,10],
  lydian:             [0,2,4,6,7,9,11],
  "pentatonic major": [0,2,4,7,9],
  "pentatonic minor": [0,3,5,7,10],
  "harmonic minor":   [0,2,3,5,7,8,11],
  blues:              [0,3,5,6,7,10],
};
const DISP_OCT_BASE = 2;   // pitch_offset 0 (root) reads as octave 2
const currentRoot      = () => (project && project.key_root) || 0;
const currentScaleName = () => (project && project.key_scale) || "chromatic";
const currentScale     = () => SCALES[currentScaleName()] || SCALES.chromatic;
// Scale degree (+ octave offset) -> semitones from the root (for melodic presets).
function degreeNote(d, o, scale) {
  const len = scale.length;
  const idx = ((d % len) + len) % len;
  return (o + Math.floor(d / len)) * 12 + scale[idx];
}
const noteLabel = (absSemi) => NOTE_NAMES[((absSemi % 12) + 12) % 12] + (Math.floor(absSemi / 12) + DISP_OCT_BASE);
const inScaleAbs = (absSemi) => currentScale().includes((((absSemi - currentRoot()) % 12) + 12) % 12);

// Suppress WebView2's native right-click menu so our oncontextmenu handlers
// (inspect-without-toggle) are what the user gets.
document.addEventListener("contextmenu", (e) => e.preventDefault());

function setStatus(msg, ok) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("ok", !!ok);
}

const findTrack = (id) => project.tracks.find(t => t.id === id);

// Rotary knob: drag vertically to turn, double-click to reset to `def`. Returns
// { el, set(v) } — set() updates the visual without firing onInput, and is a
// no-op while the user is dragging this knob (so host-automation pushes don't
// fight a drag). The cap shows the label, swapping to the live value while dragging.
function makeKnob({ min, max, value, label, def, color, format, onInput, onChange, valueInCap, tip, useTrackColor }) {
  const wrap = document.createElement("div");
  wrap.className = "knob-wrap";
  const knob = document.createElement("div");
  knob.className = "knob" + (color === "v2" ? " v2" : "") + (useTrackColor ? " track-clr" : "");
  const ring = document.createElement("div"); ring.className = "knob-ring";
  const face = document.createElement("div"); face.className = "knob-face";
  const ind = document.createElement("div"); ind.className = "knob-ind"; ind.innerHTML = "<i></i>";
  knob.append(ring, face, ind);
  // valueInCap: name label above + the live value in the cap (inspector style).
  // otherwise: label in the cap, value shown only while dragging (channel strip).
  if (valueInCap) { const n = document.createElement("span"); n.className = "knob-name"; n.textContent = label; wrap.appendChild(n); }
  wrap.appendChild(knob);
  const cap = document.createElement("span"); cap.className = "knob-cap";
  wrap.appendChild(cap);

  const arc = color === "v2" ? "#8b6cf0" : "#34e3c4";
  // useTrackColor: read the row's live --track-color so recolouring updates the
  // dial instantly (the var re-resolves with no rebuild). Falls back to teal.
  const arcExpr = useTrackColor ? "var(--track-color, #34e3c4)" : arc;
  const fmt = format || (v => Math.round(v));
  let val = value, dragging = false;
  function paint() {
    const norm = (val - min) / (max - min);
    const deg = norm * 270;
    ring.style.background = `conic-gradient(from 225deg, ${arcExpr} 0deg ${deg}deg, #1c2836 ${deg}deg 270deg, transparent 270deg 360deg)`;
    ind.style.transform = `rotate(${-135 + norm * 270}deg)`;
    cap.textContent = valueInCap ? fmt(val) : label;
  }
  function set(v, fire) {
    val = Math.max(min, Math.min(max, v));
    paint();
    if (fire && onInput) onInput(val);
  }
  paint();

  knob.addEventListener("mousedown", (e) => {
    e.preventDefault();
    dragging = true;
    const startY = e.clientY, startVal = val, span = max - min;
    if (!valueInCap) cap.textContent = fmt(val);
    const onMove = (ev) => { set(startVal + ((startY - ev.clientY) / 160) * span, true); if (!valueInCap) cap.textContent = fmt(val); };
    const onUp = () => {
      dragging = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      if (!valueInCap) cap.textContent = label;
      if (onChange) onChange(val);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
  if (def !== undefined) knob.addEventListener("dblclick", () => { set(def, true); if (onChange) onChange(val); });

  // Mouse wheel: fine adjust (~2% of range per notch). Commit (onChange) is
  // debounced so wheeling doesn't spam; the cap flashes the value (non-cap mode).
  let wheelCommit = null, capRevert = null;
  knob.addEventListener("wheel", (e) => {
    e.preventDefault();
    set(val + (e.deltaY < 0 ? 1 : -1) * (max - min) / 50, true);
    if (!valueInCap) { cap.textContent = fmt(val); clearTimeout(capRevert); capRevert = setTimeout(() => { cap.textContent = label; }, 800); }
    clearTimeout(wheelCommit);
    wheelCommit = setTimeout(() => { if (onChange) onChange(val); }, 160);
  }, { passive: false });

  if (tip) attachTip(knob, tip);

  return { el: wrap, set: (v) => { if (!dragging) set(v, false); } };
}

// A step carries non-default params worth flagging with a dot.
function stepIsLocked(s) {
  const pl = s.p_locks || {};
  return s.probability < 100 || (s.trig_condition && s.trig_condition !== "always") ||
         (s.micro_timing || 0) !== 0 || (s.pitch_offset || 0) !== 0 || (s.retrig ?? 1) > 1 ||
         pl.start !== undefined || pl.end !== undefined ||
         pl.cutoff !== undefined || pl.resonance !== undefined ||
         pl.lfo_depth !== undefined || pl.lfo_rate !== undefined || pl.filter_mode !== undefined ||
         (s.velocity !== undefined && s.velocity !== 100);
}

// ── Render ────────────────────────────────────────────────────────────────
function renderTracks() {
  tracksEl.innerHTML = "";
  for (const track of project.tracks) {
    if (track.active === false) continue;   // soft-deleted in this pattern (shown as a re-add chip)
    const row = document.createElement("div");
    row.className = "track-row";
    row.dataset.trackId = track.id;
    if (track.color) row.style.setProperty("--track-color", track.color);   // cascades to dot + cells

    const ms = document.createElement("div");
    ms.className = "ms";
    const mute = document.createElement("button");
    mute.className = "mute" + (track.muted ? " on" : "");
    mute.textContent = "M";
    mute.onclick = () => toggleMute(track.id);
    attachTip(mute, "<b>Mute</b> — silence this track.");
    const solo = document.createElement("button");
    solo.className = "solo" + (track.solo ? " on" : "");
    solo.textContent = "S";
    solo.onclick = () => toggleSolo(track.id);
    attachTip(solo, "<b>Solo</b> — hear only soloed tracks.");
    const del = document.createElement("button");
    del.className = "del";
    del.textContent = "×";
    const delDefaultTitle = "hide in this pattern · shift-click: remove from every pattern";
    del.title = delDefaultTitle;
    let delTimer = null;
    const disarm = () => { del.classList.remove("armed", "armed-global"); del.title = delDefaultTitle; };
    del.onclick = (e) => {
      if (del.classList.contains("armed")) {            // confirmed
        clearTimeout(delTimer);
        if (del._global) deleteTrack(track.id);         // shift: remove the lane everywhere
        else             hideTrack(track.id);           // default: soft-delete this pattern only
        return;
      }
      del._global = e.shiftKey;                          // arm — second click confirms
      del.classList.add("armed");
      del.classList.toggle("armed-global", del._global);
      del.title = del._global ? "click again to remove from ALL patterns" : "click again to hide here";
      clearTimeout(delTimer);
      delTimer = setTimeout(disarm, 3000);
    };
    ms.appendChild(mute);
    ms.appendChild(solo);
    ms.appendChild(del);

    const dot = document.createElement("div");
    dot.className = "track-color-dot";
    dot.title = "track colour — click to change";
    dot.onclick = (e) => { e.stopPropagation(); openColorPop(dot, track.id); };

    const name = document.createElement("div");
    name.className = "track-name";
    name.textContent = track.name;
    name.title = "click for track options · double-click to rename";
    name.onclick = () => selectTrack(track.id);
    name.ondblclick = () => startRename(track.id, name);

    const slot = document.createElement("div");
    slot.className = "sample-slot" + (track.samples && track.samples.length ? " loaded" : "");
    slot.textContent = sampleLabel(track);
    slot.onclick = (e) => { e.stopPropagation(); openLibrary(track.id, track.name); };
    attachTip(slot, (track.samples && track.samples[0])
      ? "<b>Sample</b> — " + track.samples[0].path.split("/").pop() + " · click to change"
      : "<b>Sample</b> — click to load one from the library.");

    const mix = document.createElement("div");
    mix.className = "track-mix";
    const knobRow = document.createElement("div");
    knobRow.className = "knob-row";
    const pct = v => Math.round(v * 100);
    const kVol = makeKnob({ min: 0, max: 1, value: track.volume ?? 1, label: "Vol", def: 1, format: pct, useTrackColor: true,
      tip: "<b>Volume</b> — output level of this track.",
      onInput: v => { track.volume = v; PUT(`/tracks/${track.id}/volume`, { volume: v }); } });
    const kCut = makeKnob({ min: 0, max: 1, value: track.cutoff ?? 1, label: "Cut", def: 1, format: pct, useTrackColor: true,
      tip: "<b>Cutoff</b> — filter frequency. Lower = darker/muffled.",
      onInput: v => { track.cutoff = v; PUT(`/tracks/${track.id}/cutoff`, { cutoff: v }); } });
    const kPan = makeKnob({ min: -1, max: 1, value: track.pan ?? 0, label: "Pan", def: 0, format: pct, useTrackColor: true,
      tip: "<b>Pan</b> — left / right position in the stereo field.",
      onInput: v => { track.pan = v; PUT(`/tracks/${track.id}/pan`, { pan: v }); } });
    const kRes = makeKnob({ min: 0, max: 1, value: track.resonance ?? 0, label: "Res", def: 0, format: pct, useTrackColor: true,
      tip: "<b>Resonance</b> — emphasis at the cutoff; high adds a whistle/peak.",
      onInput: v => { track.resonance = v; PUT(`/tracks/${track.id}/resonance`, { resonance: v }); } });
    knobRow.append(kVol.el, kCut.el, kPan.el, kRes.el);
    mix.appendChild(knobRow);

    const fmode = document.createElement("select");
    fmode.className = "fmode"; fmode.title = "filter mode";
    [["lowpass", "LP"], ["highpass", "HP"], ["bandpass", "BP"]].forEach(([v, t]) => {
      const o = document.createElement("option"); o.value = v; o.textContent = t; fmode.appendChild(o);
    });
    fmode.value = track.filter_mode || "lowpass";
    fmode.addEventListener("change", () => { track.filter_mode = fmode.value; PUT(`/tracks/${track.id}/filter_mode`, { mode: fmode.value }); });
    mix.appendChild(fmode);

    row._mix = { vol: kVol.set, cut: kCut.set, pan: kPan.set, res: kRes.set };

    const grid = document.createElement("div");
    grid.className = "step-grid";
    // Render only the active page's window; cells keep their GLOBAL step index so
    // edits / PUT / playhead all address the real step regardless of the page.
    const start = currentPage * STEPS_PER_PAGE;
    const end = Math.min(track.steps.length, start + STEPS_PER_PAGE);
    for (let idx = start; idx < end; idx++) {
      const step = track.steps[idx];
      const cell = document.createElement("div");
      cell.dataset.stepIdx = idx;
      paintCell(cell, track.id, idx, step);
      cell.onclick = () => { toggleStep(track.id, idx); selectStep(track.id, idx); };
      cell.oncontextmenu = (e) => { e.preventDefault(); resetStep(track.id, idx); };
      grid.appendChild(cell);
    }

    const partsBtn = document.createElement("button");
    partsBtn.className = "parts-btn";
    partsBtn.textContent = "≣";
    partsBtn.title = "load a pattern part onto this track";
    partsBtn.onclick = (e) => { e.stopPropagation(); openParts(track.id, track.name); };

    row.appendChild(ms);
    row.appendChild(dot);
    row.appendChild(name);
    row.appendChild(slot);
    row.appendChild(partsBtn);
    row.appendChild(mix);
    row.appendChild(grid);
    tracksEl.appendChild(row);
  }

  // Hidden (soft-deleted) lanes are NOT shown; "+ Track" restores the next one
  // (see addTrack). The button stays enabled while any lane is hidden, even at the
  // 8-lane pool cap — restoring doesn't grow the pool.
  const addBtn = document.getElementById("add-track");
  if (addBtn) addBtn.disabled = project.tracks.length >= 8
                                && ! project.tracks.some(t => t.active === false);
  _lastCol = {};   // force the playhead to re-light after a rebuild (page/length change)
}

// ── Pattern selector (which PatternBank slot the grid edits / plays) ─────────
const patName = (i) => "A" + String(i + 1).padStart(2, "0");

function renderPatternSelect() {
  if (!project) return;
  const count = project.pattern_count || 16;
  const cur = project.current_pattern || 0;
  patternSelectEl.innerHTML = "";
  for (let i = 0; i < count; i++) {
    const o = document.createElement("option");
    o.value = i; o.textContent = patName(i);
    patternSelectEl.appendChild(o);
  }
  patternSelectEl.value = cur;
}

// ── Pattern length + pages (master length; pages of 16, view state only) ─────
const pageCount = () => Math.max(1, Math.ceil(((project && project.pattern_length) || 16) / STEPS_PER_PAGE));

// Reflect the current pattern's length in the LEN box and (re)build the page
// chips. Hidden entirely when the pattern fits in one page (<= 16 steps).
function renderPatternMeta() {
  if (!project) return;
  patternLenEl.value = project.pattern_length || 16;

  const pages = pageCount();
  if (currentPage >= pages) currentPage = pages - 1;

  pageBarEl.innerHTML = "";
  if (pages <= 1) { pageBarEl.classList.remove("visible"); return; }
  pageBarEl.classList.add("visible");

  const lbl = document.createElement("span");
  lbl.className = "page-lbl"; lbl.textContent = "Page";
  pageBarEl.appendChild(lbl);
  for (let i = 0; i < pages; i++) {
    const chip = document.createElement("button");
    chip.className = "page-chip" + (i === currentPage ? " active" : "");
    chip.textContent = (i + 1) + ":" + pages;   // e.g. 1:4, 2:4
    chip.title = `steps ${i * STEPS_PER_PAGE + 1}–${Math.min((project.pattern_length || 16), (i + 1) * STEPS_PER_PAGE)}`;
    chip.onclick = () => setPage(i);
    pageBarEl.appendChild(chip);
  }
}

// Flip the visible 16-step window — pure front-end, no backend call.
function setPage(p) {
  currentPage = Math.max(0, Math.min(pageCount() - 1, p));
  renderTracks();
  renderPatternMeta();
}

// LEN change: resize the pattern (message-thread), re-fetch the grid, clamp page.
async function setPatternLength(len) {
  let v = parseInt(len);
  if (isNaN(v)) v = 16;
  v = Math.max(1, Math.min(128, v));
  try {
    await PUT("/pattern/length", { length: v });
    project = await GET("/project");
  } catch { setStatus("length change failed", false); return; }
  if (currentPage >= pageCount()) currentPage = pageCount() - 1;
  renderTracks();
  renderPatternMeta();
  const pages = pageCount();
  setStatus(`pattern length ${v} (${pages} page${pages > 1 ? "s" : ""})`, true);
}

// Switch the edited/played pattern: persist, re-fetch the grid (steps come from
// the new slot), and reset the inspector since the selected step is now stale.
async function selectPattern(i) {
  try {
    await PUT("/pattern/select", { index: i });
    project = await GET("/project");
  } catch { setStatus("pattern switch failed", false); return; }
  ensureTrackColors();
  sel = { trackId: null, idx: null };
  currentPage = 0;
  renderTracks();
  renderPatternSelect();
  renderPatternMeta();
  hideTrimmer();
  lfoPanel.classList.remove("visible");
  $("insp-empty").style.display = "block";
  $("insp-fields").style.display = "none";
  $("insp-title").textContent = "INSPECTOR";
  $("insp-sub").textContent = "left-click toggles · right-click inspects";
  setStatus(`editing pattern ${patName(i)}`, true);
}

// ── Per-track colour ─────────────────────────────────────────────────────────
// Assign the next UNused palette colour to any track without one (new tracks,
// pre-colour projects). Sets it locally + persists (fire-and-forget). Must run
// before renderTracks so the row picks up its --track-color with no flash.
function ensureTrackColors() {
  if (!project) return;
  const used = new Set(project.tracks.map(t => t.color).filter(Boolean));
  project.tracks.forEach((t, i) => {
    if (t.color) return;
    const next = THEME_PALETTE.find(c => !used.has(c)) || THEME_PALETTE[i % THEME_PALETTE.length];
    t.color = next;
    used.add(next);
    PUT(`/tracks/${t.id}/color`, { color: next });
  });
}

// The row's --track-color cascades to the dot + the active step cells.
function applyTrackColor(id, color) {
  const t = findTrack(id); if (t) t.color = color;
  const row = document.querySelector(`[data-track-id="${id}"]`);
  if (row) row.style.setProperty("--track-color", color);
}
function setTrackColor(id, color) { applyTrackColor(id, color); PUT(`/tracks/${id}/color`, { color }); }

// Palette popover anchored to a track's colour dot.
let _colorPop = null;
function closeColorPop() {
  if (!_colorPop) return;
  _colorPop.remove(); _colorPop = null;
  document.removeEventListener("mousedown", _colorPopOutside, true);
}
function _colorPopOutside(e) { if (_colorPop && !_colorPop.contains(e.target)) closeColorPop(); }
function openColorPop(anchorEl, trackId) {
  closeColorPop();
  const t = findTrack(trackId);
  const pop = document.createElement("div");
  pop.className = "color-pop";
  for (const c of THEME_PALETTE) {
    const sw = document.createElement("div");
    sw.className = "sw" + (t && t.color === c ? " active" : "");
    sw.style.background = c;
    sw.title = c + (t && t.color === c ? " (current)" : "");
    sw.onclick = () => { setTrackColor(trackId, c); closeColorPop(); };
    pop.appendChild(sw);
  }
  document.body.appendChild(pop);
  const r = anchorEl.getBoundingClientRect();
  pop.style.left = Math.max(6, Math.min(r.left, window.innerWidth - pop.offsetWidth - 6)) + "px";
  pop.style.top  = (r.bottom + 6) + "px";
  _colorPop = pop;
  setTimeout(() => document.addEventListener("mousedown", _colorPopOutside, true), 0);
}

// ── Track management (add / remove / rename) ─────────────────────────────────
// add/remove publish a new Project+bank via setProject -> projectEpoch bump, so
// the UI rebuilds through the "project" event. Rename is a snapshot edit only.
async function addTrack() {
  // If this pattern has hidden lanes, "+ Track" restores the next one (lowest
  // index) instead of growing the global pool — e.g. A02 showing 2 of 4 lanes
  // brings back lane 3. Only when nothing is hidden does it add a brand-new lane.
  const hidden = project.tracks.find(t => t.active === false);
  if (hidden) { await restoreTrack(hidden.id); return; }
  const res = await POST("/tracks", {});
  if (res && res.error) setStatus(res.error, false);
}

async function deleteTrack(id) {
  await DEL(`/tracks/${id}`);
}

// Per-pattern soft-delete / restore (Phase 7b). These only touch the CURRENT
// pattern, and the route uses editProject (no epoch bump), so we re-fetch + re-render.
async function hideTrack(id) {
  await PUT(`/tracks/${id}/pattern-active`, { active: false });
  await onProjectReload();
}

async function restoreTrack(id) {
  await PUT(`/tracks/${id}/pattern-active`, { active: true });
  await onProjectReload();
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
  step.retrig = 1;
  step.retrig_fade = 0;
  step.p_locks = {};        // clears start/end/cutoff/resonance/lfo/filter_mode
  repaintCell(trackId, idx);
  selectStep(trackId, idx); // select + refresh the inspector to the reset values
  await PUT(`/tracks/${trackId}/steps/${idx}`, { step });
  setStatus("step reset", true);
}

// ── Inspector ───────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const curStep = () => findTrack(sel.trackId)?.steps[sel.idx];

// The per-step knobs (built once into #insp-knobs). Each descriptor reads its
// value for the selected step (p-lock ?? track base) and writes it back; PUT
// happens on release (onChange). Scalars live on the step; the rest are p-locks.
const inspKnobs = {};
const INSP_KNOBS = [
  { id:"vel",   label:"Vel",     min:0,   max:127, def:100, fmt:v=>Math.round(v),
    tip:"<b>Velocity</b> — how hard this step hits (louder / brighter).",
    read:s=>s.velocity ?? 100,                                write:(s,v)=>{ s.velocity = Math.round(v); } },
  { id:"prob",  label:"Prob",    min:0,   max:100, def:100, fmt:v=>Math.round(v)+"%",
    tip:"<b>Probability</b> — chance this step fires on each pass.",
    read:s=>s.probability ?? 100,                             write:(s,v)=>{ s.probability = Math.round(v); } },
  { id:"micro", label:"Micro",   min:-23, max:23,  def:0,   fmt:v=>fmtSigned(Math.round(v)),
    tip:"<b>Micro-timing</b> — nudge this hit earlier / later than the grid.",
    read:s=>s.micro_timing ?? 0,                              write:(s,v)=>{ s.micro_timing = Math.round(v); } },
  { id:"retrig", label:"Retrig", min:1, max:8, def:1, fmt:v=>Math.round(v)+"×",
    tip:"<b>Retrig</b> — re-fire the sample this many times within the step (ratchet / roll).",
    read:s=>s.retrig ?? 1,                                    write:(s,v)=>{ s.retrig = Math.round(v); } },
  { id:"rfade", label:"R.Fade", min:-1, max:1, def:0, color:"v2", fmt:v=>Math.round(v*100)+"%",
    tip:"<b>Retrig Fade</b> — velocity ramp across the retrigs (+ swells up, − fades out).",
    read:s=>s.retrig_fade ?? 0,                               write:(s,v)=>{ s.retrig_fade = v; } },
  // Pitch is set via the note keyboard (below the knob grid), not a knob.
  { id:"cut",   label:"Cut",     min:0, max:1, def:1, color:"v2", fmt:v=>Math.round(v*100)+"%",
    tip:"<b>Cutoff</b> — filter for this step (overrides the track). Lower = darker.",
    read:(s,t)=> (s.p_locks?.cutoff ?? t.cutoff ?? 1),        write:(s,v)=>{ (s.p_locks=s.p_locks||{}).cutoff = v; } },
  { id:"res",   label:"Res",     min:0, max:1, def:0, color:"v2", fmt:v=>Math.round(v*100)+"%",
    tip:"<b>Resonance</b> — filter emphasis for this step; high adds a peak.",
    read:(s,t)=> (s.p_locks?.resonance ?? t.resonance ?? 0),  write:(s,v)=>{ (s.p_locks=s.p_locks||{}).resonance = v; } },
  { id:"ldep",  label:"LFO Dep", min:0, max:1, def:0, color:"v2", fmt:v=>Math.round(v*100)+"%",
    tip:"<b>LFO Depth</b> — how far the LFO modulates, for this step.",
    read:(s,t)=> (s.p_locks?.lfo_depth ?? (t.lfo && t.lfo.depth) ?? 0), write:(s,v)=>{ (s.p_locks=s.p_locks||{}).lfo_depth = v; } },
  { id:"lrate", label:"LFO Hz",  min:0, max:1, def:0.435, color:"v2", fmt:v=>fmtHz(sliderToRate(v*100)),
    tip:"<b>LFO Rate</b> — speed of the modulation, for this step.",
    read:(s,t)=> rateToSlider((s.p_locks && s.p_locks.lfo_rate) ?? (t.lfo && t.lfo.rate) ?? 1)/100,
    write:(s,v)=>{ (s.p_locks=s.p_locks||{}).lfo_rate = sliderToRate(v*100); } },
  { id:"start", label:"Start",   min:0, max:1, def:0, fmt:v=>Math.round(v*100)+"%",
    tip:"<b>Sample Start</b> — trim where the sample begins, for this step.",
    read:s=> (s.p_locks?.start ?? 0),                         write:(s,v)=>{ (s.p_locks=s.p_locks||{}).start = v; } },
  { id:"end",   label:"End",     min:0, max:1, def:1, fmt:v=>Math.round(v*100)+"%",
    tip:"<b>Sample End</b> — trim where the sample ends, for this step.",
    read:s=> (s.p_locks?.end ?? 1),                           write:(s,v)=>{ (s.p_locks=s.p_locks||{}).end = v; } },
];

function buildInspectorKnobs() {
  const grid = $("insp-knobs");
  if (!grid) return;
  for (const p of INSP_KNOBS) {
    const k = makeKnob({
      min: p.min, max: p.max, value: p.def, label: p.label, def: p.def, color: p.color,
      format: p.fmt, valueInCap: true, tip: p.tip, useTrackColor: true,
      onInput: v => { const s = curStep(); if (s) p.write(s, v); },
      onChange: () => { if (curStep()) saveSelectedStep(); }
    });
    inspKnobs[p.id] = k;
    grid.appendChild(k.el);
  }
}

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
  $("insp-knobs").style.setProperty("--track-color", track.color || "#34e3c4");   // tint the step dials

  for (const p of INSP_KNOBS) inspKnobs[p.id].set(p.read(step, track));
  $("i-trig").value   = step.trig_condition || "always";
  $("i-fmode").value  = pl.filter_mode ?? track.filter_mode ?? "lowpass";
  $("i-length").value = String(step.length ?? 0);   // 0 = ∞ one-shot (default)

  // Note keyboard: jump to the selected note's octave + reflect the project key.
  kbOct = Math.floor((currentRoot() + (step.pitch_offset ?? 0)) / 12);
  syncKeySelectors();
  renderKeyboard();

  showTrimmer(trackId);   // trimmer follows the selected track's sample
  showLfo(trackId);       // LFO panel follows the selected track
}

const fmtSigned = (v) => (Number(v) > 0 ? "+" + v : String(v));

// ── Note keyboard (per-step pitch, key-aware) ────────────────────────────────
const KB_WHITE = [0, 2, 4, 5, 7, 9, 11];                                 // C D E F G A B
const KB_BLACK = [{ s:1, after:0 }, { s:3, after:1 }, { s:6, after:3 }, { s:8, after:4 }, { s:10, after:5 }];
let kbOct = 0;   // chromatic octave shown on the keyboard (X = kbOct*12 + pos)

const octRange = () => [Math.floor((currentRoot() - 24) / 12), Math.floor((currentRoot() + 24) / 12)];

function setStepNote(absX) {
  const s = curStep(); if (!s) return;
  s.pitch_offset = Math.max(-24, Math.min(24, absX - currentRoot()));
  saveSelectedStep();   // persists the step + repaints the cell (pitch lights the dot)
  renderKeyboard();
}
function shiftOctave(d) {
  const [lo, hi] = octRange();
  kbOct = Math.max(lo, Math.min(hi, kbOct + d));
  renderKeyboard();
}
function renderKeyboard() {
  const kb = $("keyboard"); if (!kb) return;
  kb.innerHTML = "";
  const s = curStep();
  const absSel = s ? currentRoot() + (s.pitch_offset ?? 0) : null;
  const key = (pos, cls, after) => {
    const abs = kbOct * 12 + pos;
    const k = document.createElement("div");
    k.className = cls + (inScaleAbs(abs) ? " scale" : "") + (abs === absSel ? " sel" : "");
    if (after !== undefined) k.style.left = ((after + 1) / 7 * 100) + "%";
    k.title = noteLabel(abs);
    k.onclick = (e) => { e.stopPropagation(); setStepNote(abs); };
    kb.appendChild(k);
  };
  KB_WHITE.forEach(pos => key(pos, "wkey"));
  KB_BLACK.forEach(b => key(b.s, "bkey", b.after));
  $("note-readout").textContent = s ? noteLabel(absSel) : "—";
  $("oct-val").textContent = kbOct + DISP_OCT_BASE;
}
function buildKeySelectors() {
  const r = $("key-root"); r.innerHTML = "";
  NOTE_NAMES.forEach((n, i) => { const o = document.createElement("option"); o.value = i; o.textContent = n; r.appendChild(o); });
  const sc = $("key-scale"); sc.innerHTML = "";
  Object.keys(SCALES).forEach(name => {
    const o = document.createElement("option"); o.value = name;
    o.textContent = name.replace(/\b\w/g, c => c.toUpperCase());
    sc.appendChild(o);
  });
}
function syncKeySelectors() {
  if ($("key-root")) $("key-root").value = currentRoot();
  if ($("key-scale")) $("key-scale").value = currentScaleName();
}
function setKey() {
  const root = parseInt($("key-root").value);
  const scale = $("key-scale").value;
  if (project) { project.key_root = root; project.key_scale = scale; }
  renderKeyboard();   // scale highlighting updates live
  PUT("/key", { root, scale });
}

function wireInspector() {
  buildInspectorKnobs();   // the per-step knobs (commit on release via onChange)
  buildKeySelectors();
  $("key-root").addEventListener("change", setKey);
  $("key-scale").addEventListener("change", setKey);
  $("oct-down").addEventListener("click", () => shiftOctave(-1));
  $("oct-up").addEventListener("click", () => shiftOctave(1));
  $("i-trig").addEventListener("change", () => { const s = curStep(); if (s) { s.trig_condition = $("i-trig").value; saveSelectedStep(); } });
  $("i-fmode").addEventListener("change", () => { const s = curStep(); if (s) { (s.p_locks = s.p_locks || {}).filter_mode = $("i-fmode").value; saveSelectedStep(); } });
  $("i-length").addEventListener("change", () => { const s = curStep(); if (s) { s.length = parseFloat($("i-length").value); saveSelectedStep(); } });
}

// ── Playhead (C++ -> UI) ────────────────────────────────────────────────────
let _lastCol = {};
function onPlayhead(ppq) {
  const p = Number(ppq);
  barBeatEl.textContent = `${Math.floor(p / 4) + 1} · ${(Math.floor(p) % 4) + 1}`;
  const globalStep = Math.floor(p * 4);
  if (!project) return;
  for (const track of project.tracks) {
    const n = track.steps.length;
    if (!n) continue;
    const col = ((globalStep % n) + n) % n;
    if (_lastCol[track.id] === col) continue;
    _lastCol[track.id] = col;
    // Only the current page's cells exist in the DOM; match by GLOBAL step index
    // so paging just works (an off-page cell simply isn't present to light).
    document.querySelectorAll(`[data-track-id="${track.id}"] .step`).forEach(c =>
      c.classList.toggle("playing", parseInt(c.dataset.stepIdx) === col));
  }
  // Glow the page chip holding the playhead (master length => same for all tracks).
  const N = project.pattern_length || 16;
  const playPage = Math.floor((((globalStep % N) + N) % N) / STEPS_PER_PAGE);
  pageBarEl.querySelectorAll(".page-chip").forEach((chip, i) =>
    chip.classList.toggle("playing", i === playPage));
}

// ── Transport status (C++ -> UI) ────────────────────────────────────────────
// Pushed on change (and fetched once on boot) — replaces app.js's 2 s poll.
function onStatus(s) {
  const playing = !!s.playing;
  _playing = playing;
  transportEl.classList.toggle("playing", playing);
  playStateEl.textContent = playing ? "PLAYING" : "STOPPED";
  playBtn.textContent = playing ? "■" : "▶";
  playBtn.classList.toggle("playing", playing);
  playBtn.title = playing ? "stop" : "play";

  // Reflect tempo, but don't clobber the display while the user is wheeling it.
  if (s.bpm != null) {
    uiBpm = Number(s.bpm);
    if (Date.now() - _bpmWheelAt > 400) bpmEl.textContent = uiBpm.toFixed(1);
  }

  // Active song-mode pattern (null = off/stopped). Slot 0 -> "A", 1 -> "B"...
  const slot = s.current_song_slot;
  activePatEl.textContent = (slot != null) ? "PATTERN " + String.fromCharCode(65 + slot) : "";

  // Song-mode playhead row (null = not in a song). Highlight it in the editor.
  songPlayRow = (s.current_song_row != null) ? s.current_song_row : -1;
  if (songScreen.classList.contains("open")) highlightSongRow();

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
    // Knob.set() is a no-op while that knob is being dragged, so this won't fight.
    if (row._mix) {
      if (c.volume !== undefined)    row._mix.vol(c.volume);
      if (c.cutoff !== undefined)    row._mix.cut(c.cutoff);
      if (c.pan !== undefined)       row._mix.pan(c.pan);
      if (c.resonance !== undefined) row._mix.res(c.resonance);
    }
    const fmEl = row.querySelector(".fmode");
    if (fmEl && fmEl !== document.activeElement && c.filter_mode !== undefined) fmEl.value = c.filter_mode;
  }
}

// ── Project reload (DAW state load swapped in a whole new Project) ────────────
async function onProjectReload() {
  try { project = await GET("/project"); } catch { return; }
  ensureTrackColors();
  sel = { trackId: null, idx: null };
  currentPage = 0;
  renderTracks();
  renderPatternSelect();
  renderPatternMeta();
  hideTrimmer();
  lfoPanel.classList.remove("visible");
  lfoTrackId = null;
  if (swingKnob) swingKnob.set(project.swing || 0);       // header reflects restored swing
  const mv = Math.round((project.master_vol ?? 1) * 100); // …and restored master volume
  masterVolEl.value = mv; masterPctEl.textContent = mv + "%";
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
  if (lfoRateKnob)  lfoRateKnob.set(rateToSlider(L.rate ?? 1) / 100);
  if (lfoDepthKnob) lfoDepthKnob.set(L.depth ?? 0);
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

// ── Song Mode editor (Digitakt-style row arrangement) ────────────────────────
//   GET  /song                  -> active song + song list + limits
//   PUT  /song/active|name|end  -> select / rename / end-behaviour
//   POST /song/new              -> create + select an empty song
//   POST /song/rows {index?,row?}-> insert a row (append if no index)
//   PUT  /song/rows/{i}         -> edit one row's fields (label/ptn/repeat/
//                                  length/tempo/mutes/mute_toggle)
//   DELETE /song/rows/{i}       -> delete a row
// Every mutation returns the fresh song state; the playing row comes from the
// "status" event's current_song_row.
const songScreen   = document.getElementById("song-screen");
const songBtn      = document.getElementById("song-btn");
const songSelect   = document.getElementById("song-select");
const songNewBtn   = document.getElementById("song-new");
const songNameEl   = document.getElementById("song-name");
const songEndEl    = document.getElementById("song-end");
const songCloseEl  = document.getElementById("song-close");
const songRowsEl   = document.getElementById("song-rows");
const songEmptyEl  = document.getElementById("song-empty");
const songAddRowEl = document.getElementById("song-add-row");

let songState  = null;   // { song, songs, active_song, max_rows, max_songs, pattern_slots }
let songPlayRow = -1;    // playing row from the status event

const ptnLabel = (slot) => "A" + String(slot + 1).padStart(2, "0");

async function openSongEditor() { songScreen.classList.add("open"); await refreshSong(); }
function closeSongEditor() { songScreen.classList.remove("open"); }

async function refreshSong() {
  try { songState = await GET("/song"); } catch { return; }
  renderSong();
}

// Structural changes (insert/delete/select/new) re-render from the response;
// field edits keep focus by NOT re-rendering (see editRow).
function applySongState(state) { if (state) { songState = state; renderSong(); } }

function renderSong() {
  if (!songState) return;
  songSelect.innerHTML = "";
  (songState.songs || []).forEach((name, i) => {
    const o = document.createElement("option");
    o.value = i; o.textContent = (name && name.trim()) ? name : ("Song " + (i + 1));
    songSelect.appendChild(o);
  });
  if (songState.active_song >= 0) songSelect.value = songState.active_song;

  const song = songState.song;
  songNameEl.value = song ? (song.name || "") : "";
  songEndEl.value  = song ? (song.end || "loop") : "loop";

  const maxRows = songState.max_rows || 99;
  const rowCount = song && song.rows ? song.rows.length : 0;
  songAddRowEl.disabled = rowCount >= maxRows;

  songRowsEl.innerHTML = "";
  if (!song || !rowCount) {
    songEmptyEl.textContent = song ? "No rows yet — click “+ Insert Row” to begin the arrangement."
                                   : "No song yet — “+ Insert Row” starts one automatically.";
    songEmptyEl.style.display = "block";
    return;
  }
  songEmptyEl.style.display = "none";
  song.rows.forEach((row, i) => songRowsEl.appendChild(buildSongRow(row, i)));
  highlightSongRow();
}

function numCell(value, min, max, onCommit) {
  const td = document.createElement("td");
  const inp = document.createElement("input");
  inp.className = "s-num"; inp.type = "number"; inp.min = min; inp.max = max; inp.value = value;
  inp.addEventListener("change", () => {
    let v = parseInt(inp.value); if (isNaN(v)) v = min;
    v = Math.max(min, Math.min(max, v)); inp.value = v; onCommit(v);
  });
  td.appendChild(inp); return td;
}

function buildSongRow(row, i) {
  const tr = document.createElement("tr");
  tr.dataset.rowIdx = i;

  const idx = document.createElement("td");
  idx.className = "song-idx";
  idx.textContent = String(i + 1).padStart(2, "0");
  tr.appendChild(idx);

  // LABEL
  const tdL = document.createElement("td");
  const label = document.createElement("input");
  label.className = "s-label"; label.value = row.label || ""; label.placeholder = "label…";
  label.addEventListener("change", () => editRow(i, { label: label.value }));
  tdL.appendChild(label); tr.appendChild(tdL);

  // PTN
  const tdP = document.createElement("td");
  const ptn = document.createElement("select"); ptn.className = "s-ptn"; ptn.title = "pattern slot";
  for (let s = 0; s < (songState.pattern_slots || 8); s++) {
    const o = document.createElement("option"); o.value = s; o.textContent = ptnLabel(s); ptn.appendChild(o);
  }
  ptn.value = row.pattern_slot || 0;
  ptn.addEventListener("change", () => editRow(i, { pattern_slot: parseInt(ptn.value) }));
  tdP.appendChild(ptn); tr.appendChild(tdP);

  // ↺ repeat (1..32), +I length (2..1024)
  tr.appendChild(numCell(row.repeat ?? 1, 1, 32,   v => editRow(i, { repeat: v })));
  tr.appendChild(numCell(row.length ?? 16, 2, 1024, v => editRow(i, { length: v })));

  // BPM override (blank/0 = global; Standalone only)
  const tdB = document.createElement("td");
  const bpm = document.createElement("input");
  bpm.className = "s-num s-bpm"; bpm.type = "number"; bpm.min = 0; bpm.max = 300; bpm.step = "0.1";
  bpm.value = row.tempo ? row.tempo : ""; bpm.placeholder = "—";
  bpm.title = "row tempo override — blank/0 uses the global tempo (Standalone only)";
  bpm.addEventListener("change", () => editRow(i, { tempo: parseFloat(bpm.value) || 0 }));
  tdB.appendChild(bpm); tr.appendChild(tdB);

  // MUTE — 8 per-track toggles (only as many as there are tracks are enabled)
  const tdM = document.createElement("td");
  const mutes = document.createElement("div"); mutes.className = "s-mutes";
  const trackCount = project ? project.tracks.length : 8;
  for (let s = 0; s < 8; s++) {
    const b = document.createElement("button");
    const muted = (row.mutes & (1 << s)) !== 0;
    if (muted) b.classList.add("on");
    b.textContent = String(s + 1);
    if (s >= trackCount) b.disabled = true;
    const tname = (project && project.tracks[s]) ? project.tracks[s].name : ("Track " + (s + 1));
    b.title = tname + (muted ? " — muted for this row" : "");
    b.addEventListener("click", () => toggleRowMute(i, s, b));
    mutes.appendChild(b);
  }
  tdM.appendChild(mutes); tr.appendChild(tdM);

  // delete
  const tdD = document.createElement("td");
  const del = document.createElement("button"); del.className = "s-del"; del.textContent = "×"; del.title = "delete row";
  del.addEventListener("click", () => deleteRow(i));
  tdD.appendChild(del); tr.appendChild(tdD);

  return tr;
}

// Field edit: reflect locally, PUT, keep the authoritative response WITHOUT a
// re-render so the focused input isn't torn out mid-edit.
async function editRow(i, patch) {
  if (songState && songState.song && songState.song.rows[i])
    Object.assign(songState.song.rows[i], patch);
  try { const s = await PUT(`/song/rows/${i}`, patch); if (s) songState = s; } catch {}
}

async function toggleRowMute(i, slot, btn) {
  const on = btn.classList.toggle("on");
  const r = songState && songState.song && songState.song.rows[i];
  if (r) r.mutes = on ? (r.mutes | (1 << slot)) : (r.mutes & ~(1 << slot));
  btn.title = ((project && project.tracks[slot]) ? project.tracks[slot].name : ("Track " + (slot + 1)))
            + (on ? " — muted for this row" : "");
  try { const s = await PUT(`/song/rows/${i}`, { mute_toggle: slot }); if (s) songState = s; } catch {}
}

async function addSongRow()      { applySongState(await POST("/song/rows", {})); }
async function deleteRow(i)      { applySongState(await DEL(`/song/rows/${i}`)); }
async function selectSong(i)     { applySongState(await PUT("/song/active", { index: i })); }
async function newSong()         { applySongState(await POST("/song/new", {})); }
async function renameSong()      { applySongState(await PUT("/song/name", { name: songNameEl.value.trim() })); }
async function setSongEnd()      { applySongState(await PUT("/song/end", { end: songEndEl.value })); }

function highlightSongRow() {
  songRowsEl.querySelectorAll("tr").forEach((tr, i) => tr.classList.toggle("playing", i === songPlayRow));
}

// ── Factory pattern parts (per-track presets, sound-agnostic step data) ──────
// Drum parts use a 16-char string: X=accent(120) x=hit(100) o=ghost(60) .=off.
// Melodic parts use notes [{i:step, p:semitones, v:velocity}]. Loading tiles the
// part across the pattern's master length (a 16-step part fills a 32-step pattern).
const FACTORY_PARTS = [
  { cat: "Kick", parts: [
    { name: "Four on the Floor", str: "x...x...x...x..." },
    { name: "Boom Bap",          str: "x.....x...x....." },
    { name: "Trap",              str: "x......x..x....." },
    { name: "Electro",           str: "x...x..xx...x..." },
    { name: "Offbeat",           str: "..x...x...x...x." },
    { name: "Half-time",         str: "x.......x......." },
    { name: "808 Bounce",        str: "x..x....x..x...." },
    { name: "House Pump",        str: "x...x...x...x..x" },
    { name: "Techno Drive",      str: "x...x...x...x.x." },
    { name: "Garage Skip",       str: "x..x..x...x..x.." },
    { name: "Breakbeat",         str: "x..x.....x.x...." },
    { name: "Amen",              str: "x.........x....." },
  ]},
  { cat: "Snare", parts: [
    { name: "Backbeat",          str: "....x.......x..." },
    { name: "Boom Bap Ghosts",   str: "....X..o..o.X..." },
    { name: "Roll End",          str: "............xxxx" },
    { name: "Half-time",         str: "........x......." },
    { name: "Double Hit",        str: "....x.......x..x" },
    { name: "Syncopated",        str: "....x.....x.x..." },
    { name: "Garage",            str: "....x....x..x..." },
    { name: "Trap",              str: "........x...x..." },
    { name: "Rimshot Roll",      str: "..o..o..x..o..o." },
    { name: "March",             str: "x.x.x.x.x.x.x.x." },
  ]},
  { cat: "Clap", parts: [
    { name: "On 2 & 4",          str: "....x.......x..." },
    { name: "Double",            str: "....x.x.....x.x." },
    { name: "Layered Roll",      str: "....X..o....X..o" },
    { name: "Offbeat",           str: "..x...x...x...x." },
  ]},
  { cat: "Hi-Hat", parts: [
    { name: "8ths",              str: "x.x.x.x.x.x.x.x." },
    { name: "16ths",             str: "xxxxxxxxxxxxxxxx" },
    { name: "Offbeat",           str: "..x...x...x...x." },
    { name: "Accented",          str: "x.X.x.X.x.X.x.X." },
    { name: "Trap Roll",         str: "x.x.x.xxx.x.x.xx" },
    { name: "Swung",             str: "x..xx..xx..xx..x" },
    { name: "House",             str: "x.xxx.xxx.xxx.xx" },
    { name: "Triplet Feel",      str: "x..x..x..x..x..x" },
    { name: "Skippy",            str: "x.xx.x.xx.x.xx.x" },
    { name: "32nd Burst",        str: "x.x.xxxxx.x.xxxx" },
  ]},
  { cat: "Open Hat", parts: [
    { name: "Offbeat",           str: "..x...x...x...x." },
    { name: "Disco",             str: "..x...x...x...xx" },
    { name: "Drive",             str: "..x.x.x...x.x.x." },
  ]},
  { cat: "Perc", parts: [
    { name: "Clave",             str: "x..x..x...x.x..." },
    { name: "Tambourine",        str: "..x...x...x...x." },
    { name: "Shaker",            str: "X.x.X.x.X.x.X.x." },
    { name: "Conga",             str: "..x.x..x..x.x..x" },
    { name: "Rumba",             str: "x..x..x..x.x...." },
    { name: "Bossa",             str: "x..x.x..x..x.x.." },
    { name: "Cowbell",           str: "..x..x.x..x..x.x" },
    { name: "Woodblock",         str: "x...x.x.x...x.x." },
  ]},
  { cat: "Tom", parts: [
    { name: "Fill Down",         str: "..........xxxxxx" },
    { name: "Tom Groove",        str: "x..x..x..x..x..x" },
    { name: "Jungle",            str: "..x..x..x.x..x.." },
  ]},
  // Melodic parts use scale DEGREES (d) + octave offset (o) so they land in the
  // current key/scale: d=0 root, d=2 the 3rd, d=4 the 5th, etc.
  { cat: "Bass", parts: [
    { name: "Octave Pulse",      notes: [{i:0,d:0},{i:4,d:0,o:1},{i:8,d:0},{i:12,d:0,o:1}] },
    { name: "Root 8ths",         notes: [{i:0,d:0},{i:2,d:0},{i:4,d:0},{i:6,d:0},{i:8,d:0},{i:10,d:0},{i:12,d:0},{i:14,d:0}] },
    { name: "Syncopated",        notes: [{i:0,d:0},{i:3,d:0},{i:6,d:0},{i:8,d:0},{i:11,d:0},{i:14,d:0}] },
    { name: "Walking",           notes: [{i:0,d:0},{i:4,d:2},{i:8,d:3},{i:12,d:4}] },
    { name: "Root & Fifth",      notes: [{i:0,d:0},{i:4,d:4},{i:8,d:0},{i:12,d:4}] },
    { name: "Arp Bass",          notes: [{i:0,d:0},{i:4,d:2},{i:8,d:4},{i:12,d:2}] },
    { name: "Acid Line",         notes: [{i:0,d:0},{i:2,d:0,o:1},{i:4,d:0},{i:6,d:2},{i:8,d:0},{i:10,d:0,o:1},{i:12,d:4},{i:14,d:0}] },
    { name: "Reggae Offbeat",    notes: [{i:2,d:0},{i:6,d:0},{i:10,d:0},{i:14,d:0}] },
    { name: "Funk",              notes: [{i:0,d:0},{i:3,d:0,o:1},{i:6,d:0},{i:8,d:4},{i:11,d:0},{i:14,d:2}] },
    { name: "Sub Pulse",         notes: [{i:0,d:0},{i:8,d:0}] },
    { name: "Driving 16ths",     notes: [{i:0,d:0},{i:1,d:0},{i:2,d:0},{i:3,d:0},{i:4,d:0},{i:5,d:0},{i:6,d:0},{i:7,d:0},{i:8,d:0},{i:9,d:0},{i:10,d:0},{i:11,d:0},{i:12,d:0},{i:13,d:0},{i:14,d:0},{i:15,d:0}] },
    { name: "Pedal Octaves",     notes: [{i:0,d:0},{i:2,d:0,o:1},{i:4,d:0},{i:6,d:0,o:1},{i:8,d:0},{i:10,d:0,o:1},{i:12,d:0},{i:14,d:0,o:1}] },
  ]},
  { cat: "Lead / Arp", parts: [
    { name: "Arp Up",            notes: [{i:0,d:0},{i:2,d:2},{i:4,d:4},{i:6,d:0,o:1},{i:8,d:0},{i:10,d:2},{i:12,d:4},{i:14,d:0,o:1}] },
    { name: "Arp Down",          notes: [{i:0,d:0,o:1},{i:2,d:4},{i:4,d:2},{i:6,d:0},{i:8,d:0,o:1},{i:10,d:4},{i:12,d:2},{i:14,d:0}] },
    { name: "Up-Down",           notes: [{i:0,d:0},{i:2,d:2},{i:4,d:4},{i:6,d:0,o:1},{i:8,d:4},{i:10,d:2},{i:12,d:0},{i:14,d:2}] },
    { name: "Octave Run",        notes: [{i:0,d:0},{i:2,d:0,o:1},{i:4,d:0},{i:6,d:0,o:1},{i:8,d:0},{i:10,d:0,o:1},{i:12,d:0},{i:14,d:0,o:1}] },
    { name: "Stabs",             notes: [{i:0,d:0},{i:6,d:4},{i:8,d:0,o:1},{i:14,d:4}] },
    { name: "Scale Run",         notes: [{i:0,d:0},{i:2,d:1},{i:4,d:2},{i:6,d:3},{i:8,d:4},{i:10,d:5},{i:12,d:6},{i:14,d:0,o:1}] },
    { name: "Trance Arp",        notes: [{i:0,d:0},{i:2,d:4},{i:4,d:2},{i:6,d:4},{i:8,d:0,o:1},{i:10,d:4},{i:12,d:2},{i:14,d:4}] },
    { name: "Pluck Seq",         notes: [{i:0,d:0},{i:3,d:2},{i:6,d:4},{i:8,d:2},{i:11,d:0,o:1},{i:14,d:4}] },
    { name: "16th Arp",          notes: [{i:0,d:0},{i:1,d:2},{i:2,d:4},{i:3,d:0,o:1},{i:4,d:0},{i:5,d:2},{i:6,d:4},{i:7,d:0,o:1},{i:8,d:0},{i:9,d:2},{i:10,d:4},{i:11,d:0,o:1},{i:12,d:0},{i:13,d:2},{i:14,d:4},{i:15,d:0,o:1}] },
    { name: "Call & Response",   notes: [{i:0,d:0},{i:2,d:2},{i:4,d:4},{i:8,d:4},{i:10,d:2},{i:12,d:0}] },
    { name: "Minor Motif",       notes: [{i:0,d:0},{i:2,d:2},{i:4,d:1},{i:6,d:0},{i:10,d:4},{i:12,d:2}] },
    { name: "Octave Stabs",      notes: [{i:0,d:0},{i:4,d:0,o:1},{i:8,d:4},{i:12,d:0,o:1}] },
  ]},
];

// Expand a preset into a full step array (sound-agnostic — active/velocity/pitch).
function partToSteps(preset) {
  if (preset.str) {
    return [...preset.str].map(c =>
      c === "X" ? { active: true, velocity: 120 } :
      c === "x" ? { active: true, velocity: 100 } :
      c === "o" ? { active: true, velocity: 60 } :
                  { active: false });
  }
  const len = preset.len || 16;
  const scale = currentScale();
  const out = Array.from({ length: len }, () => ({ active: false }));
  for (const n of (preset.notes || []))
    if (n.i >= 0 && n.i < len)   // d = scale degree, o = octave offset -> in-key semitones
      out[n.i] = { active: true, velocity: n.v || 100, pitch_offset: degreeNote(n.d || 0, n.o || 0, scale) };
  return out;
}

let partsTrackId = null;
function openParts(trackId, trackName) {
  partsTrackId = trackId;
  partsTargetEl.innerHTML = `load onto <b>${trackName}</b> · pattern ${patName(project.current_pattern || 0)}`;
  partsSearch.value = "";
  partsModal.classList.add("open");
  renderParts("");
  partsSearch.focus();
}
function closeParts() { partsModal.classList.remove("open"); partsTrackId = null; }

function renderParts(filter) {
  const q = filter.trim().toLowerCase();
  partsTree.innerHTML = "";
  for (const group of FACTORY_PARTS) {
    const matching = group.parts.filter(p => !q || p.name.toLowerCase().includes(q) || group.cat.toLowerCase().includes(q));
    if (!matching.length) continue;
    const d = document.createElement("details");
    d.className = "lib-cat";
    d.open = !!q;
    const s = document.createElement("summary");
    s.innerHTML = `${group.cat}<span class="count">${matching.length}</span>`;
    d.appendChild(s);
    for (const p of matching) {
      const row = document.createElement("div");
      row.className = "lib-sample";
      row.textContent = p.name;
      row.onclick = () => loadPart(p);
      d.appendChild(row);
    }
    partsTree.appendChild(d);
  }
  if (!partsTree.children.length) partsTree.innerHTML = '<div class="lib-empty">no matches</div>';
}

// Load a part onto the target track's column in the current pattern, then refresh.
async function loadPart(preset) {
  if (!partsTrackId) return;
  try { await PUT(`/tracks/${partsTrackId}/steps`, { steps: partToSteps(preset) }); }
  catch { setStatus("part load failed", false); return; }
  closeParts();
  try { project = await GET("/project"); } catch { return; }
  renderTracks();
  setStatus(`loaded "${preset.name}"`, true);
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
  ensureTrackColors();
  renderTracks();
  renderPatternSelect();
  renderPatternMeta();
  wireInspector();
  syncKeySelectors();   // reflect the project key (selectors live in the inspector)
  patternSelectEl.addEventListener("change", () => selectPattern(parseInt(patternSelectEl.value)));
  patternLenEl.addEventListener("change", () => setPatternLength(patternLenEl.value));

  // Initial transport status (live updates after this arrive via the event).
  try { onStatus(await GET("/sequencer/status")); } catch { /* ignore */ }

  // Transport: play/stop toggles the internal transport; the BPM readout is a
  // scroll wheel (±1 BPM/notch, 20..300). Both no-op against a playing host.
  playBtn.addEventListener("click", () => PUT("/transport/playing", { playing: !_playing }));
  bpmEl.addEventListener("wheel", (e) => {
    e.preventDefault();
    uiBpm = Math.max(20, Math.min(300, (uiBpm || 120) + (e.deltaY < 0 ? 1 : -1)));
    bpmEl.textContent = uiBpm.toFixed(1);
    _bpmWheelAt = Date.now();
    clearTimeout(_bpmPutTimer);
    _bpmPutTimer = setTimeout(() => PUT("/transport/bpm", { bpm: uiBpm }), 80);
  }, { passive: false });

  // Swing is now a rotary dial (matches the channel-strip aesthetic); the % shows
  // in the cap, live while dragging. onChange PUTs the swing param.
  swingKnob = makeKnob({
    min: 0, max: 1, value: project.swing || 0, label: "Swing", def: 0, valueInCap: true,
    format: v => Math.round(v * 100) + "%",
    tip: "<b>Swing</b> — shuffle feel; delays the off-beat 16ths.",
    onChange: v => PUT("/project/swing", { swing: v }),
  });
  swingHostEl.appendChild(swingKnob.el);

  // Master volume fader (0–200% of unity; param range 0..2, 1 = unity).
  const mv0 = Math.round((project.master_vol ?? 1) * 100);
  masterVolEl.value = mv0;
  masterPctEl.textContent = mv0 + "%";
  masterVolEl.addEventListener("input", () => { masterPctEl.textContent = masterVolEl.value + "%"; });
  masterVolEl.addEventListener("change", () => PUT("/master/volume", { volume: parseInt(masterVolEl.value) / 100 }));

  const songMode = getToggleState("songModeToggle");
  const reflect = () => { songEl.checked = songMode.getValue(); songBtn.classList.toggle("engaged", songMode.getValue()); };
  songMode.valueChangedEvent.addListener(reflect);
  reflect();
  songEl.addEventListener("change", () => songMode.setValue(songEl.checked));

  // Song Mode editor (Digitakt-style arrangement).
  songBtn.addEventListener("click", openSongEditor);
  songCloseEl.addEventListener("click", closeSongEditor);
  songSelect.addEventListener("change", () => selectSong(parseInt(songSelect.value)));
  songNewBtn.addEventListener("click", newSong);
  songNameEl.addEventListener("change", renameSong);
  songEndEl.addEventListener("change", setSongEnd);
  songAddRowEl.addEventListener("click", addSongRow);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && songScreen.classList.contains("open")) closeSongEditor(); });

  // Library browser controls.
  libSearch.addEventListener("input", () => renderLibrary(libSearch.value));
  libCloseEl.addEventListener("click", closeLibrary);
  libModal.addEventListener("click", (e) => { if (e.target === libModal) closeLibrary(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeLibrary(); });

  // Pattern-parts browser controls.
  partsSearch.addEventListener("input", () => renderParts(partsSearch.value));
  partsCloseEl.addEventListener("click", closeParts);
  partsModal.addEventListener("click", (e) => { if (e.target === partsModal) closeParts(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeParts(); });

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

  // LFO panel: shape/dest/sync controls + the rate/depth dials.
  lfoShapeEl.addEventListener("change", () => sendLfo({ shape: lfoShapeEl.value }));
  lfoDestEl.addEventListener("change", () => sendLfo({ destination: lfoDestEl.value }));
  lfoSyncEl.addEventListener("change", () => sendLfo({ sync: lfoSyncEl.checked }));
  lfoRateKnob = makeKnob({ min: 0, max: 1, value: 0.435, label: "Rate", def: 0.435, color: "v2", valueInCap: true,
    format: v => fmtHz(sliderToRate(v * 100)), tip: "<b>LFO Rate</b> — modulation speed (Hz).",
    onChange: v => sendLfo({ rate: sliderToRate(v * 100) }) });
  lfoDepthKnob = makeKnob({ min: 0, max: 1, value: 0, label: "Depth", def: 0, color: "v2", valueInCap: true,
    format: v => Math.round(v * 100) + "%", tip: "<b>LFO Depth</b> — how strongly the LFO modulates its destination.",
    onChange: v => sendLfo({ depth: v }) });
  document.getElementById("lfo-knobs").append(lfoRateKnob.el, lfoDepthKnob.el);
  attachTip(lfoShapeEl, "<b>LFO Shape</b> — modulation waveform (sine, saw, square, S&amp;H…).");
  attachTip(lfoDestEl, "<b>LFO Destination</b> — what the LFO modulates (cutoff / volume / pitch).");
  attachTip(lfoSyncEl, "<b>Sync</b> — restart the LFO each note; off = free-running.");

  // Tooltips on the header transport / actions (themed, replacing native titles).
  attachTip(playBtn, "<b>Play / Stop</b> — start or stop the internal transport (a playing host overrides this).");
  attachTip(bpmEl, "<b>Tempo</b> — scroll to change BPM (the host tempo wins while it plays).");
  attachTip(masterVolEl, "<b>Master Volume</b> — overall output level (100% = unity gain).");
  attachTip(patternSelectEl, "<b>Pattern</b> — which pattern slot the grid edits and plays.");
  attachTip(patternLenEl, "<b>Length</b> — pattern length in steps (1–128). Shrinking discards steps past the new end.");
  attachTip(songBtn, "<b>Song</b> — open the arrangement editor (Digitakt-style row chain).");
  attachTip(projectsBtn, "<b>Projects</b> — save / load projects in ~/SILA/projects.");
  attachTip(exportBtn, "<b>Export</b> — transcode all project samples to 48k/16-bit mono WAVs for the Elektron Digitakt.");
  // Inspector's discrete selects.
  attachTip($("i-trig"), "<b>Trig condition</b> — when this step is allowed to fire.");
  attachTip($("i-fmode"), "<b>Filter mode</b> — low-pass / high-pass / band-pass.");
  attachTip($("i-length"), "<b>Length</b> — note length / gate (∞ = one-shot).");

  setStatus(`connected — ${project.tracks.length} tracks · click a step, right-click to inspect`, true);
}

boot().catch(e => setStatus("bridge error: " + (e && e.message ? e.message : e)));
