"""Real, audible alert sounds.

Tk's own bell() just asks Windows for its default beep, which is often switched off, so reminders
were silent. These chimes use winsound.Beep, which plays through the speakers whatever the Windows
sound scheme says. They play on a background thread, so the window never freezes.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Optional

try:
    import winsound
except ImportError:                       # not Windows
    winsound = None

PATTERNS: dict[str, list[tuple[int, int]]] = {       # (frequency Hz, milliseconds)
    "reminder": [(659, 140), (784, 140), (988, 140), (1319, 380)],
    "alert": [(988, 160), (740, 160), (988, 160), (740, 160), (988, 300)],
    "warn": [(880, 150), (660, 260)],
    "ok": [(880, 120), (1175, 140)],
}
REPEATS = {"reminder": 3, "alert": 2, "warn": 1, "ok": 1}


def _beep(freq: int, ms: int) -> None:
    if winsound is not None:
        winsound.Beep(freq, ms)
    else:
        time.sleep(ms / 1000)


BEEP: Callable[[int, int], None] = _beep               # tests replace this
SLEEP: Callable[[float], None] = time.sleep
_lock = threading.Lock()


def available() -> bool:
    return winsound is not None or not sys.platform.startswith("win")


def chime(kind: str = "reminder", repeats: Optional[int] = None, wait: bool = False) -> Optional[threading.Thread]:
    """Play a named sound. Overlapping chimes queue up instead of garbling each other."""
    notes = PATTERNS.get(kind)
    if not notes:
        return None
    times = REPEATS.get(kind, 1) if repeats is None else max(1, int(repeats))

    def work() -> None:
        with _lock:
            for i in range(times):
                for freq, ms in notes:
                    try:
                        BEEP(freq, ms)
                    except Exception:             # no sound device: never let that crash anything
                        return
                if i < times - 1:
                    SLEEP(0.45)
    t = threading.Thread(target=work, daemon=True)
    t.start()
    if wait:
        t.join()
    return t
