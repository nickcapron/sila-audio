"""
Tests for AudioEngine internal state management.

All tests run without opening a real audio device — they exercise
the death-detection and flag logic directly.
"""

from sila.engine.audio import AudioEngine


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_stream_died_false_initially():
    assert AudioEngine().stream_died is False


def test_healthy_false_without_stream():
    # No stream opened → healthy must be False so callers don't assume OK.
    assert AudioEngine().healthy is False


# ---------------------------------------------------------------------------
# _on_stream_finished — the callback wired to PortAudio
# ---------------------------------------------------------------------------

def test_unexpected_finish_sets_stream_died():
    engine = AudioEngine()
    engine._on_stream_finished()
    assert engine.stream_died is True


def test_intentional_stop_does_not_set_stream_died():
    # stop() sets _stopping_intentionally before closing; callback must ignore it.
    engine = AudioEngine()
    engine._stopping_intentionally = True
    engine._on_stream_finished()
    assert engine.stream_died is False


# ---------------------------------------------------------------------------
# stop() sets the intentional flag before teardown
# ---------------------------------------------------------------------------

def test_stop_marks_intentional_before_closing():
    engine = AudioEngine()
    # stop() with no stream open should still set the flag so that
    # a stale finished_callback arriving after stop() is ignored.
    engine.stop()
    assert engine._stopping_intentionally is True


def test_stop_clears_voices():
    import numpy as np
    engine = AudioEngine()
    # Inject a fake voice directly so we can check it gets cleared.
    engine._voices.append(object())  # type: ignore[arg-type]
    engine.stop()
    assert engine._voices == []
