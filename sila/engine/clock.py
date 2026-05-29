"""
Playback clock — drives the sequencer at BPM-derived 16th-note intervals
and routes TrigEvents through the sampler into the audio engine.
"""
from __future__ import annotations

import math
import random
import threading
import time

from sila.engine.audio import AudioEngine
from sila.engine.fx import apply_lowpass
from sila.engine.lfo import compute_lfo_value
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
        # Per-track LFO phase (radians).  Initialised to 0; advances each tick.
        self._lfo_phases: dict[str, float] = {}

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

    def _effective_fx(self, track) -> tuple[float, float, float, float]:
        """Return (volume, pan, filter_cutoff, filter_resonance) after LFO modulation."""
        lfo = track.lfo
        phase = self._lfo_phases.get(track.id, 0.0)
        lfo_val = compute_lfo_value(lfo, phase)
        dest = lfo.destination

        volume    = track.fx.volume
        pan       = track.fx.pan
        cutoff    = track.fx.filter_cutoff
        resonance = track.fx.filter_resonance

        if dest == "volume":
            volume = max(0.0, min(2.0, volume + lfo_val))
        elif dest == "pan":
            pan = max(-1.0, min(1.0, pan + lfo_val))
        elif dest == "filter_cutoff":
            cutoff = max(0.0, min(1.0, cutoff + lfo_val))
        elif dest == "filter_resonance":
            resonance = max(0.0, min(1.0, resonance + lfo_val))

        return volume, pan, cutoff, resonance

    def _run(self) -> None:
        next_tick = time.perf_counter()
        two_pi = 2.0 * math.pi
        tick_count = 0
        song_chain_pos = 0  # index into song_chain list

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
                pl = event.p_locks
                try:
                    raw_s = pl.get("start") if pl else None
                    raw_e = pl.get("end")   if pl else None
                    p_start = float(raw_s) if raw_s is not None else None
                    p_end   = float(raw_e) if raw_e is not None else None
                except (TypeError, ValueError):
                    p_start = p_end = None
                if p_start is not None or p_end is not None:
                    audio = player.get_with_offset(event.velocity, p_start, p_end)
                else:
                    audio = player.get(event.velocity)
                if audio is None:
                    continue
                track = self._seq.get_track(event.track_id)
                if track:
                    volume, pan, cutoff, resonance = self._effective_fx(track)
                    if cutoff < 0.999:
                        audio = apply_lowpass(audio, cutoff, resonance)
                    # Humanize: velocity variation + micro-timing jitter
                    h = getattr(track, "humanize", 0.0)
                    if h > 0.0:
                        vel_var = int(h * 20 * (2.0 * random.random() - 1.0))
                        volume = max(0.01, volume + h * 0.3 * (2.0 * random.random() - 1.0))
                        if h > 0.05:
                            time.sleep(random.random() * h * self._interval * 0.06)
                else:
                    volume, pan = 1.0, 0.0
                self._audio.play(audio, volume=volume, pan=pan)

            # Advance each track's LFO phase by one 16th-note worth of time.
            for track in list(self._seq._project.tracks):
                phase = self._lfo_phases.get(track.id, 0.0)
                self._lfo_phases[track.id] = (
                    phase + two_pi * track.lfo.rate * self._interval
                ) % two_pi

            # Song mode: advance chain after each 16-step bar if all tracks have
            # completed a full loop.  We advance when the global tick count is a
            # multiple of the longest track's step count.
            proj = self._seq._project
            if getattr(proj, "song_mode", False):
                chain = getattr(proj, "song_chain", [])
                if chain:
                    lengths = [len(t.steps) for t in proj.tracks if t.steps]
                    bar_len = max(lengths, default=16)
                    if tick_count > 0 and tick_count % bar_len == 0:
                        song_chain_pos = (song_chain_pos + 1) % len(chain)
                        slot = chain[song_chain_pos]
                        snapshot = proj.pattern_bank.slots.get(slot)
                        if snapshot:
                            for track in proj.tracks:
                                if track.id in snapshot:
                                    track.steps = list(snapshot[track.id])

            # Swing: shift odd-numbered 16th-notes late, even ones early so the
            # average tempo stays exactly correct.  swing=0 = straight;
            # swing=1 = full triplet swing (off-beat at 1.5× the on-beat gap).
            swing = getattr(self._seq._project, "swing", 0.0)
            swing_offset = swing * self._interval * 0.5
            if tick_count % 2 == 0:
                step_interval = self._interval - swing_offset  # on-beat: slightly early
            else:
                step_interval = self._interval + swing_offset  # off-beat: delayed

            tick_count += 1
            next_tick += step_interval
            sleep = next_tick - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
