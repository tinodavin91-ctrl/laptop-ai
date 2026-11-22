"""What is playing right now, for the home page's music strip. Reads the VLC that Laptop AI started (its local remote)."""
from __future__ import annotations

import base64
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional


def parse_status(xml_text: str) -> dict:
    """VLC's status.xml -> {"state": playing|paused|stopped, "title", "artist"} (pure, testable)."""
    root = ET.fromstring(xml_text)
    state = (root.findtext("state") or "stopped").strip().lower()
    meta = {}
    for info in root.iter("info"):
        meta[(info.get("name") or "").lower()] = (info.text or "").strip()
    title = meta.get("title") or meta.get("filename") or ""
    if "." in title and title.lower().rsplit(".", 1)[-1] in ("mp3", "m4a", "wav", "flac", "ogg", "aac", "wma", "opus"):
        title = title.rsplit(".", 1)[0]
    return {"state": state if state in ("playing", "paused") else "stopped", "title": title, "artist": meta.get("artist", "")}


def current(fetch=None) -> Optional[dict]:
    """None when VLC's remote isn't there (nothing we can read), otherwise the dict above."""
    try:
        text = (fetch or _fetch)()
        return parse_status(text)
    except Exception:
        return None


def _fetch() -> str:
    from . import tools  # noqa: F401  (loads the tool packs in the right order)
    from . import tools_v2
    req = urllib.request.Request(f"http://127.0.0.1:{tools_v2.VLC_PORT}/requests/status.xml")
    req.add_header("Authorization", "Basic " + base64.b64encode(f":{tools_v2.VLC_PASSWORD}".encode()).decode())
    with urllib.request.urlopen(req, timeout=1.2) as r:
        return r.read().decode("utf-8", "replace")
