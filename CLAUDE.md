# CLAUDE.md

## What this repo is

**SILA** — a Digitakt-inspired sampler/step-sequencer, built as a native JUCE 8
**VST3 / AU / Standalone** plugin under `vst/`. **That is the product.**

- `vst/` — the active codebase. `vst/DESIGN.md` explains the architecture
  (host-synced timing, the RCU state seam, the WebView bridge). Read it before
  touching engine code.
- `sila/` — the **abandoned** Python predecessor (FastAPI + browser UI). It is
  kept only as the porting spec/reference. Do not review, fix, or extend it
  unless explicitly asked.
- `projects/`, `run-sila.bat`, `requirements.txt` — legacy Python-app artifacts.

## Build (Windows dev box)

`cmake` is **not on PATH**. Use the VS Build Tools copy (generator "Visual Studio 18 2026"):

```powershell
$cmake = "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
& $cmake --build C:\Users\foois\vdigitakt\vst\build --target SILA_Standalone --config Release
```

- Targets: `SILA_VST3`, `SILA_Standalone`, `SILA_Tests`.
- The VST3 auto-installs to `%USERPROFILE%\VST3` (`COPY_PLUGIN_AFTER_BUILD`).
  Reaper scans it — **reload the plugin instance** after a rebuild.
- Standalone exe: `vst\build\SILA_artefacts\<Config>\Standalone\SILA.exe`.
- `webui/` files are bundled via `juce_add_binary_data` — **any HTML/JS edit
  requires a rebuild** (BinaryData regen) to show up.
- WebView2 SDK comes from the NuGet package cache under
  `%USERPROFILE%\AppData\Local\PackageManagement\NuGet\Packages` (JUCE globs it;
  not auto-downloaded).

### Build gotchas

- **Kill any running `SILA.exe` / DAW holding the .vst3 before relinking** —
  file lock → `LNK1168`. `Get-Process SILA | Stop-Process -Force`.
- `LNK1163` ("invalid selection for COMDAT section") = stale incremental-link
  state, not a code bug. Delete the affected `.obj` + `*.ilk` in `vst/build`
  and relink.
- The Standalone can block a few seconds opening the audio device *before* its
  window appears. Not a crash.
- `VST3_AUTO_MANIFEST FALSE` is deliberate: Windows Smart App Control blocks
  `juce_vst3_helper.exe` from loading the unsigned freshly-built .vst3. The
  manifest is only an enumeration cache; the plugin still loads in a DAW.

## Tests

```powershell
& $cmake --build vst\build --target SILA_Tests --config Debug
vst\build\SILA_Tests_artefacts\Debug\SILA_Tests.exe
```

Sanitizer / `safeChild` path-traversal backstop. Run after touching
`engine/Library.*` or any filename/path sanitization.

## Architecture in one screen

- `vst/src/PluginProcessor.{h,cpp}` — audio thread: host-synced transport,
  sample-accurate trig scheduling, voice mixer, master stage, APVTS params.
- `vst/src/PluginEditor.{h,cpp}` — WebView editor + the native bridge: the UI
  makes REST-shaped calls (`[method, path, body]`) into `handleBackendCall`,
  which routes them on the message thread.
- `vst/src/engine/` — `Project.h` (immutable data model), `Sequencer` (purely
  structural: patterns, trig conditions, song mode), `Sampler` (resample-on-load),
  `VoiceMixer` (voices, TPT-SVF filter, LFO, gate), `ProjectJson` (single
  source of truth for serialization), `MidiExport` (song → SMF),
  `Library` (sample importer/manager), `DigitaktExport`, `Resample.h`.
- `vst/webui/` — vanilla HTML/JS (no framework). `bridge.js` holds all UI logic.

### Invariants you must not break

1. **RCU snapshots.** `liveProject` is `std::atomic<shared_ptr<const Project>>`.
   Audio thread does one acquire-load per block and never mutates. All edits go
   through `editProject` (copy → mutate → exchange) on the **message thread**;
   old snapshots go to retire lists reaped by the editor timer so the audio
   thread never frees memory. The sampler bank set follows the same pattern.
