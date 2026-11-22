"""The 3.0 pack: see what is on your network, link a phone, link other PCs.

  READ   scan_network, list_devices, phone_status, phone_notifications, pc_status, list_linked_pcs
  SAFE   phone_screenshot
  LOW    link_pc, allow_pc_link
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import psutil

from . import config, netmap, peers, phonelink
from .monitor import read_stats
from .tools import LOW, SAFE, _obj, err, ok, tool

_server: Optional[peers.PeerServer] = None


def _my_status() -> dict:
    """What another linked PC is allowed to see: the four numbers and the three busiest programs."""
    st = read_stats()
    top = []
    try:
        procs = sorted(psutil.process_iter(["name", "memory_info"]), key=lambda p: (p.info["memory_info"].rss if p.info.get("memory_info") else 0), reverse=True)
        top = [p.info["name"] for p in procs[:3] if p.info.get("name")]
    except (psutil.Error, OSError):
        pass
    return {"cpu": st.get("cpu"), "ram": st.get("ram"), "disk": st.get("disk"), "battery": st.get("battery"), "top": top}


def server() -> peers.PeerServer:
    global _server
    if _server is None:
        _server = peers.PeerServer(_my_status)
    return _server


# ---------------------------------------------------------------- network
@tool("scan_network", "Look for every device connected to this Wi-Fi / network (phones, PCs, TVs, printers) and list them. Takes ~15 seconds.")
def scan_network() -> str:
    try:
        devices = netmap.scan()
    except Exception as e:                  # a network scan must never crash the assistant
        return err(f"The scan failed: {e}")
    others = [d for d in devices if not d.is_me]
    new_ones = [d for d in others if d.is_new]
    text = netmap.describe(devices)
    if new_ones:
        text += f"\n{len(new_ones)} of them I haven't seen before: " + ", ".join(d.label for d in new_ones) + "."
    return ok(count=len(others), new_count=len(new_ones), text=text,
              devices=[{"name": d.label, "type": d.kind_label, "ip": d.ip, "router": d.is_gateway, "new": d.is_new} for d in devices])


@tool("wifi_status", "This PC's own Wi-Fi: which network it's on, signal strength and link speed.")
def wifi_status() -> str:
    info = netmap.wifi_info()
    if not info or not info.get("connected"):
        return ok(text="This PC isn't on Wi-Fi right now (it may be wired, or Wi-Fi is off).", wifi={})
    bits = [f"On \"{info['ssid']}\"" if info.get("ssid") else "Connected"]
    if info.get("signal_pct") is not None:
        bits.append(f"signal {info['signal_pct']}%")
    if info.get("mbps"):
        bits.append(f"{info['mbps']:.0f} Mbps")
    return ok(text=", ".join(bits) + ".", wifi=info)


@tool("list_devices", "List the devices found by the last network scan (does not scan again).")
def list_devices() -> str:
    when, devices = netmap.load()
    if not devices:
        return ok(count=0, text=netmap.describe([]), devices=[])
    return ok(count=len(devices) - 1, text=netmap.describe(devices),
              devices=[{"name": d.label, "type": d.kind_label, "ip": d.ip} for d in devices])


# ---------------------------------------------------------------- phone
@tool("phone_status", "What is going on on the linked Android phone: battery, storage, screen, the app that is open.")
def phone_status() -> str:
    st = phonelink.status()
    return err(st["error"]) if "error" in st else ok(text=phonelink.describe(st), phone=st)


@tool("phone_notifications", "The latest notifications on the linked Android phone (which app and the title).")
def phone_notifications() -> str:
    st = phonelink.status()
    if "error" in st:
        return err(st["error"])
    items = phonelink.notifications(st["serial"])
    text = ("Latest notifications: " + "; ".join(f"{i['app']}: {i['title'] or 'new message'}" for i in items)) if items \
        else "No notifications on the phone right now."
    return ok(text=text, notifications=items)


@tool("phone_send_file", "Send a file from this PC to the linked phone's Download folder.",
      _obj({"path": {"type": "string", "description": "the full path of the file on this PC"}}, ["path"]), LOW,
      lambda a: f"Send {a.get('path')} to the phone?")
def phone_send_file(path: str) -> str:
    worked, message = phonelink.push_file(path)
    return ok(text=message) if worked else err(message)


@tool("phone_pull_file", "Copy a file off the linked phone onto this PC.",
      _obj({"remote_path": {"type": "string", "description": "the file's full path on the phone, e.g. /sdcard/Download/photo.jpg"}}, ["remote_path"]), LOW,
      lambda a: f"Pull {a.get('remote_path')} from the phone?")
def phone_pull_file(remote_path: str) -> str:
    worked, message, path = phonelink.pull_file(remote_path, config.APP_DIR / "from_phone")
    return ok(text=message, path=str(path)) if worked else err(message)


@tool("phone_apps", "List the apps installed on the linked phone, so one can be opened.")
def phone_apps() -> str:
    apps = phonelink.list_apps()
    if not apps:
        return err("I couldn't list the phone's apps. Is it linked and unlocked?")
    names = ", ".join(a["label"] for a in apps[:15]) + ("..." if len(apps) > 15 else ".")
    return ok(count=len(apps), apps=apps, text=f"Apps on the phone: {names}")


@tool("phone_launch_app", "Open a named app on the linked phone's screen.",
      _obj({"package": {"type": "string", "description": "the app's package name, e.g. com.whatsapp"}}, ["package"]), LOW,
      lambda a: f"Open {a.get('package')} on the phone?")
def phone_launch_app(package: str) -> str:
    worked, message = phonelink.launch_app(package)
    return ok(text=message) if worked else err(message)


@tool("send_file_to_pc", "Send a file from this PC to an already-linked PC.",
      _obj({"name": {"type": "string", "description": "the linked PC's name"},
            "path": {"type": "string", "description": "the full path of the file on this PC"}}, ["name", "path"]), LOW,
      lambda a: f"Send {a.get('path')} to {a.get('name')}?")
def send_file_to_pc(name: str, path: str) -> str:
    worked, message = peers.send_file(name, path)
    return ok(text=message) if worked else err(message)


@tool("phone_screenshot", "Take a picture of the linked Android phone's screen and open it.", None, SAFE)
def phone_screenshot() -> str:
    path = config.APP_DIR / "phone_screen.png"
    problem = phonelink.screenshot(path)
    if problem:
        return err(problem)
    try:
        if sys.platform == "win32":
            os.startfile(str(path))           # noqa: S606
    except OSError:
        pass
    return ok(text=f"Saved the phone's screen to {path}.", path=str(path))


# ---------------------------------------------------------------- other PCs
@tool("allow_pc_link", "Let other PCs running Laptop AI link to this one (or stop). Returns the 6-digit code to type on the other PC.",
      _obj({"on": {"type": "boolean", "description": "true to allow, false to stop. Default true."}}), LOW,
      lambda a: "Let other PCs link to this one?" if a.get("on", True) else "Stop other PCs linking to this one?")
def allow_pc_link(on: bool = True) -> str:
    s = config.Settings.load()
    s.allow_pc_link = bool(on)
    s.save()
    srv = server()
    if not on:
        srv.stop()
        return ok(text="Other PCs can no longer link to this one.")
    if not srv.start():
        return err("I couldn't open the link door (another program may be using it, or the firewall blocked it).")
    code = srv.new_code()
    return ok(code=code, seconds_left=peers.CODE_LIFETIME_S,
              text=f"Other PCs can link now. On the other PC say \"link to PC {netmap.local_ip()} code {code}\". "
                   f"The code works for 10 minutes. Windows may ask to allow Laptop AI through the firewall: choose Private networks.")


@tool("link_pc", "Link to another PC running Laptop AI using its address and the 6-digit code shown there.",
      _obj({"host": {"type": "string", "description": "the other PC's address, like 192.168.1.20"},
            "code": {"type": "string", "description": "the 6-digit code"}}, ["host", "code"]), LOW,
      lambda a: f"Link to the PC at {a.get('host')}?")
def link_pc(host: str, code: str) -> str:
    worked, message = peers.pair(str(host).strip(), str(code), config.Settings.load().user_name and f"{os.environ.get('COMPUTERNAME', 'PC')}"
                                 or os.environ.get("COMPUTERNAME", "PC"))
    return ok(text=message) if worked else err(message)


@tool("pc_status", "How a linked PC is doing (CPU, memory, disk, battery, busiest programs).",
      _obj({"name": {"type": "string", "description": "the linked PC's name"}}, ["name"]))
def pc_status(name: str) -> str:
    d = peers.fetch_status(name)
    return err(d["error"]) if "error" in d else ok(text=peers.describe(d), status=d)


@tool("list_linked_pcs", "List the PCs this one is linked to.")
def list_linked_pcs() -> str:
    names = list(peers.linked())
    return ok(count=len(names), text=("Linked PCs: " + ", ".join(names) + ".") if names else
              "No PCs are linked yet. On the other PC, say \"let other PCs link\", then link from here.", pcs=names)
