"""
FastAPI routes. Every route requires the session token via require_token.
No route is reachable without a valid X-SILA-Token header.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from sila.engine.audio import AudioEngine
from sila.engine.clock import PlaybackClock
from sila.engine.sampler import SamplePlayer
from sila.engine.sequencer import Sequencer, TrigEvent
from sila.export.digitakt import export_for_digitakt, export_result_summary
from sila.models.project import ProjectModel, SampleLayer, TrackModel
from sila.models.step import Step
from sila.security import require_token, sanitize_notes
from sila.storage.project_store import ProjectStore

router = APIRouter(dependencies=[Depends(require_token)])

_store: ProjectStore = ProjectStore()
_sequencer: Sequencer | None = None
_audio_engine = AudioEngine()
_sample_players: dict[str, SamplePlayer] = {}
_clock: PlaybackClock | None = None


def set_store(store: ProjectStore) -> None:
    global _store
    _store = store


def _get_seq() -> Sequencer:
    global _sequencer
    if _sequencer is None:
        _sequencer = Sequencer(_store.project)
    return _sequencer


def _reset_seq() -> None:
    global _sequencer, _clock
    if _clock is not None:
        _clock.stop()
        _clock = None
    _audio_engine.stop()
    _sequencer = None


def _load_sample_players() -> None:
    global _sample_players
    _sample_players = {}
    for track in _store.project.tracks:
        player = SamplePlayer()
        player.load(_store.samples_dir, track.samples)
        _sample_players[track.id] = player


# ---------------------------------------------------------------------------
# Project endpoints
# ---------------------------------------------------------------------------

class NewProjectRequest(BaseModel):
    name: str


class LoadProjectRequest(BaseModel):
    name: str


class BpmRequest(BaseModel):
    bpm: float = Field(gt=0, le=300)


@router.post("/project/new")
async def new_project(req: NewProjectRequest) -> ProjectModel:
    _reset_seq()
    project = _store.new_project(req.name)
    _load_sample_players()
    return project


@router.post("/project/load")
async def load_project(req: LoadProjectRequest) -> ProjectModel:
    _reset_seq()
    project = _store.load(req.name)
    _load_sample_players()
    return project


@router.post("/project/save")
async def save_project() -> dict[str, str]:
    path = _store.save()
    return {"saved": str(path)}


@router.get("/project")
async def get_project() -> ProjectModel:
    return _store.project


@router.put("/project/bpm")
async def set_bpm(req: BpmRequest) -> dict[str, float]:
    _store.project.bpm = req.bpm
    return {"bpm": req.bpm}


@router.post("/project/undo")
async def undo() -> dict[str, Any]:
    project = _store.undo()
    if project is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Nothing to undo")
    _reset_seq()
    _load_sample_players()
    return {"ok": True, "project": project.model_dump()}


@router.post("/project/redo")
async def redo() -> dict[str, Any]:
    project = _store.redo()
    if project is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Nothing to redo")
    _reset_seq()
    _load_sample_players()
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
async def add_track(req: AddTrackRequest) -> TrackModel:
    _store.snapshot()
    track = TrackModel(name=req.name, step_count=req.step_count)
    track.ensure_steps()
    _get_seq().add_track(track)
    player = SamplePlayer()
    player.load(_store.samples_dir, track.samples)
    _sample_players[track.id] = player
    return track


@router.delete("/tracks/{track_id}")
async def remove_track(track_id: str) -> dict[str, bool]:
    _store.snapshot()
    _get_seq().remove_track(track_id)
    _sample_players.pop(track_id, None)
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


@router.put("/tracks/{track_id}/samples")
async def set_track_samples(track_id: str, req: SetSamplesRequest) -> dict[str, Any]:
    """Assign sample layers to a track and reload its sample player."""
    track = _find_track(track_id)
    _store.snapshot()
    track.samples = req.samples
    player = SamplePlayer()
    player.load(_store.samples_dir, track.samples)
    _sample_players[track_id] = player
    return {"track_id": track_id, "sample_count": len(req.samples)}


# ---------------------------------------------------------------------------
# Sequencer control
# ---------------------------------------------------------------------------

class StartRequest(BaseModel):
    bpm: float | None = Field(default=None, gt=0, le=300)


@router.post("/sequencer/start")
async def start_sequencer(req: StartRequest = StartRequest()) -> dict[str, Any]:
    global _clock
    if _clock is not None and _clock.running:
        return {"ok": True, "already_running": True}
    bpm = req.bpm if req.bpm is not None else _store.project.bpm
    _store.project.bpm = bpm
    try:
        _audio_engine.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    seq = _get_seq()
    seq.reset()
    _clock = PlaybackClock(seq, _sample_players, _audio_engine)
    _clock.start(bpm)
    return {"ok": True, "bpm": bpm}


@router.post("/sequencer/stop")
async def stop_sequencer() -> dict[str, bool]:
    global _clock
    if _clock is not None:
        _clock.stop()
        _clock = None
    _audio_engine.stop()
    _get_seq().reset()
    return {"ok": True}


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
