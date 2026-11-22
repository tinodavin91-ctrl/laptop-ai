"""Find and open any app installed on this PC, by name.

Two places are searched (both are what the Windows Start menu itself shows):
  * Start Menu shortcuts (.lnk files): classic desktop programs
  * Get-StartApps: Microsoft Store apps such as Xbox, Photos or Calculator
Nothing is run except the app you named. There is no way to give this a command line.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

CACHE_SECONDS = 300
_SKIP = ("uninstall", "readme", "release notes", "documentation", "license", "website", "help", "manual")
_cache: dict = {"at": 0.0, "apps": []}


def start_menu_dirs() -> list[Path]:
    dirs = []
    for var in ("ProgramData", "APPDATA"):
        base = os.environ.get(var)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return dirs


def scan_shortcuts(dirs: list[Path]) -> list[dict]:
    apps = []
    for d in dirs:
        if not d.is_dir():
            continue
        for lnk in d.rglob("*.lnk"):
            name = lnk.stem
            if any(w in name.lower() for w in _SKIP):
                continue
            apps.append({"name": name, "kind": "shortcut", "target": str(lnk)})
    return apps


def powershell_start_apps() -> list[dict]:
    """Microsoft Store (and other packaged) apps. Empty list if PowerShell is not available."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json"],
                             capture_output=True, text=True, timeout=20)
        data = json.loads(out.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    if isinstance(data, dict):
        data = [data]
    return [{"name": str(d["Name"]), "kind": "store", "target": str(d["AppID"])}
            for d in data if isinstance(d, dict) and d.get("Name") and d.get("AppID")]


def installed_apps(force: bool = False, dirs: Optional[list[Path]] = None,
                   store: Callable[[], list[dict]] = powershell_start_apps) -> list[dict]:
    if not force and dirs is None and _cache["apps"] and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["apps"]
    apps, seen = [], set()
    for a in scan_shortcuts(dirs if dirs is not None else start_menu_dirs()) + store():
        key = a["name"].lower()
        if key not in seen:
            seen.add(key)
            apps.append(a)
    apps.sort(key=lambda a: a["name"].lower())
    if dirs is None:
        _cache.update(at=time.time(), apps=apps)
    return apps


def find(query: str, apps: list[dict]) -> list[dict]:
    """Best matches first: exact name, then name starts with it, then every word appears in the name."""
    q = " ".join(str(query).lower().split())
    if not q:
        return []
    words = q.split()
    exact = [a for a in apps if a["name"].lower() == q]
    starts = [a for a in apps if a["name"].lower().startswith(q) and a not in exact]
    words_in = [a for a in apps if all(w in a["name"].lower() for w in words) and a not in exact + starts]
    return exact + starts + words_in


def launch(app: dict) -> None:
    if app["kind"] == "shortcut":
        os.startfile(app["target"])                                   # type: ignore[attr-defined]
    else:
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app["target"]])
