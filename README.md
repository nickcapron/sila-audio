# SILA

**A free, Digitakt-style sampler and step sequencer for Windows — as a VST3
plugin and a Standalone app.** Eight sample lanes, 16 patterns of up to 128
steps, per-step parameter locks, trig conditions, retrig, per-pattern kits, a
song mode, and a sample library with an importer. The UI is built for one thing:
**getting a beat down fast.**

> **Download:** grab `SILA-v1.0.0-win64.zip` from the
> [Releases](../../releases) page. It contains the VST3, the Standalone, an
> installer script and a README. Source is in [`vst/`](vst/).

![SILA](vst/branding/sila-logo.svg)

---

## Install (2 minutes)

1. Unzip.
2. **Plugin:** double-click `install-vst3.cmd` (it copies `SILA.vst3` into
   `C:\Program Files\Common Files\VST3`, the folder every DAW scans, and asks
   for admin rights to do it). Rescan plugins in your DAW and add **SILA** as an
   instrument.
   **Standalone:** put `SILA.exe` anywhere and run it.
3. Windows shows *"Windows protected your PC"* the first time — this is a free,
   unsigned build. Click **More info → Run anyway**.

Needs the Microsoft Edge WebView2 Runtime, which nearly every Windows 10/11 PC
already has. If it is missing SILA says so and links to the download.

Your samples and projects live in `%USERPROFILE%\SILA\` (`library\`,
`projects\`). The bundled factory kit (Behringer RD-6 drums + Casio CZ-1 voices)
installs itself there on first run and never overwrites your files.

## First five minutes

- A new instance opens to an **empty project with 8 lanes and the factory kit
  loaded**. Click pads to program steps; it makes sound immediately.
- **PROJECTS → Factory Showcase** loads a complete example song. Turn on
  *Song Mode* and press play.
- **Right-click a pad** to inspect it: velocity, probability, trig condition,
  micro-timing, retrig, per-step filter / LFO / sample-slice locks.
- **Drag** across pads to paint a run, **wheel** over a pad for velocity,
  **hold FILL** (or `F`) to fire fill steps, **COPY / PASTE** to make a
  variation of a pattern.
- Press **`?`** inside SILA for the full keyboard and mouse cheat-sheet.
- Click a lane's sample slot to pick a sound; **LIBRARY → + Import** pulls in
  any folder of WAV/AIFF files and sorts it by type (kick, snare, hat, bass…).

## How it compares to a Digitakt

SILA is *inspired by* the Elektron Digitakt's workflow. It is not a clone and
not a replacement for the hardware; it's the parts of that workflow that make
sense inside a DAW, plus a few things the box doesn't do.

| | Digitakt (hardware) | SILA (plugin) |
|---|---|---|
| Tracks | 8 audio + 8 MIDI | 8 sample lanes |
| Pattern length | 64 steps (4 pages) | 128 steps (8 pages), master length per pattern |
| Patterns / banks | 128 (8 banks × 16) | 16 patterns per project |
| Per-pattern sounds | Sound per track per pattern (kit) | **Kit per pattern** — same lane, different sample/LFO per pattern |
| Parameter locks | Almost every parameter | Pitch, velocity, cutoff, resonance, filter mode, LFO depth/rate, sample start/end, length, micro-timing |
| Trig conditions | Large set (A:B, PRE, NEI, FILL, %…) | Always, 1:2, 1:4, Fill, Not Fill, plus per-step probability |
| Retrig / ratchet | Yes, with rate + velocity curve | Yes, ×2–×8 with a velocity swell/fade |
| Micro-timing | ± | Late only (the engine can't play before the grid) |
| Velocity layers / round-robin | No | **Yes** — multiple samples per lane by velocity range, round-robin groups |
| Filter | Multimode + base-width | TPT state-variable LP/HP/BP per voice |
| Amp envelope | Attack / hold / decay / release | Gate length + one-shot (no envelope stages) |
| LFO | 1 per track (2 on Digitakt II), many destinations | 1 per lane per pattern → cutoff / volume / pitch, synced or free |
| Effects | Overdrive, delay, reverb, compressor | **None built in** — use your DAW's effects via the per-lane outputs |
| Sampling / resampling | Yes | No (import files instead) |
| Song mode | Yes | Yes — label / pattern / repeat / length / tempo / per-lane mutes, loop or stop |
| Chromatic play | Yes | Key + scale note keyboard per step; live MIDI in (channel N = lane N) |
| Sequencing external gear | 8 MIDI tracks | No live MIDI out — **export the song as a .mid** instead |
| Outputs | Stereo + individual outs (II) | Main mix + **one stereo bus per lane** to the DAW |
| Undo | Yes | No — save often |
| Price | Hardware | Free |

The short version: SILA gets you the *step-sequencer-with-locks* feeling, the
kit-per-pattern arrangement trick, and a song mode, inside the DAW you already
have. Effects, sampling, and the knobs-under-your-hands part stay with the
hardware.

## In a DAW

- **Tempo and transport follow the DAW.** The BPM readout in SILA is display-only
  when hosted; the internal clock only runs in the Standalone.
- **Per-lane outputs.** SILA exposes a Main mix plus one stereo bus per lane.
  In Reaper: set the SILA track to 16 channels, then add tracks with receives
  from channel pairs 3/4 (lane 1), 5/6 (lane 2), … and mute the SILA track's
  master send if you don't want to hear lanes twice.
- **MIDI export** bounces the active song (or the current pattern) to a Standard
  MIDI File, one track per lane on its own channel. **Live MIDI in** mirrors that
  map: channel N triggers lane N, C3 is the lane's programmed pitch.
- **Everything is saved with the DAW project**, and you can also save/load
  named projects in SILA's PROJECTS panel.

## Known gaps (v1.0)

- No undo.
- Renaming, moving or deleting a sample in LIBRARY doesn't update projects that
  reference it; they show a *sample missing* badge on the lane — click it to
  pick a replacement.
- Windows only for now. The code builds for macOS (VST3 / AU) but that has not
  been tested.
- Song mode does not recall each pattern's mix snapshot as it advances (it uses
  the live mixer values).

## Building from source

```
cmake -B vst/build -S vst -DCMAKE_BUILD_TYPE=Release
cmake --build vst/build --config Release
```

Needs a C++20 toolchain, CMake ≥ 3.22, and on Windows the WebView2 SDK NuGet
package. JUCE 8 and the VST3 SDK are fetched automatically. See
[`vst/README.md`](vst/README.md) for the layout and Windows build notes,
[`vst/DESIGN.md`](vst/DESIGN.md) for the architecture (host-synced timing, the
lock-free state seam, the WebView bridge), and `vst/release/make-release.ps1`
to package a release zip.

## License

MIT — see [`LICENSE`](LICENSE). The factory samples were recorded by the
author from his own RD-6 and CZ-1 and are released under the same terms.
JUCE and the VST3 SDK are used under their own licenses.

## The Python app (`sila/`)

The original prototype: a local FastAPI server with the grid UI in a browser.
The plugin's C++ engine was ported from it. It is kept as reference only and is
not maintained. `pip install -r requirements.txt && python -m sila.main`, then
open `http://127.0.0.1:8765`.
