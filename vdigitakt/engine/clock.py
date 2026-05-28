"""
Playback clock — drives the sequencer at BPM-derived 16th-note intervals
and routes TrigEvents through the sampler into the audio engine.
"""
from __future__ import annotations

import threading
import time

from vdigitakt.engine.audio import AudioEngine
from vdigitakt.engine.sampler import SamplePlayer
from vdigitakt.engine.sequencer import Sequencer


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

    @property
    def running(self) -> bool:
        return self._running

    def start(self, bpm: float) -> None:
        if self._running:
            return
        self._running = True
        interval = 60.0 / bpm / 4.0  # 16th-note in seconds
        self._thread = threading.Thread(
            target=self._run,
            args=(interval,),
            daemon=True,
            name="vdigitakt-clock",
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self, interval: float) -> None:
        next_tick = time.perf_counter()
        while self._running:
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
            next_tick += interval
            sleep = next_tick - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
