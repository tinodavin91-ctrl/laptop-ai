"""Everything the assistant is allowed to do on the laptop.

Design rules (this file is the safety boundary):
  * The AI can only call the tools registered here. There is NO "run any command" tool.
  * Every tool has a risk level, and the Executor decides when to ask the user:
        READ  never asks (looking at things)
        SAFE  never asks (reminders and schedules)
        LOW   asks in "ask" mode, automatic in "auto_safe" mode (open an app, clean temp files)
        HIGH  ALWAYS asks (closing programs)
  * "report_only" mode blocks everything except READ tools.
"""
from __future__ import annotations

import json
import os
import platform
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import psutil

from . import apps, config
from .scheduler import Scheduler

READ, SAFE, LOW, HIGH = "read", "safe", "low", "high"


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    risk: str
    func: Callable[..., str]
    describe: Optional[Callable[[dict], str]] = None   # human text for the confirmation dialog


REGISTRY: dict[str, Tool] = {}
ADVANCED = {"run_command"}          # only offered when Settings > Advanced is switched on


def tool(name: str, description: str, parameters: Optional[dict] = None, risk: str = READ,
         describe: Optional[Callable[[dict], str]] = None):
    def deco(fn):
        REGISTRY[name] = Tool(name, description, parameters or _obj(), risk, fn, describe)
        return fn
    return deco


def _obj(props: Optional[dict] = None, required: Optional[list] = None) -> dict:
    return {"type": "object", "properties": props or {}, "required": required or []}


def ok(**data) -> str:
    return json.dumps({"status": "ok", **data})


def err(message: str) -> str:
    return json.dumps({"status": "error", "message": message})


def _mb(n: float) -> str:
    return f"{n / 1048576:,.0f} MB" if n < 1073741824 else f"{n / 1073741824:.1f} GB"


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------
# READ tools: look, never touch
# --------------------------------------------------------------------------
@tool("get_current_time", "Get the laptop's current local date, time and weekday.")
def get_current_time() -> str:
    now = datetime.now()
    return ok(local_time=now.strftime("%Y-%m-%d %H:%M:%S"), weekday=now.strftime("%A"))


@tool("get_system_status", "Get live CPU, memory, disk, battery and uptime for this laptop.")
def get_system_status() -> str:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(Path.home().anchor or "/")
    batt = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    return ok(
        cpu_percent=psutil.cpu_percent(interval=0.5),
        cpu_threads=psutil.cpu_count(logical=True),
        ram_percent=vm.percent, ram_used=_mb(vm.used), ram_total=_mb(vm.total),
        disk_percent=disk.percent, disk_free=_mb(disk.free),
        battery=("No battery in this PC" if not batt else
                 f"{batt.percent:.0f}% ({'plugged in' if batt.power_plugged else 'running on battery'})"),
        battery_percent=batt.percent if batt else None,
        plugged_in=batt.power_plugged if batt else None,
        # clearly named so a small model can't mistake it for a battery figure
        pc_running_since_last_restart_hours=round((time.time() - psutil.boot_time()) / 3600, 1),
    )


