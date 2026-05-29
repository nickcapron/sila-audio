"""
Playback clock — drives the sequencer at BPM-derived 16th-note intervals
and routes TrigEvents through the sampler into the audio engine.
"""
from __future__ import annotations

import threading
import time

from sila.engine.audio import AudioEngine
from sila.engine.sampler import SamplePlayer
from sila.engine.sequencer import Sequencer


class PlaybackClock:
    """Background daemon thread: tick sequencer → sampler → audio."""

    def __init__(
        self,
        sequencer: Sequencer,
        sample_players: dict[str, SamplePlayer],
        audio_engine: AudioEngine,
    ) -> None:
        self._seq = sequencer
        self._players = sample_players
        self._audio = audio_engine
        self._thread: threading.Thread | None = None
        self._running = False
        self._start_time: float | None = None
        self._error: str | None = None
        self._interval: float = 0.0  # seconds per 16th-note; written by start()/set_bpm()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def healthy(self) -> bool:
        return self._running and self._error is None and self._audio.healthy

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def start_time(self) -> float | None:
        """Unix timestamp (seconds) when the clock's first tick fired."""
        return self._start_time

    def start(self, bpm: float) -> None:
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
        self._interval = 60.0 / bpm / 4.0  # 16th-note in seconds
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="sila-clock",
        )
        self._thread.start()

    def set_bpm(self, bpm: float) -> None:
        """Update tempo during live playback; takes effect on the next tick."""
        self._interval = 60.0 / bpm / 4.0

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self) -> None:
        next_tick = time.perf_counter()
        while self._running:
            # If the stream died unexpectedly, attempt one restart before
            # giving up. This handles transient device glitches.
            if self._audio.stream_died:
                try:
                    self._audio.start()
                except RuntimeError as exc:
                    self._error = str(exc)
                    self._running = False
                    break
            for event in self._seq.tick():
                player = self._players.get(event.track_id)
                if player is None:
                    continue
                audio = player.get(event.velocity)
                if audio is None:
                    continue
                track = self._seq.get_track(event.track_id)
                volume = track.fx.volume if track else 1.0
                pan    = track.fx.pan    if track else 0.0
                self._audio.play(audio, volume=volume, pan=pan)
            next_tick += self._interval
            sleep = next_tick - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
