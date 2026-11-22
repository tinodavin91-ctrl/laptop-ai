"""Find what is connected to your Wi-Fi / network and draw it as a map: internet -> router -> every device.

It only looks: a quick "ping" to every address on your own network, then the PC's own address table (arp) to read the
hardware addresses. Nothing is sent outside your network and nothing is changed on any device.

What it can and cannot tell (honest limits):
  - It sees every device that answers on your network: phones, PCs, TVs, printers, speakers.
  - It cannot see *inside* a device. For that there are the phone link (Android) and the PC link (another Laptop AI).
  - Names and types are educated guesses from the device's own name, its open doors (ports) and its address style.
"""
from __future__ import annotations

import concurrent.futures as cf
import ipaddress
import json
import re
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from . import config

PEER_PORT = 58765                       # where another Laptop AI answers (see peers.py)
PROBE_PORTS = (21, 23, 80, 135, 139, 445, 3389, 5555, 62078, 9100, 631, 8008, 8009, 7000, 554, PEER_PORT)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# A small, well-known slice of hardware-address prefixes, just enough to put a maker's name on common devices.
# Unknown prefixes simply show no vendor — this is not, and isn't meant to be, a complete database.
OUI_VENDORS = {
    "f4f5e8": "Google", "3c5ab4": "Google", "d8a2b5": "Google", "a4c138": "Google",
    "a4b1c1": "Samsung", "5c0a5b": "Samsung", "8c7712": "Samsung", "ac5f3e": "Samsung", "e8508b": "Samsung",
    "a85c2c": "Apple", "f0b479": "Apple", "3c0754": "Apple", "d0e140": "Apple", "88e9fe": "Apple", "b8ff61": "Apple",
    "001a11": "Google", "18b430": "Huawei", "00e04c": "Realtek", "b827eb": "Raspberry Pi", "dca632": "Raspberry Pi",
    "f4f26d": "TP-Link", "50c7bf": "TP-Link", "d85d4c": "ASUS", "1cb72c": "ASUS",
    "d4bed9": "Dell", "f8bc12": "Dell", "a4bb6d": "Lenovo", "0019d1": "Lenovo",
    "3417eb": "Xiaomi", "7820b8": "Xiaomi", "28a183": "Xiaomi", "9c28ef": "Amazon", "fc65de": "Amazon",
    "b0f1ec": "Sonos", "5caafd": "Sonos", "0c2a69": "Roku", "dc56e7": "Roku", "001132": "Canon", "00265b": "HP",
}

RISK_PORTS = {
    21: "unencrypted file transfer (FTP) is open",
    23: "unencrypted remote access (Telnet) is open — turn it off if you don't need it",
}


def vendor_for(mac: str) -> str:
    """A maker's name guessed from the hardware address's first half (never certain, and often unknown)."""
    key = mac.lower().replace(":", "").replace("-", "")[:6]
    return OUI_VENDORS.get(key, "")


def risk_note(ports: list) -> str:
    """One short, plain-words warning if a device has an old, insecure door open; '' if nothing stands out."""
    hits = [RISK_PORTS[p] for p in ports if p in RISK_PORTS]
    return "; ".join(hits)

KINDS = {"router": "Router", "me": "This PC", "laptop_ai": "PC with Laptop AI", "pc": "Computer", "phone": "Phone / tablet",
         "tv": "TV / streaming", "printer": "Printer", "speaker": "Speaker", "camera": "Camera", "other": "Device"}


@dataclass
class Device:
    ip: str
    mac: str = ""
    name: str = ""
    kind: str = "other"
    ports: list = field(default_factory=list)
    is_me: bool = False
    is_gateway: bool = False
    is_new: bool = False                 # first time this scan has ever seen it (not a security verdict, just "unfamiliar")
    alias: str = ""                      # a name the person gave it
    vendor: str = ""                     # maker guessed from the hardware address, when it's a prefix we recognise
    latency_ms: Optional[float] = None   # how long this device took to answer a ping, during the scan
    risk: str = ""                       # a plain-words note if an old, insecure door is open; '' if nothing stands out

    @property
    def label(self) -> str:
        return self.alias or self.name or KINDS.get(self.kind, "Device")

    @property
    def kind_label(self) -> str:
        return KINDS.get(self.kind, "Device")


