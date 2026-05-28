"""
FastAPI routes. Every route requires the session token via require_token.
No route is reachable without a valid X-VDigitakt-Token header.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from vdigitakt.engine.sequencer import Sequencer, TrigEvent
from vdigitakt.export.digitakt import export_for_digitakt, export_result_summary
from vdigitakt.models.project import ProjectModel, TrackModel
from vdigitakt.models.step import Step
from vdigitakt.security import require_token, sanitize_notes
from vdigitakt.storage.project_store import ProjectStore

router = APIRouter(dependencies=[Depends(require_token)])

# Module-level store and sequencer. Replaced at startup via set_store().
_store: ProjectStore = ProjectStore()
_sequencer: Sequencer | None = None


def set_store(store: ProjectStore) -> None:
    global _store
    _store = store


def _get_seq() -> Sequencer:
    global _sequencer
    if _sequencer is None:
        _sequencer = Sequencer(_store.project)
    return _sequencer


def _reset_seq() -> None:
    global _sequencer
    _sequencer = None


# ---------------------------------------------------------------------------
# Project endpoints
# ---------------------------------------------------------------------------

class NewProjectRequest(BaseModel):
    name: str


class LoadProjectRequest(BaseModel):
    name: str


@router.post("/project/new")
async def new_project(req: NewProjectRequest) -> ProjectModel:
    _reset_seq()
    return _store.new_project(req.name)


@router.post("/project/load")
async def load_project(req: LoadProjectRequest) -> ProjectModel:
    _reset_seq()
    return _store.load(req.name)


@router.post("/project/save")
async def save_project() -> dict[str, str]:
    path = _store.save()
    return {"saved": str(path)}


@router.get("/project")
async def get_project() -> ProjectModel:
    return _store.project


@router.post("/project/undo")
async def undo() -> dict[str, Any]:
    project = _store.undo()
    if project is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Nothing to undo")
    _reset_seq()
    return {"ok": True, "project": project.model_dump()}


@router.post("/project/redo")
async def redo() -> dict[str, Any]:
    project = _store.redo()
    if project is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Nothing to redo")
    _reset_seq()
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


@router.post("/tracks")
async def add_track(req: AddTrackRequest) -> TrackModel:
    _store.snapshot()
    track = TrackModel(name=req.name, step_count=req.step_count)
    track.ensure_steps()
    _get_seq().add_track(track)
    return track


@router.delete("/tracks/{track_id}")
async def remove_track(track_id: str) -> dict[str, bool]:
    _store.snapshot()
    _get_seq().remove_track(track_id)
    return {"ok": True}


@router.put("/tracks/{track_id}/notes")
async def update_track_notes(track_id: str, req: UpdateTrackNotesRequest) -> dict[str, str]:
    track = _find_track(track_id)
    _store.snapshot()
    track.notes = sanitize_notes(req.notes)
    return {"notes": track.notes}


@router.put("/tracks/{track_id}/steps/{step_index}")
async def update_step(track_id: str, step_index: int, req: UpdateStepRequest) -> Step:
    track = _find_track(track_id)
    if step_index < 0 or step_index >= len(track.steps):
        raise HTTPException(status_code=404, detail="Step index out of range")
    _store.snapshot()
    track.steps[step_index] = req.step
    return req.step


@router.put("/tracks/{track_id}/mute")
async def toggle_mute(track_id: str) -> dict[str, bool]:
    track = _find_track(track_id)
    _store.snapshot()
    track.muted = not track.muted
    return {"muted": track.muted}


# ---------------------------------------------------------------------------
# Sequencer control
# ---------------------------------------------------------------------------

@router.post("/sequencer/fill")
async def set_fill(active: bool) -> dict[str, bool]:
    _get_seq().fill_active = active
    return {"fill_active": active}


@router.post("/sequencer/reset")
async def reset_sequencer() -> dict[str, bool]:
    _get_seq().reset()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class ExportRequest(BaseModel):
    output_dir: str


@router.post("/export/digitakt")
async def export_digitakt(req: ExportRequest) -> dict[str, str]:
    from vdigitakt.security import safe_path as _safe_path
    # Output dir is user-chosen; we validate it's an absolute path (no traversal
    # relative to project root — user picks it via file picker so any abs path is ok).
    out = Path(req.output_dir)
    if not out.is_absolute():
        raise HTTPException(status_code=400, detail="output_dir must be an absolute path")
    result = export_for_digitakt(_store.project, _store.samples_dir, out)
    return {"summary": export_result_summary(result)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_track(track_id: str) -> TrackModel:
    for t in _store.project.tracks:
        if t.id == track_id:
            return t
    raise HTTPException(status_code=404, detail=f"Track {track_id!r} not found")
