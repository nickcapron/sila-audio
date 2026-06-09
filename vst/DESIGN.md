# SILA as a VST3/AU plugin — design & port plan

> Status: **scaffold / decision-ready**. No audio code has been ported yet.
> This document + the stubs in `vst/` exist so we can judge the effort before
> committing to the full build.

## TL;DR

SILA today is a standalone Python app (FastAPI + sounddevice + browser UI).
A plugin must run *inside the DAW's process and audio thread*, so the audio
engine has to be rebuilt natively in C++ (JUCE). The **UI is reusable**: the
existing `sila/ui` HTML/JS/CSS runs in a JUCE WebView, with REST calls swapped
for a native bridge. This is a **port of the engine**, not a wrapper around the
Python app.

```
            STANDALONE (today)                         PLUGIN (target)
  ┌───────────────────────────────┐        ┌────────────────────────────────────┐
  │ browser UI  ──REST──► FastAPI  │        │  DAW host                          │
  │ PlaybackClock (wall clock)     │   ==>  │   └─ SILA.vst3 (JUCE/C++)          │
  │ sounddevice ► system audio     │        │       UI (WebView) ─bridge─► engine│
  │ ProjectStore ► ~/SILA          │        │       host transport ► sequencer   │
  └───────────────────────────────┘        │       processBlock ► host mixer    │
                                            │       getStateInformation ► preset │
                                            └────────────────────────────────────┘
```

## What carries over, what changes, what's dropped

| Area | Today (Python) | Plugin (C++/JUCE) | Effort |
|------|----------------|-------------------|--------|
| **UI** | `sila/ui/*.html/js/css` | **Reused** in `juce::WebBrowserComponent`; `api()` → native bridge | Low (shim only) |
| Sequencer | `engine/sequencer.py` | `engine/Sequencer.{h,cpp}` — pure logic, near 1:1 port | Low–Med |
| Timing/clock | `engine/clock.py` (sleep loop, swing, micro-timing, song mode, LFO phase) | Folded into `processBlock`, driven by **host transport** (PPQ/BPM) | **High** (model change) |
| Sampler | `engine/sampler.py` | `engine/Sampler.{h,cpp}` (`juce::AudioFormatManager`) | Med |
| Voice mixing | `engine/audio.py` (`_Voice`, mix, pan, delay, soft-clip, small-speaker) | `engine/VoiceMixer.{h,cpp}` in `processBlock` | Med |
| FX / LFO | `engine/fx.py`, `engine/lfo.py` | `engine/Fx.{h,cpp}` (`juce::dsp::IIR` biquad) | Low–Med |
| Data model | `models/project.py` (pydantic) | C++ structs + JSON (`juce::var`) | Med |
| API layer | `api/*.py` (FastAPI routes) | Native bridge functions (1 per endpoint) | Med |
| Persistence | `storage/project_store.py` → `~/SILA` | Plugin state (`get/setStateInformation`) + optional disk project browser | Med |
| Audio device | `sounddevice`/PortAudio + device watcher | **Dropped** — host owns the device | — (delete) |
| Server plumbing | `main.py`, `security.py` (token), heartbeat watchdog, `--open` | **Dropped** — no server in a plugin | — (delete) |

## The hard part: host-synced timing

The current `PlaybackClock` is a wall-clock `time.sleep` loop that ticks the
sequencer every 16th note. A plugin is **pull-based**: the host calls
`processBlock(buffer, midi)` every N samples, and we must figure out which
16th-note boundaries fall inside that block and render them sample-accurately.

Per block:
1. Read `AudioPlayHead::PositionInfo` → `bpm`, `ppqPosition`, `isPlaying`,
   `timeSig`.
2. Convert PPQ → 16th-note index. Find each 16th boundary whose sample offset
   lands in `[0, numSamples)`.
3. For each boundary: `Sequencer::tick()` → `TrigEvent`s → spawn voices with a
   **sample offset** (this replaces `delay_frames`); swing and micro-timing
   become adjustments to that offset.
4. Mix all active voices into the block (port of `audio.py::_callback`), then
   the master stage (soft-clip / small-speaker monitor as a bypassable param).

Song-mode pattern swaps happen on the bar boundary exactly as in
`clock.py`, but keyed off PPQ instead of a tick counter. Tempo/transport come
"for free" from the host — no BPM field, no internal start time.

