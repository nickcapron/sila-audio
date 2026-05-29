"""Audio output engine — mixes active voices into a sounddevice stream."""
from __future__ import annotations

import math
import sys
import threading

import numpy as np
import sounddevice as sd

SR = 48_000
BLOCK = 512  # frames per callback


def _get_waveout_device_count() -> int:
    """Return the Windows WinMM waveOut device count, or -1 on non-Windows.

    Unlike PortAudio's device list (cached at Pa_Initialize time), WinMM always
    reflects the live hardware state, so a change here means a device was added
    or removed since the last check.
    """
    if sys.platform != "win32":
        return -1
    try:
        import ctypes
        return ctypes.windll.winmm.waveOutGetNumDevs()
    except Exception:
        return -1


def _refresh_portaudio() -> None:
    """Force PortAudio to re-enumerate audio devices.

    PortAudio caches the device list at Pa_Initialize() time.  Terminating and
    reinitializing causes it to rescan, so the next sd.query_* call sees any
    devices that were plugged in or unplugged since startup.  Only safe to call
    when no OutputStream is open.
    """
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        pass


def _find_output_device() -> int | None:
    """Return the best output device index for a 48 kHz stream, or None.

    Preference order:
    1. The WASAPI host API's default output device (avoids MME's 44100 Hz limit).
    2. Any WASAPI output device, if the host API has no default set.
    3. None — lets sounddevice pick the system default (last resort).
    """
    try:
        wasapi_api_idx = None
        for api_idx, api in enumerate(sd.query_hostapis()):
            if "WASAPI" in api["name"]:
                wasapi_api_idx = api_idx
                dev_idx = int(api.get("default_output_device", -1))
                if dev_idx >= 0:
                    return dev_idx
                break

        # WASAPI host API found but no default device set — scan for any output.
        if wasapi_api_idx is not None:
            for dev_idx, dev in enumerate(sd.query_devices()):
                if dev["hostapi"] == wasapi_api_idx and dev["max_output_channels"] > 0:
                    return dev_idx
    except Exception:
        pass
    return None


class _Voice:
    __slots__ = ("audio", "pos", "volume", "pan_l", "pan_r")

    def __init__(self, audio: np.ndarray, volume: float, pan: float) -> None:
        self.audio = np.ascontiguousarray(audio, dtype=np.float32)
        self.pos = 0
        self.volume = float(volume)
        angle = (pan + 1.0) * 0.5 * (math.pi / 2.0)
        self.pan_l = float(math.cos(angle))
        self.pan_r = float(math.sin(angle))


