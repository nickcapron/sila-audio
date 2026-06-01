"""
Tests for shutdown behaviour — watchdog, clock/audio ordering, and audio
blocking after shutdown is initiated.
"""
import asyncio
import signal
from unittest.mock import MagicMock

import pytest

from sila.api.routes import AppState


# ---------------------------------------------------------------------------
# Watchdog decision helper
# ---------------------------------------------------------------------------

def test_watchdog_fires_when_ping_is_stale(monkeypatch):
    """_should_watchdog_fire() must return True when ping age exceeds timeout."""
    import sila.main as m
    monkeypatch.setattr(m, "_HEARTBEAT_TIMEOUT", 30.0)
    monkeypatch.setattr(m, "last_ping_age", lambda: 60.0)  # 60 s > 30 s
    assert m._should_watchdog_fire() is True


def test_watchdog_does_not_fire_when_ping_is_fresh(monkeypatch):
    """_should_watchdog_fire() must return False while the browser is active."""
    import sila.main as m
    monkeypatch.setattr(m, "_HEARTBEAT_TIMEOUT", 30.0)
    monkeypatch.setattr(m, "last_ping_age", lambda: 5.0)  # 5 s < 30 s
    assert m._should_watchdog_fire() is False


def test_watchdog_boundary_at_exact_timeout(monkeypatch):
    """Ping age equal to timeout must NOT yet fire (strictly greater-than)."""
    import sila.main as m
    monkeypatch.setattr(m, "_HEARTBEAT_TIMEOUT", 30.0)
    monkeypatch.setattr(m, "last_ping_age", lambda: 30.0)  # equal, not stale yet
    assert m._should_watchdog_fire() is False


# ---------------------------------------------------------------------------
# Async watchdog loop
# ---------------------------------------------------------------------------

def test_watchdog_async_fires_and_sends_sigterm(monkeypatch):
    """The async watchdog loop must call os.kill(pid, SIGTERM) when stale."""
    import sila.main as m

    monkeypatch.setattr(m, "_HEARTBEAT_POLL", 0.001)
    monkeypatch.setattr(m, "_HEARTBEAT_TIMEOUT", 0.005)
    monkeypatch.setattr(m, "last_ping_age", lambda: 999.0)

    kills: list[int] = []
    monkeypatch.setattr(m.os, "kill", lambda pid, sig: kills.append(sig))

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(m._heartbeat_watchdog())
    finally:
        loop.close()

    assert kills == [signal.SIGTERM]


def test_watchdog_async_does_not_fire_while_fresh(monkeypatch):
    """The async watchdog must not fire when the browser is actively pinging."""
    import sila.main as m

    async def _run_briefly():
        task = asyncio.create_task(m._heartbeat_watchdog())
        await asyncio.sleep(0.025)  # enough for ~25 iterations at 1 ms poll
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(m, "_HEARTBEAT_POLL", 0.001)
    monkeypatch.setattr(m, "_HEARTBEAT_TIMEOUT", 100.0)
    monkeypatch.setattr(m, "last_ping_age", lambda: 1.0)

    kills: list[int] = []
    monkeypatch.setattr(m.os, "kill", lambda pid, sig: kills.append(sig))

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_briefly())
    finally:
        loop.close()

    assert not kills


# ---------------------------------------------------------------------------
# Shutdown sequence ordering
# ---------------------------------------------------------------------------

def test_clock_stopped_before_audio_engine():
    """reset_seq() must stop the clock before stopping the audio engine."""
    state = AppState()

    call_order: list[str] = []
    mock_clock = MagicMock()
    mock_clock.stop.side_effect = lambda: call_order.append("clock")
    mock_audio = MagicMock()
    mock_audio.stop.side_effect = lambda: call_order.append("audio")

    state.clock = mock_clock
    state.audio_engine = mock_audio

    state.reset_seq()

    assert call_order == ["clock", "audio"], (
        f"Wrong shutdown order: {call_order}. Clock must stop before audio engine."
    )
    assert state.clock is None


def test_audio_engine_stopped_even_without_clock():
    """reset_seq() must stop the audio engine even when no clock is running."""
    state = AppState()
    mock_audio = MagicMock()
    state.audio_engine = mock_audio
    state.clock = None

    state.reset_seq()

    mock_audio.stop.assert_called_once()


def test_clock_is_dereferenced_after_shutdown():
    """After reset_seq() the clock reference must be None so it can be GC'd."""
    state = AppState()
    state.clock = MagicMock()
    state.audio_engine = MagicMock()

    state.reset_seq()

    assert state.clock is None


# ---------------------------------------------------------------------------
# Audio blocked after shutdown
# ---------------------------------------------------------------------------

def test_audio_engine_not_healthy_after_reset_seq():
    """After reset_seq(), healthy must be False — no audio can be scheduled."""
    state = AppState()
    # No real stream was opened so stop() just sets flags.
    state.reset_seq()
    assert not state.audio_engine.healthy


def test_audio_engine_stopping_flag_set_after_reset_seq():
    """_stopping_intentionally must be True after reset_seq() so callbacks are ignored."""
    state = AppState()
    state.reset_seq()
    assert state.audio_engine._stopping_intentionally is True
