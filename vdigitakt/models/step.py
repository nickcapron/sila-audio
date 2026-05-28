"""Step model — a single cell in a track's step grid."""

from typing import Any
from pydantic import BaseModel, Field, field_validator


class TrigCondition(str):
    """Valid trig condition tokens."""
    ALWAYS = "always"
    ONE_IN_2 = "1:2"
    ONE_IN_4 = "1:4"
    FILL = "fill"
    NOT_FILL = "not_fill"


VALID_TRIG_CONDITIONS = {
    TrigCondition.ALWAYS,
    TrigCondition.ONE_IN_2,
    TrigCondition.ONE_IN_4,
    TrigCondition.FILL,
    TrigCondition.NOT_FILL,
}


class Step(BaseModel):
    active: bool = False
    velocity: int = Field(default=100, ge=0, le=127)
    # Semitone offset from track base pitch.
    pitch_offset: int = Field(default=0, ge=-24, le=24)
    # 0–100 percent chance the step fires.
    probability: int = Field(default=100, ge=0, le=100)
    trig_condition: str = Field(default=TrigCondition.ALWAYS)
    # Parameter locks: any param name → value. Validated at engine level.
    p_locks: dict[str, Any] = Field(default_factory=dict)

    @field_validator("trig_condition")
    @classmethod
    def _valid_trig(cls, v: str) -> str:
        if v not in VALID_TRIG_CONDITIONS:
            raise ValueError(
                f"trig_condition must be one of {VALID_TRIG_CONDITIONS}, got {v!r}"
            )
        return v
