"""Library browser, heartbeat, and export routes."""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sila.api.routes import AppState, get_state
from sila.engine.audio_loader import load_audio_mono_f32
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
    result = export_for_digitakt(state.store.project, state.store.samples_dir, out)
    return {"summary": export_result_summary(result)}
