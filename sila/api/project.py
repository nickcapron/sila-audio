"""Project, track, and samples routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

# routes.py defines AppState/get_state before importing this module, so the
# circular import resolves correctly via Python's partial-module mechanism.
import random as _random

from sila.api.routes import AppState, get_state
from sila.engine.sampler import SamplePlayer
from sila.models.project import ProjectModel, SampleLayer, TrackModel
from sila.models.step import Step
from sila.security import sanitize_notes, sanitize_project_name

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
    state.reset_seq()
    project = state.store.new_project(req.name)
    state.load_sample_players()
    return project


@router.post("/project/load")
async def load_project(
    req: LoadProjectRequest, state: AppState = Depends(get_state)
) -> ProjectModel:
    state.reset_seq()
    project = state.store.load(req.name)
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
    step_count: int = 16


class StepCountRequest(BaseModel):
    step_count: int = Field(ge=1, le=256)


class RandomizeRequest(BaseModel):
    density: float = Field(default=0.5, ge=0.0, le=1.0)


class HumanizeRequest(BaseModel):
    amount: float = Field(default=0.0, ge=0.0, le=1.0)


class UpdateTrackNameRequest(BaseModel):
    name: str


class UpdateTrackNotesRequest(BaseModel):
    notes: str


class UpdateStepRequest(BaseModel):
    step: Step


class SetSamplesRequest(BaseModel):
    samples: list[SampleLayer]


class PastePatternRequest(BaseModel):
    steps: list[Step]


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
    state.autosave()
    return {"chain": req.chain}


@router.put("/song/mode")
async def set_song_mode(
    active: bool, state: AppState = Depends(get_state)
) -> dict[str, bool]:
    state.store.project.song_mode = active
    state.autosave()
    return {"song_mode": active}


@router.get("/patterns")
async def get_patterns(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {
        "slots_used": list(state.store.project.pattern_bank.slots.keys()),
        "chain": state.store.project.song_chain,
        "song_mode": state.store.project.song_mode,
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


@router.put("/tracks/{track_id}/samples")
async def set_track_samples(
    track_id: str,
    req: SetSamplesRequest,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Assign sample layers to a track and reload its sample player."""
    track = _find_track(state, track_id)
    state.store.snapshot()
    track.samples = req.samples
    player = SamplePlayer()
    player.load(state.store.samples_dir, track.samples)
    state.sample_players[track_id] = player
    state.autosave()
    return {"track_id": track_id, "sample_count": len(req.samples)}


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
