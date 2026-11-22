"""Focus mode: a do-not-disturb timer. While it runs, warnings and phone alerts stay quiet.
Reminders you set yourself still come through."""
from __future__ import annotations

import time
from typing import Callable

from . import config


class FocusState:
    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock

    def _until(self) -> float:
        data = config.read_json(config.FOCUS_FILE, {})
        try:
            return float(data.get("until", 0)) if isinstance(data, dict) else 0.0
        except (TypeError, ValueError):
            return 0.0

    def start(self, minutes: float) -> float:
        minutes = max(1.0, min(float(minutes), 600.0))
        until = self.clock() + minutes * 60
        config.write_json(config.FOCUS_FILE, {"until": until})
        return until

    def stop(self) -> bool:
        was = self.active()
        config.write_json(config.FOCUS_FILE, {"until": 0})
        return was

    def active(self) -> bool:
        return self._until() > self.clock()

    def minutes_left(self) -> int:
        return max(0, int((self._until() - self.clock() + 59) // 60))


STATE = FocusState()


def active() -> bool:
    return STATE.active()
