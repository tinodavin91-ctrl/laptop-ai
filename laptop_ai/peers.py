"""PC-to-PC link: two PCs that both run Laptop AI can see each other's health, like a tiny private network.

How it works: one PC switches on "Allow other PCs to link" and shows a 6-digit code. On the other PC you type that PC's
address and the code once. After that the second PC can look at the first one's CPU, memory, disk, battery and busiest
programs. It only looks (read-only), and only on your own network. The link uses a secret key made at pairing.

Security, said plainly:
  - A request is answered only if it comes from a private-network address (your own Wi-Fi/LAN or this PC itself).
    A request arriving from the open internet — for example because a router forwarded the port by mistake — is refused
    before anything else runs, so this never becomes reachable from outside your home network.
  - Every request after pairing must be signed with the shared key (HMAC) and carry a fresh timestamp; a copied or
    replayed request older than a minute is refused.
  - A pairing key that hasn't been used in 30 days expires on its own, and you can see and revoke any key from Devices.
  - Honest limit: traffic on your home network is signed but not encrypted (plain HTTP). Use it on a network you trust.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

from . import __version__, config

PORT = 58765
CODE_LIFETIME_S = 600           # 10 minutes: enough time to walk to the other PC and type it in
KEY_IDLE_DAYS = 30
REQUEST_SKEW_S = 60
MAX_FILE_BYTES = 50 * 1024 * 1024
FIREWALL_RULE_NAME = "Laptop AI PC Link"


def _received_dir() -> Path:
    d = config.APP_DIR / "received_files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(name: str) -> str:
    """Just the file's own name, no folders — never lets a sender write outside received_files."""
    name = Path(str(name).replace("\\", "/")).name or "file"
    name = re.sub(r"[^\w.\-() ]+", "_", name).lstrip(".") or "file"
    return name[:120]


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suf, i = path.stem, path.suffix, 2
    while True:
        cand = path.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
        i += 1


def list_received() -> list[dict]:
    """Files other linked PCs have sent us, newest first, for the Devices screen."""
    out = []
    for p in sorted(_received_dir().glob("*"), key=lambda p: -p.stat().st_mtime):
        if p.is_file():
            out.append({"name": p.name, "path": str(p), "bytes": p.stat().st_size, "when": p.stat().st_mtime})
    return out


def allow_firewall(port: int = PORT) -> None:
    """Best-effort: open the Windows firewall for this port so pairing doesn't silently fail. Needs no admin
    rights to *try*; if it can't (not elevated), this just does nothing and the normal Windows prompt still works."""
    if sys.platform != "win32":
        return
    try:
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE_NAME}"],
            capture_output=True, timeout=4, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE_NAME}", "dir=in",
             "action=allow", "protocol=TCP", f"localport={port}", "profile=private"],
            capture_output=True, timeout=4, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass            # never let a firewall tweak stop Laptop AI from starting


def _incoming():
    return config.APP_DIR / "peers_in.json"        # keys we gave out: {key: {"name":, "added":, "last_seen":}}


def _outgoing():
    return config.APP_DIR / "peers_out.json"       # PCs we linked to: {name: {host, key, port}}


def _read(path) -> dict:
    data = config.read_json(path, {})
    return data if isinstance(data, dict) else {}


def _read_incoming() -> dict:
    """{key: {"name", "added", "last_seen"}}, upgrading any key saved by an older version (plain name string)."""
    data = _read(_incoming())
    now = time.time()
    out = {}
    for key, val in data.items():
        out[key] = val if isinstance(val, dict) else {"name": val, "added": now, "last_seen": now}
    return out


def _prune_idle(keys: dict) -> dict:
    cutoff = time.time() - KEY_IDLE_DAYS * 86400
    return {k: v for k, v in keys.items() if v.get("last_seen", v.get("added", 0)) >= cutoff}


