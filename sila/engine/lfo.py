"""Per-track LFO processor."""

from __future__ import annotations

import math
import random

from sila.models.project import LFOModel


def _random_phase_value(phase: float) -> float:
    """Simple stepped random — changes value each cycle."""
    return 1.0 if (phase % (2 * math.pi)) < math.pi else -1.0


def compute_lfo_value(lfo: LFOModel, phase: float) -> float:
    """
    Return LFO output in [-1, 1] for the given phase (radians).
    phase advances by 2π * rate / sample_rate each sample.
    """
    shape = lfo.shape
    if shape == "sine":
        raw = math.sin(phase)
    elif shape == "triangle":
        raw = 2 * abs((phase / math.pi % 2) - 1) - 1
    elif shape == "square":
        raw = 1.0 if math.sin(phase) >= 0 else -1.0
    elif shape == "sawtooth":
        raw = (phase / math.pi % 2) - 1
    elif shape == "random":
        raw = _random_phase_value(phase)
    else:
        raw = 0.0
    return raw * lfo.depth
