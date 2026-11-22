"""Spots one program that is eating the PC's memory or processor and ASKS before closing it.

The logic is separate from the window so it can be tested: the app passes in `ask` (shows a Yes/No box and waits)
and `close` (really closes the program). Nothing is ever closed without a Yes.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

import psutil

from . import config

MIN_MEMORY_MB = 600          # a program must hold at least this much to be blamed for high memory
MIN_CPU_PCT = 35.0           # ...or use at least this much of the processor
ASKED_AGAIN_S = 30 * 60      # the same program is not offered again for 30 minutes
DECLINED_AGAIN_S = 60 * 60   # ...or for an hour after you said no
SELF_NAMES = {"laptop ai.exe", "laptopai.exe", "laptop_ai.exe"}


def pick_culprit(stats: dict, settings: config.Settings, rows_by: Callable[[str], list[dict]],
                 protected: set[str], mine: set[int]) -> Optional[dict]:
    """The one program to blame, or None. rows_by("memory"|"cpu") gives programs ranked as tools.top_processes does."""
    ram, cpu = stats.get("ram"), stats.get("cpu")
    reason = None
    if ram is not None and ram >= settings.ram_alert_pct:
        reason = "memory"
    elif cpu is not None and cpu >= settings.cpu_alert_pct:
        reason = "cpu"
    if reason is None:
        return None
    for row in rows_by(reason):
        name = str(row.get("name", ""))
        low = name.lower()
        if low in protected or low in SELF_NAMES or mine & set(row.get("pids", [])):
            continue
        big = row.get("memory_mb", 0) >= MIN_MEMORY_MB if reason == "memory" else row.get("cpu_percent", 0) >= MIN_CPU_PCT
        if not big:
            return None              # the biggest eligible program is not really the problem: do not blame an innocent one
        return {**row, "reason": reason, "ram": ram, "cpu": cpu}
    return None


def question(c: dict) -> str:
    name = c["name"]
    if c["reason"] == "memory":
        gb = c["memory_mb"] / 1024
        used = f"{gb:.1f} GB" if gb >= 1 else f"{c['memory_mb']} MB"
        what = f"Your memory is {c['ram']:.0f}% full and {name} is using {used} of it."
    else:
        what = f"Your processor is at {c['cpu']:.0f}% and {name} is using about {c['cpu_percent']:.0f}% of it."
    many = f" ({c['processes']} processes)" if c.get("processes", 1) > 1 else ""
    return f"{what}{many}\n\nClose {name}? Unsaved work in it will be lost."


class HogWatcher:
    def __init__(self, settings: config.Settings, ask: Callable[[str], bool], close: Callable[[str], bool],
                 note: Callable[[str], None] = lambda m: None, busy_now: Callable[[], bool] = lambda: False,
                 rows_by: Optional[Callable[[str], list[dict]]] = None):
        self.s, self.ask, self.close, self.note, self.busy_now = settings, ask, close, note, busy_now
        self._rows_by = rows_by
        self._until: dict[str, float] = {}
        self._cpu_streak = 0
        self._busy = threading.Lock()

    def _rows(self, by: str) -> list[dict]:
        if self._rows_by:
            return self._rows_by(by)
        from . import tools
        return tools.top_processes("cpu" if by == "cpu" else "memory", 12)

    def check(self, stats: dict, now: Optional[float] = None) -> Optional[str]:
        """One look. Returns 'closed', 'declined', 'failed' or None (nothing to do). Blocks while the question is open."""
        now = time.time() if now is None else now
        if not getattr(self.s, "suggest_close_apps", True) or self.busy_now():
            return None
        from . import tools
        # a brief CPU spike is normal: ask only when it is high on two readings in a row (memory is asked at once)
        cpu_high = stats.get("cpu") is not None and stats["cpu"] >= self.s.cpu_alert_pct
        self._cpu_streak = self._cpu_streak + 1 if cpu_high else 0
        if (stats.get("ram") or 0) < self.s.ram_alert_pct and self._cpu_streak < 2:
            return None
        c = pick_culprit(stats, self.s, self._rows, tools.PROTECTED, {os.getpid(), os.getppid()})
        if not c or self._until.get(c["name"].lower(), 0) > now:
            return None
        self._until[c["name"].lower()] = now + ASKED_AGAIN_S
        if not self.ask(question(c)):
            self._until[c["name"].lower()] = now + DECLINED_AGAIN_S
            return "declined"
        if self.close(c["name"]):
            self.note(f"Closed {c['name']}. That should free up your {'memory' if c['reason'] == 'memory' else 'processor'}.")
            return "closed"
        self.note(f"I couldn't close {c['name']}. Windows wouldn't let me, so you may need to close it yourself.")
        return "failed"

    def offer(self, reason: str, stats: dict) -> str:
        """The map's "Close the heaviest app" button: you asked, so pick the top eligible program whatever its size, then still ask."""
        from . import tools
        by = "cpu" if reason == "cpu" else "memory"
        mine = {os.getpid(), os.getppid()}
        for row in self._rows(by):
            low = str(row.get("name", "")).lower()
            if low in tools.PROTECTED or low in SELF_NAMES or mine & set(row.get("pids", [])):
                continue
            c = {**row, "reason": by, "ram": stats.get("ram") or 0, "cpu": stats.get("cpu") or 0}
            if not self.ask(question(c)):
                return "declined"
            if self.close(row["name"]):
                self.note(f"Closed {row['name']}.")
                return "closed"
            self.note(f"I couldn't close {row['name']}. Windows wouldn't let me.")
            return "failed"
        self.note("I couldn't find a program I'm allowed to close.")
        return "none"

    def feed(self, stats: dict) -> None:
        """Called by the monitor every reading. The question runs on its own thread so monitoring never waits for you."""
        if not self._busy.acquire(blocking=False):
            return

        def work():
            try:
                self.check(stats)
            except Exception:
                pass
            finally:
                self._busy.release()
        threading.Thread(target=work, daemon=True).start()
