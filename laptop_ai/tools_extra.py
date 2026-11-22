"""More tools: find files, notes, and the daily morning report.

Same rules as tools.py: only what is registered here can be done, and every tool has a risk level.
  * find_files   READ   looks at file NAMES only. It never opens or reads a file, and it only searches
                        your personal folders (Desktop, Documents, Downloads, Pictures, Videos, Music).
  * add_note / search_notes   SAFE / READ   a small notes list kept in ~/.laptop_ai/notes.json
  * delete_note               LOW           asks first
  * schedule_morning_report   SAFE          a daily summary at a time you choose
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from . import config
from .tools import HIGH, LOW, READ, SAFE, _mb, _obj, _when, err, get_scheduler, ok, tool

# ---------------------------------------------------------------- find files
FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "appdata", "$recycle.bin"}
MAX_SCANNED = 300_000
MAX_SECONDS = 8.0


def _roots(folder: str = "") -> list[Path]:
    home = Path.home()
    names = [n for n in FOLDERS if not folder or n.lower() == folder.strip().lower()]
    found = []
    for n in names:
        for base in (home, home / "OneDrive"):             # OneDrive often holds Desktop/Documents
            p = base / n
            if p.is_dir() and p not in found:
                found.append(p)
    return found


@tool("find_files",
      "Find files by part of their NAME in the user's personal folders (Desktop, Documents, Downloads, "
      "Pictures, Videos, Music). Optionally only files changed in the last N days. Never reads file contents.",
      _obj({"query": {"type": "string", "description": "Words that appear in the file name"},
            "folder": {"type": "string", "description": "Optional: one of " + ", ".join(FOLDERS)},
            "days": {"type": "number", "description": "Optional: only files changed in the last N days"},
            "limit": {"type": "integer", "description": "How many to return (default 10, max 25)"}},
           ["query"]), READ)
def find_files(query: str, folder: str = "", days: float = 0, limit: int = 10) -> str:
    words = [w for w in str(query).lower().split() if w]
    if not words and not days:
        return err("Tell me part of the file name, or how recent it is.")
    if folder and folder.strip().lower() not in [f.lower() for f in FOLDERS]:
        return err("folder must be one of: " + ", ".join(FOLDERS))
    roots = _roots(folder)
    if not roots:
        return err("I couldn't find those folders on this PC.")
    limit = max(1, min(int(limit or 10), 25))
    cutoff = time.time() - float(days) * 86400 if days else 0
    deadline = time.time() + MAX_SECONDS
    hits, scanned, truncated = [], 0, False
    for root in roots:
        for dirpath, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            for n in names:
                scanned += 1
                if scanned > MAX_SCANNED or time.time() > deadline:
                    truncated = True
                    break
                low = n.lower()
                if words and not all(w in low for w in words):
                    continue
                fp = os.path.join(dirpath, n)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                if cutoff and st.st_mtime < cutoff:
                    continue
                hits.append((st.st_mtime, fp, st.st_size))
            if truncated:
                break
        if truncated:
            break
    hits.sort(reverse=True)
    return ok(found=len(hits), showing=[{"path": fp, "size": _mb(size), "modified": _when(m)}
                                        for m, fp, size in hits[:limit]],
              search_was_cut_short=truncated)


# ---------------------------------------------------------------- notes
def _load_notes() -> list[dict]:
    try:
        data = json.loads(config.NOTES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_notes(notes: list[dict]) -> None:
    config.ensure_dir()
    config.NOTES_FILE.write_text(json.dumps(notes, indent=2), encoding="utf-8")


@tool("add_note", "Save a short note for the user (for example 'call Kwame tomorrow').",
      _obj({"text": {"type": "string"}}, ["text"]), SAFE)
def add_note(text: str) -> str:
    text = str(text).strip()
    if not text:
        return err("The note is empty.")
    notes = _load_notes()
    note = {"id": uuid.uuid4().hex[:6], "text": text[:1000], "created": time.time()}
    notes.append(note)
    _save_notes(notes)
    return ok(id=note["id"], saved=note["text"])


@tool("search_notes", "Look through the user's saved notes. An empty query lists the latest ones.",
      _obj({"query": {"type": "string"}}), READ)
def search_notes(query: str = "") -> str:
    words = [w for w in str(query).lower().split() if w]
    notes = [n for n in _load_notes() if all(w in n["text"].lower() for w in words)]
    notes.sort(key=lambda n: n["created"], reverse=True)
    return ok(found=len(notes), notes=[{"id": n["id"], "text": n["text"], "saved": _when(n["created"])}
                                       for n in notes[:10]])


@tool("delete_note", "Delete one saved note by its id.", _obj({"note_id": {"type": "string"}}, ["note_id"]), LOW,
      lambda a: f"Delete note {a.get('note_id', '')}?")
def delete_note(note_id: str) -> str:
    notes = _load_notes()
    kept = [n for n in notes if n["id"] != str(note_id)]
    if len(kept) == len(notes):
        return err("No note with that id.")
    _save_notes(kept)
    return ok(deleted=True)


# ---------------------------------------------------------------- morning report
def _seconds_until(hhmm: str, now: Optional[datetime] = None) -> float:
    now = now or datetime.now()
    h, m = (int(x) for x in hhmm.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


@tool("schedule_morning_report",
      "Send the user a short daily summary of the laptop and their reminders at a set time, every day. "
      "time is 24-hour HH:MM, for example 07:30.",
      _obj({"time": {"type": "string", "description": "HH:MM, 24-hour"}}, ["time"]), SAFE)
def schedule_morning_report(time: str) -> str:        # noqa: A002  (the tool argument is called "time")
    try:
        hh, mm = (int(x) for x in str(time).strip().split(":"))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
    except ValueError:
        return err("time must look like 07:30 (24-hour).")
    sched = get_scheduler()
    for t in sched.list():                              # one morning report at a time
        if t["kind"] == "morning_report":
            sched.remove(t["id"])
    t = sched.add("morning_report", _seconds_until(f"{hh:02d}:{mm:02d}"), every_s=86400)
    return ok(id=t["id"], first_report=_when(t["next_run"]), repeats="every day")


def morning_report_text(status: dict, tasks: list[dict], notes: int, now: Optional[float] = None,
                        problems: Optional[list[str]] = None, trend: str = "") -> str:
    """Plain-text summary. Pure function so it can be tested."""
    now = now or time.time()
    lines = [f"☀ Good morning. {datetime.fromtimestamp(now).strftime('%A %d %B')}"]
    batt = status.get("battery_percent")
    lines.append(f"CPU {status.get('cpu_percent')}%, memory {status.get('ram_percent')}%, "
                 f"disk {status.get('disk_percent')}% full ({status.get('disk_free')} free).")
    lines.append("Battery: " + ("none (desktop PC)." if batt is None else
                                f"{batt:.0f}%{' and charging' if status.get('plugged_in') else ''}."))
    hours = status.get("pc_running_since_last_restart_hours")
    if isinstance(hours, (int, float)) and hours >= 72:
        lines.append(f"⚠ This PC hasn't been restarted for {hours / 24:.0f} days. A restart may help.")
    soon = [t for t in tasks if t["kind"] == "remind" and t["next_run"] - now <= 86400]
    if soon:
        lines.append("Reminders in the next 24 hours:")
        lines += [f"  • {_when(t['next_run'])}: {t['message']}" for t in soon[:8]]
    else:
        lines.append("No reminders in the next 24 hours.")
    if notes:
        lines.append(f"You have {notes} saved note{'s' if notes != 1 else ''}.")
    if problems:
        lines.append("Needs attention:")
        lines += [f"  • {p}" for p in problems[:5]]
    if trend and "enough history" not in trend:
        lines.append("")
        lines.append(trend)
    return "\n".join(lines)


# ---------------------------------------------------------------- advanced: services and PowerShell
@tool("list_services", "List Windows services (the background programs), optionally filtered by name or status "
                       "(running or stopped).",
      _obj({"query": {"type": "string"}, "status": {"type": "string", "enum": ["running", "stopped", ""]}}))
def list_services(query: str = "", status: str = "") -> str:
    import psutil
    if not hasattr(psutil, "win_service_iter"):
        return err("Services can only be listed on Windows.")
    q = str(query).lower().strip()
    out = []
    for s in psutil.win_service_iter():
        try:
            info = s.as_dict()
        except Exception:
            continue
        if status and info.get("status") != status:
            continue
        if q and q not in info.get("name", "").lower() and q not in info.get("display_name", "").lower():
            continue
        out.append({"name": info.get("name"), "display_name": info.get("display_name"),
                    "status": info.get("status"), "start_type": info.get("start_type")})
    return ok(found=len(out), services=out[:30])


COMMAND_TIMEOUT = 60
MAX_COMMAND_CHARS = 2000
MAX_OUTPUT_CHARS = 4000
RUNNER = None          # tests replace this; None means subprocess.run


def _argv(command: str) -> list[str]:
    if os.name == "nt":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["sh", "-c", command]


@tool("run_command",
      "Run ONE PowerShell command on this PC and return its output. Only use this when no other tool can do "
      "the job. The user sees the exact command and must approve it every time.",
      _obj({"command": {"type": "string"}}, ["command"]), HIGH,
      lambda a: "Run this PowerShell command on your PC?\n\n" + str(a.get("command", ""))[:1200])
def run_command(command: str) -> str:
    import subprocess
    command = str(command).strip()
    if not command:
        return err("The command is empty.")
    if len(command) > MAX_COMMAND_CHARS:
        return err(f"The command is too long (over {MAX_COMMAND_CHARS} characters).")
    run = RUNNER or subprocess.run
    try:
        r = run(_argv(command), capture_output=True, text=True, timeout=COMMAND_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return err(f"The command took longer than {COMMAND_TIMEOUT} seconds and was stopped.")
    except OSError as e:
        return err(f"Couldn't start it ({e.__class__.__name__}).")
    clip = lambda t: (t or "")[:MAX_OUTPUT_CHARS] + ("…(cut off)" if len(t or "") > MAX_OUTPUT_CHARS else "")  # noqa: E731
    return ok(exit_code=r.returncode, output=clip(r.stdout), errors=clip(r.stderr))
