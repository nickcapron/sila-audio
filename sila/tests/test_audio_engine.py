"""
Tests for AudioEngine internal state management.

All tests run without opening a real audio device — they exercise
the death-detection and flag logic directly.
"""

from unittest.mock import patch, call

from sila.engine.audio import AudioEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _limit_watcher(engine, ticks):
    """Patch _watcher_stop.wait so _device_watcher exits after *ticks* loop iterations.

    Returns a list whose sole element is the total number of wait() calls made,
    which lets tests distinguish 'continue' (all ticks run) from 'break' (exits early).
    """
    call_count = [0]
    def fake_wait(timeout=None):
        call_count[0] += 1
        return call_count[0] > ticks
    engine._watcher_stop.wait = fake_wait
    return call_count


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


# ---------------------------------------------------------------------------
# _device_watcher — lazy polling
#
# _find_output_device() scans PortAudio structures and holds the GIL for
# several ms on each call. Calling it every 1-second watcher tick caused
# timing jitter in the clock thread (clock woke from time.sleep but had to
# wait for the GIL). It must only be called when something meaningful happens.
# ---------------------------------------------------------------------------

def test_watcher_does_not_call_find_device_on_quiet_ticks():
    """No hardware change, no stream death → _find_output_device not called before slow poll."""
    engine = AudioEngine()
    engine._device_idx = 5
    _limit_watcher(engine, ticks=3)  # 3 < _SLOW_POLL=5, no events
    with patch("sila.engine.audio._get_waveout_device_count", return_value=2), \
         patch("sila.engine.audio._find_output_device") as mock_find:
        engine._device_watcher()
    assert mock_find.call_count == 0


def test_watcher_calls_find_device_immediately_on_hardware_change():
    """WinMM device count change → _find_output_device called on that same tick."""
    engine = AudioEngine()
    engine._device_idx = 5
    _limit_watcher(engine, ticks=1)
    # First call is the initialiser before the loop; second is inside iteration 1.
    with patch("sila.engine.audio._get_waveout_device_count", side_effect=[2, 3]), \
         patch("sila.engine.audio._find_output_device", return_value=5) as mock_find, \
         patch.object(engine, "_restart_stream"):
        engine._device_watcher()
    assert mock_find.call_count == 1


def test_watcher_calls_find_device_immediately_on_stream_died():
    """Stream death → _find_output_device called on that same tick."""
    engine = AudioEngine()
    engine._device_idx = 5
    engine._stream_died.set()
    _limit_watcher(engine, ticks=1)
    with patch("sila.engine.audio._get_waveout_device_count", return_value=2), \
         patch("sila.engine.audio._find_output_device", return_value=5) as mock_find, \
         patch.object(engine, "_restart_stream"):
        engine._device_watcher()
    assert mock_find.call_count == 1


# ---------------------------------------------------------------------------
# _device_watcher — thread survival during restart
#
# _restart_stream() holds _stopping_intentionally=True for the duration of
# the stream swap. The old code used 'break' when it saw that flag, which
# permanently killed the watcher thread. Subsequent plug/unplug events were
# never detected. The fix is 'continue' so the thread stays alive.
# ---------------------------------------------------------------------------

def test_watcher_survives_stopping_intentionally():
    """Watcher must not exit early when _stopping_intentionally is True (continue, not break)."""
    engine = AudioEngine()
    engine._device_idx = 5
    engine._stopping_intentionally = True
    call_count = _limit_watcher(engine, ticks=3)
    with patch("sila.engine.audio._get_waveout_device_count", return_value=2), \
         patch("sila.engine.audio._find_output_device"), \
         patch.object(engine, "_restart_stream"):
        engine._device_watcher()
    # With 'continue': all 4 wait() calls happen (3 returning False + 1 returning True).
    # With the old 'break': only 1 call before the thread exits.
    assert call_count[0] == 4


# ---------------------------------------------------------------------------
# _device_watcher — no restart loop on device=None fallback
#
# When start() or _restart_stream() falls back to device=None (system
# default), _device_idx is set to None. On the next slow-poll tick,
# _find_output_device() returns a specific WASAPI index X. The old code
# compared X != None → True → triggered a restart to X → which might fail
# again → back to None → loop every 5 seconds, corrupting audio.
# ---------------------------------------------------------------------------

def test_watcher_no_restart_when_device_idx_none_and_specific_device_found():
    """_device_idx=None (system-default fallback) + specific device found → no restart."""
    engine = AudioEngine()
    engine._device_idx = None  # we're on device=None and it's working
    _limit_watcher(engine, ticks=5)  # slow poll fires at tick 5
    with patch("sila.engine.audio._get_waveout_device_count", return_value=2), \
         patch("sila.engine.audio._find_output_device", return_value=5), \
         patch.object(engine, "_restart_stream") as mock_restart:
        engine._device_watcher()
    assert mock_restart.call_count == 0


def test_watcher_restarts_when_specific_device_changes():
    """_device_idx=5, new device=3 → restart is triggered on slow poll."""
    engine = AudioEngine()
    engine._device_idx = 5
    _limit_watcher(engine, ticks=5)  # slow poll fires at tick 5
    with patch("sila.engine.audio._get_waveout_device_count", return_value=2), \
         patch("sila.engine.audio._find_output_device", return_value=3), \
         patch.object(engine, "_restart_stream") as mock_restart:
        engine._device_watcher()
    assert mock_restart.call_count == 1
