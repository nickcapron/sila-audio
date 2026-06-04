"""Project, track, and samples routes."""
from __future__ import annotations

import shutil as _shutil
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

# routes.py defines AppState/get_state before importing this module, so the
# circular import resolves correctly via Python's partial-module mechanism.
import random as _random

from sila.api.routes import AppState, get_state
from sila.engine.sampler import SamplePlayer
from sila.models.project import ProjectModel, SampleLayer, TrackModel
from sila.models.step import Step
from sila.security import sanitize_notes, sanitize_project_name, safe_path

router = APIRouter()

_TRACK_PALETTE = [
    "#e8632a", "#2ae8c6", "#8b2ae8", "#2ae83c",
    "#2a68e8", "#e82a4e", "#e8d82a", "#e82aab",
]


# ---------------------------------------------------------------------------
# Project management
# ---------------------------------------------------------------------------

class CreateProjectRequest(BaseModel):
    name: str


@router.get("/projects")
async def list_projects(state: AppState = Depends(get_state)) -> dict[str, list[str]]:
    return {"projects": state.store.list_projects()}


@router.post("/projects")
async def create_project(
    req: CreateProjectRequest, state: AppState = Depends(get_state)
) -> ProjectModel:
    """Create a new project; name is sanitized to a safe directory name."""
    safe_name = sanitize_project_name(req.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Project name is empty after sanitization")
    state.reset_seq()
    project = state.store.new_project(safe_name)
    state.load_sample_players()
    return project


@router.put("/projects/{name}/load")
async def load_named_project(
    name: str, state: AppState = Depends(get_state)
) -> ProjectModel:
    """Load a saved project by name and make it the active project."""
    state.reset_seq()
    try:
        project = state.store.load(name)
    except (FileNotFoundError, OSError):
        raise HTTPException(status_code=404, detail=f"Project {name!r} not found")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    state.load_sample_players()
    return project


class RenameProjectRequest(BaseModel):
    new_name: str


@router.put("/projects/{name}/rename")
async def rename_project(
    name: str,
    req: RenameProjectRequest,
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    """Rename a saved project's folder and stored name. If it is the project
    currently open, the in-memory project is updated to match."""
    safe_new = sanitize_project_name(req.new_name)
    if not safe_new:
        raise HTTPException(status_code=400, detail="Project name is empty after sanitization")
    try:
        state.store.rename(name, safe_new)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {name!r} not found")
    except FileExistsError:
        raise HTTPException(
            status_code=409, detail=f"A project named {safe_new!r} already exists"
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"old_name": name, "new_name": safe_new}


# ---------------------------------------------------------------------------
# Project endpoints (legacy — kept for UI backward compat)
# ---------------------------------------------------------------------------

class NewProjectRequest(BaseModel):
    name: str


class LoadProjectRequest(BaseModel):
    name: str


class BpmRequest(BaseModel):
    bpm: float = Field(gt=0, le=300)


class SwingRequest(BaseModel):
    swing: float = Field(ge=0.0, le=1.0)


@router.post("/project/new")
async def new_project(
    req: NewProjectRequest, state: AppState = Depends(get_state)
) -> ProjectModel:
    safe_name = sanitize_project_name(req.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Project name is empty after sanitization")
    state.reset_seq()
    project = state.store.new_project(safe_name)
    state.load_sample_players()
    return project


@router.post("/project/load")
async def load_project(
    req: LoadProjectRequest, state: AppState = Depends(get_state)
) -> ProjectModel:
    safe_name = sanitize_project_name(req.name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Project name is empty after sanitization")
    state.reset_seq()
    project = state.store.load(safe_name)
    state.load_sample_players()
    return project


@router.post("/project/save")
async def save_project(state: AppState = Depends(get_state)) -> dict[str, str]:
    path = state.store.save()
    return {"saved": str(path)}


@router.get("/project")
async def get_project(state: AppState = Depends(get_state)) -> ProjectModel:
    return state.store.project


@router.put("/project/bpm")
async def set_bpm(
    req: BpmRequest, state: AppState = Depends(get_state)
) -> dict[str, float]:
    state.store.project.bpm = req.bpm
    if state.clock is not None and state.clock.running:
        state.clock.set_bpm(req.bpm)
    state.autosave()
    return {"bpm": req.bpm}


@router.put("/project/swing")
async def set_swing(
    req: SwingRequest, state: AppState = Depends(get_state)
) -> dict[str, float]:
    state.store.project.swing = req.swing
    state.autosave()
    return {"swing": req.swing}


@router.post("/project/undo")
async def undo(state: AppState = Depends(get_state)) -> dict[str, Any]:
    project = state.store.undo()
    if project is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Nothing to undo")
    state.reset_seq()
    state.load_sample_players()
    state.autosave()
    return {"ok": True, "project": project.model_dump()}


@router.post("/project/redo")
async def redo(state: AppState = Depends(get_state)) -> dict[str, Any]:
    project = state.store.redo()
    if project is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Nothing to redo")
    state.reset_seq()
    state.load_sample_players()
    state.autosave()
    return {"ok": True, "project": project.model_dump()}


# ---------------------------------------------------------------------------
# Track endpoints
# ---------------------------------------------------------------------------

class AddTrackRequest(BaseModel):
    name: str = "Track"
    step_count: int = Field(default=16, ge=1, le=256)


class StepCountRequest(BaseModel):
    step_count: int = Field(ge=1, le=256)


class RandomizeRequest(BaseModel):
    density: float = Field(default=0.5, ge=0.0, le=1.0)


class EuclideanRequest(BaseModel):
    hits:  int = Field(ge=0, le=256)
    steps: int = Field(ge=1, le=256)


class HumanizeRequest(BaseModel):
    amount: float = Field(default=0.0, ge=0.0, le=1.0)


class FxRequest(BaseModel):
    filter_cutoff:    float | None = Field(default=None, ge=0.0, le=1.0)
    filter_resonance: float | None = Field(default=None, ge=0.0, le=1.0)
    volume:           float | None = Field(default=None, ge=0.0, le=2.0)
    pan:              float | None = Field(default=None, ge=-1.0, le=1.0)


class LfoRequest(BaseModel):
    shape:       Literal["sine", "triangle", "square", "sawtooth", "random"] | None = None
    rate:        float | None = Field(default=None, ge=0.01, le=20.0)
    depth:       float | None = Field(default=None, ge=0.0, le=1.0)
    destination: Literal["volume", "pan", "filter_cutoff", "filter_resonance"] | None = None


class UpdateTrackNameRequest(BaseModel):
    name: str


class UpdateTrackNotesRequest(BaseModel):
    notes: str


class UpdateStepRequest(BaseModel):
    step: Step


class SetSamplesRequest(BaseModel):
    samples: list[SampleLayer]


class PastePatternRequest(BaseModel):
    steps: list[Step] = Field(max_length=256)


@router.post("/tracks")
async def add_track(
    req: AddTrackRequest, state: AppState = Depends(get_state)
) -> TrackModel:
    state.store.snapshot()
    color = _TRACK_PALETTE[len(state.store.project.tracks) % len(_TRACK_PALETTE)]
    track = TrackModel(name=req.name, step_count=req.step_count, color=color)
    track.ensure_steps()
    state.get_seq().add_track(track)
    player = SamplePlayer()
    player.load(state.store.samples_dir, track.samples)
    state.sample_players[track.id] = player
    state.autosave()
    return track


@router.delete("/tracks/{track_id}")
async def remove_track(
    track_id: str, state: AppState = Depends(get_state)
) -> dict[str, bool]:
    state.store.snapshot()
    state.get_seq().remove_track(track_id)
    state.sample_players.pop(track_id, None)
    state.autosave()
    return {"ok": True}


@router.put("/tracks/{track_id}/name")
async def update_track_name(
    track_id: str,
    req: UpdateTrackNameRequest,
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    track = _find_track(state, track_id)
    state.store.snapshot()
    track.name = (req.name.strip() or "Track")[:64]
    state.autosave()
    return {"name": track.name}


@router.put("/tracks/{track_id}/notes")
async def update_track_notes(
    track_id: str,
    req: UpdateTrackNotesRequest,
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    track = _find_track(state, track_id)
    state.store.snapshot()
    track.notes = sanitize_notes(req.notes)
    state.autosave()
    return {"notes": track.notes}


@router.put("/tracks/{track_id}/steps/{step_index}")
async def update_step(
    track_id: str,
    step_index: int,
    req: UpdateStepRequest,
    state: AppState = Depends(get_state),
) -> Step:
    track = _find_track(state, track_id)
    if step_index < 0 or step_index >= len(track.steps):
        raise HTTPException(status_code=404, detail="Step index out of range")
    state.store.snapshot()
    track.steps[step_index] = req.step
    state.autosave()
    return req.step


# ---------------------------------------------------------------------------
# Pattern bank / song chain
# ---------------------------------------------------------------------------

@router.post("/patterns/{slot}/save")
async def save_pattern_slot(
    slot: int, state: AppState = Depends(get_state)
) -> dict[str, int]:
    """Snapshot current track steps into pattern slot 0-7."""
    if not (0 <= slot <= 7):
        raise HTTPException(status_code=400, detail="slot must be 0-7")
    snapshot = {t.id: list(t.steps) for t in state.store.project.tracks}
    state.store.project.pattern_bank.slots[slot] = snapshot
    state.autosave()
    return {"slot": slot}


@router.post("/patterns/{slot}/load")
async def load_pattern_slot(
    slot: int, state: AppState = Depends(get_state)
) -> dict[str, int]:
    """Restore track steps from pattern slot 0-7."""
    if not (0 <= slot <= 7):
        raise HTTPException(status_code=400, detail="slot must be 0-7")
    snapshot = state.store.project.pattern_bank.slots.get(slot)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Pattern slot {slot} is empty")
    for track in state.store.project.tracks:
        if track.id in snapshot:
            track.steps = list(snapshot[track.id])
    state.autosave()
    return {"slot": slot}


class SongChainRequest(BaseModel):
    chain: list[int]  # ordered slot indices


@router.put("/song/chain")
async def set_song_chain(
    req: SongChainRequest, state: AppState = Depends(get_state)
) -> dict[str, Any]:
    state.store.project.song_chain = req.chain
    # Chain changed — reset position so next play starts from the beginning.
    if state.clock is not None:
        state.clock.reset_song_pos()
    state.autosave()
    return {"chain": req.chain}


@router.put("/song/mode")
async def set_song_mode(
    active: bool, state: AppState = Depends(get_state)
) -> dict[str, bool]:
    state.store.project.song_mode = active
    if active and state.clock is not None:
        # Activating song mode — restart from slot 0 of the chain.
        state.clock.reset_song_pos()
    state.autosave()
    return {"song_mode": active}


@router.get("/patterns")
async def get_patterns(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {
        "slots_used": list(state.store.project.pattern_bank.slots.keys()),
        "chain": state.store.project.song_chain,
        "song_mode": state.store.project.song_mode,
    }


@router.get("/tracks/{track_id}/waveform")
async def get_waveform(
    track_id: str,
    points: int = 600,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Return a downsampled waveform for display in the sample trimmer.

    *points* is the number of peak-envelope samples to return (default 600).
    Returns peak absolute values per window so the shape is readable even
    at low resolution.
    """
    import numpy as np
    player = state.sample_players.get(track_id)
    if player is None or not player._layers:
        return {"waveform": [], "length": 0}
    audio = player._layers[0].audio  # mono float32
    n = len(audio)
    if n == 0:
        return {"waveform": [], "length": 0}
    pts = max(1, min(points, n))
    window = max(1, n // pts)
    peaks = []
    for i in range(pts):
        chunk = audio[i * window: (i + 1) * window]
        peaks.append(float(np.max(np.abs(chunk))) if len(chunk) else 0.0)
    track = _find_track(state, track_id)
    layer = track.samples[0] if track.samples else None
    return {
        "waveform": peaks,
        "length": n,
        "start": layer.start if layer else 0.0,
        "end":   layer.end   if layer else 1.0,
    }


@router.put("/tracks/{track_id}/pattern")
async def paste_pattern(
    track_id: str,
    req: PastePatternRequest,
    state: AppState = Depends(get_state),
) -> dict[str, int]:
    """Replace a track's steps with the provided list (for copy/paste)."""
    track = _find_track(state, track_id)
    state.store.snapshot()
    track.steps = list(req.steps)
    track.step_count = len(req.steps)
    state.autosave()
    return {"steps": len(track.steps)}


@router.put("/tracks/{track_id}/fx")
async def update_track_fx(
    track_id: str,
    req: FxRequest,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Update FX parameters (filter, volume, pan) on a track."""
    track = _find_track(state, track_id)
    if req.filter_cutoff    is not None: track.fx.filter_cutoff    = req.filter_cutoff
    if req.filter_resonance is not None: track.fx.filter_resonance = req.filter_resonance
    if req.volume           is not None: track.fx.volume           = req.volume
    if req.pan              is not None: track.fx.pan              = req.pan
    state.autosave()
    return {"fx": track.fx.model_dump()}


@router.put("/tracks/{track_id}/lfo")
async def update_lfo(
    track_id: str,
    req: LfoRequest,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Update one or more LFO parameters on a track."""
    track = _find_track(state, track_id)
    lfo = track.lfo
    if req.shape       is not None: lfo.shape       = req.shape
    if req.rate        is not None: lfo.rate        = req.rate
    if req.depth       is not None: lfo.depth       = req.depth
    if req.destination is not None: lfo.destination = req.destination
    state.autosave()
    return {"lfo": lfo.model_dump()}


@router.put("/tracks/{track_id}/humanize")
async def set_humanize(
    track_id: str,
    req: HumanizeRequest,
    state: AppState = Depends(get_state),
) -> dict[str, float]:
    track = _find_track(state, track_id)
    track.humanize = req.amount
    state.autosave()
    return {"humanize": track.humanize}


@router.post("/tracks/{track_id}/euclidean")
async def euclidean_track(
    track_id: str,
    req: EuclideanRequest,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Apply a Euclidean (Bjorklund) rhythm to a track's steps."""
    track = _find_track(state, track_id)
    state.store.snapshot()
    steps  = max(1, min(req.steps, 256))
    hits   = max(0, min(req.hits, steps))

    # Bjorklund / Euclidean rhythm algorithm
    def _euclidean(h: int, s: int) -> list[bool]:
        if h == 0:  return [False] * s
        if h == s:  return [True]  * s
        groups_a: list[list[bool]] = [[True]]  * h
        groups_b: list[list[bool]] = [[False]] * (s - h)
        while len(groups_b) > 1:
            n = min(len(groups_a), len(groups_b))
            new_a = [groups_a[i] + groups_b[i] for i in range(n)]
            rest  = (groups_a[n:] if len(groups_a) > len(groups_b)
                     else groups_b[n:])
            groups_a = new_a
            groups_b = rest
        flat: list[bool] = [v for g in groups_a + groups_b for v in g]
        return flat

    pattern = _euclidean(hits, steps)

    # Resize track steps to match requested step count
    track.step_count = steps
    track.ensure_steps()
    for i, step in enumerate(track.steps):
        step.active = pattern[i] if i < len(pattern) else False
    state.autosave()
    return {"ok": True, "steps": [{"active": s.active} for s in track.steps]}


@router.post("/tracks/{track_id}/randomize")
async def randomize_track(
    track_id: str,
    req: RandomizeRequest,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Randomize step pattern with a musical density bias.

    Avoids consecutive active steps (flamming) and favors downbeats (steps
    divisible by 4) and 8th-note positions (steps divisible by 2).
    """
    track = _find_track(state, track_id)
    state.store.snapshot()
    n = len(track.steps)
    if n == 0:
        return {"ok": True}

    density = req.density  # 0=sparse, 0.5=medium, 1=dense
    # Base probability, biased by density
    base_p = 0.15 + density * 0.55  # 0.15 at sparse, 0.70 at dense

    new_active = [False] * n
    for i in range(n):
        if new_active[i - 1] if i > 0 else False:
            # No flamming: skip step after an active step
            continue
        # Downbeat bonus: divisible by 4 gets 2×, 8th by 1.5×, else 1×
        if i % 4 == 0:
            p = min(1.0, base_p * 2.0)
        elif i % 2 == 0:
            p = min(1.0, base_p * 1.5)
        else:
            p = base_p
        new_active[i] = _random.random() < p

    for step, active in zip(track.steps, new_active):
        step.active = active
    state.autosave()
    return {"ok": True, "steps": [{"active": s.active} for s in track.steps]}


@router.put("/tracks/{track_id}/step_count")
async def update_step_count(
    track_id: str,
    req: StepCountRequest,
    state: AppState = Depends(get_state),
) -> dict[str, int]:
    track = _find_track(state, track_id)
    state.store.snapshot()
    track.step_count = req.step_count
    track.ensure_steps()
    state.autosave()
    return {"step_count": track.step_count, "steps": len(track.steps)}


@router.put("/tracks/{track_id}/solo")
async def toggle_solo(
    track_id: str, state: AppState = Depends(get_state)
) -> dict[str, Any]:
    track = _find_track(state, track_id)
    state.store.snapshot()
    track.solo = not track.solo
    state.autosave()
    any_solo = any(t.solo for t in state.store.project.tracks)
    return {"solo": track.solo, "any_solo": any_solo}


@router.put("/tracks/{track_id}/mute")
async def toggle_mute(
    track_id: str, state: AppState = Depends(get_state)
) -> dict[str, bool]:
    track = _find_track(state, track_id)
    state.store.snapshot()
    track.muted = not track.muted
    state.autosave()
    return {"muted": track.muted}


def _resolve_sample_layer(layer: SampleLayer, samples_dir: Path) -> SampleLayer:
    """Ensure *layer.path* is project-samples-relative (just a filename).

    If the path contains directory components AND the file does not already
    exist inside *samples_dir*, we treat it as a library-relative path, copy
    the file into *samples_dir*, and return a new layer whose path is the bare
    filename.  This fixes Issue B: clients sending paths like
    ``"My Samples/01. Kick/kick.wav"`` instead of ``"kick.wav"``.
    """
    import sila.library.browser as _lb  # accessed at call time so test patches apply

    path = layer.path
    # A purely project-relative path has no directory separator.
    if "/" not in path and "\\" not in path:
        return layer  # already a bare filename — nothing to do

    # Multi-component path: check whether the file is already in samples_dir.
    try:
        candidate = safe_path(samples_dir, path)
        if candidate.exists():
            return layer  # it's there — accept as-is
    except ValueError:
        pass  # path traversal attempt — fall through; sampler will log & skip

    # Try to find it under LIBRARY_ROOT (accessed via module ref so tests can patch).
    try:
        lib_src = safe_path(_lb.LIBRARY_ROOT, path)
        if lib_src.is_file():
            dest = samples_dir / lib_src.name
            if not dest.exists():
                _shutil.copy2(lib_src, dest)
            return layer.model_copy(update={"path": lib_src.name})
    except (ValueError, Exception):
        pass  # library resolution failed — leave the path unchanged; sampler will log

    return layer  # unchanged; sampler will log the skip when it can't find the file


@router.put("/tracks/{track_id}/samples")
async def set_track_samples(
    track_id: str,
    req: SetSamplesRequest,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Assign sample layers to a track and reload its sample player.

    Library-relative paths (e.g. ``"Pack/Cat/kick.wav"``) are detected,
    copied into the project's samples/ directory, and stored as bare filenames
    so the project stays self-contained.
    """
    track = _find_track(state, track_id)
    samples_dir = state.store.samples_dir
    resolved = [_resolve_sample_layer(lyr, samples_dir) for lyr in req.samples]
    state.store.snapshot()
    track.samples = resolved
    player = SamplePlayer()
    player.load(samples_dir, track.samples)
    state.sample_players[track_id] = player
    state.autosave()
    return {"track_id": track_id, "sample_count": len(resolved)}


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------

@router.get("/samples")
async def list_samples(state: AppState = Depends(get_state)) -> dict[str, list[str]]:
    """Return audio files available in the current project's samples/ directory."""
    samples_dir = state.store.samples_dir
    if not samples_dir.exists():
        return {"files": []}
    files = sorted(
        f.name for f in samples_dir.iterdir()
        if f.suffix.lower() in {".wav", ".aiff", ".aif"}
    )
    return {"files": files}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _find_track(state: AppState, track_id: str) -> TrackModel:
    for t in state.store.project.tracks:
        if t.id == track_id:
            return t
    raise HTTPException(status_code=404, detail=f"Track {track_id!r} not found")
