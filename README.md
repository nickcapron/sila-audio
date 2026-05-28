# VDigitakt

A Python step sequencer and sample instrument inspired by the Elektron Digitakt, running as a local FastAPI server with an HTML/JS UI. Designed to run inside Reaper via a JUCE VST3 shell (Phase 3).

## Quick start

```
pip install -r requirements.txt
python -m vdigitakt.main
```

Open `http://127.0.0.1:8765` in a browser. The session token is printed to stdout on startup — the UI reads it from the URL hash (`#token=...`) or localStorage.

## Project layout

```
vdigitakt/
  main.py              Entry point — binds to 127.0.0.1:8765
  security.py          All security primitives (import this first)
  engine/
    sequencer.py       Polyrhythmic step sequencer
    sampler.py         Sample player with velocity layers + round-robin
    lfo.py             Per-track LFO shapes
    fx.py              Volume / pan / filter
  models/
    project.py         ProjectModel, TrackModel, SampleLayer, FX, LFO
    step.py            Step model with trig conditions
  export/
    digitakt.py        Phase 2 — export to Elektron Transfer-ready WAV
  api/
    routes.py          FastAPI routes (all token-gated)
  ui/
    index.html / app.js  Grid-first instrument UI
  storage/
    project_store.py   Load/save JSON + undo/redo
  tests/
    test_security.py
    test_export.py
```

## Security model

- Server binds to `127.0.0.1` only.
- Every API route requires `X-VDigitakt-Token` header (session token, generated at startup).
- All file paths go through `safe_path()` — no traversal possible.
- All notes fields go through `sanitize_notes()` — prompt injection stripped.
- Project files are backed up before every write.

## Projects

Stored at `~/VDigitakt/projects/<name>/project.json`. Samples at `~/VDigitakt/projects/<name>/samples/`.

## Digitakt export (Phase 2)

Click **Export for Digitakt**, choose an output folder. Each sample is:
- Resampled to 48 000 Hz, 16-bit PCM, mono
- Stereo summed as (L+R)×0.5
- Filename sanitized to ASCII, max 16 chars, spaces → underscores
- Validated against 33-second / 170 MB limits (warned, not silently skipped)

The output folder drops directly into Elektron Transfer.
