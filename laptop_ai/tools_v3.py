"""The 2.8 pack: activity history and undo, smart cleanup (with a safe bin), backup to a drive, window control,
brightness, scenes, "why is my internet slow" and health history.

  READ   activity_history, find_junk, find_drives, why_is_internet_slow, health_trend, list_scenes
  SAFE   save_scene
  LOW    undo_last_action, backup_files, window_control, brightness, run_scene
  HIGH   move_to_trash   (files go to a safe bin first, never straight to nowhere, so "undo" can bring them back)
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import psutil

from . import config, health, history, trends
from .tools import HIGH, LOW, REGISTRY, SAFE, _mb, _obj, err, ok, tool
from .tools_extra import FOLDERS, SKIP_DIRS, _roots

TRASH_DIR = lambda: config.APP_DIR / "trash"          # noqa: E731  (a function so tests can move APP_DIR)
TRASH_KEEP_DAYS = 30


# ---------------------------------------------------------------- activity history and undo
@tool("activity_history", "Show the latest things Laptop AI did on this PC (closed programs, cleanups, power commands...).",
      _obj({"limit": {"type": "integer", "description": "How many, default 10"}}))
def activity_history(limit: int = 10) -> str:
    rows = [r for r in history.read_log() if r["source"] != "undo"][-max(1, min(int(limit or 10), 40)):]
    if not rows:
        return ok(activity=[], note="Nothing yet. Actions that change something are listed here.")
    last = history.last_undoable()
    return ok(activity=[history.describe(r) for r in reversed(rows)],
              can_undo=last[1][2] if last else None)


def _undo_text(a: dict) -> str:
    last = history.last_undoable()
    return f"{last[1][2]}?" if last else "Undo the last action?"


@tool("undo_last_action", "Undo the most recent action that can be reversed (start-up item, reminder, fact, deleted files, "
                          "volume, focus mode, pending shutdown...).", None, LOW, _undo_text)
def undo_last_action() -> str:
    last = history.last_undoable()
    if not last:
        return err("There is nothing I can undo. I can reverse start-up changes, reminders, saved facts, deleted files, "
                   "volume, focus mode and pending shutdowns.")
    row, (name, args, sentence) = last
    t = REGISTRY.get(name)
    if t is None:
        return err("I can't undo that one any more.")
    result = t.func(**args)
    history.mark_undone(row)
    from .tools import _log
    _log("undo", name, args, result)
    if '"status": "error"' in result:
        return result
    return ok(undone=sentence)


# ---------------------------------------------------------------- the safe bin and smart cleanup
def _allowed_roots() -> list[Path]:
    return [p.resolve() for p in _roots()]


def _inside(path: Path, roots: list[Path]) -> bool:
    try:
        r = path.resolve()
    except OSError:
        return False
    return any(r == root or root in r.parents for root in roots)


LAST_JUNK: list[dict] = []                              # what the last find_junk offered, so "delete those" knows


def _head_hash(path: str, size: int) -> str:
    h = hashlib.sha1(str(size).encode())
    try:
        with open(path, "rb") as f:
            h.update(f.read(1 << 20))
            if size > (2 << 20):
                f.seek(-(1 << 20), 2)
                h.update(f.read(1 << 20))
    except OSError:
        return ""
    return h.hexdigest()


def scan_junk(roots: list[Path], seconds: float = 12.0, max_files: int = 60000, now: Optional[float] = None,
              big_mb: int = 300, big_days: int = 60, installer_days: int = 30) -> list[dict]:
    """Candidates to remove, each with a reason. Never touches anything outside `roots`."""
    now = now or time.time()
    deadline = time.time() + seconds
    seen, by_size, out = 0, {}, []
    installer_ext = {".exe", ".msi", ".iso", ".msix", ".dmg"}
    for root in roots:
        for dirpath, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            in_downloads = "downloads" in [p.lower() for p in Path(dirpath).parts]
            for n in names:
                p = os.path.join(dirpath, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                seen += 1
                age = (now - st.st_mtime) / 86400
                ext = os.path.splitext(n)[1].lower()
                if st.st_size >= big_mb * 1048576 and age >= big_days:
                    out.append({"path": p, "size": st.st_size, "why": f"big and untouched for {age:.0f} days"})
                elif in_downloads and ext in installer_ext and age >= installer_days and st.st_size >= 5 << 20:
                    out.append({"path": p, "size": st.st_size, "why": f"old installer ({age:.0f} days in Downloads)"})
                elif st.st_size >= 1 << 20:
                    by_size.setdefault(st.st_size, []).append((p, st.st_mtime))
                if seen >= max_files or time.time() > deadline:
                    break
            if seen >= max_files or time.time() > deadline:
                break
    taken = {c["path"] for c in out}
    for size, files in by_size.items():
        if len(files) < 2:
            continue
        groups: dict[str, list] = {}
        for p, mt in files:
            groups.setdefault(_head_hash(p, size), []).append((p, mt))
        for h, g in groups.items():
            if not h or len(g) < 2:
                continue
            g.sort(key=lambda x: x[1])                  # keep the oldest copy, offer the newer ones
            for p, _mt in g[1:]:
                if p not in taken:
                    out.append({"path": p, "size": size, "why": f"same as {os.path.basename(g[0][0])}"})
    out.sort(key=lambda c: -c["size"])
    return out


@tool("find_junk", "Look for big old files, old installers and duplicate files in the user's personal folders and list them "
                   "with sizes. Look-only: nothing is deleted.")
def find_junk() -> str:
    found = scan_junk(_roots())
    LAST_JUNK[:] = found[:40]
    total = sum(c["size"] for c in found[:40])
    return ok(found=len(found), could_free=_mb(total),
              candidates=[{"n": i + 1, "file": os.path.basename(c["path"]), "size": _mb(c["size"]), "why": c["why"],
                           "folder": os.path.dirname(c["path"])} for i, c in enumerate(found[:12])],
              note="Say \"delete those\" to move them to the safe bin (you can undo for 30 days).")


def _trash_text(a: dict) -> str:
    paths = _pick(a)
    size = sum(c["size"] for c in paths)
    names = ", ".join(os.path.basename(c["path"]) for c in paths[:3])
    return (f"Move {len(paths)} file{'s' if len(paths) != 1 else ''} ({_mb(size)}) to the safe bin? {names}"
            f"{'...' if len(paths) > 3 else ''}\nThey stay recoverable for {TRASH_KEEP_DAYS} days (say \"undo\").")


def _pick(a: dict) -> list[dict]:
    if str(a.get("which", "")).lower() == "last" or not a.get("paths"):
        return list(LAST_JUNK)
    paths = a["paths"] if isinstance(a["paths"], list) else [a["paths"]]
    roots = _allowed_roots()
    out = []
    for p in paths:
        pp = Path(str(p))
        if pp.is_file() and _inside(pp, roots):
            out.append({"path": str(pp), "size": pp.stat().st_size, "why": ""})
    return out


@tool("move_to_trash", "Move files to Laptop AI's safe bin (recoverable for 30 days). which='last' moves everything find_junk "
                       "offered; or give exact paths inside the user's personal folders.",
      _obj({"which": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}}), HIGH, _trash_text)
def move_to_trash(which: str = "", paths: Optional[list] = None) -> str:
    chosen = _pick({"which": which, "paths": paths})
    roots = _allowed_roots()
    chosen = [c for c in chosen if Path(c["path"]).is_file() and _inside(Path(c["path"]), roots)]
    if not chosen:
        return err("There are no files to move. Say \"find junk\" first, or give a file inside your personal folders.")
    batch = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    folder = TRASH_DIR() / batch
    folder.mkdir(parents=True, exist_ok=True)
    manifest, freed = [], 0
    for i, c in enumerate(chosen):
        src = Path(c["path"])
        dst = folder / f"{i:03d}_{src.name}"
        try:
            size = src.stat().st_size
            shutil.move(str(src), str(dst))
        except OSError:
            continue
        manifest.append({"from": str(src), "to": str(dst)})
        freed += size
    config.write_json(folder / "manifest.json", manifest)
    LAST_JUNK.clear()
    _purge_old_trash()
    if not manifest:
        return err("Windows wouldn't let me move those files (they may be open in another program).")
    return ok(batch=batch, moved=len(manifest), freed=_mb(freed), note="Say \"undo\" to put them back.")


def _purge_old_trash(now: Optional[float] = None) -> None:
    now = now or time.time()
    try:
        for d in TRASH_DIR().iterdir():
            if d.is_dir() and now - d.stat().st_mtime > TRASH_KEEP_DAYS * 86400:
                shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


@tool("restore_from_trash", "Put files from the safe bin back where they were (by batch id).",
      _obj({"batch": {"type": "string"}}, ["batch"]), LOW, lambda a: "Put the deleted files back where they were?")
def restore_from_trash(batch: str) -> str:
    folder = TRASH_DIR() / str(batch).strip()
    manifest = config.read_json(folder / "manifest.json", [])
    if not manifest:
        return err("That batch isn't in the safe bin any more.")
    back = 0
    for m in manifest:
        src, dst = Path(m["to"]), Path(m["from"])
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst = dst.with_name(dst.stem + " (restored)" + dst.suffix)
        try:
            shutil.move(str(src), str(dst))
            back += 1
        except OSError:
            continue
    shutil.rmtree(folder, ignore_errors=True)
    return ok(restored=back)


# ---------------------------------------------------------------- backup to a drive
def _removable_drives() -> list[str]:
    out = []
    if os.name == "nt":
        import ctypes
        for part in psutil.disk_partitions(all=False):
            try:
                if ctypes.windll.kernel32.GetDriveTypeW(part.mountpoint) == 2:          # type: ignore[attr-defined]
                    out.append(part.mountpoint)
            except Exception:
                continue
    else:
        out = [p.mountpoint for p in psutil.disk_partitions(all=False) if "removable" in p.opts.lower()]
    return out


DRIVES: Callable[[], list[str]] = _removable_drives          # tests replace this


@tool("find_drives", "List flash drives and other external drives that are plugged in, with free space.")
def find_drives() -> str:
    rows = []
    for d in DRIVES():
        try:
            u = psutil.disk_usage(d)
            rows.append({"drive": d, "free": _mb(u.free), "size": _mb(u.total)})
        except OSError:
            continue
    return ok(drives=rows, note="" if rows else "No external drive is plugged in.")


def _backup_text(a: dict) -> str:
    folders = a.get("folders") or "Documents, Desktop, Pictures"
    where = a.get("destination") or (DRIVES() or ["your external drive"])[0]
    return f"Copy your {folders} folders to {where} (folder Laptop_AI_Backup)? Only new or changed files are copied. Nothing is deleted."


@tool("backup_files", "Copy the user's personal folders (default Documents, Desktop, Pictures) to a plugged-in external drive. "
                      "Only new or changed files are copied and nothing is deleted anywhere.",
      _obj({"folders": {"type": "string", "description": "Comma separated, e.g. Documents, Desktop"},
            "destination": {"type": "string", "description": "A drive letter or folder. Default: the first external drive"}}),
      LOW, _backup_text)
def backup_files(folders: str = "", destination: str = "", seconds: float = 1500.0) -> str:
    names = [n.strip() for n in re.split(r"[,&]| and ", folders or "Documents, Desktop, Pictures") if n.strip()]
    names = [n for n in names if n.lower() in {f.lower() for f in FOLDERS}] or ["Documents", "Desktop", "Pictures"]
    if destination:
        dest_root = Path(destination if len(destination) > 2 else destination.rstrip(":\\/") + ":\\")
    else:
        drives = DRIVES()
        if not drives:
            return err("I can't see an external drive. Plug in the flash drive or hard drive and try again.")
        dest_root = Path(drives[0])
    if not dest_root.exists():
        return err(f"I can't reach {dest_root}.")
    target = dest_root / "Laptop_AI_Backup"
    sources = [p for n in names for p in _roots(n)]
    if not sources:
        return err("I couldn't find those folders on this PC.")
    deadline = time.time() + seconds
    copied = skipped = 0
    size = 0
    for src in sources:
        for dirpath, dirs, files in os.walk(src, followlinks=False):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            rel = Path(dirpath).relative_to(src.parent)
            for n in files:
                s, d = Path(dirpath) / n, target / rel / n
                try:
                    st = s.stat()
                    if st.st_size > 4 << 30:
                        continue
                    if d.exists() and d.stat().st_size == st.st_size and d.stat().st_mtime >= st.st_mtime - 2:
                        skipped += 1
                        continue
                    d.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(s, d)
                    copied += 1
                    size += st.st_size
                except OSError:
                    continue
                if time.time() > deadline:
                    return ok(copied=copied, already_there=skipped, size=_mb(size), to=str(target),
                              note="I stopped after the time limit. Run it again to carry on.")
    return ok(copied=copied, already_there=skipped, size=_mb(size), to=str(target))


# ---------------------------------------------------------------- windows and brightness
class _WinBackend:
    """Talks to Windows. Tests replace it with a fake."""

    def windows(self) -> list[dict]:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32                                               # type: ignore[attr-defined]
        out: list[dict] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(hwnd, _lp):
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                proc = psutil.Process(pid.value).name().lower()
            except psutil.Error:
                proc = ""
            out.append({"hwnd": hwnd, "title": buf.value, "process": proc, "pid": pid.value})
            return True
        user32.EnumWindows(each, 0)
        return out

    def do(self, hwnd: int, action: str) -> None:
        import ctypes
        user32 = ctypes.windll.user32                                               # type: ignore[attr-defined]
        if action == "close":
            user32.PostMessageW(hwnd, 0x0010, 0, 0)                                  # WM_CLOSE: the program may ask to save
            return
        user32.ShowWindow(hwnd, {"minimise": 6, "maximise": 3}.get(action, 9))      # 6 minimise, 3 maximise, 9 restore
        if action in ("focus", "snap_left", "snap_right"):
            user32.SetForegroundWindow(hwnd)
        if action in ("snap_left", "snap_right"):
            time.sleep(0.15)
            self.hotkey([0x5B, 0x25 if action == "snap_left" else 0x27])

    def hotkey(self, keys: list[int]) -> None:
        import ctypes
        u = ctypes.windll.user32                                                    # type: ignore[attr-defined]
        for k in keys:
            u.keybd_event(k, 0, 0, 0)
        for k in reversed(keys):
            u.keybd_event(k, 0, 2, 0)


WIN: object = _WinBackend()
WINDOW_ACTIONS = ("minimise_all", "restore_all", "close", "focus", "minimise", "maximise", "snap_left", "snap_right")
GENERIC = {"app", "window", "it", "this", "that", "program"}


def _window_text(a: dict) -> str:
    act, app = str(a.get("action", "")), str(a.get("app", "")).strip()
    return {"minimise_all": "Minimise every window and show the desktop?", "restore_all": "Bring the windows back?"}.get(
        act, f"{act.replace('_', ' ').capitalize()} {app or 'that window'}?")


def _match(app: str, wins: list[dict]) -> list[dict]:
    key = re.sub(r"\.exe$", "", app.strip().lower())
    alias = {"vs code": "code", "visual studio code": "code", "google chrome": "chrome", "edge": "msedge",
             "file explorer": "explorer", "word": "winword", "excel": "excel", "powerpoint": "powerpnt"}.get(key, key)
    mine = os.getpid()
    out = []
    for w in wins:
        proc = re.sub(r"\.exe$", "", w["process"])
        if w.get("pid") == mine:
            continue                                   # never close, hide or move this app's own window
        if alias and (alias == proc or alias in w["title"].lower() or (len(alias) > 3 and alias in proc)):
            out.append(w)
    return out


@tool("window_control", "Control open windows: minimise_all, restore_all, or for one program (app): focus, minimise, "
                        "maximise, close (politely, the program can ask to save), snap_left, snap_right.",
      _obj({"action": {"type": "string", "enum": list(WINDOW_ACTIONS)}, "app": {"type": "string"}}, ["action"]), LOW, _window_text)
def window_control(action: str, app: str = "") -> str:
    action = str(action).strip().lower().replace(" ", "_")
    if action not in WINDOW_ACTIONS:
        return err("action must be one of: " + ", ".join(WINDOW_ACTIONS))
    if os.name != "nt" and isinstance(WIN, _WinBackend):
        return err("Window control only works on Windows.")
    try:
        if action == "minimise_all":
            WIN.hotkey([0x5B, 0x44])                                                  # Win + D
            return ok(done="showed the desktop")
        if action == "restore_all":
            WIN.hotkey([0x5B, 0x10, 0x4D])                                            # Win + Shift + M
            return ok(done="restored the windows")
        if not app or app.strip().lower() in GENERIC:
            return err("Which program? For example: \"snap chrome to the left\".")
        found = _match(app, WIN.windows())
        if not found:
            return err(f"I can't see an open window for {app}.")
        for w in (found if action == "close" else found[:1]):
            WIN.do(w["hwnd"], action)
        return ok(done=action, program=app, windows=len(found) if action == "close" else 1)
    except (OSError, AttributeError) as e:
        return err(f"Windows wouldn't do that ({e.__class__.__name__}).")


@tool("brightness", "Set the laptop screen brightness, 0-100 (laptops and some monitors only).",
      _obj({"level": {"type": "integer"}}, ["level"]), LOW, lambda a: f"Set the screen brightness to {a.get('level', '?')}%?")
def brightness(level: int) -> str:
    try:
        level = int(level)
    except (TypeError, ValueError):
        return err("level must be a number from 0 to 100.")
    if not 0 <= level <= 100:
        return err("level must be between 0 and 100.")
    if not sys.platform.startswith("win") and health.powershell is _REAL_PS:
        return err("Brightness only works on Windows.")
    out = health.powershell(f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level}); 'ok'")
    if "ok" not in (out or ""):
        return err("This screen doesn't allow brightness changes from software (a desktop monitor, usually). Use its buttons.")
    return ok(brightness=level)


_REAL_PS = health.powershell


# ---------------------------------------------------------------- scenes
BUILTIN_SCENES = {
    "study": [("focus_mode", {"minutes": 90}), ("volume", {"action": "set", "level": 15}), ("brightness", {"level": 70})],
    "movie": [("brightness", {"level": 60}), ("volume", {"action": "set", "level": 45}), ("focus_mode", {"minutes": 150})],
    "work": [("open_app", {"name": "code"}), ("open_app", {"name": "chrome"}), ("volume", {"action": "set", "level": 25})],
    "relax": [("volume", {"action": "set", "level": 35}), ("brightness", {"level": 50}), ("play_local_music", {"query": ""})],
    "goodnight": [("media_control", {"action": "play_pause"}), ("volume", {"action": "set", "level": 0}), ("brightness", {"level": 20})],
}


def scenes() -> dict[str, list[tuple[str, dict]]]:
    out = {k: [(t, dict(a)) for t, a in v] for k, v in BUILTIN_SCENES.items()}
    for name, steps in (getattr(config.Settings.load(), "scenes", {}) or {}).items():
        if isinstance(steps, list):
            out[str(name).lower()] = [(s[0], s[1]) for s in steps if isinstance(s, (list, tuple)) and len(s) == 2 and isinstance(s[1], dict)]
    return out


SCENE_TOOLS = {"focus_mode", "volume", "brightness", "open_app", "open_website", "play_local_music", "media_control",
               "stop_focus_mode", "window_control"}


def parse_steps(text: str) -> list[tuple[str, dict]]:
    """'open steam, volume 60, brightness 100, focus 45 minutes, play music' -> steps. Unknown parts are skipped."""
    steps: list[tuple[str, dict]] = []
    for part in re.split(r",|;|\band then\b|\bthen\b|\band\b(?=\s+(?:open|volume|brightness|focus|play|pause|mute|set))", text.lower()):
        p = part.strip()
        if not p:
            continue
        m = re.match(r"^(?:open|start|launch)\s+(.+)$", p)
        if m:
            steps.append(("open_app", {"name": m.group(1).strip()}))
        elif (m := re.match(r"^(?:set )?(?:the )?(?:volume|sound)(?: to)?\s*(\d{1,3})", p)):
            steps.append(("volume", {"action": "set", "level": min(100, int(m.group(1)))}))
        elif (m := re.match(r"^(?:set )?(?:the )?(?:screen )?brightness(?: to)?\s*(\d{1,3})", p)):
            steps.append(("brightness", {"level": min(100, int(m.group(1)))}))
        elif (m := re.match(r"^focus(?: mode)?(?: for)?\s*(\d+)?", p)):
            steps.append(("focus_mode", {"minutes": int(m.group(1) or 30)}))
        elif re.match(r"^(?:play|start) (?:some )?music", p):
            steps.append(("play_local_music", {"query": ""}))
        elif re.match(r"^(?:pause|stop) (?:the )?music", p):
            steps.append(("media_control", {"action": "play_pause"}))
        elif re.match(r"^mute", p):
            steps.append(("volume", {"action": "set", "level": 0}))
    return steps


def _step_words(step: tuple[str, dict]) -> str:
    t, a = step
    return {"focus_mode": lambda: f"focus mode for {a.get('minutes', 30)} min (closes your distracting programs)",
            "volume": lambda: f"volume {a.get('level', '')}%", "brightness": lambda: f"brightness {a.get('level')}%",
            "open_app": lambda: f"open {a.get('name')}", "play_local_music": lambda: "play music from your PC",
            "media_control": lambda: "pause the music", "open_website": lambda: f"open {a.get('url')}"}.get(t, lambda: t)()


def _scene_text(a: dict) -> str:
    name = str(a.get("name", "")).strip().lower()
    steps = scenes().get(name)
    if not steps:
        return f"Run the scene {name}?"
    return f"Start {name} mode?\n" + "\n".join("  • " + _step_words(s) for s in steps)


@tool("list_scenes", "List the scenes (one phrase that runs several actions), like study mode or movie mode.")
def list_scenes() -> str:
    return ok(scenes=[{"name": n, "does": "; ".join(_step_words(s) for s in steps)} for n, steps in sorted(scenes().items())])


@tool("save_scene", "Create or replace a scene from plain words, for example name='gaming', steps='open steam, volume 60, "
                    "brightness 100'.", _obj({"name": {"type": "string"}, "steps": {"type": "string"}}, ["name", "steps"]), SAFE)
def save_scene(name: str, steps: str) -> str:
    name = re.sub(r"[^a-z0-9 ]", "", str(name).lower()).strip()
    parsed = parse_steps(str(steps))
    if not name or not parsed:
        return err("I need a name and at least one step, like: open steam, volume 60, brightness 100.")
    s = config.Settings.load()
    s.scenes = {**s.scenes, name: [[t, a] for t, a in parsed]}
    s.save()
    return ok(saved=name, does="; ".join(_step_words(x) for x in parsed))


@tool("run_scene", "Run a scene by name: study, movie, work, relax, goodnight, or one the user made. Asks once, then does every step.",
      _obj({"name": {"type": "string"}}, ["name"]), LOW, _scene_text)
def run_scene(name: str) -> str:
    key = str(name).strip().lower().replace(" mode", "").replace(" scene", "")
    steps = scenes().get(key)
    if not steps:
        return err(f"I don't know a scene called {name}. I know: " + ", ".join(sorted(scenes())) + ".")
    done, failed = [], []
    for t, a in steps:
        tool_ = REGISTRY.get(t)
        if tool_ is None or t not in SCENE_TOOLS:
            continue
        try:
            res = tool_.func(**a)
        except Exception as e:                                                       # one failing step must not stop the rest
            res = err(str(e))
        (failed if '"status": "error"' in res else done).append(_step_words((t, a)))
    return ok(scene=key, done=done, skipped=failed)


# ---------------------------------------------------------------- slow internet
RUN: Callable = subprocess.run
GET: Optional[Callable] = None


def _ping(host: str = "1.1.1.1") -> Optional[tuple[float, float]]:
    """(average ms, loss %) or None when ping isn't available."""
    flag = "-n" if os.name == "nt" else "-c"
    try:
        r = RUN(["ping", flag, "4", host], capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
    except Exception:
        return None
    text = r.stdout or ""
    loss = re.search(r"(\d+(?:\.\d+)?)%\s*(?:packet\s*)?loss", text, re.I)
    avg = re.search(r"(?:Average|avg)[^\d]*=?\s*[\d.]*/?(\d+(?:\.\d+)?)", text, re.I)
    times = [float(x) for x in re.findall(r"time[=<]\s*(\d+(?:\.\d+)?)", text)]
    ms = float(avg.group(1)) if avg and os.name == "nt" else (sum(times) / len(times) if times else None)
    if ms is None and avg:
        ms = float(avg.group(1))
    if ms is None:
        return None
    return ms, float(loss.group(1)) if loss else 0.0


def _wifi() -> Optional[dict]:
    if os.name != "nt":
        return None
    try:
        r = RUN(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return None
    sig = re.search(r"^\s*Signal\s*:\s*(\d+)%", r.stdout or "", re.M)
    ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", r.stdout or "", re.M)
    rate = re.search(r"Receive rate \(Mbps\)\s*:\s*([\d.]+)", r.stdout or "")
    if not sig:
        return None
    return {"signal": int(sig.group(1)), "name": ssid.group(1).strip() if ssid else "", "mbps": float(rate.group(1)) if rate else None}


def _busy_programs(limit: int = 3) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == "ESTABLISHED" and c.pid:
                try:
                    n = psutil.Process(c.pid).name()
                except psutil.Error:
                    continue
                counts[n] = counts.get(n, 0) + 1
    except (psutil.Error, OSError):
        return []
    return sorted(counts.items(), key=lambda kv: -kv[1])[:limit]


def _rate_now(seconds: float = 2.0) -> float:
    a = psutil.net_io_counters()
    time.sleep(seconds)
    b = psutil.net_io_counters()
    return ((b.bytes_recv - a.bytes_recv) + (b.bytes_sent - a.bytes_sent)) / seconds


def _download_mbps(seconds: float = 8.0) -> Optional[float]:
    import requests
    get = GET or requests.get
    try:
        t0 = time.time()
        n = len(get("https://speed.cloudflare.com/__down?bytes=8000000", timeout=seconds + 7).content)
        return n * 8 / 1e6 / max(time.time() - t0, 0.001)
    except Exception:
        return None


@tool("why_is_internet_slow", "Find out why the internet feels slow: signal strength, delay, lost data, download speed and "
                              "which programs are busiest on the network. Takes about 15 seconds.")
def why_is_internet_slow() -> str:
    facts, reasons = {}, []
    wifi = _wifi()
    if wifi:
        facts["wifi"] = f"{wifi['name']} signal {wifi['signal']}%" + (f", link {wifi['mbps']:.0f} Mbps" if wifi["mbps"] else "")
        if wifi["signal"] < 45:
            reasons.append(f"The Wi-Fi signal is weak ({wifi['signal']}%). Move closer to the router.")
    ping = _ping()
    if ping:
        facts["delay"] = f"{ping[0]:.0f} ms, {ping[1]:.0f}% lost"
        if ping[1] >= 10:
            reasons.append(f"{ping[1]:.0f}% of data is getting lost on the way. The connection is unstable (router or signal).")
        elif ping[0] > 150:
            reasons.append(f"The delay is high ({ping[0]:.0f} ms). The line or the router is struggling.")
    else:
        reasons.append("I couldn't reach the internet at all. Check the router or the cable, then ask again.")
        return ok(facts=facts, verdict=" ".join(reasons))
    speed = _download_mbps()
    if speed is not None:
        facts["download"] = f"{speed:.1f} Mbps"
        if speed < 3:
            reasons.append(f"The download speed is low ({speed:.1f} Mbps).")
    busy = _busy_programs()
    if busy:
        facts["busiest_programs"] = ", ".join(f"{n} ({c} connections)" for n, c in busy)
        if busy[0][1] >= 40:
            reasons.append(f"{busy[0][0]} has {busy[0][1]} connections open. It may be hogging the line. Pause its downloads or updates.")
    rate = _rate_now(1.5)
    facts["traffic_now"] = f"{rate / 1024:,.0f} KB/s"
    if rate > 1.5e6:
        reasons.append("Something on this PC is using the network heavily right now (an update or a download).")
    return ok(facts=facts, verdict=" ".join(reasons) or "Nothing looks wrong on this PC. If it is still slow, the problem is "
                                                      "probably the provider or the website. Try restarting the router.")


# ---------------------------------------------------------------- health history
@tool("health_trend", "Show how the PC has been doing over the last days (CPU, memory, disk, battery averages and changes).",
      _obj({"days": {"type": "integer", "description": "1, 7 or 30"}}))
def health_trend(days: int = 7) -> str:
    return ok(report=trends.summary(max(1, min(int(days or 7), 30))))
