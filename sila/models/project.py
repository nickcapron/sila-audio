"""Project and Track Pydantic models — source of truth for disk format."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from sila.models.step import Step
from sila.security import sanitize_notes


class LFOModel(BaseModel):
    shape: Literal["sine", "triangle", "square", "sawtooth", "random"] = "sine"
    # Rate in Hz when sync is off; or note-division string like "1/8" when synced.
    rate: float = Field(default=1.0, gt=0)
    depth: float = Field(default=0.5, ge=0.0, le=1.0)
    destination: str = "volume"  # any parameter name on the track


class FXModel(BaseModel):
    filter_cutoff: float = Field(default=1.0, ge=0.0, le=1.0)
    filter_resonance: float = Field(default=0.0, ge=0.0, le=1.0)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)


class SampleLayer(BaseModel):
    """One sample file with its playback settings."""
    path: str  # relative to project samples/ dir; validated by safe_path at load time
    velocity_min: int = Field(default=0, ge=0, le=127)
    velocity_max: int = Field(default=127, ge=0, le=127)
    start: float = Field(default=0.0, ge=0.0, le=1.0)  # 0–1 fraction of file length
    end: float = Field(default=1.0, ge=0.0, le=1.0)
    loop: bool = False
    # round-robin slot: engine cycles through layers sharing the same velocity range
    rr_group: int = Field(default=0, ge=0)


class TrackModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = "Track"
    # Intent annotation — stored and displayed; never executed.
    notes: str = ""
    muted: bool = False
    solo: bool = False

    step_count: int = Field(default=16, ge=1, le=256)
    steps: list[Step] = Field(default_factory=list)

    samples: list[SampleLayer] = Field(default_factory=list)
    lfo: LFOModel = Field(default_factory=LFOModel)
    fx: FXModel = Field(default_factory=FXModel)

    @field_validator("notes", mode="before")
    @classmethod
    def _sanitize(cls, v: str) -> str:
        return sanitize_notes(str(v)) if v else ""

    @field_validator("steps", mode="before")
    @classmethod
    def _pad_steps(cls, v: list, info: object) -> list:
        # Filled in by engine after step_count is known; tolerate mismatches.
        return v

    def ensure_steps(self) -> None:
        """Pad or trim steps list to match step_count."""
        current = len(self.steps)
        if current < self.step_count:
            self.steps.extend([Step() for _ in range(self.step_count - current)])
        elif current > self.step_count:
            self.steps = self.steps[: self.step_count]


class ProjectModel(BaseModel):
    version: int = 1
    name: str = "Untitled"
    bpm: float = Field(default=120.0, gt=0, le=300)
    swing: float = Field(default=0.0, ge=0.0, le=1.0)
    tracks: list[TrackModel] = Field(default_factory=list)

    # fill_active drives "fill" trig conditions during playback.
    fill_active: bool = False