def top_processes(sort_by: str = "memory", limit: int = 8) -> list[dict]:
    """Programs ranked by memory or CPU. Processes with the same name are grouped
    (a browser is dozens of processes), so 'chrome.exe' appears once with its total."""
    ncpu = psutil.cpu_count(logical=True) or 1
    procs = list(psutil.process_iter(["pid", "name", "memory_info"]))
    for p in procs:
        try:
            p.cpu_percent(None)             # first call primes the counter
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(0.3)

    groups: dict[str, dict] = {}
    for p in procs:
        try:
            mem = p.info.get("memory_info")
            name = p.info.get("name") or "?"
            g = groups.setdefault(name, {"name": name, "processes": 0, "cpu_percent": 0.0,
                                         "memory_mb": 0, "pids": []})
            g["processes"] += 1
            g["cpu_percent"] = round(g["cpu_percent"] + p.cpu_percent(None) / ncpu, 1)
            g["memory_mb"] += round((mem.rss if mem else 0) / 1048576)
            if len(g["pids"]) < 5:
                g["pids"].append(p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    key = "cpu_percent" if sort_by == "cpu" else "memory_mb"
    rows = sorted(groups.values(), key=lambda r: r[key], reverse=True)
    return rows[: max(1, min(int(limit), 25))]


@tool("list_top_processes",
      "List the programs using the most memory or CPU. Same-name processes are grouped into one row.",
      _obj({"sort_by": {"type": "string", "enum": ["memory", "cpu"]},
            "limit": {"type": "integer", "description": "How many rows, 1-25"}}))
def list_top_processes(sort_by: str = "memory", limit: int = 8) -> str:
    return ok(sorted_by=sort_by, programs=top_processes(sort_by, limit))


@tool("find_process", "Check whether a program is running (for example 'chrome') and how much it uses.",
      _obj({"name": {"type": "string"}}, ["name"]))
def find_process(name: str) -> str:
    n = name.strip().lower()
    rows = [r for r in top_processes("memory", 25)
            if n in r["name"].lower()]
    if not rows:
        # top_processes only returns 25 groups; do a direct scan as well
        count = sum(1 for p in psutil.process_iter(["name"]) if n in (p.info.get("name") or "").lower())
        return ok(running=count > 0, processes=count)
    return ok(running=True, programs=rows)


# --------------------------------------------------------------------------
# HIGH risk: closing programs (always asks)
# --------------------------------------------------------------------------
PROTECTED = {
    "system", "system idle process", "registry", "memory compression", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe", "dwm.exe",
    "fontdrvhost.exe", "explorer.exe", "msmpeng.exe", "securityhealthservice.exe",
    "systemd", "init", "kthreadd", "xorg", "launchd",
}


def _safe_name(p: psutil.Process) -> str:
    try:
        return p.name()
    except psutil.Error:
        return "?"


def _targets(pid, name) -> list:
    if pid is not None:
        try:
            return [psutil.Process(int(pid))]
        except psutil.NoSuchProcess:
            return []
    n = (name or "").strip().lower()
    if not n:
        return []
    found = []
    for p in psutil.process_iter(["name"]):
        pn = (p.info.get("name") or "").lower()
        if pn == n or pn == n + ".exe":
            found.append(p)
    return found


def _kill_describe(args: dict) -> str:
    try:
        targets = _targets(args.get("pid"), args.get("name"))
    except Exception:
        targets = []
    if not targets:
        return "Close a program? (No matching process is running right now.)"
    names = sorted({_safe_name(p) for p in targets})
    return (f"Close {len(targets)} process(es): {', '.join(names)}?\n"
            "Unsaved work in them will be lost.")


@tool("kill_process",
      "Close a running program, by pid or by name (all processes with that name). "
      "Look the program up first with find_process or list_top_processes. "
      "System processes are protected and will be refused.",
      _obj({"pid": {"type": "integer"}, "name": {"type": "string", "description": "e.g. chrome.exe"}}),
      HIGH, _kill_describe)
def kill_process(pid=None, name=None) -> str:
    if pid is None and not name:
        return err("Give a pid or a name.")
    targets = _targets(pid, name)
    if not targets:
        return err("No matching process is running.")

    me = {os.getpid(), os.getppid()}
    closed, protected, denied = 0, [], 0
    for p in targets:
        pname = _safe_name(p)
        if p.pid in me or p.pid <= 4 or pname.lower() in PROTECTED:
            protected.append(pname)
            continue
        try:
            p.terminate()
            try:
                p.wait(3)
            except psutil.TimeoutExpired:
                p.kill()
            closed += 1
        except psutil.NoSuchProcess:
            closed += 1
        except psutil.AccessDenied:
            denied += 1
    if closed == 0 and protected and not denied:
        return err(f"Refused: {protected[0]} is a protected system process (or this assistant).")
    return ok(closed=closed, skipped_protected=len(protected), access_denied=denied)


# --------------------------------------------------------------------------
# LOW risk: small changes (asks in "ask" mode)
# --------------------------------------------------------------------------
TEMP_DIR = Path(tempfile.gettempdir())


def _old_temp_files(older_than_hours: float) -> list[tuple[str, int]]:
    cutoff = time.time() - older_than_hours * 3600
    files = []
    for root, _dirs, names in os.walk(TEMP_DIR, followlinks=False):
        for n in names:
            fp = os.path.join(root, n)
            try:
                if os.path.islink(fp):
                    continue
                st = os.stat(fp)
                if st.st_mtime < cutoff:
                    files.append((fp, st.st_size))
            except OSError:
                continue
    return files


@tool("clean_temp_files",
      "Delete old files from the user's temporary-files folder to free disk space. "
      "Use dry_run=true to only measure how much could be freed.",
      _obj({"older_than_hours": {"type": "number", "description": "Only files older than this (default 24)"},
            "dry_run": {"type": "boolean"}}),
      LOW,
      lambda a: ("Measure" if a.get("dry_run") else "Delete") +
                f" temp files older than {a.get('older_than_hours', 24)} hours in {TEMP_DIR}?")
def clean_temp_files(older_than_hours: float = 24, dry_run: bool = False) -> str:
    hours = max(1.0, float(older_than_hours))
    files = _old_temp_files(hours)
    total = sum(size for _, size in files)
    if dry_run:
        return ok(dry_run=True, files=len(files), could_free=_mb(total))
    deleted, freed, skipped = 0, 0, 0
    for fp, size in files:
        try:
            os.remove(fp)
            deleted += 1
            freed += size
        except OSError:               # in use or no permission: just leave it
            skipped += 1
    return ok(deleted=deleted, freed=_mb(freed), skipped_in_use=skipped)


APPS = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "file explorer": "explorer.exe",
    "task manager": "taskmgr.exe", "settings": "ms-settings:", "control panel": "control.exe",
    "paint": "mspaint.exe", "chrome": "chrome", "google chrome": "chrome", "edge": "msedge",
    "firefox": "firefox", "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
    "vs code": "code", "terminal": "wt.exe",
}


