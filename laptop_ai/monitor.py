"""Background watcher: raises an alert when the laptop crosses a threshold.

check_thresholds() is a pure function so it can be tested without a real laptop.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import psutil

from . import config


def check_thresholds(stats: dict, s: config.Settings) -> list[tuple[str, str]]:
    """Return [(key, message)] for everything over its limit."""
    alerts = []
    if stats.get("cpu") is not None and stats["cpu"] >= s.cpu_alert_pct:
        alerts.append(("cpu", f"CPU is at {stats['cpu']:.0f}%."))
    if stats.get("ram") is not None and stats["ram"] >= s.ram_alert_pct:
        alerts.append(("ram", f"Memory is at {stats['ram']:.0f}%."))
    if stats.get("disk") is not None and stats["disk"] >= s.disk_alert_pct:
        alerts.append(("disk", f"Disk is {stats['disk']:.0f}% full."))
    batt = stats.get("battery")
    if batt is not None and batt <= s.battery_alert_pct and not stats.get("plugged"):
        alerts.append(("battery", f"Battery is low ({batt:.0f}%). Plug in the charger."))
    return alerts


def read_stats() -> dict:
    batt = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "ram": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage("C:\\" if psutil.WINDOWS else "/").percent,
        "battery": batt.percent if batt else None,
        "plugged": batt.power_plugged if batt else None,
    }


class Monitor:
    COOLDOWN_S = 600        # the same alert at most once every 10 minutes

    def __init__(self, settings: config.Settings, on_alert: Callable[[str], None],
                 on_stats: Optional[Callable[[dict], None]] = None):
        self.s, self.on_alert, self.on_stats = settings, on_alert, on_stats
        self._last: dict[str, float] = {}
        self._stop = threading.Event()

    def tick(self, stats: Optional[dict] = None, now: Optional[float] = None, alert: bool = True) -> list[str]:
        stats = stats or read_stats()
        now = time.time() if now is None else now
        if self.on_stats:
            self.on_stats(stats)
        fired = []
        if alert and self.s.monitor_enabled:
            for key, msg in check_thresholds(stats, self.s):
                if now - self._last.get(key, 0) >= self.COOLDOWN_S:
                    self._last[key] = now
                    fired.append(msg)
                    self.on_alert(msg)
        return fired

    def start(self) -> None:
        psutil.cpu_percent(interval=None)       # prime the counter
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        if self._stop.wait(2.5):            # let the window settle, then show the first reading
            return
        first = True
        while True:
            try:
                # The first reading only fills the gauges: it measures the app's own startup,
                # so it must not trigger a "CPU is high" alert.
                self.tick(alert=not first)
            except Exception:
                pass
            first = False
            if self._stop.wait(max(5, self.s.monitor_interval_s)):
                break

    def stop(self) -> None:
        self._stop.set()
