"""The 2.0 pack: memory, document search, volume and media, focus mode, weekly cleanup, health check.

  READ   search_documents, list_memories, run_diagnostics, focus_status
  SAFE   remember, forget, stop_focus_mode, schedule_weekly_cleanup
  LOW    volume, media_control          (asks in "ask" mode)
  HIGH   focus_mode                     (ALWAYS asks: it closes distracting programs)
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import psutil

from . import config, doctor, docsearch, focus, memory, tools_extra, volume
from .tools import HIGH, LOW, READ, SAFE, _obj, _when, err, get_scheduler, kill_process, ok, tool


# ---------------------------------------------------------------- memory
@tool("remember", "Save one short fact the user asked you to remember (for example 'my wifi is Home5G').",
      _obj({"text": {"type": "string"}}, ["text"]), SAFE)
def remember(text: str) -> str:
    entry = memory.add(text)
    return ok(saved=entry["text"], id=entry["id"]) if entry else err("Nothing to remember.")


@tool("list_memories", "List the facts the user asked you to remember.")
def list_memories() -> str:
    return ok(memories=[{"id": f["id"], "text": f["text"]} for f in memory.load()])


@tool("forget", "Forget remembered facts. Give the words that appear in the fact, or its id.",
      _obj({"text": {"type": "string"}}, ["text"]), SAFE)
def forget(text: str) -> str:
    n = memory.remove(text)
    return ok(forgotten=n) if n else err("I don't have anything remembered that matches that.")


# ---------------------------------------------------------------- document search
@tool("search_documents",
      "Search INSIDE the user's documents (text files, notes, code, Word .docx) in Desktop, Documents and "
      "Downloads for words, and show where they appear. Use find_files when they only know the file NAME.",
      _obj({"query": {"type": "string", "description": "Words to look for"},
            "folder": {"type": "string", "description": "Optional: Desktop, Documents or Downloads"},
            "limit": {"type": "integer"}}, ["query"]))
def search_documents(query: str, folder: str = "", limit: int = 6) -> str:
    roots = [r for r in tools_extra._roots(folder) if r.name in ("Desktop", "Documents", "Downloads")]
    if not roots:
        return err("I couldn't find your Documents, Desktop or Downloads folder.")
    res = docsearch.search(query, roots, max(1, min(int(limit), 15)))
    if "error" in res:
        return err(res["error"])
    return ok(**res)


# ---------------------------------------------------------------- volume and media
@tool("volume", "Change the PC's sound: up, down, mute (toggle), or set to a level 0-100.",
      _obj({"action": {"type": "string", "enum": ["up", "down", "mute", "set"]},
            "level": {"type": "integer", "description": "For 'set': 0-100"},
            "steps": {"type": "integer", "description": "For up/down: how many steps (each about 2%), default 5"}},
           ["action"]), LOW,
      lambda a: {"up": "Turn the volume up?", "down": "Turn the volume down?", "mute": "Mute or unmute the sound?",
                 "set": f"Set the volume to {a.get('level', '?')}%?"}.get(str(a.get("action")), "Change the volume?"))
def volume_tool(action: str, level: int = -1, steps: int = 5) -> str:
    action = str(action).lower()
    try:
        if action in ("up", "down"):
            n = max(1, min(int(steps), 50))
            volume.press(action, n)
            return ok(done=f"volume {action}", approx_change=f"{n * volume.STEP}%")
        if action == "mute":
            volume.press("mute")
            return ok(done="mute toggled")
        if action == "set":
            if not 0 <= int(level) <= 100:
                return err("level must be between 0 and 100.")
            return ok(done="volume set", level=volume.set_level(int(level)))
    except OSError as e:
        return err(str(e))
    return err("action must be up, down, mute or set.")




@tool("media_control", "Control music or video playing on the PC: play_pause, next or previous.",
      _obj({"action": {"type": "string", "enum": ["play_pause", "next", "previous"]}}, ["action"]), LOW,
      lambda a: f"Send '{a.get('action', 'play/pause')}' to the media player?")
def media_control(action: str) -> str:
    if action not in ("play_pause", "next", "previous"):
        return err("action must be play_pause, next or previous.")
    via = vlc_control(action)                        # VLC ignores the keyboard's media keys unless it has focus, so talk to it directly
    if via:
        return ok(done=action, via=via)
    try:
        volume.press(action)
    except OSError as e:
        return err(str(e))
    return ok(done=action)


MEDIA_WORDS = {"pause": "play_pause", "stop": "play_pause", "play": "play_pause", "resume": "play_pause",
               "next": "next", "skip": "next", "previous": "previous", "mute": "mute"}
MEDIA_DONE = {"play_pause": "Paused / resumed the music", "next": "Skipped to the next song", "previous": "Went back a song",
              "mute": "Muted the sound"}


def do_media(action: str) -> str:
    """What the scheduler runs when a timed media task is due. Returns a short sentence for the chat."""
    if action == "mute":
        volume.press("mute")
    else:
        out = json.loads(media_control(action))
        if out.get("status") == "error":
            return str(out.get("message", "That didn't work."))
    return MEDIA_DONE.get(action, "Done") + "."


@tool("schedule_media", "Pause, play, skip or mute the music after some minutes (for 'pause in 30 seconds', use minutes 0.5).",
      _obj({"action": {"type": "string", "enum": ["pause", "play", "next", "previous", "mute"]},
            "minutes": {"type": "number", "description": "Minutes from now (0.5 = 30 seconds)"}}, ["action", "minutes"]), SAFE)
def schedule_media(action: str, minutes: float) -> str:
    action = str(action).strip().lower()
    if action not in MEDIA_WORDS:
        return err("action must be pause, play, next, previous or mute.")
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        return err("minutes must be a number.")
    if not (0 < minutes <= 1440):
        return err("minutes must be between 0 and 1440 (one day).")
    t = get_scheduler().add("media", minutes * 60, message=action, params={"action": MEDIA_WORDS[action]})
    return ok(id=t["id"], due=datetime.fromtimestamp(t["next_run"]).strftime("%Y-%m-%d %H:%M:%S"), action=action)


# ---------------------------------------------------------------- focus mode
def _running_focus_apps() -> list[str]:
    want = {n.lower() for n in config.Settings.load().focus_apps}
    seen = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if n in want:
            seen.add(n)
    return sorted(seen)


def _focus_text(a: dict) -> str:
    try:
        apps = _running_focus_apps()
    except Exception:
        apps = []
    mins = a.get("minutes") or 30
    return (f"Start focus mode for {mins} minutes? Warnings and phone alerts go quiet (your own reminders still "
            f"ring)." + (f"\nThis will close: {', '.join(apps)}. Unsaved work in them will be lost." if apps
                         else "\nNo distracting programs are running right now."))


@tool("focus_mode",
      "Start focus mode for some minutes: pauses warnings and phone alerts and closes the user's distracting "
      "programs (chosen in Settings). The user's own reminders still come through.",
      _obj({"minutes": {"type": "number", "description": "How long, 1-600 (default 30)"}}), HIGH, _focus_text)
def focus_mode(minutes: float = 30) -> str:
    until = focus.STATE.start(minutes)
    closed = []
    for name in _running_focus_apps():
        r = json.loads(kill_process(name=name))
        if r.get("status") == "ok" and r.get("closed"):
            closed.append(name)
    return ok(focus_until=_when(until), minutes=max(1, round((until - time.time()) / 60)), closed=closed)


@tool("stop_focus_mode", "End focus mode early.", None, SAFE)
def stop_focus_mode() -> str:
    return ok(stopped=focus.STATE.stop())


@tool("focus_status", "Say whether focus mode is on and how long is left.")
def focus_status() -> str:
    return ok(focus_on=focus.STATE.active(), minutes_left=focus.STATE.minutes_left())


# ---------------------------------------------------------------- weekly cleanup
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def next_weekday(day: str, hhmm: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    h, m = (int(x) for x in hhmm.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    target += timedelta(days=(DAYS.index(day) - now.weekday()) % 7)
    if target <= now:
        target += timedelta(days=7)
    return target


@tool("schedule_weekly_cleanup",
      "Clean the temp-files folder automatically once a week, on a day and time (24-hour HH:MM). "
      "It still follows the autonomy setting, so in 'ask' mode it asks first.",
      _obj({"day": {"type": "string", "enum": DAYS}, "time": {"type": "string", "description": "HH:MM, 24-hour"}},
           ["day"]), SAFE)
def schedule_weekly_cleanup(day: str, time: str = "10:00") -> str:     # noqa: A002 (the tool's argument is "time")
    day = str(day).strip().lower()
    if day not in DAYS:
        return err("day must be a weekday name such as sunday.")
    try:
        when = next_weekday(day, time)
    except (ValueError, AttributeError):
        return err("time must look like 10:00 (24-hour).")
    task = get_scheduler().add("clean_temp", (when - datetime.now()).total_seconds(), every_s=7 * 86400,
                               params={"older_than_hours": 24})
    return ok(id=task["id"], first_run=_when(task["next_run"]), repeats="every week")


# ---------------------------------------------------------------- health check
@tool("run_diagnostics", "Check that the assistant itself is healthy (AI brains, microphone, phone link, "
                         "storage, saved files) and explain any problem and how to fix it.")
def run_diagnostics() -> str:
    checks = doctor.run_all(config.Settings.load())
    return ok(summary=doctor.summary(checks),
              checks=[{"check": c.name, "result": c.status, "detail": c.detail, "fix": c.fix} for c in checks])


# ---------------------------------------------------------------- hardware / malware health
@tool("hardware_health",
      "Scan for signs of malware and hardware trouble: Windows Defender detections and settings, suspicious programs, "
      "GPU and CPU temperature, disk health, hardware errors in the Windows log, broken devices, battery wear. "
      "Look-only; the findings are warning signs, not proof. Optional area: gpu, cpu, disk, memory, malware, "
      "motherboard or battery, to answer about one part.",
      _obj({"area": {"type": "string", "description": "Optional: gpu, cpu, disk, memory, malware, motherboard, battery"}}), READ)
def hardware_health(area: str = "") -> str:
    from . import health
    area = str(area or "").strip().lower()
    found = health.scan()
    real = [f for f in found if f.level != health.INFO]
    return ok(problems=len(real), findings=health.as_dicts(found), report=health.report(found, area))


# ---------------------------------------------------------------- music from this PC
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".wma", ".opus"}


def _open_file(path) -> None:
    import os
    import subprocess
    import sys
    if sys.platform.startswith("win"):
        os.startfile(str(path))                                  # noqa: the PC's default music player
    else:
        subprocess.Popen(["xdg-open", str(path)])


OPENER = _open_file                      # tests replace this so nothing really opens
VLC_PATHS = (r"C:\Program Files\VideoLAN\VLC\vlc.exe", r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe")


def _vlc_path() -> str:
    import os
    return next((p for p in VLC_PATHS if os.path.isfile(p)), "")


# VLC is started with its small local web remote on, so Laptop AI can pause and skip it for sure (only this PC can reach it).
VLC_PORT, VLC_PASSWORD = 58080, "laptopai"
VLC_HTTP_ARGS = ["--extraintf=http", "--http-host=127.0.0.1", f"--http-port={VLC_PORT}", f"--http-password={VLC_PASSWORD}"]
VLC_COMMANDS = {"play_pause": "pl_pause", "next": "pl_next", "previous": "pl_previous"}
APPCOMMAND = {"play_pause": 14, "next": 11, "previous": 12}          # WM_APPCOMMAND codes


def _open_in_vlc(playlist) -> bool:
    import subprocess
    exe = _vlc_path()
    if not exe:
        return False
    subprocess.Popen([exe, *VLC_HTTP_ARGS, str(playlist)])
    return True


def _vlc_running() -> bool:
    try:
        return any((p.info.get("name") or "").lower() in ("vlc.exe", "vlc") for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def _vlc_http(command: str) -> bool:
    """Ask the VLC we started to do something. False when it is not listening."""
    import base64
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{VLC_PORT}/requests/status.xml?command={command}")
    req.add_header("Authorization", "Basic " + base64.b64encode(f":{VLC_PASSWORD}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def _vlc_appcommand(action: str) -> bool:
    """For a VLC opened some other way: post the media command straight to its windows (Windows only)."""
    import os
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32                               # type: ignore[attr-defined]
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == "vlc.exe"}
    hits = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lp):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            hits.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    for hwnd in hits:
        user32.PostMessageW(hwnd, 0x0319, hwnd, APPCOMMAND[action] << 16)       # WM_APPCOMMAND
    return bool(hits)


def vlc_control(action: str) -> str:
    """'VLC' when VLC was found and told, otherwise ''."""
    if not _vlc_running():
        return ""
    if _vlc_http(VLC_COMMANDS[action]) or _vlc_appcommand(action):
        return "VLC"
    return ""


def find_music(query: str, roots, limit: int = 40, seconds: float = 8.0, max_files: int = 5000) -> dict:
    import os
    words = [w for w in str(query).lower().replace("-", " ").split() if w]
    deadline, scanned, hits = time.time() + seconds, 0, []
    for root in roots:
        for dirpath, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d.lower() not in tools_extra.SKIP_DIRS and not d.startswith(".")]
            for n in names:
                if os.path.splitext(n)[1].lower() not in AUDIO_EXT:
                    continue
                scanned += 1
                hay = (os.path.join(os.path.basename(dirpath), n)).lower()
                if all(w in hay for w in words):
                    hits.append(os.path.join(dirpath, n))
            if scanned >= max_files or time.time() > deadline:
                return {"files": hits[:limit], "total": len(hits), "cut_short": True}
    return {"files": hits[:limit], "total": len(hits), "cut_short": False}


@tool("play_local_music",
      "Play music stored on this PC (Music, Downloads, Desktop, Documents folders) in the default music player. "
      "A query picks songs or artists by name; an empty query shuffles everything found.",
      _obj({"query": {"type": "string", "description": "Optional song, artist or album words"},
            "player": {"type": "string", "description": "Optional: vlc to use VLC instead of the default player"}}), LOW,
      lambda a: f"Play {a.get('query') or 'music'} from your computer?")
def play_local_music(query: str = "", player: str = "") -> str:
    import os
    import random
    if not str(player).strip():
        player = config.Settings.load().music_player          # your favourite app from Settings
    roots = [r for r in tools_extra._roots("") if r.name in ("Music", "Downloads", "Desktop", "Documents")]
    roots.sort(key=lambda r: r.name != "Music")               # the Music folder first
    if not roots:
        return err("I couldn't find your Music, Downloads or Documents folders.")
    found = find_music(query, roots)
    if not found["files"]:
        what = f"called \"{query}\" " if query else ""
        return err(f"I found no music {what}in Music, Downloads, Desktop or Documents.")
    files = found["files"]
    if not query:
        random.shuffle(files)
    config.ensure_dir()
    playlist = config.APP_DIR / "playlist.m3u8"
    playlist.write_text("#EXTM3U\n" + "\n".join(files) + "\n", encoding="utf-8")
    used = ""
    try:
        if str(player).strip().lower().startswith("vlc") and OPENER is _open_file and _open_in_vlc(playlist):
            used = "VLC"
        else:
            OPENER(playlist)
    except OSError as e:
        return err(f"I found {len(files)} songs but couldn't start a player ({e.__class__.__name__}).")
    return ok(playing=os.path.basename(files[0]), queued=len(files), found=found["total"], player=used)
