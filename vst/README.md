# vst/ — SILA as a plugin (scaffold)

This directory is a **decision-ready scaffold** for turning SILA into a
VST3/AU/Standalone plugin. No audio has been ported yet.

- **[DESIGN.md](DESIGN.md)** — the plan: what carries over (the web UI), what
  gets rewritten in C++ (the engine), the host-transport timing model, build
  instructions, and a phased roadmap. **Read this first.**
- `CMakeLists.txt` — JUCE 8 plugin project (fetches JUCE, bundles `../sila/ui`).
- `src/` — `PluginProcessor` (host-synced audio) + `PluginEditor` (WebView host)
  + `engine/` headers mapped 1:1 from `../sila/engine/*.py`.
- `webui/bridge.js` — the only change the existing UI needs: swaps `fetch("/api")`
  for the native JUCE bridge.

It is **not buildable in this repo's CI/dev container** — it needs a C++20
toolchain, JUCE, and a DAW to test. Structure compiles; engine bodies are TODO
stubs that point at their Python spec.

Build (on a machine with a toolchain):

```
cmake -B vst/build -S vst -DCMAKE_BUILD_TYPE=Release
cmake --build vst/build
```
