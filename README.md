# SILA

A fast, sample-based step sequencer and groovebox inspired by the Elektron
Digitakt. The goal is simple: **make a beat faster than with any other tool.**

SILA exists in two forms in this repo:

- **`vst/` — the native plugin (active, feature-complete).** A JUCE 8
  **VST3 / AU / Standalone** instrument written in C++, with the UI running in
  an embedded WebView. This is the primary effort and what you'll want to build.
- **`sila/` — the original Python app (reference).** A local FastAPI server +
  HTML/JS grid UI that the plugin's engine was ported from. Still runnable; kept
  as the engine spec and for the Digitakt export pipeline.

---

## The plugin (`vst/`)

A self-contained instrument plugin — no external server, no scripts, no
configuration. Load it in your DAW (or run the Standalone) and go.

### Features

**Sampler**
- Velocity layers + round-robin per track
- Per-layer start/end trimming over a waveform view
- Sample-rate conversion on load (windowed-sinc), so off-rate files play in tune
- Per-step varispeed pitch (cubic-Hermite interpolation)

**Sequencer** (host-synced, sample-accurate)
- Polyrhythmic step lengths, derived from the host's PPQ (loop/seek-safe)
- Swing + positive micro-timing
- Trig conditions (always, 1:2, 1:4, fill, not-fill) and per-step probability
- Per-step **parameter locks** (pitch, cutoff, resonance, filter mode, LFO depth/rate, …)
- Per-step **note length / gate** and **retrig / ratchet** (1–8 hits with a velocity fade)

**Per-voice DSP**
- TPT state-variable filter — low-pass / high-pass / band-pass, with cutoff + resonance
- Per-voice LFO (sine / triangle / square / saw / random S&H) routable to
  cutoff, volume or pitch, trig-synced or free-running

**Mixer & automation**
- Per-track volume + constant-power pan, master volume, small-speaker monitor
- Per-track volume / pan / cutoff / resonance / filter-mode, swing and master
  exposed as host-automatable parameters

**Arrangement**
- Pattern bank: 16 slots, up to 128 steps each, paged in 16s with a per-pattern master length
- **Song mode** — a Digitakt-style row chain (label / pattern / repeat / length /
  tempo override / per-track mutes, loop or stop at the end)

**Musical tools**
- Global key + scale with a mini note-keyboard (chromatic, in-scale notes highlighted)
- Scale-aware melodic factory presets
- ~80 factory **pattern parts** (per-track preset sequences) across 9 categories

**Workflow**
- Add / remove / rename tracks, per-track colour
- Vanilla HTML/JS UI in a JUCE WebView: rotary dials, hover tooltips, beat-grouped grid
- Project save/load (`~/SILA/projects`) **and** full DAW state persistence
- Digitakt export — transcode all samples to 48 kHz / 16-bit / mono WAV

See **[`vst/DESIGN.md`](vst/DESIGN.md)** for the architecture (host-transport
timing model, the lock-free RCU state seam, the WebView bridge) and the phased
roadmap.

### Build

Requires a C++20 toolchain and CMake. JUCE 8 and the VST3 SDK are fetched
automatically. On Windows you also need the WebView2 SDK (NuGet) and the
WebView2 runtime (ships with modern Windows).

```
cmake -B vst/build -S vst -DCMAKE_BUILD_TYPE=Release
cmake --build vst/build
```

The build installs the VST3 to a user-writable folder (`%USERPROFILE%/VST3` on
Windows). The Standalone target lets you test without a DAW.

---

## The Python app (`sila/`) — original / reference

A local FastAPI server with the grid UI in the browser. The plugin's C++ engine
was ported from here; it remains the spec and still runs.

```
pip install -r requirements.txt
python -m sila.main
```

Open `http://127.0.0.1:8765`. The session token is printed to stdout on startup
(the UI reads it from the URL hash or localStorage).

**Layout**

```
sila/
  main.py              Entry point — binds to 127.0.0.1:8765
  security.py          Security primitives (token, safe_path, sanitize)
  engine/              sequencer · sampler · lfo · fx · audio · clock
  models/              ProjectModel, TrackModel, Step, SampleLayer, FX, LFO
  export/digitakt.py   Digitakt-ready WAV export
  api/routes.py        FastAPI routes (all token-gated)
  ui/                  index.html + app.js (grid-first UI)
  storage/             JSON load/save + undo/redo
  tests/
```

**Security model**

- Binds to `127.0.0.1` only.
- Every API route requires an `X-SILA-Token` header (session token, generated at startup).
- All file paths go through `safe_path()`; all notes fields through `sanitize_notes()`.
- Project files are backed up before every write.

Projects live at `~/SILA/projects/<name>/project.json`, samples alongside in
`samples/`.
