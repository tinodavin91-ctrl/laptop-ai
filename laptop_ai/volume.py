"""Volume and media keys, sent the same way a keyboard's media keys are. No extra software needed (Windows)."""
from __future__ import annotations

import os
import time
from typing import Callable, Optional

VK = {"mute": 0xAD, "down": 0xAE, "up": 0xAF, "next": 0xB0, "previous": 0xB1, "play_pause": 0xB3}
SENDER: Optional[Callable[[int], None]] = None        # tests replace this
STEP = 2                                              # one key press changes the volume by about 2%


def _press(vk: int) -> None:
    if SENDER is not None:
        SENDER(vk)
        return
    if os.name != "nt":
        raise OSError("Volume and media keys only work on Windows.")
    import ctypes
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)      # type: ignore[attr-defined]
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)      # key up


def press(key: str, times: int = 1) -> None:
    for _ in range(max(1, times)):
        _press(VK[key])
        if SENDER is None:
            time.sleep(0.004)


def set_level(level: int) -> int:
    """Windows has no 'set to N%' key, so go to 0 first and then up. Returns the level reached."""
    level = max(0, min(100, int(level)))
    press("down", 50)
    press("up", round(level / STEP))
    return level
