# vst/ — SILA as a plugin

A JUCE 8 **VST3 / AU / Standalone** instrument: a native C++ port of the
`../sila` Python engine, with the UI running in an embedded WebView. This is the
active, feature-complete effort (see the repo [README](../README.md) for the
full feature list).

- **[DESIGN.md](DESIGN.md)** — the architecture and phased roadmap: the
  host-transport timing model, the lock-free RCU state seam, the WebView bridge,
  and what carried over vs. got rewritten. **Read this first.**
- `CMakeLists.txt` — JUCE 8 plugin project (fetches JUCE + the VST3 SDK, bundles `webui/`).
- `src/PluginProcessor.{h,cpp}` — host-synced audio: transport, sample-accurate
  scheduling, the voice mixer, master stage, and the APVTS parameter bank.
- `src/PluginEditor.{h,cpp}` — the WebView editor and the native bridge that maps
  the UI's REST-shaped calls onto the engine.
- `src/engine/` — `Sampler`, `VoiceMixer`, `Sequencer`, `ProjectJson`,
  `MidiExport` (song → SMF), `Library` (sample importer). Ported from / inspired
  by `../sila/engine/*.py`.
- `webui/` — the vanilla HTML/JS UI; `bridge.js` swaps `fetch("/api")` for the
  native JUCE bridge (`window.__JUCE__`).

## Build

Requires a C++20 toolchain and CMake. JUCE 8 and the VST3 SDK are fetched
automatically. On Windows you also need the WebView2 SDK (NuGet) + runtime.

```
cmake -B vst/build -S vst -DCMAKE_BUILD_TYPE=Release
cmake --build vst/build
```

The VST3 auto-installs to a user-writable folder (`%USERPROFILE%/VST3` on
Windows; reload the instance in your DAW after a rebuild). The Standalone target
runs without a DAW.

### Windows build notes

- CMake/MSVC toolchain via Visual Studio Build Tools.
- Kill any running `SILA.exe` before relinking — a live instance holds a file
  lock and the link fails with `LNK1168`.
- The Standalone may block for a few seconds opening the audio device before its
  window appears (device init runs in the window constructor) — not a crash.
