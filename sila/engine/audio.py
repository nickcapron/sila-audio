"""Audio output engine — mixes active voices into a sounddevice stream."""
from __future__ import annotations

import math
import threading

import numpy as np
import sounddevice as sd

SR = 48_000
BLOCK = 512  # frames per callback


def _find_wasapi_device() -> int | None:
    """Return the default WASAPI output device index, or None if unavailable.

    On Windows the system default device is often MME at 44100 Hz, which
    rejects a 48 kHz stream. WASAPI devices natively support 48 kHz and are
    preferred when available.
    """
    try:
        for hostapi in sd.query_hostapis():
            if "WASAPI" in hostapi["name"]:
                dev_idx = int(hostapi.get("default_output_device", -1))
                if dev_idx >= 0:
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
        self._lock = threading.Lock()
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
        device = _find_wasapi_device()
        self._device_idx = device
        try:
            self._stream = sd.OutputStream(
                samplerate=SR,
                channels=2,
                dtype="float32",
                blocksize=BLOCK,
                callback=self._callback,
                finished_callback=self._on_stream_finished,
                device=device,
            )
            self._stream.start()
        except sd.PortAudioError as exc:
            self._stream = None
            raise RuntimeError(f"Audio device unavailable: {exc}") from exc
        # Start the device-change watcher if not already running.
        self._watcher_stop.clear()
        if self._watcher_thread is None or not self._watcher_thread.is_alive():
            self._watcher_thread = threading.Thread(
                target=self._device_watcher, daemon=True
            )
            self._watcher_thread.start()

    def stop(self) -> None:
        self._stopping_intentionally = True
        self._watcher_stop.set()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            self._voices.clear()

    def _device_watcher(self) -> None:
        """Daemon thread: restart the stream when the default output device changes."""
        while not self._watcher_stop.wait(timeout=1.0):
            if self._stopping_intentionally:
                break
            new_device = _find_wasapi_device()
            if (new_device != self._device_idx or self._stream_died.is_set()) \
                    and not self._stopping_intentionally:
                self._restart_stream(new_device)

    def _restart_stream(self, new_device: int | None) -> None:
        """Close the current stream and reopen on *new_device*."""
        if self._stopping_intentionally:
            return

        # Suppress finished_callback → stream_died during the swap.
        self._stopping_intentionally = True
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

        self._device_idx = new_device
        self._stopping_intentionally = False
        self._stream_died.clear()

        try:
            stream = sd.OutputStream(
                samplerate=SR,
                channels=2,
                dtype="float32",
                blocksize=BLOCK,
                callback=self._callback,
                finished_callback=self._on_stream_finished,
                device=new_device,
            )
            stream.start()
            # Guard against stop() being called while we were restarting.
            if self._stopping_intentionally:
                stream.stop()
                stream.close()
            else:
                self._stream = stream
        except sd.PortAudioError:
            pass  # device still unavailable; watcher will retry next tick

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
