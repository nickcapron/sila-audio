"""Sequencer control routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sila.api.routes import AppState, get_state
from sila.engine.clock import PlaybackClock

router = APIRouter()


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
    state.autosave()
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
    state.autosave()
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