class AudioEngine:
    """Owns a sounddevice OutputStream; voices are mixed in the audio callback."""

    def __init__(self) -> None:
        self._voices: list[_Voice] = []
        self._lock = threading.Lock()        # guards _voices (held briefly in callback)
        self._stream_lock = threading.Lock() # guards stream lifecycle + PortAudio state
        self._stream: sd.OutputStream | None = None
        self._stopping_intentionally = False
        self._stream_died = threading.Event()
        self._device_idx: int | None = None
        self._watcher_stop = threading.Event()
        self._watcher_thread: threading.Thread | None = None

    @property
    def healthy(self) -> bool:
        if self._stream is None:
            return False
        try:
            return self._stream.active and not self._stream_died.is_set()
        except Exception:
            return False

    @property
    def stream_died(self) -> bool:
        return self._stream_died.is_set()

    def start(self) -> None:
        with self._stream_lock:
            # If there's a live, active stream already, nothing to do.
            if self._stream is not None and self._stream.active:
                return
            # Clean up a dead or stopped stream before reopening.
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            self._stopping_intentionally = False
            self._stream_died.clear()
            device = _find_output_device()
            self._device_idx = device
            exc_to_raise: Exception | None = None
            for dev in ([device, None] if device is not None else [None]):
                try:
                    self._stream = sd.OutputStream(
                        samplerate=SR,
                        channels=2,
                        dtype="float32",
                        blocksize=BLOCK,
                        callback=self._callback,
                        finished_callback=self._on_stream_finished,
                        device=dev,
                    )
                    self._stream.start()
                    self._device_idx = dev
                    exc_to_raise = None
                    break
                except sd.PortAudioError as exc:
                    self._stream = None
                    exc_to_raise = exc
            if exc_to_raise is not None:
                raise RuntimeError(f"Audio device unavailable: {exc_to_raise}") from exc_to_raise
        # Start the device-change watcher outside the lock (no PortAudio calls here).
        self._watcher_stop.clear()
        if self._watcher_thread is None or not self._watcher_thread.is_alive():
            self._watcher_thread = threading.Thread(
                target=self._device_watcher, daemon=True
            )
            self._watcher_thread.start()

    def stop(self) -> None:
        self._stopping_intentionally = True
        self._watcher_stop.set()
        with self._stream_lock:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
        with self._lock:
            self._voices.clear()

    def _device_watcher(self) -> None:
        """Daemon thread: restart the stream when the default output device changes."""
        last_dev_count = _get_waveout_device_count()
        _slow_ticks = 0
        _SLOW_POLL = 5  # call _find_output_device every N seconds as fallback

        while not self._watcher_stop.wait(timeout=1.0):
            # Use continue (not break) so the watcher survives while _restart_stream
            # holds _stopping_intentionally=True mid-swap.
            if self._stopping_intentionally:
                continue

            # WinMM always reflects live hardware; PortAudio's device cache does not.
            dev_count = _get_waveout_device_count()
            hardware_changed = dev_count != last_dev_count and last_dev_count >= 0
            last_dev_count = dev_count

            stream_died = self._stream_died.is_set()
            _slow_ticks += 1

            # _find_output_device() scans PortAudio structures and holds the GIL
            # for several ms on each call. Only call it when something interesting
            # happened, not on every 1-second tick — that caused timing jitter in
            # the clock thread when it needed to wake from time.sleep().
            if not (hardware_changed or stream_died or _slow_ticks >= _SLOW_POLL):
                continue
            if _slow_ticks >= _SLOW_POLL:
                _slow_ticks = 0

            new_device = _find_output_device()

            # Avoid a restart loop when we fell back to device=None: if
            # _device_idx is None (we're already on the system default and it
            # works), don't restart just because a specific WASAPI device is
            # found. Only restart on an explicit hardware event or stream death.
            device_changed = (
                new_device != self._device_idx
                and not (self._device_idx is None and new_device is not None)
            )

            if (hardware_changed or device_changed or stream_died) \
                    and not self._stopping_intentionally:
                self._restart_stream(new_device, rescan=hardware_changed)

    def _restart_stream(self, new_device: int | None, rescan: bool = False) -> None:
        """Close the current stream and reopen, rescanning PortAudio devices if requested."""
        if self._stopping_intentionally:
            return

        # Suppress finished_callback → stream_died during the swap.
        self._stopping_intentionally = True

        with self._stream_lock:
            # _stream_lock serialises this against start() and stop() so that
            # sd.OutputStream() never races with _refresh_portaudio()'s Pa_Terminate().
            old_stream = self._stream
            self._stream = None

            if old_stream is not None:
                try:
                    old_stream.stop()
                    old_stream.close()
                except Exception:
                    pass

            with self._lock:
                self._voices.clear()

            # Stream is now closed — safe to reinitialize PortAudio and get a fresh
            # device list that includes any hardware added since startup.
            if rescan:
                _refresh_portaudio()
                new_device = _find_output_device()

            self._device_idx = new_device
            self._stopping_intentionally = False
            self._stream_died.clear()

            candidates = [new_device, None] if new_device is not None else [None]
            for dev in candidates:
                if self._stopping_intentionally:
                    return
                try:
                    stream = sd.OutputStream(
                        samplerate=SR,
                        channels=2,
                        dtype="float32",
                        blocksize=BLOCK,
                        callback=self._callback,
                        finished_callback=self._on_stream_finished,
                        device=dev,
                    )
                    stream.start()
                    # Guard against stop() being called while we were restarting.
                    if self._stopping_intentionally:
                        stream.stop()
                        stream.close()
                    else:
                        self._device_idx = dev
                        self._stream = stream
                    return
                except sd.PortAudioError:
                    pass  # try next candidate; watcher will retry next tick if all fail

    def _on_stream_finished(self) -> None:
        # Called by PortAudio on a background thread when the stream stops.
        # Setting a threading.Event here is safe from any thread.
        if not self._stopping_intentionally:
            self._stream_died.set()

    def play(self, audio: np.ndarray, volume: float = 1.0, pan: float = 0.0) -> None:
        with self._lock:
            self._voices.append(_Voice(audio, volume, pan))

    def _callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        out = np.zeros((frames, 2), dtype=np.float32)
        with self._lock:
            alive: list[_Voice] = []
            for v in self._voices:
                n = min(frames, len(v.audio) - v.pos)
                chunk = v.audio[v.pos : v.pos + n] * v.volume
                out[:n, 0] += v.pan_l * chunk
                out[:n, 1] += v.pan_r * chunk
                v.pos += n
                if v.pos < len(v.audio):
                    alive.append(v)
            self._voices = alive
        np.clip(out, -1.0, 1.0, out=out)
        outdata[:] = out
