"""Unit tests for the Step model and micro-timing offset formula."""

import pytest
from pydantic import ValidationError

from sila.models.step import Step


# ---------------------------------------------------------------------------
# micro_timing field — valid range
# ---------------------------------------------------------------------------

def test_micro_timing_default_is_zero():
    assert Step().micro_timing == 0


def test_micro_timing_accepts_boundary_values():
    assert Step(micro_timing=23).micro_timing == 23
    assert Step(micro_timing=-23).micro_timing == -23
    assert Step(micro_timing=0).micro_timing == 0


def test_micro_timing_rejects_out_of_range():
    with pytest.raises(ValidationError):
        Step(micro_timing=24)
    with pytest.raises(ValidationError):
        Step(micro_timing=-24)


def test_existing_step_fields_unaffected_by_micro_timing_addition():
    """Existing projects that don't include micro_timing load with default 0."""
    step = Step(active=True, velocity=80, pitch_offset=3,
                probability=75, trig_condition="1:2", length=2.0)
    assert step.micro_timing == 0


# ---------------------------------------------------------------------------
# Offset formula: (offset / 96) × (60 / bpm) × 4
# ---------------------------------------------------------------------------

def _offset_seconds(micro_steps: int, bpm: float) -> float:
    return (micro_steps / 96) * (60.0 / bpm) * 4.0


def test_offset_zero_produces_no_delay():
    assert _offset_seconds(0, 120) == 0.0


def test_offset_formula_at_120_bpm():
    # 1 micro-step at 120 BPM: (1/96) × (60/120) × 4 = 1/96 × 2 ≈ 20.83 ms
    result = _offset_seconds(1, 120)
    assert abs(result - (1 / 96 * 2)) < 1e-10


def test_offset_formula_at_140_bpm():
    result = _offset_seconds(6, 140)
    expected = (6 / 96) * (60.0 / 140) * 4.0
    assert abs(result - expected) < 1e-10


def test_offset_formula_scales_linearly_with_micro_steps():
    bpm = 120.0
    assert abs(_offset_seconds(10, bpm) - 10 * _offset_seconds(1, bpm)) < 1e-10


def test_offset_formula_equivalent_expressed_via_interval():
    # The clock stores interval = 60/bpm/4.  The formula can be written as
    # offset * interval / 6, which is what the clock uses internally.
    bpm = 120.0
    interval = 60.0 / bpm / 4.0
    for offset in (-23, -1, 0, 1, 6, 23):
        via_bpm      = _offset_seconds(offset, bpm)
        via_interval = offset * interval / 6.0
        assert abs(via_bpm - via_interval) < 1e-10, (
            f"offset={offset}: formula mismatch {via_bpm} vs {via_interval}"
        )


def test_max_positive_offset_at_60_bpm():
    # At 60 BPM (slowest reasonable tempo) the max offset should be < 1 beat.
    result = _offset_seconds(23, 60)
    beat_duration = 60.0 / 60.0  # 1 second
    assert result < beat_duration


def test_negative_offset_is_negative_seconds():
    assert _offset_seconds(-5, 120) < 0
