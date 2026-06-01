"""
Step sequencer engine.

Each track maintains its own step counter independently (polyrhythm).
The sequencer is clock-driven: external callers call tick() once per
smallest subdivision. BPM/subdivision → tick rate is handled by the
FastAPI layer that owns the clock.

No global mutable state: all state lives on the Sequencer instance.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from sila.models.project import ProjectModel, TrackModel
from sila.models.step import Step, TrigCondition


@dataclass
class TrigEvent:
    """Emitted when a step fires."""
    track_id: str
    step_index: int
    velocity: int
    pitch_offset: int
    p_locks: dict
    length: float = 1.0  # step note-length multiplier


class Sequencer:
    """
    Drives all tracks from a loaded project.

    Usage:
        seq = Sequencer(project)
        seq.on_trig = lambda event: audio_engine.play(event)
        seq.tick()  # call at the BPM-derived interval
    """

    def __init__(self, project: ProjectModel) -> None:
        self._project = project
        # Per-track step counters, keyed by track.id.
        self._counters: dict[str, int] = {t.id: 0 for t in project.tracks}
        self.on_trig: Callable[[TrigEvent], None] | None = None
        self._fill_active: bool = project.fill_active

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def fill_active(self) -> bool:
        return self._fill_active

    @fill_active.setter
    def fill_active(self, value: bool) -> None:
        self._fill_active = value
        self._project.fill_active = value

    def tick(self) -> list[TrigEvent]:
        """
        Advance all unmuted tracks by one step. Returns events that fired.
        Calls self.on_trig for each event if a callback is registered.
        """
        tracks = list(self._project.tracks)
        any_solo = any(t.solo for t in tracks)
        events: list[TrigEvent] = []
        for track in tracks:  # snapshot: add/remove mid-tick is safe
            if track.muted:
                continue
            if any_solo and not track.solo:
                continue  # silenced by another track's solo
            event = self._evaluate_track(track)
            if event is not None:
                events.append(event)
                if self.on_trig:
                    self.on_trig(event)
            self._advance(track)
        return events

    def reset(self) -> None:
        """Reset all track counters to step 0."""
        for track in self._project.tracks:
            self._counters[track.id] = 0

    def reset_track(self, track_id: str) -> None:
        self._counters[track_id] = 0

    def add_track(self, track: TrackModel) -> None:
        track.ensure_steps()
        self._project.tracks.append(track)
        self._counters[track.id] = 0

    def remove_track(self, track_id: str) -> None:
        self._project.tracks = [t for t in self._project.tracks if t.id != track_id]
        self._counters.pop(track_id, None)

    def get_track(self, track_id: str) -> TrackModel | None:
        for t in self._project.tracks:
            if t.id == track_id:
                return t
        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evaluate_track(self, track: TrackModel) -> TrigEvent | None:
        idx = self._counters[track.id] % max(len(track.steps), 1)
        if not track.steps:
            return None
        step = track.steps[idx]

        if not step.active:
            return None
        if not self._trig_condition_passes(step):
            return None
        if not self._probability_passes(step.probability):
            return None

        return TrigEvent(
            track_id=track.id,
            step_index=idx,
            velocity=step.velocity,
            pitch_offset=step.pitch_offset,
            p_locks=dict(step.p_locks),
            length=step.length,
        )

    def _trig_condition_passes(self, step: Step) -> bool:
        tc = step.trig_condition
        if tc == TrigCondition.ALWAYS:
            return True
        elif tc == TrigCondition.FILL:
            return self._fill_active
        elif tc == TrigCondition.NOT_FILL:
            return not self._fill_active
        elif tc == TrigCondition.ONE_IN_2:
            return random.random() < 0.5
        elif tc == TrigCondition.ONE_IN_4:
            return random.random() < 0.25
        return False

    @staticmethod
    def _probability_passes(probability: int) -> bool:
        if probability >= 100:
            return True
        if probability <= 0:
            return False
        return random.randint(1, 100) <= probability

    def _advance(self, track: TrackModel) -> None:
        step_count = max(len(track.steps), 1)
        self._counters[track.id] = (self._counters[track.id] + 1) % step_count
