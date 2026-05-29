"""Project, track, and samples routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

# routes.py defines AppState/get_state before importing this module, so the
# circular import resolves correctly via Python's partial-module mechanism.
from sila.api.routes import AppState, get_state
from sila.engine.sampler import SamplePlayer
from sila.models.project import ProjectModel, SampleLayer, TrackModel
from sila.models.step import Step
from sila.security import sanitize_notes, sanitize_project_name

router = APIRouter()


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


class UpdateTrackNotesRequest(BaseModel):
    notes: str


class UpdateStepRequest(BaseModel):
    step: Step


class SetSamplesRequest(BaseModel):
    samples: list[SampleLayer]


@router.post("/tracks")
async def add_track(
    req: AddTrackRequest, state: AppState = Depends(get_state)
) -> TrackModel:
    state.store.snapshot()
    track = TrackModel(name=req.name, step_count=req.step_count)
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
