"""Link an Android phone so Laptop AI can see what is going on: battery, storage, what app is open, notifications, screen.

How it works: Android's own "USB debugging" door, used through Google's small free tool called adb. Nothing is installed on
the phone. The phone asks you to allow this PC once. Everything stays between the phone and this PC.

Limits, said plainly:
  - Android only. iPhones do not allow this; for an iPhone use the Telegram link.
  - You must switch on Developer options > USB debugging (or Wireless debugging) on the phone yourself.
  - Mostly read-only: Laptop AI looks, and does nothing on the phone by itself. The two exceptions are things YOU
    directly ask for — sending a file to the phone's Download folder, and opening an app you named — and neither
    ever runs on its own.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from . import config

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ADB_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
ADB_DIR = lambda: config.APP_DIR / "adb"            # noqa: E731


def adb_path() -> Optional[str]:
    """adb.exe: in the app's own folder, or anywhere on the PATH."""
    mine = ADB_DIR() / "platform-tools" / ("adb.exe" if sys.platform == "win32" else "adb")
    if mine.exists():
        return str(mine)
    import shutil
    return shutil.which("adb")


def _run(argv: list[str], timeout: float = 15, binary: bool = False):
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        return b"" if binary else f"error: {e}"
    return r.stdout if binary else (r.stdout + r.stderr).decode("utf-8", "replace")


RUN: Callable = _run


def download_adb(progress: Callable[[str], None] = lambda t: None, get: Callable[[str], bytes] = None) -> str:
    """One-time download of Google's platform-tools (about 8 MB) into the app's folder. Returns '' or an error sentence."""
    def fetch(url: str) -> bytes:
        with urllib.request.urlopen(url, timeout=60) as r:          # nosec - fixed Google URL
            return r.read()
    try:
        progress("Downloading Google's phone tool (about 8 MB)...")
        data = (get or fetch)(ADB_URL)
        progress("Unpacking...")
        dest = ADB_DIR()
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                target = (dest / name).resolve()
                if dest.resolve() not in target.parents and target != dest.resolve():     # never write outside the folder
                    continue
                if name.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(name))
        return "" if adb_path() else "The download worked but adb was not found inside it."
    except Exception as e:
        return f"I couldn't download the phone tool ({e}). Check the internet, or install 'Android platform-tools' yourself."


# ---------------------------------------------------------------- parsing adb's text (pure, so it can be tested)
def parse_devices(text: str) -> list[dict]:
    out = []
    for line in text.splitlines()[1:]:
        m = re.match(r"(\S+)\s+(device|unauthorized|offline|no permissions)\b(.*)", line.strip())
        if m:
            model = re.search(r"model:(\S+)", m.group(3))
            out.append({"serial": m.group(1), "state": m.group(2), "model": model.group(1).replace("_", " ") if model else ""})
    return out


def parse_battery(text: str) -> dict:
    def num(key):
        m = re.search(rf"^\s*{key}:\s*(-?\d+)", text, re.M)
        return int(m.group(1)) if m else None
    level, scale = num("level"), num("scale") or 100
    status = num("status")
    plugged = bool(re.search(r"(AC|USB|Wireless) powered:\s*true", text))
    temp = num("temperature")
    return {"percent": round(level * 100 / scale) if level is not None else None,
            "charging": plugged or status == 2,
            "temp_c": round(temp / 10, 1) if temp is not None else None}


