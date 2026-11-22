"""The advanced pack: internet diagnosis, security check, big-file finder, startup manager,
power controls and screenshots.

Same rules as the rest: only what is registered here can be done, every tool has a risk level,
and anything that changes the PC asks first.
  READ   diagnose_internet, network_status, list_connections, security_check, find_large_files,
         list_startup_items
  LOW    take_screenshot, enable_startup_item        (asks in "ask" mode)
  HIGH   disable_startup_item, power_action          (ALWAYS asks; never available from the phone)
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import psutil

from . import config
from .tools import HIGH, LOW, _mb, _obj, _when, err, ok, tool
from .tools_extra import SKIP_DIRS, _roots

try:
    import winreg                    # Windows only
except ImportError:
    winreg = None

# ---------------------------------------------------------------- internet diagnosis
def _dns_ok(host: str = "cloudflare.com") -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def _tcp_ok(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def interpret(dns_ok: bool, ip_ok: bool, link_up: bool) -> str:
    if not link_up:
        return "This PC is not connected to any network. Check Wi-Fi or the cable."
    if ip_ok and dns_ok:
        return "The internet works: the connection and name lookup (DNS) are both fine."
    if ip_ok and not dns_ok:
        return ("The connection works but DNS (turning names into addresses) is failing. That explains sites and "
                "downloads that 'can't find the host'. Fix: set the DNS to 1.1.1.1 and 8.8.8.8 in the adapter "
                "settings, or try a hotspot.")
    if dns_ok and not ip_ok:
        return "Names resolve but connections time out. A firewall, VPN or router problem is likely."
    return "No internet: the network is up but nothing outside can be reached. Restart the router, or try a hotspot."


@tool("diagnose_internet",
      "Test the internet connection step by step (network link, DNS, reaching the internet) and say what is "
      "wrong. Set speed=true to also measure download speed (uses about 1 MB).",
      _obj({"speed": {"type": "boolean"}}))
def diagnose_internet(speed: bool = False) -> str:
    stats = psutil.net_if_stats()
    link_up = any(s.isup and not n.lower().startswith(("lo", "loopback")) for n, s in stats.items())
    dns = _dns_ok()
    ip = _tcp_ok("1.1.1.1", 443) or _tcp_ok("8.8.8.8", 53)
    result = {"network_link_up": link_up, "dns_works": dns, "can_reach_internet": ip,
              "diagnosis": interpret(dns, ip, link_up)}
    if speed and dns and ip:
        result["download_speed"] = _speed_test()
    return ok(**result)


def _speed_test(get=None) -> str:
    import requests
    get = get or requests.get
    try:
        t0 = time.time()
        n = len(get("https://speed.cloudflare.com/__down?bytes=1000000", timeout=15).content)
        secs = max(time.time() - t0, 0.001)
        return f"{n / 1024 / secs:,.0f} KB/s"
    except Exception as e:
        return f"could not measure ({e.__class__.__name__})"


@tool("network_status", "Show the network adapters that are up, with their IP addresses.")
def network_status() -> str:
    stats, addrs = psutil.net_if_stats(), psutil.net_if_addrs()
    rows = []
    for name, st in stats.items():
        if not st.isup:
            continue
        v4 = [a.address for a in addrs.get(name, []) if a.family == socket.AF_INET]
        if v4 and not v4[0].startswith("127."):
            rows.append({"adapter": name, "ip": v4[0], "speed_mbps": st.speed or None})
    return ok(adapters=rows)


@tool("list_connections",
      "List programs that are accepting connections (listening ports) and programs with active internet "
      "connections. Useful to see what is talking to the network.",
      _obj({"limit": {"type": "integer"}}))
def list_connections(limit: int = 15) -> str:
    limit = max(1, min(int(limit or 15), 40))
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return err("Windows blocked this lookup. Running the app as administrator allows it, but that is not needed normally.")
    names: dict[int, str] = {}

    def who(pid):
        if pid and pid not in names:
            try:
                names[pid] = psutil.Process(pid).name()
            except psutil.Error:
                names[pid] = "?"
        return names.get(pid, "?")
    listening = sorted({(who(c.pid), c.laddr.port, "all networks" if c.laddr.ip in ("0.0.0.0", "::") else "this PC only")
                        for c in conns if c.status == psutil.CONN_LISTEN and c.laddr})
    active = sorted({(who(c.pid), c.raddr.ip, c.raddr.port) for c in conns
                     if c.status == psutil.CONN_ESTABLISHED and c.raddr and not c.raddr.ip.startswith("127.")})
    return ok(listening=[{"program": p, "port": port, "reachable_from": r} for p, port, r in listening[:limit]],
              active_connections=[{"program": p, "to": f"{ip}:{port}"} for p, ip, port in active[:limit]])


# ---------------------------------------------------------------- security check
SYSTEM_NAMES = {"svchost.exe", "lsass.exe", "csrss.exe", "winlogon.exe", "services.exe", "smss.exe", "wininit.exe"}
RISKY_FOLDERS = ("\\appdata\\local\\temp\\", "\\downloads\\", "\\users\\public\\", "$recycle.bin", "\\windows\\temp\\")


def judge_process(name: str, exe: str, cpu: float = 0.0) -> list[str]:
    """Warning signs for one program. These are hints to look at, never proof."""
    reasons = []
    low, path = (name or "").lower(), (exe or "").lower().replace("/", "\\")
    if low in SYSTEM_NAMES and path and "\\windows\\system32\\" not in path:
        reasons.append("has the name of a Windows system program but is not in the Windows folder")
    if path and any(f in path for f in RISKY_FOLDERS):
        reasons.append("runs from a temporary, Downloads or public folder")
    if cpu >= 60 and path and any(f in path for f in RISKY_FOLDERS):
        reasons.append(f"is using {cpu:.0f}% CPU")
    return reasons


@tool("security_check",
      "Look for warning signs among the running programs (system-looking names in the wrong place, programs "
      "running from Downloads or temp folders) and the ports open to the network. Hints, not proof.")
def security_check() -> str:
    flagged = []
    for p in psutil.process_iter(["name", "exe", "cpu_percent"]):
        try:
            info = p.info
            reasons = judge_process(info.get("name") or "", info.get("exe") or "", info.get("cpu_percent") or 0)
        except psutil.Error:
            continue
        if reasons:
            flagged.append({"program": info.get("name"), "pid": p.pid, "where": info.get("exe"), "why": reasons})
    exposed = []
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.ip in ("0.0.0.0", "::"):
                try:
                    exposed.append(f"{psutil.Process(c.pid).name()} on port {c.laddr.port}")
                except (psutil.Error, TypeError):
                    exposed.append(f"unknown program on port {c.laddr.port}")
    except (psutil.AccessDenied, OSError):
        pass
    return ok(warning_signs=flagged[:15], ports_open_to_the_network=sorted(set(exposed))[:15],
              note=("Warning signs are hints, not proof: portable apps often run from Downloads. "
                    "Nothing was changed. If something looks wrong, run a Windows Security scan."
                    if flagged else "No warning signs found among running programs."))


# ---------------------------------------------------------------- big files
@tool("find_large_files",
      "Find the biggest files in the user's personal folders (Desktop, Documents, Downloads, Pictures, Videos, "
      "Music) to help free disk space. Never reads or changes files.",
      _obj({"folder": {"type": "string", "description": "Optional: one folder name, for example Downloads"},
            "limit": {"type": "integer"}}))
def find_large_files(folder: str = "", limit: int = 10) -> str:
    roots = _roots(folder)
    if not roots:
        return err("I couldn't find those folders on this PC.")
    limit = max(1, min(int(limit or 10), 25))
    deadline, scanned, cut, found = time.time() + 10, 0, False, []
    for root in roots:
        for dirpath, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            for n in names:
                scanned += 1
                if scanned > 300_000 or time.time() > deadline:
                    cut = True
                    break
                fp = os.path.join(dirpath, n)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                found.append((st.st_size, fp, st.st_mtime))
            if cut:
                break
        if cut:
            break
    found.sort(reverse=True)
    return ok(largest=[{"path": fp, "size": _mb(sz), "modified": _when(m)} for sz, fp, m in found[:limit]],
              search_was_cut_short=cut)


# ---------------------------------------------------------------- startup manager
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_backup_file() -> Path:
    return config.APP_DIR / "startup_backup.json"


def _run_values(reg, hive) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        key = reg.OpenKey(hive, RUN_KEY)
    except OSError:
        return out
    try:
        i = 0
        while True:
            try:
                name, value, _t = reg.EnumValue(key, i)
            except OSError:
                break
            out[name] = str(value)
            i += 1
    finally:
        reg.CloseKey(key)
    return out


def startup_items(reg=None) -> list[dict]:
    reg = reg or winreg
    if reg is None:
        return []
    items = [{"name": n, "command": c, "for": "this user", "can_disable": True}
             for n, c in _run_values(reg, reg.HKEY_CURRENT_USER).items()]
    items += [{"name": n, "command": c, "for": "all users", "can_disable": False}
              for n, c in _run_values(reg, reg.HKEY_LOCAL_MACHINE).items()]
    return sorted(items, key=lambda i: i["name"].lower())


@tool("list_startup_items", "List the programs that start automatically when this user signs in to Windows.")
def list_startup_items() -> str:
    if winreg is None:
        return err("The startup list is only available on Windows.")
    items = startup_items()
    disabled = list(_load_backup())
    return ok(found=len(items), items=items[:40], turned_off_by_this_app=disabled)


def _load_backup() -> dict:
    try:
        data = json.loads(startup_backup_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_backup(data: dict) -> None:
    config.ensure_dir()
    startup_backup_file().write_text(json.dumps(data, indent=2), encoding="utf-8")


@tool("disable_startup_item",
      "Stop one program from starting with Windows (only this user's list). The entry is saved so it can be "
      "turned back on with enable_startup_item.",
      _obj({"name": {"type": "string"}}, ["name"]), HIGH,
      lambda a: f"Stop '{a.get('name', '')}' from starting with Windows? (You can turn it back on later.)")
def disable_startup_item(name: str) -> str:
    reg = winreg
    if reg is None:
        return err("Only available on Windows.")
    values = _run_values(reg, reg.HKEY_CURRENT_USER)
    match = next((n for n in values if n.lower() == str(name).strip().lower()), None)
    if match is None:
        return err("No startup item with that name in your list. (Items for all users need administrator rights.)")
    backup = _load_backup()
    backup[match] = values[match]
    _save_backup(backup)                                   # save FIRST, so nothing can be lost
    key = reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE)
    try:
        reg.DeleteValue(key, match)
    finally:
        reg.CloseKey(key)
    return ok(disabled=match, can_be_restored=True)


@tool("enable_startup_item", "Turn a startup item back on that this app turned off earlier.",
      _obj({"name": {"type": "string"}}, ["name"]), LOW,
      lambda a: f"Start '{a.get('name', '')}' with Windows again?")
def enable_startup_item(name: str) -> str:
    reg = winreg
    if reg is None:
        return err("Only available on Windows.")
    backup = _load_backup()
    match = next((n for n in backup if n.lower() == str(name).strip().lower()), None)
    if match is None:
        return err("I have no saved startup item with that name.")
    key = reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE)
    try:
        reg.SetValueEx(key, match, 0, reg.REG_SZ, backup[match])
    finally:
        reg.CloseKey(key)
    del backup[match]
    _save_backup(backup)
    return ok(enabled=match)


# ---------------------------------------------------------------- power
RUNNER = None          # tests replace this


def power_argv(action: str, delay: int) -> Optional[list[str]]:
    return {"lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
            "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
            "restart": ["shutdown", "/r", "/t", str(delay)],
            "shutdown": ["shutdown", "/s", "/t", str(delay)],
            "cancel": ["shutdown", "/a"]}.get(action)


def _power_text(a: dict) -> str:
    action = str(a.get("action", ""))
    fixed = {"lock": "Lock the screen now?", "sleep": "Put the PC to sleep now?",
             "cancel": "Cancel the pending restart or shutdown?"}
    if action in fixed:
        return fixed[action]
    if action in ("restart", "shutdown"):
        try:
            delay = max(10, min(int(a.get("delay_seconds") or 15), 3600))
        except (TypeError, ValueError):
            delay = 15
        return f"{action.capitalize()} the PC in {delay} seconds? Unsaved work will be lost unless you save it."
    return "Change the PC's power state?"


@tool("power_action",
      "Lock the screen, put the PC to sleep, restart it, shut it down, or cancel a pending restart/shutdown. "
      "Restart and shutdown wait delay_seconds (default 15, at least 10) so the user can cancel.",
      _obj({"action": {"type": "string", "enum": ["lock", "sleep", "restart", "shutdown", "cancel"]},
            "delay_seconds": {"type": "integer"}}, ["action"]), HIGH, _power_text)
def power_action(action: str, delay_seconds: int = 15) -> str:
    action = str(action).strip().lower()
    delay = max(10, min(int(delay_seconds or 15), 3600))
    argv = power_argv(action, delay)
    if argv is None:
        return err("action must be lock, sleep, restart, shutdown or cancel.")
    if os.name != "nt" and RUNNER is None:
        return err("Power actions are only supported on Windows.")
    try:
        (RUNNER or subprocess.run)(argv, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        return err(f"That didn't work ({e.__class__.__name__}).")
    if action in ("restart", "shutdown"):
        return ok(scheduled=action, in_seconds=delay, cancel_with="power_action cancel")
    return ok(done=action)


# ---------------------------------------------------------------- screenshot
@tool("take_screenshot", "Take a screenshot of the whole screen and save it in Pictures\\Laptop_AI_Screenshots.",
      None, LOW, lambda a: "Take a screenshot of your screen and save it to Pictures?")
def take_screenshot() -> str:
    try:
        from PIL import ImageGrab
        image = ImageGrab.grab()
    except Exception as e:
        return err(f"Couldn't capture the screen ({e.__class__.__name__}).")
    folder = Path.home() / "Pictures" / "Laptop_AI_Screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    image.save(path)
    return ok(saved=str(path))