@tool("open_app", "Open any app installed on this PC by its name (for example chrome, xbox, spotify, word, "
                  "notepad). Use list_apps first if you are not sure of the name.",
      _obj({"name": {"type": "string"}}, ["name"]), LOW,
      lambda a: f"Open {a.get('name', 'an app')}?")
def open_app(name: str) -> str:
    key = name.strip().lower()
    if platform.system() != "Windows":
        return err("Opening apps is only supported on Windows in this version.")
    target = APPS.get(key)
    try:
        if target:
            os.startfile(target)      # type: ignore[attr-defined]  (Windows only)
            return ok(opened=name)
        matches = apps.find(key, apps.installed_apps())
        if not matches:
            return err(f"I couldn't find an installed app called '{name}'. Try list_apps.")
        if len(matches) > 1 and matches[0]["name"].lower() != key:
            return err("Several apps match: " + ", ".join(m["name"] for m in matches[:6]) +
                       ". Ask the user which one, then call open_app with the exact name.")
        apps.launch(matches[0])
        return ok(opened=matches[0]["name"])
    except OSError:
        return err(f"I couldn't open {name}. Is it installed?")


@tool("list_apps", "Search the apps installed on this PC by name. An empty query lists the first 40.",
      _obj({"query": {"type": "string"}}))
def list_apps(query: str = "") -> str:
    found = apps.find(query, apps.installed_apps()) if query.strip() else apps.installed_apps()
    return ok(found=len(found), apps=[a["name"] for a in found[:40]])


@tool("open_website", "Open a website (an http or https address) in the user's browser.",
      _obj({"url": {"type": "string"}}, ["url"]), LOW,
      lambda a: f"Open {a.get('url', 'a website')} in your browser?")
def open_website(url: str) -> str:
    url = url.strip()
    if "://" in url:
        if not url.lower().startswith(("http://", "https://")):
            return err("Only http and https addresses can be opened.")
    elif re.match(r"^[a-z][a-z0-9+.-]*:(?!\d)", url, re.I):          # javascript:, file:, mailto: ... (not host:8080)
        return err("Only http and https addresses can be opened.")
    else:
        url = "https://" + url
    import webbrowser
    webbrowser.open(url)
    return ok(opened=url)


# --------------------------------------------------------------------------
# SAFE tools: reminders and schedules
# --------------------------------------------------------------------------
SCHEDULER: Optional[Scheduler] = None


def get_scheduler() -> Scheduler:
    global SCHEDULER
    if SCHEDULER is None:
        SCHEDULER = Scheduler()
    return SCHEDULER


@tool("add_reminder", "Remind the user once, after some minutes (work it out from the current time).",
      _obj({"message": {"type": "string"}, "minutes": {"type": "number", "description": "Minutes from now"}},
           ["message", "minutes"]), SAFE)
