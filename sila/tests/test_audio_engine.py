"""
Tests for AudioEngine internal state management.

All tests run without opening a real audio device — they exercise
the death-detection and flag logic directly.
"""

import math

import numpy as np
import pytest
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


# ---------------------------------------------------------------------------
# _callback — DSP mixing logic
#
# _callback is called by PortAudio on a real-time thread.  We drive it
# directly here (no stream needed) by injecting voices via play() and
# passing a zeroed numpy buffer as the hardware output.
# ---------------------------------------------------------------------------

BLOCK = 512  # matches AudioEngine's BLOCK constant


def _drive(engine: AudioEngine, frames: int = BLOCK) -> np.ndarray:
    """Call _callback and return the output buffer."""
    buf = np.zeros((frames, 2), dtype=np.float32)
    engine._callback(buf, frames, None, None)
    return buf


def _ones(n: int = BLOCK * 2) -> np.ndarray:
    return np.ones(n, dtype=np.float32)


def test_callback_silence_with_no_voices():
    assert np.all(_drive(AudioEngine()) == 0.0)


def test_callback_single_voice_center_pan():
    engine = AudioEngine()
    engine.play(_ones(), volume=1.0, pan=0.0)
    buf = _drive(engine)
    expected = math.cos(math.pi / 4)  # ≈ 0.7071 for pan=0
    assert buf[:, 0] == pytest.approx(expected, abs=1e-5)
    assert buf[:, 1] == pytest.approx(expected, abs=1e-5)


def test_callback_pan_hard_left():
    engine = AudioEngine()
    engine.play(_ones(), volume=1.0, pan=-1.0)
    buf = _drive(engine)
    assert buf[:, 0] == pytest.approx(1.0, abs=1e-5)
    assert buf[:, 1] == pytest.approx(0.0, abs=1e-5)


def test_callback_pan_hard_right():
    engine = AudioEngine()
    engine.play(_ones(), volume=1.0, pan=1.0)
    buf = _drive(engine)
    assert buf[:, 0] == pytest.approx(0.0, abs=1e-5)
    assert buf[:, 1] == pytest.approx(1.0, abs=1e-5)


def test_callback_volume_scales_output():
    engine = AudioEngine()
    engine.play(_ones(), volume=0.5, pan=-1.0)
    buf = _drive(engine)
    assert buf[:, 0] == pytest.approx(0.5, abs=1e-5)


def test_callback_two_voices_accumulate():
    """Two simultaneous voices must add, not overwrite."""
    engine = AudioEngine()
    engine.play(_ones(), volume=0.3, pan=-1.0)
    engine.play(_ones(), volume=0.4, pan=-1.0)
    buf = _drive(engine)
    assert buf[:, 0] == pytest.approx(0.7, abs=1e-4)


def test_callback_clips_to_unity():
    """Sum exceeding 1.0 must be hard-clipped, not wrapped or saturated differently."""
    engine = AudioEngine()
    engine.play(_ones(), volume=1.0, pan=-1.0)
    engine.play(_ones(), volume=1.0, pan=-1.0)
    buf = _drive(engine)
    assert np.all(buf[:, 0] <= 1.0 + 1e-6)
    assert buf[0, 0] == pytest.approx(1.0, abs=1e-5)


