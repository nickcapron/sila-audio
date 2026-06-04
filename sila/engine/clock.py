"""
Playback clock — drives the sequencer at BPM-derived 16th-note intervals
and routes TrigEvents through the sampler into the audio engine.
"""
from __future__ import annotations

import logging
import math
import random
import threading
import time

log = logging.getLogger(__name__)

import numpy as np

from sila.engine.audio import AudioEngine
from sila.engine.fx import apply_lowpass
from sila.engine.lfo import compute_lfo_value
from sila.engine.sampler import SamplePlayer
from sila.engine.sequencer import Sequencer


def _make_click(freq: float = 1000.0, amp: float = 0.3, sr: int = 48_000) -> np.ndarray:
    """Generate a short percussive click — exponentially decaying sine burst."""
    dur = 0.012  # 12 ms
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    click = amp * np.sin(2.0 * np.pi * freq * t) * np.exp(-t * 400.0)
    return click.astype(np.float32)


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
        self.metronome: bool = False
        self._click_beat1 = _make_click(freq=1200.0, amp=0.35)
        self._click_beat3 = _make_click(freq=900.0,  amp=0.22)
        # Song mode chain position — persists across stop/play so playback resumes
        # at the correct slot instead of always restarting from slot 0.
        self._song_chain_pos: int = 0

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

    def reset_song_pos(self) -> None:
        """Reset the song-chain position to slot 0.

        Call this when the chain is replaced or song mode is first activated
        so the next bar starts from the beginning of the chain.
        """
        self._song_chain_pos = 0

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

    def _fire_event(self, event, base_delay_frames: int = 0) -> None:
        """Resolve a TrigEvent to audio and hand it to the audio engine."""
        player = self._players.get(event.track_id)
        if player is None:
            log.debug("dropped trig — no player for track %s (step %d)",
                      event.track_id, event.step_index)
            return
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
            log.debug("dropped trig — player returned no audio for track %s (step %d, vel %d)",
                      event.track_id, event.step_index, event.velocity)
            return
        track = self._seq.get_track(event.track_id)
        max_frames = None
        humanize_delay = 0
        if track:
            volume, pan, cutoff, resonance = self._effective_fx(track)
            if cutoff < 0.999:
                audio = apply_lowpass(audio, cutoff, resonance)
            step_len = event.length
            if step_len != 1.0 and step_len < 4.0:
                max_samp = int(step_len * self._interval * 48_000)
                if 0 < max_samp < len(audio):
                    max_frames = max_samp
            h = getattr(track, "humanize", 0.0)
            if h > 0.0:
                volume = max(0.01, volume + h * 0.3 * (2.0 * random.random() - 1.0))
                if h > 0.05:
                    humanize_delay = int(random.random() * h * self._interval * 0.06 * 48_000)
        else:
            volume, pan = 1.0, 0.0
        total_delay = base_delay_frames + humanize_delay
        self._audio.play(audio, volume=volume, pan=pan, delay_frames=total_delay, max_frames=max_frames)

    def _run(self) -> None:
        next_tick = time.perf_counter()
        two_pi = 2.0 * math.pi
        tick_count = 0

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

            # Song mode: swap pattern before the sequencer evaluates this tick so
            # the new pattern's step 0 plays on the bar boundary, not the old one.
            proj = self._seq._project
            if getattr(proj, "song_mode", False):
                chain = getattr(proj, "song_chain", [])
                if chain:
                    lengths = [len(t.steps) for t in proj.tracks if t.steps]
                    bar_len = max(lengths, default=16)
                    if tick_count > 0 and tick_count % bar_len == 0:
                        self._song_chain_pos = (self._song_chain_pos + 1) % len(chain)
                        slot = chain[self._song_chain_pos]
                        snapshot = proj.pattern_bank.slots.get(slot)
                        if snapshot:
                            for track in proj.tracks:
                                if track.id in snapshot:
                                    track.steps = list(snapshot[track.id])

            # Metronome: beat 1 (tick 0 mod 16) and beat 3 (tick 8 mod 16)
            if self.metronome:
                beat = tick_count % 16
                if beat == 0:
                    self._audio.play(self._click_beat1)
                elif beat == 8:
                    self._audio.play(self._click_beat3)

            for event in self._seq.tick():
                mt_offset = event.micro_timing * self._interval / 6.0
                delay_frames = int(mt_offset * 48_000) if mt_offset > 0 else 0
                self._fire_event(event, base_delay_frames=delay_frames)

            # Advance each track's LFO phase by one 16th-note worth of time.
            for track in list(self._seq._project.tracks):
                phase = self._lfo_phases.get(track.id, 0.0)
                self._lfo_phases[track.id] = (
                    phase + two_pi * track.lfo.rate * self._interval
                ) % two_pi

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
