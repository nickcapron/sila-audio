"""Library browser, heartbeat, and export routes."""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sila.api.routes import AppState, get_state
from sila.engine.audio_loader import load_audio_mono_f32
from sila.engine.midi import get_midi_input_names
from sila.export.digitakt import export_for_digitakt, export_result_summary
from sila.library.browser import (
    PackInfo,
    copy_to_project,
    get_library_tree,
    resolve_library_path,
)

router = APIRouter()


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
# Heartbeat
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# MIDI
# ---------------------------------------------------------------------------

@router.get("/midi/status")
async def midi_status(state: AppState = Depends(get_state)) -> dict:
    return {
        "devices": get_midi_input_names(),
        "active": state.midi_listener.active,
        "learning": state.midi_learn_track_id,
        "note_map": {str(k): v for k, v in state.midi_note_map.items()},
    }


@router.post("/midi/learn/{track_id}")
async def midi_learn(track_id: str, state: AppState = Depends(get_state)) -> dict:
    state.midi_learn_track_id = track_id
    return {"learning": track_id}


@router.post("/midi/cancel_learn")
async def midi_cancel_learn(state: AppState = Depends(get_state)) -> dict:
    state.midi_learn_track_id = None
    return {"learning": None}


@router.delete("/midi/mapping/{note}")
async def midi_delete_mapping(note: int, state: AppState = Depends(get_state)) -> dict:
    state.midi_note_map.pop(note, None)
    return {"ok": True}


@router.post("/midi/open/{device_index}")
async def midi_open_device(device_index: int, state: AppState = Depends(get_state)) -> dict:
    state.midi_listener.close()
    ok = state.midi_listener.open(device_index)
    return {"ok": ok, "device_index": device_index}


@router.post("/ping")
async def ping(state: AppState = Depends(get_state)) -> dict[str, bool]:
    """Browser calls this every few seconds so the server knows the UI is open."""
    state.last_ping = time.monotonic()
    return {"ok": True}


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
    # Restrict exports to inside the user's home directory so the endpoint
    # cannot write to arbitrary system paths.
    try:
        from sila.security import safe_path as _safe_path
        out = _safe_path(Path.home(), out)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                "output_dir must be inside your home directory "
                f"({Path.home()})"
            ),
        )
    result = export_for_digitakt(state.store.project, state.store.samples_dir, out)
    return {"summary": export_result_summary(result)}
