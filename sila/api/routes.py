"""
FastAPI routes. Every route requires the session token via require_token.
No route is reachable without a valid X-SILA-Token header.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from sila.engine.audio import AudioEngine
from sila.engine.audio_loader import load_audio_mono_f32
from sila.engine.clock import PlaybackClock
from sila.engine.sampler import SamplePlayer
from sila.engine.sequencer import Sequencer
from sila.export.digitakt import export_for_digitakt, export_result_summary
from sila.import_tool.scanner import ImportResult, ScanResult, execute_import, scan_folder
from sila.library.browser import (
    CANONICAL_CATEGORIES,
    LIBRARY_ROOT,
    PackInfo,
    copy_to_project,
    ensure_my_samples,
    get_library_tree,
    resolve_library_path,
)
from sila.models.project import ProjectModel, SampleLayer, TrackModel
from sila.models.step import Step
from sila.security import require_token, sanitize_notes, sanitize_project_name
from sila.storage.project_store import ProjectStore

router = APIRouter(dependencies=[Depends(require_token)])


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

class AppState:
    """All mutable server state in one place; injected into routes via Depends."""

    def __init__(self) -> None:
        self.store: ProjectStore = ProjectStore()
        self.sequencer: Sequencer | None = None
        self.audio_engine: AudioEngine = AudioEngine()
        self.sample_players: dict[str, SamplePlayer] = {}
        self.clock: PlaybackClock | None = None
        self.last_ping: float = 0.0

    def startup(self) -> None:
        self.last_ping = time.monotonic()
        ensure_my_samples()
        if self.store.load_latest() is not None:
            self.load_sample_players()

    def last_ping_age(self) -> float:
        return time.monotonic() - self.last_ping

    def get_seq(self) -> Sequencer:
        if self.sequencer is None:
            self.sequencer = Sequencer(self.store.project)
        return self.sequencer

    def reset_seq(self) -> None:
        if self.clock is not None:
            self.clock.stop()
            self.clock = None
        self.audio_engine.stop()
        self.sequencer = None

    def load_sample_players(self) -> None:
        # Mutate in place so any running PlaybackClock's reference stays valid.
        self.sample_players.clear()
        for track in self.store.project.tracks:
            player = SamplePlayer()
            player.load(self.store.samples_dir, track.samples)
            self.sample_players[track.id] = player


_state = AppState()


def get_state() -> AppState:
    return _state


# Shims called by main.py lifespan and heartbeat watchdog.
def startup() -> None:
    _state.startup()


def last_ping_age() -> float:
    return _state.last_ping_age()


# ---------------------------------------------------------------------------
# Project management
# ---------------------------------------------------------------------------

@router.get("/projects")
async def list_projects(state: AppState = Depends(get_state)) -> dict[str, list[str]]:
    """List all saved project names, most recently modified first."""
    return {"projects": state.store.list_projects()}


class CreateProjectRequest(BaseModel):
    name: str


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
    return {"bpm": req.bpm}


@router.post("/project/undo")
async def undo(state: AppState = Depends(get_state)) -> dict[str, Any]:
    project = state.store.undo()
    if project is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Nothing to undo")
    state.reset_seq()
    state.load_sample_players()
    return {"ok": True, "project": project.model_dump()}


@router.post("/project/redo")
async def redo(state: AppState = Depends(get_state)) -> dict[str, Any]:
    project = state.store.redo()
    if project is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Nothing to redo")
    state.reset_seq()
    state.load_sample_players()
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
    return track


@router.delete("/tracks/{track_id}")
async def remove_track(
    track_id: str, state: AppState = Depends(get_state)
) -> dict[str, bool]:
    state.store.snapshot()
    state.get_seq().remove_track(track_id)
    state.sample_players.pop(track_id, None)
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
    return req.step


@router.put("/tracks/{track_id}/mute")
async def toggle_mute(
    track_id: str, state: AppState = Depends(get_state)
) -> dict[str, bool]:
    track = _find_track(state, track_id)
    state.store.snapshot()
    track.muted = not track.muted
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
    return {"track_id": track_id, "sample_count": len(req.samples)}


# ---------------------------------------------------------------------------
# Sequencer control
# ---------------------------------------------------------------------------

class StartRequest(BaseModel):
    bpm: float | None = Field(default=None, gt=0, le=300)


@router.post("/sequencer/start")
async def start_sequencer(
    req: StartRequest = StartRequest(),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    if state.clock is not None and state.clock.running:
        return {
            "ok": True,
            "already_running": True,
            "bpm": state.store.project.bpm,
            "started_at": state.clock.start_time,
        }
    bpm = req.bpm if req.bpm is not None else state.store.project.bpm
    state.store.project.bpm = bpm
    try:
        state.audio_engine.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    seq = state.get_seq()
    seq.reset()
    state.clock = PlaybackClock(seq, state.sample_players, state.audio_engine)
    state.clock.start(bpm)
    return {"ok": True, "bpm": bpm, "started_at": state.clock.start_time}


@router.post("/sequencer/stop")
async def stop_sequencer(state: AppState = Depends(get_state)) -> dict[str, bool]:
    if state.clock is not None:
        state.clock.stop()
        state.clock = None
    state.audio_engine.stop()
    state.get_seq().reset()
    return {"ok": True}


@router.post("/sequencer/fill")
async def set_fill(
    active: bool, state: AppState = Depends(get_state)
) -> dict[str, bool]:
    state.get_seq().fill_active = active
    return {"fill_active": active}


@router.get("/sequencer/status")
async def sequencer_status(state: AppState = Depends(get_state)) -> dict[str, Any]:
    playing = state.clock is not None and state.clock.running
    error = state.clock.error if state.clock is not None else None
    try:
        bpm: float | None = state.store.project.bpm
    except RuntimeError:
        bpm = None
    return {
        "playing": playing,
        "healthy": state.clock.healthy if state.clock is not None else True,
        "error": error,
        "bpm": bpm,
    }


@router.post("/sequencer/reset")
async def reset_sequencer(state: AppState = Depends(get_state)) -> dict[str, bool]:
    state.get_seq().reset()
    return {"ok": True}


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
# Heartbeat
# ---------------------------------------------------------------------------

@router.post("/ping")
async def ping(state: AppState = Depends(get_state)) -> dict[str, bool]:
    """Browser calls this every few seconds so the server knows the UI is open."""
    state.last_ping = time.monotonic()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Library browser
# ---------------------------------------------------------------------------

@router.get("/library")
async def get_library(state: AppState = Depends(get_state)) -> dict[str, list[PackInfo]]:
    """Return the full pack/category/sample tree for the sample browser."""
    return {"packs": get_library_tree()}


class LibraryPreviewRequest(BaseModel):
    path: str  # relative path from LIBRARY_ROOT


@router.post("/library/preview")
async def preview_library_sample(
    req: LibraryPreviewRequest, state: AppState = Depends(get_state)
) -> dict[str, bool]:
    """Play a library sample through the audio engine without assigning it to a track."""
    try:
        src = resolve_library_path(req.path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid library path")
    if not src.exists():
        raise HTTPException(status_code=404, detail="Sample not found in library")
    try:
        audio = load_audio_mono_f32(src)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to load sample: {exc}")
    if not state.audio_engine.healthy:
        try:
            state.audio_engine.start()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
    state.audio_engine.play(audio)
    return {"ok": True}


class LibraryAddRequest(BaseModel):
    path: str  # relative path from LIBRARY_ROOT


@router.post("/library/add")
async def add_library_sample(
    req: LibraryAddRequest, state: AppState = Depends(get_state)
) -> dict[str, str]:
    """Copy a library sample into the current project's samples/ folder."""
    try:
        filename = copy_to_project(req.path, state.store.samples_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid library path")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"filename": filename}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class ExportRequest(BaseModel):
    output_dir: str


@router.post("/export/digitakt")
async def export_digitakt(
    req: ExportRequest, state: AppState = Depends(get_state)
) -> dict[str, str]:
    out = Path(req.output_dir)
    if not out.is_absolute():
        raise HTTPException(status_code=400, detail="output_dir must be an absolute path")
    result = export_for_digitakt(state.store.project, state.store.samples_dir, out)
    return {"summary": export_result_summary(result)}


# ---------------------------------------------------------------------------
# Sample import tool
# ---------------------------------------------------------------------------

import asyncio as _asyncio
import threading as _threading

_browse_lock = _threading.Lock()  # one native dialog open at a time
_CANONICAL_SET = frozenset(CANONICAL_CATEGORIES)


def _open_folder_dialog() -> str:
    """Open a native folder-picker and return the chosen path (or '' on cancel)."""
    with _browse_lock:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError as exc:
            raise RuntimeError(f"tkinter is not available: {exc}") from exc
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        path = filedialog.askdirectory(parent=root, title="Select a sample pack folder")
        root.destroy()
        return path or ""


class ImportScanRequest(BaseModel):
    path: str  # absolute filesystem path the user wants to scan


class ImportExecuteRequest(BaseModel):
    source_path: str
    pack_name: str
    mappings: dict[str, str]  # group_name → SILA canonical category


@router.post("/import/browse")
async def import_browse() -> dict[str, str]:
    """Open a native OS folder picker and return the selected path.

    Runs tkinter's blocking dialog in a thread so the async event loop
    is not blocked.  Protected by the session token like every other route.
    """
    try:
        loop = _asyncio.get_running_loop()
        path = await loop.run_in_executor(None, _open_folder_dialog)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"path": path}