# ---------------------------------------------------------------- system hooks (replaced in tests)
def _run(argv: list[str], timeout: float = 8) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW,
                           encoding="utf-8", errors="replace")
        return r.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


RUN: Callable[[list[str]], str] = _run


def _ping(ip: str) -> bool:
    flag = ["-n", "1", "-w", "500"] if sys.platform == "win32" else ["-c", "1", "-W", "1"]
    try:
        return subprocess.run(["ping", *flag, ip], capture_output=True, timeout=4, creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


PING: Callable[[str], bool] = _ping


def _open_port(ip: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


PORT: Callable[[str, int], bool] = _open_port


def _hostname(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0].split(".")[0]
    except (OSError, UnicodeError):
        return ""


HOST: Callable[[str], str] = _hostname


# ---------------------------------------------------------------- reading the network's own facts
def local_ip() -> str:
    """This PC's address on the network (no traffic is sent: connecting a UDP socket only picks the route)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def parse_gateway(text: str) -> str:
    """The router's address from `ipconfig` (Windows), `route print` or `ip route` output."""
    m = re.search(r"Default Gateway[ .]*:\s*((?:\d{1,3}\.){3}\d{1,3})", text)
    if m:
        return m.group(1)
    m = re.search(r"default via ((?:\d{1,3}\.){3}\d{1,3})", text)
    if m:
        return m.group(1)
    m = re.search(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+((?:\d{1,3}\.){3}\d{1,3})", text, re.M)
    return m.group(1) if m else ""


def gateway() -> str:
    if sys.platform == "win32":
        return parse_gateway(RUN(["ipconfig"]))
    return parse_gateway(RUN(["ip", "route"]))


def parse_arp(text: str) -> dict[str, str]:
    """ip -> mac from `arp -a` (Windows style 'ip  aa-bb-..  dynamic', or Linux 'ip dev x lladdr aa:bb:..')."""
    out: dict[str, str] = {}
    for line in text.splitlines():                    # one line at a time, so the 'Interface:' line can't pair with a later address
        m = re.match(r"\s*((?:\d{1,3}\.){3}\d{1,3})\s+(?:\S+\s+)*?([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})\b", line)
        if not m:
            continue
        mac = m.group(2).lower().replace("-", ":")
        if mac not in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00") and not mac.startswith("01:00:5e"):
            out[m.group(1)] = mac
    return out


def arp_table() -> dict[str, str]:
    return parse_arp(RUN(["arp", "-a"] if sys.platform == "win32" else ["ip", "neigh"]))


def hosts_in(ip: str, prefix: int = 24) -> list[str]:
    """Every usable address in this PC's network (limited to a /24 = 254 addresses, which is a normal home network)."""
    net = ipaddress.ip_network(f"{ip}/{max(prefix, 24)}", strict=False)
    return [str(h) for h in net.hosts()]


# ---------------------------------------------------------------- guessing what a device is
def private_mac(mac: str) -> bool:
    """Phones and tablets hide behind a made-up hardware address (the 'locally administered' bit is set)."""
    try:
        return bool(int(mac.split(":")[0], 16) & 2)
    except (ValueError, IndexError):
        return False


NAME_HINTS = (
    (r"iphone|ipad|android|galaxy|pixel|redmi|xiaomi|oppo|vivo|tecno|infinix|itel|huawei|oneplus|realme|phone|tablet|-a\d{2}", "phone"),
    (r"desktop|laptop|-pc|^pc-|dell|lenovo|hp-|asus|acer|msi|macbook|imac|thinkpad|surface|windows|ubuntu", "pc"),
    (r"tv|bravia|roku|firetv|chromecast|appletv|androidtv|webos|tizen|shield", "tv"),
    (r"printer|epson|canon|brother|laserjet|deskjet|officejet", "printer"),
    (r"echo|alexa|homepod|sonos|nest-?(mini|audio)|speaker", "speaker"),
    (r"cam|ipcam|hikvision|reolink|dahua", "camera"),
)


def classify(d: Device) -> str:
    if d.is_me:
        return "me"
    if d.is_gateway:
        return "router"
    if PEER_PORT in d.ports:
        return "laptop_ai"
    name = d.name.lower()
    for pattern, kind in NAME_HINTS:
        if name and re.search(pattern, name):
            return kind
    ports = set(d.ports)
    if ports & {62078, 5555}:
        return "phone"
    if ports & {9100, 631}:
        return "printer"
    if ports & {8008, 8009, 7000}:
        return "tv"
    if 554 in ports:
        return "camera"
    if ports & {135, 139, 445, 3389}:
        return "pc"
    if private_mac(d.mac) and not ports:
        return "phone"
    return "other"


# ---------------------------------------------------------------- the scan
def scan(progress: Callable[[str, float], None] = lambda text, frac: None, deep: bool = True) -> list[Device]:
    """Look for devices on the network. Takes about 10-20 seconds. Returns this PC first, then the router, then the rest."""
    me = local_ip()
    gw = gateway()
    progress("Looking at your network...", 0.02)
    targets = hosts_in(me) if me != "127.0.0.1" else []
    alive: set[str] = set()
    latency: dict[str, float] = {}

    def _timed_ping(ip: str):
        t0 = time.perf_counter()
        ok = PING(ip)
        return ok, (time.perf_counter() - t0) * 1000

    with cf.ThreadPoolExecutor(max_workers=64) as pool:
        futs = {pool.submit(_timed_ping, ip): ip for ip in targets if ip != me}
        done = 0
        for f in cf.as_completed(futs):
            done += 1
            try:
                ok, ms = f.result()
                if ok:
                    alive.add(futs[f])
                    latency[futs[f]] = ms
            except Exception:
                pass
            if done % 12 == 0:
                progress(f"Asking addresses... {len(alive)} found", 0.05 + 0.5 * done / max(1, len(futs)))
    table = arp_table()
    # some devices ignore ping but are still in the address table
    for ip in table:
        if ip in set(targets) and ip != me:
            alive.add(ip)
    if gw:
        alive.add(gw)
    devices = [Device(ip=ip, mac=table.get(ip, ""), latency_ms=latency.get(ip))
               for ip in sorted(alive, key=lambda x: tuple(int(p) for p in x.split(".")))]
    progress("Reading device names...", 0.6)
    with cf.ThreadPoolExecutor(max_workers=32) as pool:
        names = list(pool.map(lambda d: HOST(d.ip), devices))
        for d, n in zip(devices, names):
            d.name = n
        if deep:
            progress("Checking what each one is...", 0.75)
            def probe(d: Device) -> list:
                return [p for p in PROBE_PORTS if PORT(d.ip, p)]
            for d, ports in zip(devices, pool.map(probe, devices)):
                d.ports = ports
    for d in devices:
        d.is_gateway = d.ip == gw
        d.kind = classify(d)
        d.vendor = vendor_for(d.mac) if d.mac else ""
        d.risk = risk_note(d.ports)
    mine = Device(ip=me, name=socket.gethostname(), is_me=True, kind="me")
    mine.mac = ""
    ordered = [mine] + sorted((d for d in devices if d.is_gateway), key=lambda d: d.ip) + [d for d in devices if not d.is_gateway]
    apply_aliases(ordered)
    mark_new(ordered)
    save(ordered)
    progress("Done", 1.0)
    return ordered


# ---------------------------------------------------------------- "have I seen this device before?"
def _known_file():
    return config.APP_DIR / "known_devices.json"


def mark_new(devices: list[Device]) -> list[Device]:
    """Flags any device (besides this PC and the router) never seen in an earlier scan, then remembers all of them.
    Not a security verdict by itself — a new phone or a neighbour's device both show this way — just "unfamiliar"."""
    known = set(config.read_json(_known_file(), []) or [])
    new_ones = []
    for d in devices:
        key = d.mac or d.ip
        if not d.is_me and not d.is_gateway and key not in known:
            d.is_new = True
            new_ones.append(d)
        known.add(key)
    config.write_json(_known_file(), sorted(known))
    return new_ones


# ---------------------------------------------------------------- this PC's own Wi-Fi signal
def parse_wifi(text: str) -> dict:
    """From Windows' `netsh wlan show interfaces`. Anchored at line-start so 'SSID' never matches inside 'BSSID'."""
    def grab(label):
        m = re.search(rf"^\s*{label}\s*:\s*(.+)$", text, re.M)
        return m.group(1).strip() if m else ""
    signal = grab("Signal").rstrip("%")
    rx = grab(r"Receive rate \(Mbps\)")
    state = grab("State")
    return {"ssid": grab("SSID"), "signal_pct": int(signal) if signal.isdigit() else None,
            "radio": grab("Radio type"), "mbps": float(rx) if rx.replace(".", "", 1).isdigit() else None,
            "connected": state.lower() == "connected" if state else bool(grab("SSID"))}


def wifi_info() -> dict:
    if sys.platform != "win32":
        return {}
    text = RUN(["netsh", "wlan", "show", "interfaces"])
    if not text.strip():
        return {}
    return parse_wifi(text)


# ---------------------------------------------------------------- remembering the last scan and the names you gave
def _file():
    return config.APP_DIR / "devices.json"


def _aliases() -> dict:
    data = config.read_json(config.APP_DIR / "device_names.json", {})
    return data if isinstance(data, dict) else {}


def apply_aliases(devices: list[Device]) -> None:
    names = _aliases()
    for d in devices:
        d.alias = str(names.get(d.mac or d.ip, ""))[:30]


def rename(device: Device, alias: str) -> None:
    names = _aliases()
    key = device.mac or device.ip
    alias = re.sub(r"[^\w .'\-]", "", alias or "").strip()[:30]
    if alias:
        names[key] = alias
    else:
        names.pop(key, None)
    config.write_json(config.APP_DIR / "device_names.json", names)
    device.alias = alias


def save(devices: list[Device]) -> None:
    config.ensure_dir()
    config.write_json(_file(), {"time": time.time(), "devices": [asdict(d) for d in devices]})


def load() -> tuple[float, list[Device]]:
    data = config.read_json(_file(), {})
    if not isinstance(data, dict):
        return 0.0, []
    out = []
    for raw in data.get("devices", []):
        try:
            out.append(Device(**{k: v for k, v in raw.items() if k in Device.__dataclass_fields__}))
        except TypeError:
            continue
    apply_aliases(out)
    return float(data.get("time", 0) or 0), out


def describe(devices: list[Device]) -> str:
    """Plain words for chat and voice."""
    if not devices:
        return "I haven't scanned your network yet. Say \"scan my network\"."
    others = [d for d in devices if not d.is_me and not d.is_gateway]
    lines = [f"I can see {len(others)} other device{'s' if len(others) != 1 else ''} on your network:"]
    for d in devices:
        if d.is_me:
            continue
        lines.append(f"- {d.label} ({d.kind_label}) at {d.ip}" + (" - this is your router" if d.is_gateway else ""))
    phones = sum(1 for d in others if d.kind == "phone")
    peers = [d for d in others if d.kind == "laptop_ai"]
    if peers:
        lines.append(f"{len(peers)} of them run Laptop AI, so you can link to them.")
    if phones:
        lines.append(f"{phones} look like phones or tablets. Android phones can be linked with USB debugging.")
    risky = [d for d in others if d.risk]
    if risky:
        lines.append(f"Worth a look: {', '.join(d.label for d in risky)} — " + "; ".join(d.risk for d in risky) + ".")
    new = [d for d in others if d.is_new]
    if new:
        lines.append(f"New since last time: {', '.join(d.label for d in new)}.")
    return "\n".join(lines)


# ---------------------------------------------------------------- the map layout (pure, so it can be tested)
def layout(devices: list[Device], w: float, h: float) -> dict:
    """Positions for the topology map: internet at the top, the router below it, the devices in one, two or three
    gently curved rows under the router (no overlaps). Returns {'nodes': [(id, x, y, kind, label)], 'edges': [(id1, id2)]}."""
    import math
    cx = w / 2
    nodes = [("internet", cx, h * 0.08, "internet", "Internet")]
    router = next((d for d in devices if d.is_gateway), None)
    rid = router.ip if router else "router"
    nodes.append((rid, cx, h * 0.26, "router", router.label if router else "Router"))
    edges = [("internet", rid)]
    rest = [d for d in devices if not d.is_gateway]
    rows = 1 if len(rest) <= 7 else 2 if len(rest) <= 16 else 3
    for r in range(rows):
        group = rest[r::rows]
        for j, d in enumerate(group):
            t = (j + 0.5) / len(group)
            y = h * (0.56 + 0.17 * r) + math.sin(math.pi * t) * h * 0.06
            nodes.append((d.ip, w * (0.09 + 0.82 * t), y, d.kind, d.label))
            edges.append((rid, d.ip))
    return {"nodes": nodes, "edges": edges}
