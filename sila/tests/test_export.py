"""
Tests for export/digitakt.py.

Uses synthetic WAV files so no real samples are needed.
"""

import tempfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from sila.export.digitakt import (
    MAX_DURATION_S,
    MAX_FILE_SIZE_B,
    TARGET_SR,
    ExportResult,
    ExportWarning,
    _collect_sample_paths,
    _unique_output_name,
    _validate_limits,
    export_for_digitakt,
    export_result_summary,
)
from sila.models.project import ProjectModel, SampleLayer, TrackModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav(path: Path, sr: int, channels: int, duration_s: float) -> None:
    """Write a synthetic sine-wave WAV at the given params."""
    n = int(sr * duration_s)
    t = np.linspace(0, duration_s, n, endpoint=False)
    data = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    if channels == 2:
        data = np.stack([data, data], axis=1)
    sf.write(str(path), data, sr, subtype="PCM_16")


def _make_project(*sample_paths: str) -> ProjectModel:
    tracks = []
    for p in sample_paths:
        track = TrackModel(name="T")
        track.samples = [SampleLayer(path=p)]
        tracks.append(track)
    return ProjectModel(tracks=tracks)


# ---------------------------------------------------------------------------
# _collect_sample_paths
# ---------------------------------------------------------------------------

def test_collect_deduplicates():
    proj = _make_project("a.wav", "b.wav", "a.wav")
    paths = _collect_sample_paths(proj)
    assert paths.count("a.wav") == 1
    assert len(paths) == 2


def test_collect_empty_project():
    proj = ProjectModel()
    assert _collect_sample_paths(proj) == []


# ---------------------------------------------------------------------------
# _validate_limits
# ---------------------------------------------------------------------------

def test_validate_ok():
    # 1 second of audio at target SR → well within limits
    audio = np.zeros(TARGET_SR)
    warnings = _validate_limits("test.wav", audio, TARGET_SR)
    assert warnings == []


def test_validate_duration_warning():
    # 34 seconds — over the 33 s limit
    audio = np.zeros(TARGET_SR * 34)
    warnings = _validate_limits("long.wav", audio, TARGET_SR)
    assert any(w.reason == "exceeds_duration" for w in warnings)
    assert any(w.value > MAX_DURATION_S for w in warnings)


