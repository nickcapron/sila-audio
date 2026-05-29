"""
Tests for PlaybackClock restart and error-propagation logic.

Uses a stub AudioEngine so no real device is needed.
"""

import threading
import time

import pytest

from sila.engine.clock import PlaybackClock
from sila.engine.sequencer import Sequencer
from sila.models.project import ProjectModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _StubAudio:
    """Minimal AudioEngine stand-in for clock tests."""

    def __init__(self, *, dies: bool = False, restart_raises: str | None = None) -> None:
        self._died = dies
        self._restart_raises = restart_raises

    @property
    def stream_died(self) -> bool:
        return self._died

    @property
    def healthy(self) -> bool:
        return not self._died

    def start(self) -> None:
        if self._restart_raises:
            raise RuntimeError(self._restart_raises)
        self._died = False

    def stop(self) -> None:
        pass

    def play(self, *args, **kwargs) -> None:
        pass


def _make_clock(audio: _StubAudio) -> PlaybackClock:
    seq = Sequencer(ProjectModel())
    return PlaybackClock(seq, {}, audio)


# ---------------------------------------------------------------------------
# healthy / error properties
# ---------------------------------------------------------------------------

def test_clock_healthy_false_when_not_running():
    clock = _make_clock(_StubAudio())
    assert clock.healthy is False


def test_clock_healthy_false_when_has_error():
    audio = _StubAudio()
    clock = _make_clock(audio)
    clock._running = True
    clock._error = "boom"
    assert clock.healthy is False


def test_clock_healthy_false_when_audio_unhealthy():
    audio = _StubAudio(dies=True)
    clock = _make_clock(audio)
    clock._running = True
    assert clock.healthy is False


def test_clock_healthy_true_when_running_and_audio_ok():
    audio = _StubAudio(dies=False)
    clock = _make_clock(audio)
    clock._running = True
    assert clock.healthy is True


# ---------------------------------------------------------------------------
# _run — stream-died detection and restart
# ---------------------------------------------------------------------------

def test_clock_sets_error_and_stops_when_restart_fails():
    """Stream dies, restart raises → _run exits with error, running=False."""
    audio = _StubAudio(dies=True, restart_raises="device lost")
    clock = _make_clock(audio)
    clock._running = True
    clock._interval = 0.001
    # _run exits on the first iteration once restart fails (no sleep reached).
    clock._run()
    assert clock.error is not None
    assert "device lost" in clock.error
    assert clock.running is False


def test_clock_recovers_and_continues_when_restart_succeeds():
    """Stream dies but restart succeeds → clock keeps running, no error."""
    audio = _StubAudio(dies=True)  # start() clears the died flag
    clock = _make_clock(audio)
    clock._interval = 0.005

    t = threading.Thread(target=clock._run)
    clock._running = True
    t.start()
    time.sleep(0.05)
    clock._running = False
    t.join(timeout=1.0)

    assert not t.is_alive(), "clock thread did not exit cleanly"
    assert clock.error is None
    assert audio.stream_died is False  # start() was called and cleared the flag


def test_clock_no_error_on_healthy_stream():
    """Normal operation — no error after several ticks."""
    audio = _StubAudio(dies=False)
    clock = _make_clock(audio)
    clock._interval = 0.005

    t = threading.Thread(target=clock._run)
    clock._running = True
    t.start()
    time.sleep(0.05)
    clock._running = False
    t.join(timeout=1.0)

    assert not t.is_alive(), "clock thread did not exit cleanly"
    assert clock.error is None


# ---------------------------------------------------------------------------
# start() initialisation and set_bpm()
# ---------------------------------------------------------------------------

def test_start_sets_correct_interval_for_bpm():
    """start(bpm) must store the right 16th-note interval or tempo will be wrong."""
    clock = _make_clock(_StubAudio())
    clock.start(120.0)
    try:
        assert clock._interval == pytest.approx(60.0 / 120.0 / 4.0)
    finally:
        clock.stop()


def test_start_sets_start_time():
    """start_time is used by the UI to anchor the visual playhead; must be set."""
    clock = _make_clock(_StubAudio())
    before = time.time()
    clock.start(120.0)
    try:
        assert clock.start_time is not None
        assert clock.start_time >= before
    finally:
        clock.stop()


def test_set_bpm_updates_interval():
    """set_bpm must overwrite _interval — that is the only mechanism for live tempo change."""
    clock = _make_clock(_StubAudio())
    clock._interval = 60.0 / 120.0 / 4.0  # pretend it was started at 120 BPM
    clock.set_bpm(60.0)
    assert clock._interval == pytest.approx(60.0 / 60.0 / 4.0)


def test_set_bpm_live_halves_interval_when_doubling_tempo():
    """Doubling BPM on a running clock must halve the tick interval."""
    clock = _make_clock(_StubAudio())
    clock.start(120.0)
    try:
        original = clock._interval
        clock.set_bpm(240.0)
        assert clock._interval == pytest.approx(original / 2.0)
    finally:
        clock.stop()
