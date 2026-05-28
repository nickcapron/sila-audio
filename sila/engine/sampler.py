"""
Sample engine — loads WAV/AIFF, manages velocity layers, round-robin.

SamplePlayer is per-track and stateful (tracks RR position).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from sila.models.project import SampleLayer, TrackModel
from sila.security import safe_path

TARGET_SR = 48_000


class LoadedSample:
    """A decoded audio buffer for one SampleLayer."""

    def __init__(self, layer: SampleLayer, audio: np.ndarray, sr: int) -> None:
        self.layer = layer
        self.sr = sr
        # Resample to target SR if needed.
        if sr != TARGET_SR:
            audio = soxr.resample(audio, sr, TARGET_SR, quality="HQ")
        self.audio = audio  # float32 mono, target SR

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
            data, sr = sf.read(str(src), dtype="float32", always_2d=True)
            mono = data[:, 0] if data.shape[1] == 1 else data.mean(axis=1)
            self._layers.append(LoadedSample(layer, mono, sr))

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
