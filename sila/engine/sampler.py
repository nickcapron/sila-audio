"""
Sample engine — loads WAV/AIFF, manages velocity layers, round-robin.

SamplePlayer is per-track and stateful (tracks RR position).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from sila.engine.audio_loader import load_audio_mono_f32
from sila.models.project import SampleLayer
from sila.security import safe_path


class LoadedSample:
    """A decoded audio buffer for one SampleLayer (mono float32 at TARGET_SR)."""

    def __init__(self, layer: SampleLayer, audio: np.ndarray) -> None:
        self.layer = layer
        self.audio = audio  # already mono float32 at TARGET_SR

    def slice(self) -> np.ndarray:
        """Return the start–end region of the audio buffer."""
        n = len(self.audio)
        start = int(self.layer.start * n)
        end = int(self.layer.end * n)
        return self.audio[start:end]


class SamplePlayer:
    """Manages layers and round-robin state for one track."""

    def __init__(self) -> None:
        self._layers: list[LoadedSample] = []
        self._rr_counters: dict[int, int] = {}  # rr_group → counter

    def load(self, samples_dir: Path, layers: list[SampleLayer]) -> None:
        """Load all layers for a track. Call once after project load."""
        self._layers = []
        for layer in layers:
            src = safe_path(samples_dir, layer.path)
            if not src.exists():
                continue
            try:
                audio = load_audio_mono_f32(src)
            except Exception:
                continue
            self._layers.append(LoadedSample(layer, audio))

    def get_with_offset(
        self,
        velocity: int,
        start: float | None = None,
        end: float | None = None,
    ) -> np.ndarray | None:
        """Like get(), but override the start/end slice positions."""
        candidates = [
            s for s in self._layers
            if s.layer.velocity_min <= velocity <= s.layer.velocity_max
        ]
        if not candidates:
            return None
        group = candidates[0].layer.rr_group
        idx = self._rr_counters.get(group, 0) % len(candidates)
        self._rr_counters[group] = idx + 1
        sample = candidates[idx]
        n = len(sample.audio)
        s = int((start if start is not None else sample.layer.start) * n)
        e = int((end   if end   is not None else sample.layer.end)   * n)
        s = max(0, min(s, n - 1))
        e = max(s + 1, min(e, n))
        return sample.audio[s:e]

    def get(self, velocity: int) -> np.ndarray | None:
        """
        Return audio for the given velocity using velocity-layer selection
        and round-robin within the matching group.
        Returns None if no matching layer.
        """
        candidates = [
            s for s in self._layers
            if s.layer.velocity_min <= velocity <= s.layer.velocity_max
        ]
        if not candidates:
            return None

        # Group by rr_group and advance counter.
        group = candidates[0].layer.rr_group
        idx = self._rr_counters.get(group, 0) % len(candidates)
        self._rr_counters[group] = idx + 1

        return candidates[idx].slice()
