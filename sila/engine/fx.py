"""Per-track FX processing (filter, volume, pan)."""
from __future__ import annotations

import math

import numpy as np

from sila.models.project import FXModel

try:
    from scipy.signal import lfilter as _lfilter
    _SCIPY = True
except ImportError:
    _SCIPY = False


def apply_lowpass(
    audio: np.ndarray,
    cutoff_norm: float,
    resonance_norm: float = 0.0,
    sr: int = 48_000,
) -> np.ndarray:
    """
    Apply a lowpass filter to a mono float32 audio buffer.

    cutoff_norm  : 0.0 = 20 Hz (near silence) … 1.0 = pass-through
    resonance_norm: 0.0 = no resonance (Q=0.5) … 1.0 = high resonance (Q=20)

    With scipy: biquad IIR (Audio EQ Cookbook lowpass).
    Without scipy: windowed-sinc FIR (no resonance, still musically useful).
    """
    if cutoff_norm >= 0.999 or len(audio) < 4:
        return audio

    # Exponential frequency mapping: 20 Hz → 20 kHz
    fc_hz = max(20.0, min(20.0 * (1000.0 ** cutoff_norm), sr * 0.499))

    if _SCIPY:
        Q = 0.5 + resonance_norm * 19.5
        w0 = 2.0 * math.pi * fc_hz / sr
        sin_w0 = math.sin(w0)
        cos_w0 = math.cos(w0)
        alpha = sin_w0 / (2.0 * Q)
        b0 = (1.0 - cos_w0) * 0.5
        b1 = 1.0 - cos_w0
        b2 = b0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha
        b = np.array([b0 / a0, b1 / a0, b2 / a0])
        a = np.array([1.0, a1 / a0, a2 / a0])
        return _lfilter(b, a, audio.astype(np.float64)).astype(np.float32)
    else:
        # Windowed-sinc FIR fallback (no Q, but fast and correct cutoff shape)
        fc = fc_hz / sr
        M = 31
        n = np.arange(M, dtype=np.float64) - M // 2
        # Avoid division by zero at centre tap
        with np.errstate(invalid="ignore", divide="ignore"):
            h = np.where(n == 0, 2.0 * fc, np.sin(2.0 * math.pi * fc * n) / (math.pi * n))
        h *= np.hamming(M)
        h /= h.sum()
        return np.convolve(audio.astype(np.float64), h, mode="same").astype(np.float32)


def apply_fx(audio: np.ndarray, fx: FXModel, sample_rate: int = 48_000) -> np.ndarray:
    """
    Apply volume, pan, and lowpass filter to a mono audio buffer.
    Returns a (N, 2) stereo array.
    """
    # Filter first, then volume/pan so the level matches the dry signal.
    audio = apply_lowpass(audio, fx.filter_cutoff, fx.filter_resonance, sample_rate)
    audio = audio * fx.volume
    angle = (fx.pan + 1) * 0.5 * (np.pi / 2)
    left = np.cos(angle) * audio
    right = np.sin(angle) * audio
    return np.stack([left, right], axis=1)


def apply_volume(audio: np.ndarray, volume: float) -> np.ndarray:
    return audio * max(0.0, volume)
