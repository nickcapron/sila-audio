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
function makeKnob({ min, max, value, label, def, color, format, onInput, onChange, valueInCap, tip }) {
  const wrap = document.createElement("div");
  wrap.className = "knob-wrap";
  const knob = document.createElement("div");
  knob.className = "knob" + (color === "v2" ? " v2" : "");
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
  const fmt = format || (v => Math.round(v));
  let val = value, dragging = false;
  function paint() {
    const norm = (val - min) / (max - min);
    const deg = norm * 270;
    ring.style.background = `conic-gradient(from 225deg, ${arc} 0deg ${deg}deg, #1c2836 ${deg}deg 270deg, transparent 270deg 360deg)`;
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
    attachTip(mute, "<b>Mute</b> — silence this track.");
    const solo = document.createElement("button");
    solo.className = "solo" + (track.solo ? " on" : "");
    solo.textContent = "S";
    solo.onclick = () => toggleSolo(track.id);
    attachTip(solo, "<b>Solo</b> — hear only soloed tracks.");
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
    slot.onclick = (e) => { e.stopPropagation(); openLibrary(track.id, track.name); };
    attachTip(slot, (track.samples && track.samples[0])
      ? "<b>Sample</b> — " + track.samples[0].path.split("/").pop() + " · click to change"
      : "<b>Sample</b> — click to load one from the library.");

    const mix = document.createElement("div");
    mix.className = "track-mix";
    const knobRow = document.createElement("div");
    knobRow.className = "knob-row";
    const pct = v => Math.round(v * 100);
    const kVol = makeKnob({ min: 0, max: 1, value: track.volume ?? 1, label: "Vol", def: 1, format: pct,
      tip: "<b>Volume</b> — output level of this track.",
      onInput: v => { track.volume = v; PUT(`/tracks/${track.id}/volume`, { volume: v }); } });
    const kCut = makeKnob({ min: 0, max: 1, value: track.cutoff ?? 1, label: "Cut", def: 1, format: pct,
      tip: "<b>Cutoff</b> — filter frequency. Lower = darker/muffled.",
      onInput: v => { track.cutoff = v; PUT(`/tracks/${track.id}/cutoff`, { cutoff: v }); } });
    const kPan = makeKnob({ min: -1, max: 1, value: track.pan ?? 0, label: "Pan", def: 0, color: "v2", format: pct,
      tip: "<b>Pan</b> — left / right position in the stereo field.",
      onInput: v => { track.pan = v; PUT(`/tracks/${track.id}/pan`, { pan: v }); } });
    const kRes = makeKnob({ min: 0, max: 1, value: track.resonance ?? 0, label: "Res", def: 0, color: "v2", format: pct,
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
  { id:"pitch", label:"Pitch",   min:-24, max:24,  def:0,   fmt:v=>fmtSigned(Math.round(v)),
    tip:"<b>Pitch</b> — transpose this step in semitones.",
    read:s=>s.pitch_offset ?? 0,                              write:(s,v)=>{ s.pitch_offset = Math.round(v); } },
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
      format: p.fmt, valueInCap: true, tip: p.tip,
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

  for (const p of INSP_KNOBS) inspKnobs[p.id].set(p.read(step, track));
  $("i-trig").value   = step.trig_condition || "always";
  $("i-fmode").value  = pl.filter_mode ?? track.filter_mode ?? "lowpass";
  $("i-length").value = String(step.length ?? 0);   // 0 = ∞ one-shot (default)

  showTrimmer(trackId);   // trimmer follows the selected track's sample
  showLfo(trackId);       // LFO panel follows the selected track
}

const fmtSigned = (v) => (Number(v) > 0 ? "+" + v : String(v));

function wireInspector() {
  buildInspectorKnobs();   // the per-step knobs (commit on release via onChange)
  $("i-trig").addEventListener("change", () => { const s = curStep(); if (s) { s.trig_condition = $("i-trig").value; saveSelectedStep(); } });
  $("i-fmode").addEventListener("change", () => { const s = curStep(); if (s) { (s.p_locks = s.p_locks || {}).filter_mode = $("i-fmode").value; saveSelectedStep(); } });
  $("i-length").addEventListener("change", () => { const s = curStep(); if (s) { s.length = parseFloat($("i-length").value); saveSelectedStep(); } });
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

  // Tooltips on the header swing + the inspector's discrete selects.
  attachTip(swingEl, "<b>Swing</b> — shuffle feel; delays the off-beat 16ths.");
  attachTip($("i-trig"), "<b>Trig condition</b> — when this step is allowed to fire.");
  attachTip($("i-fmode"), "<b>Filter mode</b> — low-pass / high-pass / band-pass.");
  attachTip($("i-length"), "<b>Length</b> — note length / gate (∞ = one-shot).");

  setStatus(`connected — ${project.tracks.length} tracks · click a step, right-click to inspect`, true);
}

boot().catch(e => setStatus("bridge error: " + (e && e.message ? e.message : e)));
