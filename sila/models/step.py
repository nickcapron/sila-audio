"""Step model — a single cell in a track's step grid."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TrigCondition(StrEnum):
    ALWAYS = "always"
    ONE_IN_2 = "1:2"
    ONE_IN_4 = "1:4"
    FILL = "fill"
    NOT_FILL = "not_fill"


class StepLength(float):
    """Step note-length as a multiplier of the 16th-note interval."""
    HALF   = 0.5   # 1/32
    NORMAL = 1.0   # 1/16
    DOUBLE = 2.0   # 1/8
    TRIPLE = 3.0   # dotted 1/16


class Step(BaseModel):
    active: bool = False
    velocity: int = Field(default=100, ge=0, le=127)
    # Semitone offset from track base pitch.
    pitch_offset: int = Field(default=0, ge=-24, le=24)
    # 0–100 percent chance the step fires.
    probability: int = Field(default=100, ge=0, le=100)
    trig_condition: TrigCondition = TrigCondition.ALWAYS
    # Note-length multiplier: 0.5=half, 1.0=normal, 2.0=double, 3.0=triple
    length: float = Field(default=1.0, gt=0, le=8.0)
    # Parameter locks: any param name → value. Validated at engine level.
    p_locks: dict[str, Any] = Field(default_factory=dict)