def add_reminder(message: str, minutes: float) -> str:
    minutes = float(minutes)
    if not (0 < minutes <= 43200):
        return err("minutes must be between 0 and 43200 (30 days).")
    t = get_scheduler().add("remind", minutes * 60, message=message)
    return ok(id=t["id"], due=_when(t["next_run"]))


@tool("schedule_task",
      "Create a recurring job. task is one of: remind (needs message), system_report, clean_temp. "
      "every_minutes must be at least 5.",
      _obj({"task": {"type": "string", "enum": ["remind", "system_report", "clean_temp"]},
            "every_minutes": {"type": "number"}, "message": {"type": "string"}},
           ["task", "every_minutes"]), SAFE)
def schedule_task(task: str, every_minutes: float, message: str = "") -> str:
    if task not in ("remind", "system_report", "clean_temp"):
        return err("task must be remind, system_report or clean_temp.")
    every = float(every_minutes)
    if not (5 <= every <= 43200):
        return err("every_minutes must be between 5 and 43200.")
    if task == "remind" and not message:
        return err("A recurring reminder needs a message.")
    params = {"older_than_hours": 24} if task == "clean_temp" else {}
    t = get_scheduler().add(task, every * 60, every_s=every * 60, message=message, params=params)
    return ok(id=t["id"], first_run=_when(t["next_run"]), repeats_every_minutes=every)


@tool("list_scheduled_tasks", "List the reminders and recurring jobs that are set up.")
def list_scheduled_tasks() -> str:
    return ok(tasks=[{"id": t["id"], "task": t["kind"], "message": t["message"],
                      "next_run": _when(t["next_run"]),
                      "repeats_every_minutes": (t["every_s"] / 60) or None}
                     for t in get_scheduler().list()])


@tool("cancel_scheduled_task", "Cancel a reminder or recurring job by its id.",
      _obj({"task_id": {"type": "string"}}, ["task_id"]), SAFE)
def cancel_scheduled_task(task_id: str) -> str:
    return ok(cancelled=True) if get_scheduler().remove(task_id) else err("No task with that id.")


from . import tools_extra  # noqa: E402  (registers find_files, notes, morning report)

from . import tools_pro  # noqa: E402  (registers the advanced pack)

from . import tools_v2  # noqa: E402  (registers the 2.0 pack)

from . import tools_v3  # noqa: E402  (registers the 2.8 pack: undo, smart cleanup, backup, windows, scenes)

from . import tools_v4  # noqa: E402  (registers the 3.0 pack: network map, phone link, PC link)

EXTRA_TOOLS_MODULE = (tools_extra.__name__, tools_pro.__name__, tools_v2.__name__, tools_v3.__name__, tools_v4.__name__)


# --------------------------------------------------------------------------
# Executor: the only way a tool gets run
# --------------------------------------------------------------------------
def _log(source: str, name: str, args: dict, outcome: str) -> None:
    try:
        config.ensure_dir()
        with open(config.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}\t{source}\t{name}\t"
                    f"{json.dumps(args)}\t{outcome[:200]}\n")
    except OSError:
        pass


def clean_args(t: Tool, args: dict) -> dict:
    """Make small local models' tool calls usable.

    Small models often echo the tool's schema ({"type": "object", "properties": {...}}) instead of
    real arguments, send numbers as strings, or add arguments the tool doesn't have. This keeps only
    the arguments the tool really declares and fixes their types. It never invents a value.
    """
    props = t.parameters.get("properties", {})
    inner = args.get("properties")
    if isinstance(inner, dict) and "properties" not in props:
        # e.g. {"type": "object", "properties": {"sort_by": "cpu"}}: use real values, ignore schema fragments
        args = {**{k: v for k, v in inner.items() if not isinstance(v, (dict, list))}, **args}
    clean = {}
    for key, value in args.items():
        spec = props.get(key)
        if spec is None or isinstance(value, (dict, list)):
            continue                                   # not a declared argument (or a schema fragment)
        kind = spec.get("type")
        try:
            if kind == "boolean" and isinstance(value, str):
                value = value.strip().lower() in ("true", "yes", "1")
            elif kind == "integer" and not isinstance(value, bool):
                value = int(float(value))
            elif kind == "number" and not isinstance(value, bool):
                value = float(value)
            elif kind == "string" and not isinstance(value, str):
                value = str(value)
        except (TypeError, ValueError):
            continue                                   # unusable value: drop it, the tool reports what is missing
        clean[key] = value
    return clean


