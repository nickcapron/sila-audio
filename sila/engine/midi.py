"""
MIDI input listener — Windows WinMM implementation via ctypes.

Works without any external MIDI package.  Falls back gracefully on
non-Windows or when no devices are present.
"""
from __future__ import annotations

import ctypes
import sys
import time
import threading
from typing import Callable

_IS_WIN = sys.platform == "win32"
_winmm = ctypes.windll.winmm if _IS_WIN else None

# WinMM constants
_MIM_OPEN  = 0x3C1
_MIM_CLOSE = 0x3C2
_MIM_DATA  = 0x3C3
_CALLBACK_FUNCTION = 0x00030000


# ---------------------------------------------------------------------------
# Device enumeration
# ---------------------------------------------------------------------------

def get_midi_input_names() -> list[str]:
    """Return names of available MIDI input devices (Windows only)."""
    if not _IS_WIN or _winmm is None:
        return []
    try:
        count = int(_winmm.midiInGetNumDevs())
        names: list[str] = []
        for i in range(count):
            class _CAPS(ctypes.Structure):
                _fields_ = [
                    ("wMid", ctypes.c_uint16),
                    ("wPid", ctypes.c_uint16),
                    ("vDriverVersion", ctypes.c_uint32),
                    ("szPname", ctypes.c_wchar * 32),
                    ("dwSupport", ctypes.c_uint32),
                ]
            caps = _CAPS()
            if _winmm.midiInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
                names.append(caps.szPname)
        return names
    except Exception:
        return []


# ---------------------------------------------------------------------------
# MIDI listener
# ---------------------------------------------------------------------------

class MidiListener:
    """Listens on a WinMM MIDI input port and dispatches note-on events."""

    def __init__(self, note_callback: Callable[[int, int], None]) -> None:
        """*note_callback(note, velocity)* is called on note-on events."""
        self._note_callback = note_callback
        self._handle = ctypes.c_void_p(0)
        self._proc: object = None   # keep reference so GC doesn't collect it
        self._active = False
        self._last_activity: float = 0.0

    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        """True if a MIDI note was received in the last 300 ms."""
        return time.monotonic() - self._last_activity < 0.3

    def open(self, device_index: int = 0) -> bool:
        """Open *device_index* and start receiving MIDI.  Returns success."""
        if not _IS_WIN or _winmm is None:
            return False
        if self._active:
            return True
        try:
            _ProcType = ctypes.WINFUNCTYPE(
                None,
                ctypes.c_void_p,   # hMidiIn
                ctypes.c_uint,     # wMsg
                ctypes.c_size_t,   # dwInstance
                ctypes.c_size_t,   # dwParam1
                ctypes.c_size_t,   # dwParam2
            )
            self._proc = _ProcType(self._callback)
            ret = _winmm.midiInOpen(
                ctypes.byref(self._handle),
                device_index,
                self._proc,
                0,
                _CALLBACK_FUNCTION,
            )
            if ret != 0:
                return False
            _winmm.midiInStart(self._handle)
            self._active = True
            return True
        except Exception:
            return False

    def close(self) -> None:
        if not self._active or not _IS_WIN or _winmm is None:
            return
        try:
            _winmm.midiInStop(self._handle)
            _winmm.midiInClose(self._handle)
        except Exception:
            pass
        self._active = False

    def _callback(
        self,
        hMidiIn:    int,
        wMsg:       int,
        dwInstance: int,
        dwParam1:   int,
        dwParam2:   int,
    ) -> None:
        # Runs in WinMM's thread — must be fast.
        if wMsg != _MIM_DATA:
            return
        status   = dwParam1 & 0xFF
        note     = (dwParam1 >> 8) & 0xFF
        velocity = (dwParam1 >> 16) & 0xFF
        if (status & 0xF0) == 0x90 and velocity > 0:
            self._last_activity = time.monotonic()
            try:
                self._note_callback(note, velocity)
            except Exception:
                pass