@router.post("/import/scan")
async def import_scan(req: ImportScanRequest) -> ScanResult:
    """Scan a local folder, group audio files, and suggest categories.

    Accepts any absolute local path — this is a local-only tool and the
    user needs to be able to point at anywhere on their PC.
    Protected by 127.0.0.1-only binding + session token.
    """
    if not Path(req.path).is_absolute():
        raise HTTPException(status_code=400, detail="path must be absolute")
    if not Path(req.path).is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {req.path!r}")
    try:
        return scan_folder(req.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/import/execute")
async def import_execute(req: ImportExecuteRequest) -> ImportResult:
    """Copy the mapped files into ~/SILA/library/<pack_name>/<category>/.

    Security: mapping values are validated against SILA's canonical category
    list so no arbitrary subdirectories can be created in the library.
    Destination paths are also guarded by safe_path() inside execute_import.
    """
    if not req.pack_name.strip():
        raise HTTPException(status_code=400, detail="pack_name cannot be empty")
    bad = [v for v in req.mappings.values() if v not in _CANONICAL_SET]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category name(s): {bad!r} — must be SILA canonical categories",
        )
    try:
        return execute_import(req.source_path, req.pack_name, req.mappings, LIBRARY_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_track(state: AppState, track_id: str) -> TrackModel:
    for t in state.store.project.tracks:
        if t.id == track_id:
            return t
    raise HTTPException(status_code=404, detail=f"Track {track_id!r} not found")
