"""Audio output engine — mixes active voices into a sounddevice stream."""
from __future__ import annotations

import math
import threading

import numpy as np
import sounddevice as sd

SR = 48_000
BLOCK = 512  # frames per callback


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

    def start(self) -> None:
        if self._stream is not None:
            return
        try:
            self._stream = sd.OutputStream(
                samplerate=SR,
                channels=2,
                dtype="float32",
                blocksize=BLOCK,
                callback=self._callback,
            )
            self._stream.start()
        except sd.PortAudioError as exc:
            self._stream = None
            raise RuntimeError(f"Audio device unavailable: {exc}") from exc

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            self._voices.clear()

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