2. **`Voice::keepAlive`** pins the voice's sampler (`shared_ptr`) so a bank swap
   can't free a buffer a ringing voice still reads (this fixed a real
   use-after-free crash). Every spawned voice must set it.
3. **Audio thread is allocation-free in steady state.** No heap allocation, no
   string-keyed APVTS lookups (`getRawParameterValue` is cached into member
   pointers in the constructor), no locks.
4. **Mix params live in the APVTS slot bank** (`t{s}_vol/pan/cutoff/res/fmode`,
   8 slots, host-automatable; tracks map to slots by index). Per-pattern values
   are snapshots in `kits[]`, captured/recalled on pattern switch and save.
5. **Serialization**: `kProjectSchemaVersion` (ProjectJson.h). Never repurpose a
   key; bump the version and write a load-time migration for old projects.
6. `getBusBuffer` returns a referencing view — **never copy-assign an
   `AudioBuffer`** (deep-copies).

### Adding a feature (the established route)

Model in `Project.h` → serialize in `ProjectJson` (schema bump + migration) →
engine logic (audio-thread-safe, see invariants) → bridge route in
`PluginEditor.cpp` → UI in `webui/index.html` + `bridge.js` → rebuild →
verify Standalone launches and soaks without crash → note what still needs
human/audible verification.

## Hard product decisions (user-locked — do not relitigate)

- **No network code in the plugin. Ever.** OSC/MCP/socket/IPC listeners were
  explicitly killed (2026-06-15). If AI generation is built, it goes directly
  in the WebUI via JavaScript `fetch` — zero user configuration.
- **Host is authoritative when hosted**: host tempo/transport win; the internal
  transport + BPM wheel govern Standalone only. A VST cannot set host tempo.
- **DAW integration = multi-out buses + MIDI file export**, not live MIDI-out.
  Main bus carries the full mix (master applied); per-lane aux buses are
  pre-master stems.
- Don't scaffold future phases (AI composition etc.) until asked.
- **No magic auto-playing demo.** A fresh instance opens to a clean default
  project (`buildDefaultProject`: factory tracks + clean kit, empty patterns,
  no song). The "Factory Showcase" example is installed as a real, loadable,
  deletable project file on first run (`installFactoryProject`, marker-guarded
  so a user deletion sticks) — it is an EXAMPLE in the PROJECTS list, not
  baked-in state. `makeShowcaseProject` builds its structure.

## Status & open items (as of 2026-07-06)

Feature-complete v1.0: sequencer (patterns/pages/song mode/p-locks/retrig),
per-pattern kits, sampler + library importer, DSP (filter/LFO/gate/pitch),
multi-out, MIDI + Digitakt export, live MIDI note input (channel N → lane N−1,
C3 = programmed pitch — mirrors the export map), factory pack, tests, hardened
webui.

Open items (public-release audit, 2026-07-06 — ordered by priority):

- **Installer + code signing** — the real blockers before public distribution.
  The VST3 currently installs to `%USERPROFILE%\VST3`, which most DAWs do NOT
  scan; a real installer must target `C:\Program Files\Common Files\VST3`, and
  unsigned binaries hit SmartScreen/Smart App Control warnings for downloaders.
- **WebView2 runtime guard** — editor hard-requires WebView2 with no fallback;
  detect a missing runtime and show a message; installer should bundle the
  Evergreen bootstrapper.
- **Reaper multi-out routing recipe** — buses are built but never verified in a
  DAW; a routing walkthrough doc is still owed. Run **pluginval** + test in
  Reaper/Ableton/FL/Bitwig before release.
- Library rename/move/delete **orphans** a loaded project's `SampleRef`
  (references are not rewritten; reload goes silent).
- Demo-kit-only sessions reload silent (synth buffers aren't reconstructable);
  ASIO for the Standalone (needs the Steinberg ASIO SDK); AU validation (needs
  macOS). Song mode does not recall per-pattern mix snapshots as it advances
  (user-accepted APVTS limitation).

Many features were verified only as "builds clean + Standalone soaks"; when a
commit message or note says **"pending human verify"**, treat the feature as
unproven until someone has actually heard/clicked it.