## UI reuse via the WebView bridge

JUCE 8's `WebBrowserComponent` supports a native↔JS bridge
(`withNativeFunction`, `withNativeIntegrationEnabled`) and a resource provider
to serve the bundled UI from `BinaryData`. The only UI change is swapping the
transport: today every call goes through `api()` in `app.js`:

```js
const res = await fetch("/api" + path, opts);   // today
```

In the plugin, `api()` becomes a thin shim over the bridge (see
`vst/webui/bridge.js`):

```js
const res = await window.__JUCE__.backend.call(method, path, body); // plugin
```

Everything downstream — the aurora theme, the rotary knobs, the sequencer
grid, song mode — runs unchanged. Push updates (playhead position, song slot)
flow the other way via `emitEvent`/`postMessage` instead of the 2 s status poll.

## Parameters & automation

- **Automatable params** (exposed via `AudioProcessorValueTreeState`): master
  volume, swing, small-speaker monitor, maybe per-track volume/pan/cutoff.
- **Structured state** (patterns, steps, samples, song chain) is too large for
  host params — stored as a JSON blob in the plugin state tree and saved/
  restored through `get/setStateInformation`. Samples are referenced by path
  and/or embedded in the preset.

## Build

CMake + JUCE 8 via `FetchContent`. Targets VST3 + AU + Standalone (the
Standalone target is handy for testing without a DAW). `juce_add_binary_data`
bundles `sila/ui` into the plugin. Requires a C++20 toolchain and the VST3 SDK
(JUCE bundles it). See `vst/CMakeLists.txt`.

```
cmake -B vst/build -S vst -DCMAKE_BUILD_TYPE=Release
cmake --build vst/build
```

> Note: this can't be built/tested in the current environment — it needs a C++
> toolchain, JUCE, and a DAW to load the result. The scaffold compiles in
> structure; the engine bodies are TODO stubs that reference their Python spec.

## Phased roadmap

1. **Scaffold:** CMake, plugin stubs, WebView host, bridge outline, engine
   headers. ✅ *done*
2. **Standalone audio:** port `Sampler` + `VoiceMixer`, get one track triggering
   on host transport in the Standalone target. ✅ *done* — see notes below.
3. **Sequencer + timing:** port `Sequencer`, host-synced scheduling, swing,
   micro-timing, song mode.
4. **UI bridge:** load `sila/ui` in the WebView, implement the bridge functions
   mapping the existing REST endpoints, push playhead events.
5. **FX/LFO + state:** port filter/LFO, implement preset save/load.
6. **Polish:** parameters/automation, AU validation, installer.

## Phase 2 notes (implemented)

- `engine/Sampler.{h,cpp}` — velocity layers + round-robin + start/end slicing,
  mono downmix on load (`AudioFormatManager`). No sample-rate conversion yet.
- `engine/VoiceMixer.{h,cpp}` — voice mixing with pan + sample-offset start
  (`renderInto`), and the master stage (`applyMaster`): hard-clip by default or
  the ported small-speaker monitor (HPF + bass harmonics + soft-limit).
- `PluginProcessor` — `processBlock` resolves the transport (host when playing),
  finds the 16th-note boundaries in the block, and triggers a hard-coded
  4-on-the-floor kick sample-accurately via `scheduleTriggers`.
- **Two pragmatic choices to make it audible now, without the UI or a DAW:**
  1. the kick is **synthesized in code** (`makeKick`) so no sample file is
     needed yet;
  2. the **Standalone wrapper free-runs** an internal clock at 120 BPM (a DAW's
     transport still governs in plugin form). Build the `Standalone` target and
     you should hear a steady 4-on-the-floor.
- Not yet: real patterns/tracks (Phase 3), file loading from the UI (Phase 4).

## Risks / open questions

- **Sample-accurate swing/micro-timing** across block boundaries is the main
  engineering risk (notes scheduled near a block edge).
- **WebView consistency** across hosts/OSes (WKWebView on macOS, WebView2 on
  Windows) — generally fine in JUCE 8 but worth an early spike.
- **Sample storage** in presets (embed vs. reference) — affects portability.
- Decide instrument (MIDI-triggered) vs. internal-sequencer-only, or both.
