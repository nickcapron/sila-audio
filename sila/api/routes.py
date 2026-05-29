"""
FastAPI route coordinator.

Defines AppState and the DI infrastructure, then assembles the single
token-protected router from the four domain modules.  main.py imports
`router`, `startup`, and `last_ping_age` from here; tests patch
`_state` and `LIBRARY_ROOT` here.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from sila.engine.audio import AudioEngine
from sila.engine.clock import PlaybackClock
from sila.engine.sampler import SamplePlayer
from sila.engine.sequencer import Sequencer
from sila.library.browser import (
    CANONICAL_CATEGORIES,
    LIBRARY_ROOT,
    ensure_my_samples,
)
from sila.security import require_token
from sila.storage.project_store import ProjectStore


# ---------------------------------------------------------------------------
# Application state  (must be defined before domain modules are imported)
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
# Router assembly  (domain modules are imported here, after AppState is defined)
# ---------------------------------------------------------------------------
# The four domain modules import AppState/get_state from this module.
# Python resolves the circular reference correctly because AppState and
# get_state are defined above before the imports below execute.

from sila.api.project import router as _project_router      # noqa: E402
from sila.api.sequencer import router as _seq_router        # noqa: E402
from sila.api.library import router as _library_router      # noqa: E402
from sila.api.imports import router as _import_router       # noqa: E402

_auth = [Depends(require_token)]

router = APIRouter()
router.include_router(_project_router, dependencies=_auth)
router.include_router(_seq_router, dependencies=_auth)
router.include_router(_library_router, dependencies=_auth)
router.include_router(_import_router, dependencies=_auth)
