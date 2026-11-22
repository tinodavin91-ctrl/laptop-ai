"""Check-for-updates: you publish a small JSON file somewhere you control (GitHub is the easy option, since you
already use it), and every copy of the app — yours and anyone you gave it to — can see a new version is out and
update itself with one click. Nobody has to be sent a fresh zip by hand again.

The manifest is just JSON, hosted anywhere reachable over plain HTTPS, for example a GitHub "raw" file URL:
    {
      "version": "3.4.0",
      "notes": "What changed in this release, a line or two.",
      "installer_url": "https://github.com/you/laptop-ai/releases/download/v3.4.0/laptop-ai-setup.exe",
      "sha256": "...the installer file's sha256, so a corrupted or tampered download is never run..."
    }
You edit that one file and upload the new installer next to it; every running copy that checks picks it up.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from . import __version__, config


@dataclass
class UpdateInfo:
    version: str
    notes: str
    installer_url: str
    sha256: str


def _version_tuple(v: str) -> tuple:
    bits = []
    for part in str(v).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        bits.append(int(digits) if digits else 0)
    return tuple(bits) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    return _version_tuple(remote) > _version_tuple(local)


def check(manifest_url: str, timeout: float = 6) -> tuple[Optional[UpdateInfo], str]:
    """Looks at the manifest. Returns (info, message). info is None if there's nothing newer, or on any problem
    (no internet, bad URL, bad JSON) — a failed check should never look like "you're up to date" to the caller,
    so the message always says which happened."""
    if not manifest_url:
        return None, "No update link has been set up yet (add one in Settings)."
    try:
        r = requests.get(manifest_url, timeout=timeout)
        r.raise_for_status()
        d = r.json()
    except requests.RequestException:
        return None, "Couldn't check for updates (no internet, or that link isn't reachable right now)."
    except ValueError:
        return None, "That update link didn't return valid information."
    version = str(d.get("version", "")).strip()
    url = str(d.get("installer_url", "")).strip()
    if not version or not url:
        return None, "That update link is missing something (version or download address)."
    if not is_newer(version):
        return None, f"You're already on the newest version ({__version__})."
    return UpdateInfo(version=version, notes=str(d.get("notes", "")), installer_url=url,
                      sha256=str(d.get("sha256", "")).lower()), f"Version {version} is available."


def download(info: UpdateInfo, progress: Callable[[float, str], None] = lambda f, s: None,
            cancelled: Callable[[], bool] = lambda: False, session=None) -> tuple[bool, str, Optional[Path]]:
    """Downloads the installer to a staging folder and checks its sha256 (when the manifest gave one) before
    trusting it. Returns (ok, message, path-to-run-or-None)."""
    get = (session or requests).get
    dest = config.APP_DIR / "updates" / Path(info.installer_url.split("?")[0]).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with get(info.installer_url, stream=True, timeout=(5, 120)) as r:
            if r.status_code != 200:
                return False, f"The download was refused ({r.status_code}).", None
            total = int(r.headers.get("Content-Length", 0)) or None
            done = 0
            h = hashlib.sha256()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if cancelled():
                        return False, "Download stopped.", None
                    if not chunk:
                        continue
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    progress((done / total) if total else -1.0, "Downloading...")
    except requests.RequestException as e:
        return False, f"The download failed ({e.__class__.__name__}). Try again.", None
    if info.sha256 and h.hexdigest().lower() != info.sha256:
        dest.unlink(missing_ok=True)
        return False, "The download didn't match what was expected, so I threw it away rather than run it. Try again.", None
    progress(1.0, "Ready to install.")
    return True, f"{info.version} is downloaded and checked.", dest


def apply_and_restart(installer_path: Path, before_exit: Callable[[], None] = lambda: None) -> bool:
    """Launches the installer (detached, so it survives this app closing) and asks the caller to close the app
    right after, since Windows can't usually overwrite a program's own running files."""
    try:
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([str(installer_path)], creationflags=flags, close_fds=True)
        else:
            subprocess.Popen([str(installer_path)], start_new_session=True)       # test/dev platforms only
    except OSError:
        return False
    before_exit()
    return True
