"""Shared audio loading — mono float32 at a target sample rate.

Both the sampler (playback) and the Digitakt exporter load audio the same way:
read any WAV/AIFF, sum to mono, resample to 48 kHz.  This module owns that
pipeline so there is exactly one implementation.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

TARGET_SR = 48_000


def load_audio_mono_f32(path: Path, target_sr: int = TARGET_SR) -> np.ndarray:
    """
    Load an audio file, sum to mono, and resample to *target_sr*.

    Returns a contiguous 1-D float32 array at *target_sr*.
    Raises whatever soundfile / soxr raises on bad input.
    """
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono: np.ndarray = data[:, 0] if data.shape[1] == 1 else data.mean(axis=1)
    if sr != target_sr:
        mono = soxr.resample(mono, sr, target_sr, quality="HQ")
    return np.ascontiguousarray(mono, dtype=np.float32)
