"""Per-track FX processing (filter, volume, pan)."""

from __future__ import annotations

import numpy as np

from vdigitakt.models.project import FXModel


def apply_fx(audio: np.ndarray, fx: FXModel, sample_rate: int = 48000) -> np.ndarray:
    """
    Apply volume and pan to a mono audio buffer.
    Returns a (N, 2) stereo array.
    Filter is placeholder — full biquad implementation comes in Phase 1 polish.
    """
    audio = audio * fx.volume
    # Pan law: constant-power panning
    angle = (fx.pan + 1) * 0.5 * (np.pi / 2)  # 0 → π/2
    left = np.cos(angle) * audio
    right = np.sin(angle) * audio
    return np.stack([left, right], axis=1)


def apply_volume(audio: np.ndarray, volume: float) -> np.ndarray:
    return audio * max(0.0, volume)