def parse_storage(text: str) -> dict:
    """From `df /data`: the data line's size, used and free (in 1K blocks)."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] != "Filesystem" and parts[1].isdigit() and parts[2].isdigit():
            total, used = int(parts[1]) * 1024, int(parts[2]) * 1024
            return {"total_gb": round(total / 1e9, 1), "free_gb": round((total - used) / 1e9, 1),
                    "used_pct": round(used * 100 / total) if total else None}
    return {}


def parse_foreground(text: str) -> str:
    m = re.search(r"(?:mResumedActivity|topResumedActivity)[^\n]*?\s([\w.]+)/", text)
    return m.group(1) if m else ""


def friendly_app(package: str) -> str:
    known = {"com.whatsapp": "WhatsApp", "com.facebook.katana": "Facebook", "com.instagram.android": "Instagram",
             "com.google.android.youtube": "YouTube", "com.android.chrome": "Chrome", "org.telegram.messenger": "Telegram",
             "com.google.android.apps.maps": "Maps", "com.spotify.music": "Spotify", "com.zhiliaoapp.musically": "TikTok",
             "com.android.settings": "Settings", "com.sec.android.app.launcher": "Home screen", "com.miui.home": "Home screen",
             "com.google.android.dialer": "Phone", "com.android.dialer": "Phone", "com.google.android.gm": "Gmail",
             "com.twitter.android": "X", "com.snapchat.android": "Snapchat", "com.google.android.apps.photos": "Photos",
             "com.android.vending": "Play Store", "com.google.android.apps.messaging": "Messages"}
    return known.get(package, package.split(".")[-1].replace("_", " ").title() if package else "")


def parse_notifications(text: str, limit: int = 8) -> list[dict]:
    """Recent notifications: app and title (never the message body, which can be private)."""
    out = []
    for block in re.split(r"NotificationRecord\(", text)[1:]:
        pkg = re.search(r"pkg=([\w.]+)", block)
        title = re.search(r"android\.title=(?:String \()?([^\n]+?)\)?\s*(?:\n|$)", block)
        if pkg and pkg.group(1) not in ("android", "com.android.systemui"):
            out.append({"app": friendly_app(pkg.group(1)), "title": (title.group(1).strip() if title else "")[:60]})
    return out[:limit]


# ---------------------------------------------------------------- using adb
def _adb(args: list[str], serial: str = "", timeout: float = 15, binary: bool = False):
    path = adb_path()
    if not path:
        return b"" if binary else "error: adb not installed"
    cmd = [path] + (["-s", serial] if serial else []) + args
    return RUN(cmd, timeout=timeout, binary=binary) if binary else RUN(cmd, timeout)


def devices() -> list[dict]:
    return parse_devices(_adb(["devices", "-l"]))


def connect_wifi(address: str) -> str:
    """Wireless debugging: 'ip:port' as shown on the phone."""
    address = address.strip()
    if not re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?", address):
        return "Type the phone's address like 192.168.1.20:5555."
    if ":" not in address:
        address += ":5555"
    out = _adb(["connect", address], timeout=10)
    return "Connected." if "connected to" in out.lower() else out.strip()[:200] or "I couldn't connect."


def _shell(serial: str, cmd: str, timeout: float = 15) -> str:
    return _adb(["shell", cmd], serial, timeout)


def status(serial: str = "") -> dict:
    """Everything useful about one phone. serial '' = the only one connected."""
    devs = devices()
    ready = [d for d in devs if d["state"] == "device"]
    if not ready:
        if any(d["state"] == "unauthorized" for d in devs):
            return {"error": "The phone is asking to trust this PC. Look at its screen and tap Allow (tick 'Always allow')."}
        if not adb_path():
            return {"error": "The phone tool isn't installed yet. Use Devices > Set up phone link."}
        return {"error": "I don't see a phone. Plug it in with USB, and switch on USB debugging in Developer options."}
    dev = next((d for d in ready if d["serial"] == serial), ready[0]) if serial else ready[0]
    s = dev["serial"]
    prop = lambda k: _shell(s, f"getprop {k}").strip()          # noqa: E731
    awake = "mWakefulness=Awake" in _shell(s, "dumpsys power | grep mWakefulness=")
    return {"serial": s, "name": f"{prop('ro.product.manufacturer').title()} {prop('ro.product.model')}".strip() or dev["model"],
            "android": prop("ro.build.version.release"),
            "battery": parse_battery(_shell(s, "dumpsys battery")),
            "storage": parse_storage(_shell(s, "df /data")),
            "app": friendly_app(parse_foreground(_shell(s, "dumpsys activity activities | grep -E 'mResumedActivity|topResumedActivity'"))),
            "screen_on": awake}


def notifications(serial: str = "") -> list[dict]:
    st = status(serial)
    if "error" in st:
        return []
    return parse_notifications(_shell(st["serial"], "dumpsys notification --noredact", 20))


def screenshot(path: Path, serial: str = "") -> str:
    st = status(serial)
    if "error" in st:
        return st["error"]
    data = _adb(["exec-out", "screencap", "-p"], st["serial"], 20, binary=True)
    if not data or not data.startswith(b"\x89PNG"):
        return "The phone didn't give me a picture (the screen may be locked)."
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return ""


def push_file(local_path, dest_dir: str = "/sdcard/Download/", serial: str = "") -> tuple[bool, str]:
    """Send a file from this PC to the phone's Download folder (or another folder you name)."""
    st = status(serial)
    if "error" in st:
        return False, st["error"]
    p = Path(local_path)
    if not p.is_file():
        return False, "That file doesn't exist."
    dest = dest_dir.rstrip("/") + "/" + p.name
    out = _adb(["push", str(p), dest], st["serial"], timeout=60)
    if "error" in out.lower() or "failed" in out.lower() or "no such file" in out.lower():
        return False, out.strip()[:200] or "The phone refused the file."
    return True, f"Sent {p.name} to the phone's Download folder."