def is_private_address(ip: str) -> bool:
    """True for this PC's own loopback and for ordinary home/office network ranges; false for a public internet address."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def sign(key: str, method: str, path: str, ts: str) -> str:
    return hmac.new(key.encode(), f"{method}:{path}:{ts}".encode(), hashlib.sha256).hexdigest()


def list_incoming() -> list[dict]:
    """Every PC that has ever paired with this one, for the Devices screen (so you can see and revoke them)."""
    keys = _prune_idle(_read_incoming())
    config.write_json(_incoming(), keys)
    return [{"key": k, **v} for k, v in sorted(keys.items(), key=lambda kv: -kv[1].get("last_seen", 0))]


def revoke_incoming(key: str) -> bool:
    keys = _read_incoming()
    found = keys.pop(key, None) is not None
    config.write_json(_incoming(), keys)
    return found


class PeerServer:
    """Answers other Laptop AI PCs. `status_fn()` returns this PC's numbers; `name_fn()` its name."""

    def __init__(self, status_fn: Callable[[], dict], name_fn: Callable[[], str] = socket.gethostname, port: int = PORT,
                 host: str = "0.0.0.0"):
        self.status_fn, self.name_fn, self.port, self.host = status_fn, name_fn, port, host
        self._code = ""
        self._code_at = 0.0
        self._lock = threading.Lock()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._tries = 0
        self.on_paired: Callable[[str], None] = lambda name: None
        self.on_file: Callable[[str, str], None] = lambda name, fname: None

    # ---- pairing code
    def new_code(self) -> str:
        with self._lock:
            self._code, self._code_at, self._tries = f"{secrets.randbelow(10**6):06d}", time.time(), 0
            return self._code

    def code_seconds_left(self) -> float:
        with self._lock:
            if not self._code:
                return 0.0
            return max(0.0, CODE_LIFETIME_S - (time.time() - self._code_at))

    def _take_code(self, given: str) -> bool:
        with self._lock:
            if not self._code or time.time() - self._code_at > CODE_LIFETIME_S or self._tries >= 5:
                return False
            self._tries += 1                                    # five wrong guesses and the code dies
            if hmac.compare_digest(self._code, str(given or "")):
                self._code = ""
                return True
            return False

    def known_key(self, key: str) -> Optional[str]:
        """The linked PC's name if this key is valid, else None. Touches last_seen so idle keys can expire."""
        keys = _read_incoming()
        for k, info in keys.items():
            if hmac.compare_digest(key, k):
                info["last_seen"] = time.time()
                config.write_json(_incoming(), keys)
                return info.get("name", "PC")
        return None

    def _verify_signature(self, key: str, method: str, path: str, headers) -> bool:
        ts = headers.get("X-Time", "")
        sig = headers.get("X-Sig", "")
        if not ts or not sig:
            return False
        try:
            if abs(time.time() - float(ts)) > REQUEST_SKEW_S:
                return False               # too old (or clock way off): refuse, so a copied request can't be replayed later
        except ValueError:
            return False
        return hmac.compare_digest(sig, sign(key, method, path, ts))

    # ---- server
    def start(self) -> bool:
        if self._httpd is not None:
            return True
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):          # keep quiet
                pass

            def _send(self, code: int, body: dict) -> None:
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _from_lan(self) -> bool:
                return is_private_address(self.client_address[0])

            def do_GET(self):
                if not self._from_lan():                      # never answer a request that isn't from this home network
                    return self._send(403, {"error": "refused: not a local-network address"})
                if self.path == "/hello":
                    return self._send(200, {"app": "laptop-ai", "name": outer.name_fn(), "version": __version__})
                if self.path == "/status":
                    key = self.headers.get("X-Key", "")
                    name = key and outer.known_key(key)
                    if not name:
                        return self._send(401, {"error": "not linked"})
                    if not outer._verify_signature(key, "GET", self.path, self.headers):
                        return self._send(401, {"error": "bad or stale signature"})
                    try:
                        return self._send(200, {"name": outer.name_fn(), **outer.status_fn()})
                    except Exception as e:
                        return self._send(500, {"error": str(e)})
                self._send(404, {"error": "no such page"})

            def do_POST(self):
                if not self._from_lan():
                    return self._send(403, {"error": "refused: not a local-network address"})
                if self.path == "/pair":
                    try:
                        n = min(int(self.headers.get("Content-Length", "0")), 2000)
                        body = json.loads(self.rfile.read(n) or b"{}")
                    except (ValueError, OSError):
                        return self._send(400, {"error": "bad request"})
                    if not outer._take_code(body.get("code", "")):
                        return self._send(403, {"error": "wrong or expired code"})
                    key = secrets.token_hex(24)
                    who = str(body.get("name", "PC"))[:40]
                    keys = _read_incoming()
                    now = time.time()
                    keys[key] = {"name": who, "added": now, "last_seen": now}
                    config.write_json(_incoming(), keys)
                    outer.on_paired(who)
                    return self._send(200, {"key": key, "name": outer.name_fn()})
                if self.path == "/file":
                    key = self.headers.get("X-Key", "")
                    sender = key and outer.known_key(key)
                    if not sender:
                        return self._send(401, {"error": "not linked"})
                    if not outer._verify_signature(key, "POST", self.path, self.headers):
                        return self._send(401, {"error": "bad or stale signature"})
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        return self._send(400, {"error": "bad request"})
                    if size <= 0 or size > MAX_FILE_BYTES:
                        return self._send(413, {"error": "too big (50 MB limit)"})
                    dest = _unique(_received_dir() / _safe_filename(self.headers.get("X-Filename", "file")))
                    try:
                        dest.write_bytes(self.rfile.read(size))
                    except OSError as e:
                        return self._send(500, {"error": str(e)})
                    outer.on_file(sender, dest.name)
                    return self._send(200, {"saved": dest.name})
                self._send(404, {"error": "no such page"})

        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError:
            return False
        allow_firewall(self.port)           # best-effort: so "linking" doesn't silently fail behind the firewall
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return True

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @property
    def running(self) -> bool:
        return self._httpd is not None