def test_callback_evicts_voice_when_audio_exhausted():
    short = np.ones(BLOCK // 2, dtype=np.float32)  # 256 samples — half a block
    engine = AudioEngine()
    engine.play(short, volume=1.0, pan=-1.0)
    _drive(engine)
    assert len(engine._voices) == 0, "voice must be evicted once pos >= len(audio)"


def test_callback_voice_survives_when_audio_not_exhausted():
    engine = AudioEngine()
    engine.play(_ones(BLOCK * 4), volume=1.0, pan=-1.0)
    _drive(engine)
    assert len(engine._voices) == 1, "voice with remaining audio must stay alive"


def test_callback_delay_larger_than_block_produces_silence():
    """delay_frames > block: voice deferred entirely, buffer stays silent."""
    engine = AudioEngine()
    engine.play(_ones(BLOCK * 4), volume=1.0, pan=-1.0, delay_frames=BLOCK + 50)
    buf = _drive(engine)
    assert np.all(buf == 0.0)
    assert engine._voices[0].delay_frames == 50  # decremented by BLOCK


def test_callback_delay_smaller_than_block_starts_mid_block():
    """delay_frames < block: first delay_frames samples silent, rest mixed."""
    engine = AudioEngine()
    delay = 100
    engine.play(_ones(BLOCK * 4), volume=1.0, pan=-1.0, delay_frames=delay)
    buf = _drive(engine)
    assert buf[:delay, 0] == pytest.approx(0.0, abs=1e-5), "delay region must be silent"
    assert buf[delay:, 0] == pytest.approx(1.0, abs=1e-5), "audio must start after delay"


def test_callback_frames_remaining_truncates_playback():
    """max_frames limits how many samples are mixed; voice evicted afterwards."""
    max_f = 50
    engine = AudioEngine()
    engine.play(_ones(BLOCK * 4), volume=1.0, pan=-1.0, max_frames=max_f)
    buf = _drive(engine)
    assert buf[:max_f, 0] == pytest.approx(1.0, abs=1e-5)
    assert buf[max_f:, 0] == pytest.approx(0.0, abs=1e-5)
    assert len(engine._voices) == 0, "voice must be evicted after frames_remaining hits 0"


def test_small_speaker_off_is_bit_identical_default():
    """Default (small_speaker=False) must leave the master bus untouched —
    high-end users on proper gear get the raw hard-clipped mix, unchanged."""
    engine = AudioEngine()
    assert engine.small_speaker is False
    engine.play(_ones(), volume=1.0, pan=-1.0)  # full-scale signal
    buf = _drive(engine)
    # Full-scale 1.0 passes through exactly (no soft-clip coloration).
    assert buf[:, 0] == pytest.approx(1.0, abs=1e-6)


def test_small_speaker_on_soft_limits_and_stays_finite():
    """With small-speaker mode on, stacked over-unity transients are soft-limited
    (never exceed 1.0) and the output is finite."""
    engine = AudioEngine()
    engine.small_speaker = True
    engine.play(_ones(), volume=1.0, pan=-1.0)
    engine.play(_ones(), volume=1.0, pan=-1.0)  # sums to 2.0 pre-limit
    buf = _drive(engine)
    assert np.all(np.abs(buf) <= 1.0 + 1e-6)
    assert np.all(np.isfinite(buf))


def test_small_speaker_filter_state_resets_on_start(monkeypatch):
    """(Re)opening the stream must clear the master filter memory so playback
    never starts with stale IIR state from a previous session."""
    engine = AudioEngine()
    engine._ms_state[:] = 0.5  # simulate leftover filter memory
    monkeypatch.setattr("sila.engine.audio.sd.OutputStream", lambda **kw: _FakeStream())
    monkeypatch.setattr("sila.engine.audio._find_output_device", lambda: None)
    engine.start()
    assert np.all(engine._ms_state == 0.0)
    engine.stop()


class _FakeStream:
    active = True
    def start(self): pass
    def stop(self): pass
    def close(self): pass


def test_callback_delay_and_frames_remaining_combined():
    """delay_frames offsets the start; frames_remaining limits the duration."""
    delay, max_f = 200, 100
    engine = AudioEngine()
    engine.play(_ones(BLOCK * 4), volume=1.0, pan=-1.0,
                delay_frames=delay, max_frames=max_f)
    buf = _drive(engine)
    assert buf[:delay, 0] == pytest.approx(0.0, abs=1e-5), "delay region silent"
    assert buf[delay : delay + max_f, 0] == pytest.approx(1.0, abs=1e-5), "audio window"
    assert buf[delay + max_f :, 0] == pytest.approx(0.0, abs=1e-5), "after truncation"
    assert len(engine._voices) == 0
