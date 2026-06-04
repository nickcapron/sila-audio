"""
FastAPI route coordinator.

Defines AppState and the DI infrastructure, then assembles the single
token-protected router from the four domain modules.  main.py imports
`router`, `startup`, and `last_ping_age` from here; tests patch
`_state` and `LIBRARY_ROOT` here.
"""
from __future__ import annotations

import logging
import time

_log = logging.getLogger(__name__)

from fastapi import APIRouter, Depends

from sila.engine.audio import AudioEngine
from sila.engine.clock import PlaybackClock
from sila.engine.fx import apply_lowpass
from sila.engine.midi import MidiListener, get_midi_input_names
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
        self.metronome_active: bool = False
        # Set during startup() if the most recent project could not be loaded.
        # Cleared by the sequencer-status endpoint after the first read so the
        # warning surfaces exactly once in the UI status bar.
        self.startup_warning: str | None = None
        # MIDI
        self.midi_listener: MidiListener = MidiListener(self._on_midi_note)
        self.midi_note_map: dict[int, str] = {}   # MIDI note → track_id
        self.midi_learn_track_id: str | None = None

    def startup(self) -> None:
        # Wire up a dedicated handler for sila.engine.clock so dropped-trig
        # debug messages reach the console even after uvicorn resets the root
        # logger.  propagate=False bypasses root's WARNING filter entirely.
        import sys as _sys
        _clock_log = logging.getLogger("sila.engine.clock")
        _clock_log.setLevel(logging.DEBUG)
        if not _clock_log.handlers:
            _h = logging.StreamHandler(_sys.stderr)
            _h.setLevel(logging.DEBUG)
            _h.setFormatter(logging.Formatter(
                "%(asctime)s [%(name)s] %(message)s", "%H:%M:%S"
            ))
            _clock_log.addHandler(_h)
        _clock_log.propagate = False

        self.last_ping = time.monotonic()
        ensure_my_samples()

        # Returning user: load the most recently modified project.
        loaded = False
        try:
            if self.store.load_latest() is not None:
                loaded = True
        except Exception as exc:
            # The project JSON is corrupt or a Pydantic validator rejected it.
            # Find the name of the failing project for the warning message, then
            # fall through to create a blank session rather than crashing.
            try:
                names = self.store.list_projects()
                failed_name = names[0] if names else "unknown"
            except Exception:
                failed_name = "unknown"
            warning = (
                f"Project \"{failed_name}\" could not be loaded "
                f"({type(exc).__name__}: {exc}). "
                f"Starting with a blank session — your data is still on disk."
            )
            _log.error("SILA startup: %s", warning)
            self.startup_warning = warning

        # First-time user (or unrecoverable load): create a blank "Untitled" session.
        if not loaded:
            self.store.new_project("Untitled")

        # Always build sample players — no-op for an empty project.
        self.load_sample_players()

        # Open first available MIDI device
        names = get_midi_input_names()
        if names:
            self.midi_listener.open(0)

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
        # Build into a temporary dict first so all file I/O completes before
        # the live dictionary is touched.  The clear()+update() swap is
        # microsecond-scale, minimising the window where the clock sees an
        # empty dict and silently drops notes.
        new_players = {}
        for track in self.store.project.tracks:
            player = SamplePlayer()
            player.load(self.store.samples_dir, track.samples)
            new_players[track.id] = player
        self.sample_players.clear()
        self.sample_players.update(new_players)

    def autosave(self) -> None:
        """Persist current project state to disk. Best-effort: never raises."""
        try:
            self.store.autosave()
        except Exception:
            pass

    def _on_midi_note(self, note: int, velocity: int) -> None:
        """Called from the WinMM callback thread on every note-on."""
        # MIDI learn: map note to the waiting track
        if self.midi_learn_track_id is not None:
            self.midi_note_map[note] = self.midi_learn_track_id
            self.midi_learn_track_id = None
            return
        # Default mapping: note 36-43 → tracks by position
        track_id = self.midi_note_map.get(note)
        if track_id is None:
            try:
                tracks = self.store.project.tracks
                idx = note - 36
                if 0 <= idx < len(tracks):
                    track_id = tracks[idx].id
            except Exception:
                return
        if track_id is None or track_id not in self.sample_players:
            return
        player = self.sample_players[track_id]
        audio = player.get(velocity)
        if audio is None:
            return
        try:
            track = next((t for t in self.store.project.tracks if t.id == track_id), None)
            vol = track.fx.volume if track else 1.0
            pan = track.fx.pan    if track else 0.0
            if track and track.fx.filter_cutoff < 0.999:
                audio = apply_lowpass(audio, track.fx.filter_cutoff, track.fx.filter_resonance)
        except Exception:
            vol, pan = 1.0, 0.0
        if not self.audio_engine.healthy:
            try:
                self.audio_engine.start()
            except Exception:
                return
        self.audio_engine.play(audio, volume=vol, pan=pan)


_state = AppState()


def get_state() -> AppState:
    return _state


# Shims called by main.py lifespan and heartbeat watchdog.
def startup() -> None:
    _state.startup()


def last_ping_age() -> float:
    return _state.last_ping_age()


def shutdown() -> None:
    """Graceful shutdown: stop clock first, then audio engine.

    Called from the lifespan cleanup so no audio plays after the browser closes.
    """
    _state.reset_seq()


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
