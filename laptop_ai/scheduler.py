"""A tiny persistent scheduler for reminders and recurring jobs.

It knows nothing about what the jobs do: when one is due it calls the runner
function you give it (the GUI decides what "remind" or "clean_temp" means).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from . import config


class Scheduler:
    def __init__(self, path: Optional[Path] = None, clock: Callable[[], float] = time.time):
        self.path = Path(path) if path else config.TASKS_FILE
        self.clock = clock
        self._lock = threading.RLock()
        self._tasks: list[dict] = self._load()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._runner: Optional[Callable[[dict], None]] = None

    # ---- storage -------------------------------------------------------
    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._tasks, indent=2), encoding="utf-8")

    # ---- public API ----------------------------------------------------
    def add(self, kind: str, delay_s: float, every_s: float = 0, message: str = "",
            params: Optional[dict] = None) -> dict:
        with self._lock:
            task = {
                "id": uuid.uuid4().hex[:6],
                "kind": kind,
                "message": message,
                "params": params or {},
                "next_run": self.clock() + delay_s,
                "every_s": every_s,
            }
            self._tasks.append(task)
            self._save()
            return dict(task)

    def remove(self, task_id: str) -> bool:
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if t["id"] != task_id]
            self._save()
            return len(self._tasks) < before

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(t) for t in sorted(self._tasks, key=lambda t: t["next_run"])]

    def run_due(self) -> int:
        """Run everything that is due. Returns how many jobs ran."""
        now = self.clock()
        due: list[dict] = []
        with self._lock:
            for task in list(self._tasks):
                if task["next_run"] <= now:
                    run = dict(task)
                    run["late_by_s"] = now - task["next_run"]
                    due.append(run)
                    if task["every_s"]:
                        task["next_run"] = now + task["every_s"]   # reschedule BEFORE running
                    else:
                        self._tasks.remove(task)
            if due:
                self._save()
        for task in due:
            if self._runner:
                try:
                    self._runner(task)
                except Exception:      # one bad job must never stop the scheduler
                    pass
        return len(due)

    # ---- background thread --------------------------------------------
    def start(self, runner: Callable[[dict], None]) -> None:
        self._runner = runner
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(1.0):
            self.run_due()

    def stop(self) -> None:
        self._stop.set()