def pull_file(remote_path: str, local_dir, serial: str = "") -> tuple[bool, str, Optional[Path]]:
    """Copy a file off the phone (you give its full path on the phone, e.g. /sdcard/Download/photo.jpg)."""
    st = status(serial)
    if "error" in st:
        return False, st["error"], None
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    name = remote_path.rstrip("/").rsplit("/", 1)[-1] or "file"
    dest = local_dir / name
    _adb(["pull", remote_path.strip(), str(dest)], st["serial"], timeout=60)
    if not dest.exists():
        return False, "Couldn't find that file on the phone. Check the full path.", None
    return True, f"Pulled {name} from the phone.", dest


def list_apps(serial: str = "") -> list[dict]:
    """Apps the phone's owner installed (not the system apps that come with Android), so one can be opened."""
    st = status(serial)
    if "error" in st:
        return []
    out = _shell(st["serial"], "pm list packages -3", 15)
    pkgs = sorted(set(re.findall(r"package:(\S+)", out)))
    return [{"package": p, "label": friendly_app(p)} for p in pkgs]


def launch_app(package: str, serial: str = "") -> tuple[bool, str]:
    """Open a named app on the phone's screen."""
    package = str(package).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.]+", package or ""):
        return False, "That doesn't look like a real app package name."
    st = status(serial)
    if "error" in st:
        return False, st["error"]
    out = _shell(st["serial"], f"monkey -p {package} -c android.intent.category.LAUNCHER 1", 10)
    if "Events injected: 1" in out:
        return True, f"Opened {friendly_app(package)} on the phone."
    return False, "Couldn't open that app — check the package name (use the apps list to find it)."


def describe(st: dict) -> str:
    if "error" in st:
        return st["error"]
    b, sto = st["battery"], st["storage"]
    parts = [f"{st['name']} (Android {st['android']})."]
    if b.get("percent") is not None:
        parts.append(f"Battery {b['percent']}%{', charging' if b.get('charging') else ''}"
                     + (f", {b['temp_c']}°C" if b.get("temp_c") else "") + ".")
    if sto:
        parts.append(f"Storage: {sto['free_gb']} GB free of {sto['total_gb']} GB.")
    parts.append("Screen is on" + (f", showing {st['app']}." if st.get("app") else ".") if st.get("screen_on") else "Screen is off.")
    return " ".join(parts)