def test_validate_size_warning():
    # Craft an array that would produce > 170 MB output.
    n_samples = (MAX_FILE_SIZE_B // 2) + 1
    audio = np.zeros(n_samples)
    warnings = _validate_limits("huge.wav", audio, TARGET_SR)
    assert any(w.reason == "exceeds_size" for w in warnings)


def test_validate_both_violations():
    n_samples = (MAX_FILE_SIZE_B // 2) + 1
    audio = np.zeros(n_samples)
    warnings = _validate_limits("huge_long.wav", audio, TARGET_SR)
    reasons = {w.reason for w in warnings}
    assert "exceeds_duration" in reasons
    assert "exceeds_size" in reasons


# ---------------------------------------------------------------------------
# _unique_output_name
# ---------------------------------------------------------------------------

def test_unique_name_no_collision():
    used: set[str] = set()
    name = _unique_output_name("kick", used)
    assert name == "kick.wav"


def test_unique_name_collision_resolved():
    used = {"kick.wav"}
    name = _unique_output_name("kick", used)
    assert name != "kick.wav"
    assert name.endswith(".wav")


def test_unique_name_sanitizes():
    used: set[str] = set()
    name = _unique_output_name("my kick drum!!!", used)
    assert len(name.replace(".wav", "")) <= 16
    assert "!" not in name


# ---------------------------------------------------------------------------
# export_for_digitakt — integration
# ---------------------------------------------------------------------------

def test_export_basic():
    with tempfile.TemporaryDirectory() as tmp:
        samples_dir = Path(tmp) / "samples"
        samples_dir.mkdir()
        out_dir = Path(tmp) / "out"

        _make_wav(samples_dir / "kick.wav", 44100, 1, 0.5)
        proj = _make_project("kick.wav")

        result = export_for_digitakt(proj, samples_dir, out_dir)

        assert result.exported == 1
        assert result.skipped == 0
        assert len(result.warnings) == 0
        assert (out_dir / "kick.wav").exists()

        # Verify output is 48000 Hz mono 16-bit
        info = sf.info(str(out_dir / "kick.wav"))
        assert info.samplerate == TARGET_SR
        assert info.channels == 1
        assert info.subtype == "PCM_16"


def test_export_stereo_transcoded_to_mono():
    with tempfile.TemporaryDirectory() as tmp:
        samples_dir = Path(tmp) / "samples"
        samples_dir.mkdir()
        out_dir = Path(tmp) / "out"

        _make_wav(samples_dir / "stereo.wav", 44100, 2, 0.5)
        proj = _make_project("stereo.wav")

        result = export_for_digitakt(proj, samples_dir, out_dir)
        assert result.exported == 1
        info = sf.info(str(out_dir / "stereo.wav"))
        assert info.channels == 1


def test_export_missing_sample_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        samples_dir = Path(tmp) / "samples"
        samples_dir.mkdir()
        out_dir = Path(tmp) / "out"

        proj = _make_project("ghost.wav")  # file does not exist
        result = export_for_digitakt(proj, samples_dir, out_dir)

        assert result.exported == 0
        assert result.skipped == 1


def test_export_path_traversal_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        samples_dir = Path(tmp) / "samples"
        samples_dir.mkdir()
        out_dir = Path(tmp) / "out"

        proj = _make_project("../../etc/passwd")
        result = export_for_digitakt(proj, samples_dir, out_dir)

        assert result.exported == 0
        assert result.skipped == 1


def test_export_warns_over_duration_but_still_exports():
    with tempfile.TemporaryDirectory() as tmp:
        samples_dir = Path(tmp) / "samples"
        samples_dir.mkdir()
        out_dir = Path(tmp) / "out"

        # 34-second file — over limit but should export with warning
        _make_wav(samples_dir / "long.wav", 48000, 1, 34.0)
        proj = _make_project("long.wav")

        result = export_for_digitakt(proj, samples_dir, out_dir)

        assert result.exported == 1
        assert any(w.reason == "exceeds_duration" for w in result.warnings)


def test_export_deduplicates_same_sample():
    with tempfile.TemporaryDirectory() as tmp:
        samples_dir = Path(tmp) / "samples"
        samples_dir.mkdir()
        out_dir = Path(tmp) / "out"

        _make_wav(samples_dir / "shared.wav", 44100, 1, 0.5)

        # Two tracks use the same sample
        track1 = TrackModel(name="T1")
        track1.samples = [SampleLayer(path="shared.wav")]
        track2 = TrackModel(name="T2")
        track2.samples = [SampleLayer(path="shared.wav")]
        proj = ProjectModel(tracks=[track1, track2])

        result = export_for_digitakt(proj, samples_dir, out_dir)
        assert result.exported == 1  # deduplicated


def test_export_renames_non_ascii():
    with tempfile.TemporaryDirectory() as tmp:
        samples_dir = Path(tmp) / "samples"
        samples_dir.mkdir()
        out_dir = Path(tmp) / "out"

        _make_wav(samples_dir / "snäre.wav", 44100, 1, 0.5)
        proj = _make_project("snäre.wav")

        result = export_for_digitakt(proj, samples_dir, out_dir)
        assert result.exported == 1
        assert result.renamed == 1


# ---------------------------------------------------------------------------
# export_result_summary
# ---------------------------------------------------------------------------

def test_summary_no_warnings():
    r = ExportResult(exported=3, renamed=1)
    s = export_result_summary(r)
    assert "3 file(s) exported" in s
    assert "1 renamed" in s
    assert "0 warning" in s


def test_summary_with_duration_warning():
    r = ExportResult(exported=1, renamed=0)
    r.warnings.append(ExportWarning("long.wav", "exceeds_duration", 35.2))
    s = export_result_summary(r)
    assert "35.2s" in s
    assert "33s limit" in s


def test_summary_with_size_warning():
    r = ExportResult(exported=1, renamed=0)
    r.warnings.append(ExportWarning("huge.wav", "exceeds_size", 180 * 1024 * 1024))
    s = export_result_summary(r)
    assert "MB" in s
    assert "170 MB limit" in s