# ---------------------------------------------------------------- the client side
def _call(host: str, path: str, port: int = PORT, key: str = "", data: Optional[dict] = None, timeout: float = 4):
    method = "POST" if data is not None else "GET"
    headers = {"Content-Type": "application/json"}
    if key:
        ts = f"{time.time():.3f}"
        headers.update({"X-Key": key, "X-Time": ts, "X-Sig": sign(key, method, path, ts)})
    req = urllib.request.Request(f"http://{host}:{port}{path}", method=method,
                                 data=json.dumps(data).encode() if data is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def hello(host: str, port: int = PORT, timeout: float = 1.5) -> Optional[dict]:
    """Is there a Laptop AI at this address? Returns its name and version, or None."""
    try:
        d = _call(host, "/hello", port, timeout=timeout)
        return d if d.get("app") == "laptop-ai" else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def pair(host: str, code: str, my_name: str, port: int = PORT) -> tuple[bool, str]:
    """Link to another PC using the code on its screen. Returns (worked, message).
    Checks the other PC is even reachable first, so a network problem is never reported as a bad code."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != 6:
        return False, "The code is 6 digits."
    if hello(host, port, timeout=3) is None:
        return False, (f"I can't find a Laptop AI at {host}. Check: both PCs are on the same Wi-Fi/network (not one "
                       "on mobile data or a guest network), that PC has 'Allow other PCs to link' switched on, "
                       "and if Windows just asked about the firewall there, it was allowed for Private networks.")
    try:
        d = _call(host, "/pair", port, data={"code": code, "name": my_name}, timeout=6)
    except urllib.error.HTTPError as e:
        return False, "That code is wrong or has expired. Get a fresh code on that PC and try again." if e.code == 403 \
            else f"The other PC refused ({e.code})."
    except (OSError, ValueError, urllib.error.URLError):
        return False, "I found that PC but the link attempt timed out. Try again — it may just have been busy."
    peers = _read(_outgoing())
    peers[d["name"]] = {"host": host, "key": d["key"], "port": port}
    config.write_json(_outgoing(), peers)
    return True, f"Linked to {d['name']}."


def send_file(peer_name: str, path) -> tuple[bool, str]:
    """Send a file to an already-linked PC. Read-only on their end otherwise — this is the one thing a link can move."""
    p = Path(path)
    if not p.is_file():
        return False, "That file doesn't exist."
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        return False, "That file is bigger than 50 MB — too big to send over the link."
    peers_map = linked()
    key_name = next((k for k in peers_map if k.lower() == peer_name.lower()), None)
    if key_name is None:
        return False, f"Not linked to {peer_name}."
    info = peers_map[key_name]
    host, port, key = info["host"], info.get("port", PORT), info["key"]
    ts = f"{time.time():.3f}"
    headers = {"X-Key": key, "X-Time": ts, "X-Sig": sign(key, "POST", "/file", ts), "X-Filename": p.name,
               "Content-Type": "application/octet-stream"}
    req = urllib.request.Request(f"http://{host}:{port}/file", data=p.read_bytes(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            json.loads(r.read())
        return True, f"Sent {p.name} to {key_name}."
    except urllib.error.HTTPError as e:
        return False, "That PC no longer trusts this link. Link it again." if e.code == 401 else f"{key_name} refused the file ({e.code})."
    except (OSError, ValueError, urllib.error.URLError):
        return False, f"{key_name} isn't answering. It may be off, asleep, or Laptop AI is closed there."


def linked() -> dict:
    return _read(_outgoing())


def unlink(name: str) -> bool:
    peers = _read(_outgoing())
    found = peers.pop(name, None) is not None
    config.write_json(_outgoing(), peers)
    return found


def fetch_status(name: str) -> dict:
    """A linked PC's numbers (or {'error': ...})."""
    peers = linked()
    key = next((k for k in peers if k.lower() == name.lower()), None) or next((k for k in peers if name.lower() in k.lower()), None)
    if key is None:
        return {"error": f"I'm not linked to a PC called {name}. Linked: {', '.join(peers) or 'none'}."}
    p = peers[key]
    try:
        return _call(p["host"], "/status", p.get("port", PORT), key=p["key"])
    except urllib.error.HTTPError as e:
        return {"error": "That PC no longer trusts this link. Link it again." if e.code == 401 else f"Error {e.code}"}
    except (OSError, ValueError, urllib.error.URLError):
        return {"error": f"{key} isn't answering. It may be off, asleep, or Laptop AI is closed there."}


def describe(d: dict) -> str:
    if "error" in d:
        return d["error"]
    bits = [f"{d.get('name', 'That PC')}:"]
    for k, label in (("cpu", "CPU"), ("ram", "memory"), ("disk", "disk")):
        if d.get(k) is not None:
            bits.append(f"{label} {d[k]:.0f}%")
    if d.get("battery") is not None:
        bits.append(f"battery {d['battery']:.0f}%")
    out = ", ".join(bits[1:])
    top = d.get("top") or []
    return f"{bits[0]} {out}." + (f" Busiest: {', '.join(top)}." if top else "")