def _usage(t: Tool) -> str:
    need = t.parameters.get("required", [])
    opt = [k for k in t.parameters.get("properties", {}) if k not in need]
    return (f"{t.name} needs: {', '.join(need) if need else 'no arguments (send {})'}"
            + (f"; optional: {', '.join(opt)}" if opt else "") + ".")


class Executor:
    def __init__(self, settings: config.Settings, confirm: Callable[[str], bool]):
        self.settings = settings
        self.confirm = confirm
        # Set (briefly) by the app for requests that did not come from a person typing at the keyboard:
        #   "ask_all"   voice wake word: anything that is not a plain lookup asks first, even in auto_safe
        #   "read_only" a message from the phone: lookups only, nothing can be changed
        self.override: Optional[str] = None

    @property
    def mode(self) -> str:
        """The autonomy mode in force right now (the user's setting, made stricter by any override)."""
        if self.override == "read_only":
            return "report_only"
        if self.override == "ask_all" and self.settings.autonomy == "auto_safe":
            return "ask"
        return self.settings.autonomy

    POWER_NO_PROMPT = {"lock", "sleep", "shutdown", "restart", "cancel"}

    def _no_prompt(self, name: str, args: dict, source: str) -> bool:
        """Power commands the person typed or said run at once (shutdown and restart still wait a few seconds, so they can be
        cancelled). The AI's own guesses and anything from the phone still ask or are blocked."""
        return (name == "power_action" and source == "user" and getattr(self.settings, "power_without_asking", True)
                and str(args.get("action", "")).lower() in self.POWER_NO_PROMPT and self.override != "read_only")

    # Raw PowerShell is the one thing that always asks, even when the person typed it: a misheard word must not run code.
    ALWAYS_ASK = {"run_command"}

    def _obeys(self, t: Tool, name: str, source: str) -> bool:
        """True when this was the person's own command (typed, said, or a tool button), so no question is needed.
        An action the AI chooses while working on the person's request ("ai") counts as their command too, whatever its size,
        because every AI turn starts from something the person typed or said. Only things Laptop AI starts by itself
        (watchdog, hog watcher, scheduled jobs) still ask."""
        if not getattr(self.settings, "obey_commands", True) or self.override == "read_only" or name in self.ALWAYS_ASK:
            return False
        if name == "power_action" and not getattr(self.settings, "power_without_asking", True):
            return False
        return source in ("user", "ai")

    def available(self) -> list[Tool]:
        """The tools the AI is even told about (report-only mode hides every action)."""
        tools_ = [t for t in REGISTRY.values() if t.name not in ADVANCED or self.settings.allow_commands]
        if self.mode == "report_only":
            return [t for t in tools_ if t.risk == READ]
        return tools_

    def run(self, name: str, args: dict, source: str = "user") -> str:
        t = REGISTRY.get(name)
        if t is None:
            return err(f"Unknown tool '{name}'. The only tools are: "
                       f"{', '.join(x.name for x in self.available())}. Do not invent tools.")
        if name in ADVANCED and not self.settings.allow_commands:
            return err("Advanced commands are switched off. The user can turn them on in Settings > Advanced.")
        args = clean_args(t, args if isinstance(args, dict) else {})

        if t.risk != READ:
            mode = self.mode
            if mode == "report_only":
                _log(source, name, args, "blocked: report-only mode")
                return err("Report-only mode is on, so I can't make changes. "
                           "Switch the autonomy setting to change things.")
            if self._no_prompt(name, args, source) or self._obeys(t, name, source):
                pass                                          # the person just told us to do this: do it, no question
            elif t.risk == HIGH or (t.risk == LOW and mode == "ask"):
                text = t.describe(args) if t.describe else f"Run {name} with {args}?"
                if not self.confirm(text):
                    _log(source, name, args, "declined by user")
                    return json.dumps({"status": "declined_by_user"})
        try:
            result = t.func(**args)
        except TypeError:
            result = err(f"Wrong arguments. {_usage(t)} Call it again correctly, or tell the user.")
        except Exception as e:                       # tool bugs must not crash the assistant
            result = err(f"{name} failed: {e}")
        if t.risk != READ:
            _log(source, name, args, result)
        return result
