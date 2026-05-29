"""
Phase 2 — Digitakt export pipeline.

Reads all samples referenced in a project, transcodes each to
48 kHz / 16-bit / mono PCM WAV (Elektron Transfer ready), sanitizes
filenames, validates per-file limits, and writes to a flat output folder.

Limits (Digitakt hardware spec):
  - Max duration: 33 seconds
  - Max file size: 170 MB  (170 * 1024 * 1024 bytes)
  - Max filename: 16 chars (enforced by sanitize_filename)
  - Mono, 48000 Hz, 16-bit PCM
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from sila.engine.audio_loader import load_audio_mono_f32
from sila.models.project import ProjectModel, SampleLayer
from sila.security import safe_path, sanitize_filename

log = logging.getLogger(__name__)

TARGET_SR = 48_000
TARGET_SUBTYPE = "PCM_16"
MAX_DURATION_S = 33.0
MAX_FILE_SIZE_B = 170 * 1024 * 1024  # 170 MB


@dataclass
class ExportWarning:
    original_path: str
    reason: str  # "exceeds_duration" | "exceeds_size"
    value: float  # actual duration or size


@dataclass
class ExportResult:
    exported: int = 0
    renamed: int = 0  # count of files whose name was sanitized / changed
    warnings: list[ExportWarning] = field(default_factory=list)
    skipped: int = 0  # files skipped due to limit violations — reported, not silently dropped


def _collect_sample_paths(project: ProjectModel) -> list[str]:
    """Return deduplicated list of sample paths from all tracks."""
    seen: set[str] = set()
    paths: list[str] = []
    for track in project.tracks:
        for layer in track.samples:
            if layer.path not in seen:
                seen.add(layer.path)
                paths.append(layer.path)
    return paths


def _load_as_mono_float(src_path: Path) -> tuple[np.ndarray, int]:
    """Load any WAV/AIFF into a float32 mono array, returning (audio, original_sr)."""
    data, sr = sf.read(str(src_path), dtype="float32", always_2d=True)
    mono = data[:, 0] if data.shape[1] == 1 else data.mean(axis=1)
    return mono, sr


def _resample_if_needed(mono: np.ndarray, src_sr: int) -> np.ndarray:
    if src_sr == TARGET_SR:
        return mono
    return soxr.resample(mono, src_sr, TARGET_SR, quality="HQ")


def _validate_limits(
    src_path: str,
    audio: np.ndarray,
    sr: int,
) -> list[ExportWarning]:
    """Return any limit violations; empty list means the file is clean."""
    warnings: list[ExportWarning] = []
    duration = len(audio) / sr
    if duration > MAX_DURATION_S:
        warnings.append(
            ExportWarning(
                original_path=src_path,
                reason="exceeds_duration",
                value=duration,
            )
        )
    # Estimate output size: samples × 2 bytes (16-bit) + 44-byte WAV header.
    estimated_size = len(audio) * 2 + 44
    if estimated_size > MAX_FILE_SIZE_B:
        warnings.append(
            ExportWarning(
                original_path=src_path,
                reason="exceeds_size",
                value=float(estimated_size),
            )
        )
    return warnings


def _unique_output_name(
    base_name: str,
    used_names: set[str],
    suffix: str = ".wav",
) -> str:
    """Ensure no collision in the flat output directory."""
    stem = sanitize_filename(base_name)
    candidate = stem + suffix
    if candidate not in used_names:
        return candidate
    # Append numeric suffix within 16-char limit.
    for i in range(1, 1000):
        short_stem = stem[: max(1, 14 - len(str(i)))] + str(i)
        candidate = short_stem + suffix
        if candidate not in used_names:
            return candidate
    raise RuntimeError(f"Could not find a unique name for {base_name!r}")


def export_for_digitakt(
    project: ProjectModel,
    samples_dir: Path,
    output_dir: Path,
) -> ExportResult:
    """
    Main export entry point.

    Args:
        project:     The loaded ProjectModel.
        samples_dir: Absolute path to the project's samples/ directory.
        output_dir:  User-chosen output folder (will be created if absent).

    Returns:
        ExportResult with counts and any per-file warnings.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result = ExportResult()
    used_names: set[str] = set()

    sample_paths = _collect_sample_paths(project)

    for rel_path in sample_paths:
        try:
            src = safe_path(samples_dir, rel_path)
        except ValueError:
            log.warning("Blocked path traversal attempt in sample path: %r", rel_path)
            result.skipped += 1
            continue

        if not src.exists():
            log.warning("Sample not found, skipping: %r", str(src))
            result.skipped += 1
            continue

        try:
            audio = load_audio_mono_f32(src)
        except Exception as exc:
            log.warning("Failed to load %r: %s", str(src), exc)
            result.skipped += 1
            continue

        violations = _validate_limits(rel_path, audio, TARGET_SR)
        if violations:
            result.warnings.extend(violations)
            # Still export — user is warned, not silently blocked.

        out_name = _unique_output_name(src.stem, used_names)
        used_names.add(out_name)

        if out_name != src.name:
            result.renamed += 1

        out_path = output_dir / out_name
        sf.write(
            str(out_path),
            audio,
            TARGET_SR,
            subtype=TARGET_SUBTYPE,
            format="WAV",
        )
        result.exported += 1

    return result


def export_result_summary(result: ExportResult) -> str:
    """Human-readable summary string for the UI."""
    parts = [
        f"{result.exported} file(s) exported",
        f"{result.renamed} renamed",
        f"{len(result.warnings)} warning(s)",
    ]
    if result.skipped:
        parts.append(f"{result.skipped} skipped (missing/invalid)")
    summary = ", ".join(parts) + "."
    for w in result.warnings:
        if w.reason == "exceeds_duration":
            summary += f"\n  WARNING {w.original_path!r}: {w.value:.1f}s exceeds 33s limit"
        elif w.reason == "exceeds_size":
            mb = w.value / (1024 * 1024)
            summary += f"\n  WARNING {w.original_path!r}: {mb:.1f} MB exceeds 170 MB limit"
    return summary
