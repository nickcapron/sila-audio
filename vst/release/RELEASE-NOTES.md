## SILA v1.0.0 — Windows x64 (VST3 + Standalone)

A free, Digitakt-style sampler and step sequencer that runs inside your DAW or on its own.

**Download** `SILA-v1.0.0-win64.zip`, unzip, then either double-click `install-vst3.cmd` (copies the plugin into `C:\Program Files\Common Files\VST3`) or run `SILA.exe` standalone. Windows will show *"Windows protected your PC"* the first time because this build is unsigned: click **More info → Run anyway**.

### What's in it
- 8 sample lanes, 16 patterns of up to 128 steps, per-pattern kits, song mode
- Per-step parameter locks (pitch, velocity, filter, LFO, sample slice, length, micro-timing), trig conditions, probability, retrig with velocity fade, **FILL**
- Velocity layers + round-robin per lane, TPT state-variable filter, per-voice LFO
- Sample library with an importer that sorts a folder of WAVs by type; factory RD-6 + CZ-1 kit installs itself
- Host-synced in a DAW; **one stereo output bus per lane**; MIDI file export; live MIDI in (channel N = lane N)
- Press `?` inside SILA for the keyboard / mouse cheat-sheet

### Known gaps
- No undo — save often (PROJECTS → Save)
- Renaming / moving / deleting a library sample doesn't rewrite projects that use it (the lane shows a *sample missing* badge; click it to pick a replacement)
- Windows only. macOS builds from source but is untested
- Needs the Microsoft Edge WebView2 Runtime (already on nearly every Windows 10/11 PC); SILA tells you if it's missing

SHA-256 of the zip is in the attached `.sha256` file.
