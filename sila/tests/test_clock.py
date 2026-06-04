"""
Tests for PlaybackClock restart and error-propagation logic.

Uses a stub AudioEngine so no real device is needed.
"""

import threading
import time

import pytest
from unittest.mock import patch

from sila.engine.clock import PlaybackClock
from sila.engine.sequencer import Sequencer
from sila.models.project import ProjectModel, TrackModel
from sila.models.step import Step


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


# ---------------------------------------------------------------------------
# Song mode — pattern bank advancement
#
# The clock's _run() loop swaps track.steps from the pattern bank when
# tick_count % bar_len == 0 (and tick_count > 0).  The swap must happen
# BEFORE seq.tick() evaluates that tick, so step 0 of the new pattern
# plays correctly at the bar boundary.
#
# We control iterations by patching time.sleep to count calls and flip
# _running=False after the desired number of ticks.
# ---------------------------------------------------------------------------

def _song_project():
    """Return (project, track, seq) with a 2-step track and two pattern slots."""
    track = TrackModel(name="T", step_count=2)
    track.ensure_steps()

    # Slot 0: both steps active; slot 1: both steps inactive
    slot0 = [Step(active=True),  Step(active=True)]
    slot1 = [Step(active=False), Step(active=False)]

    project = ProjectModel(bpm=120)
    project.tracks = [track]
    project.song_mode = True
    project.song_chain = [0, 1]
    project.pattern_bank.slots[0] = {track.id: [s.model_copy() for s in slot0]}
    project.pattern_bank.slots[1] = {track.id: [s.model_copy() for s in slot1]}
    track.steps = [s.model_copy() for s in slot0]   # start on slot 0

    seq = Sequencer(project)
    return project, track, seq


def _run_ticks(clock: PlaybackClock, n_ticks: int) -> None:
    """Drive the clock's _run loop for exactly n_ticks, then stop it."""
    sleep_calls = [0]

    def fake_sleep(s):
        sleep_calls[0] += 1
        if sleep_calls[0] >= n_ticks:
            clock._running = False

    with patch("sila.engine.clock.time.sleep", fake_sleep), \
         patch("sila.engine.clock.time.perf_counter", return_value=0.0):
        clock._running = True
        t = threading.Thread(target=clock._run)
        t.start()
        t.join(timeout=2.0)

    assert not t.is_alive(), "clock thread did not stop within timeout"


def test_song_mode_swaps_pattern_at_bar_boundary():
    """After bar_len ticks the clock must load the next slot's steps."""
    project, track, seq = _song_project()
    clock = PlaybackClock(seq, {}, _StubAudio())
    clock._interval = 0.001

    # bar_len = 2 (track has 2 steps); swap fires at tick_count=2.
    # We run 3 ticks so the swap at tick_count=2 completes before we stop.
    _run_ticks(clock, n_ticks=3)

    assert track.steps[0].active is False, "slot 1: step 0 must be inactive"
    assert track.steps[1].active is False, "slot 1: step 1 must be inactive"


def test_song_mode_does_not_swap_before_bar_boundary():
    """Steps must be unchanged after fewer ticks than bar_len."""
    project, track, seq = _song_project()
    clock = PlaybackClock(seq, {}, _StubAudio())
    clock._interval = 0.001

    # Only 2 ticks (tick_count 0 and 1): 0 > 0 is False, 1 % 2 ≠ 0. No swap.
    _run_ticks(clock, n_ticks=2)

    assert track.steps[0].active is True,  "slot 0 must still be loaded"
    assert track.steps[1].active is True,  "slot 0 must still be loaded"


def test_song_mode_chain_wraps_back_to_first_slot():
    """After len(chain) bars the chain position wraps and slot 0 reloads."""
    project, track, seq = _song_project()
    clock = PlaybackClock(seq, {}, _StubAudio())
    clock._interval = 0.001

    # Swap 1 at tick_count=2 → slot 1 (inactive).
    # Swap 2 at tick_count=4 → wraps to slot 0 (active) again.
    # Run 5 ticks so both swaps complete.
    _run_ticks(clock, n_ticks=5)

    assert track.steps[0].active is True,  "chain wrapped: slot 0 must be reloaded"
    assert track.steps[1].active is True,  "chain wrapped: slot 0 must be reloaded"
