"""Sample import tool routes."""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import sila.library.browser as _browser
from sila.import_tool.scanner import ImportResult, ScanResult, execute_import, scan_folder
from sila.library.browser import CANONICAL_CATEGORIES

router = APIRouter()

_browse_lock = threading.Lock()  # one native dialog open at a time
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
    """Open a native OS folder picker and return the selected path."""
    try:
        loop = asyncio.get_running_loop()
        path = await loop.run_in_executor(None, _open_folder_dialog)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"path": path}


@router.post("/import/scan")
async def import_scan(req: ImportScanRequest) -> ScanResult:
    """Scan a local folder, group audio files, and suggest categories."""
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
        # Access LIBRARY_ROOT via the module reference so test patches to
        # sila.library.browser.LIBRARY_ROOT are respected at call time.
        return execute_import(req.source_path, req.pack_name, req.mappings, _browser.LIBRARY_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
