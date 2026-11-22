"""What the home-page laptop model shows: which part is healthy, warning or critical.

Pure logic with no windows, so it is easy to test. The drawing lives in holo.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import config
from .monitor import check_thresholds

OK, WARN, CRIT = "ok", "warn", "crit"
RANK = {OK: 0, WARN: 1, CRIT: 2}


@dataclass(frozen=True)
class Part:
    id: str
    label: str
    ask: str            # what the "Ask about this" button sends
    blurb: str          # what this part does, shown when nothing is wrong


PARTS: list[Part] = [
    Part("display", "Display", "Is my PC healthy?", "The screen. Nothing here is monitored yet, so it shows as normal."),
    Part("cpu", "CPU", "What's using my CPU?", "The processor. Watched for heavy load and overheating."),
    Part("gpu", "GPU", "Is my GPU ok?", "The graphics chip. Watched for heat, throttling and driver crashes."),
    Part("ram", "Memory", "What's using my memory?", "The RAM. Watched for running out."),
    Part("ssd", "Storage", "What's taking up my disk space?", "Your drive. Watched for being nearly full, disk errors and failing health."),
    Part("fan", "Cooling", "Is my PC healthy?", "Fans and heat. Speeds up with the processor load."),
    Part("battery", "Battery", "How's my battery?", "Charge level and wear."),
    Part("network", "Network", "Is my internet working?", "Wi-Fi and the connection."),
    Part("security", "Security", "Run a security check", "Windows Defender, the firewall and suspicious programs."),
    Part("webcam", "Webcam", "Is my PC healthy?", "The camera above the screen. Watched through Windows device errors."),
    Part("mic", "Microphone", "Is my PC healthy?", "The built-in microphones. Watched through Windows audio device errors."),
    Part("bluetooth", "Bluetooth", "Is my PC healthy?", "The Bluetooth radio for headphones, mice and phones."),
    Part("speakers", "Speakers", "Is my PC healthy?", "The built-in speakers. Watched through Windows audio device errors."),
    Part("touchpad", "Touchpad", "Is my PC healthy?", "The trackpad. Watched through Windows input device errors."),
    Part("keyboard", "Keyboard", "Is my PC healthy?", "The keyboard. Watched through Windows input device errors."),
    Part("usb", "USB ports", "Is my PC healthy?", "The USB sockets for drives, mice and chargers."),
    Part("hdmi", "HDMI", "Is my PC healthy?", "The video-out port for monitors and TVs."),
    Part("charger", "Charger port", "How's my battery?", "Where the charger plugs in."),
    Part("board", "Motherboard", "Is my PC healthy?", "The main board. Watched through hardware errors and unexpected power loss in the Windows log."),
]
BY_ID = {p.id: p for p in PARTS}

# health.Finding.area -> the parts it lights up
AREA_PARTS = {"cpu": ["cpu"], "gpu": ["gpu"], "disk": ["ssd"], "memory": ["ram"], "motherboard": ["board"],
              "battery": ["battery"], "network": ["network"], "bluetooth": ["bluetooth"], "camera": ["webcam"],
              "audio": ["mic", "speakers"], "usb": ["usb"], "input": ["keyboard", "touchpad"], "screen": ["display"], "malware": ["security"], "system": ["board"]}
COOLING_KEYS = {"cpu_hot", "cpu_warm", "gpu_hot", "gpu_warm", "gpu_throttle"}      # heat problems also light the fan
THRESHOLD_PARTS = {"cpu": "cpu", "ram": "ram", "disk": "ssd", "battery": "battery"}


@dataclass
class Status:
    level: str = OK
    details: list[str] = field(default_factory=list)
    advice: str = ""
    value: str = ""

    def worse(self, level: str, detail: str, advice: str = "") -> None:
        if RANK[level] > RANK[self.level]:
            self.level = level
        if detail and detail not in self.details:
            self.details.append(detail)
        if advice and not self.advice:
            self.advice = advice


def _value(part: str, stats: dict) -> str:
    def pct(k):
        v = stats.get(k)
        return f"{float(v):.0f}%" if v is not None else "--"
    if part == "cpu":
        return pct("cpu")
    if part == "ram":
        return pct("ram")
    if part == "ssd":
        return pct("disk")
    if part == "battery":
        b = stats.get("battery")
        return "AC" if b is None else f"{float(b):.0f}%"
    return ""


def compute(stats: Optional[dict], findings: Iterable, settings: Optional[config.Settings] = None) -> dict[str, Status]:
    """Combine the live readings and the watchdog's findings into one Status per part."""
    stats = stats or {}
    settings = settings or config.Settings()
    out = {p.id: Status(value=_value(p.id, stats)) for p in PARTS}
    for key, message in check_thresholds(stats, settings):
        part = THRESHOLD_PARTS.get(key)
        if part:
            out[part].worse(WARN, message)
    for f in findings or []:
        raw = getattr(f, "level", "")
        level = CRIT if raw == "critical" else WARN if raw == "warning" else OK
        parts = list(AREA_PARTS.get(getattr(f, "area", ""), ["board"]))
        if getattr(f, "key", "") in COOLING_KEYS:
            parts.append("fan")
        for part in parts:
            if level == OK:                       # a note only: shown in the card, does not light the part
                msg = getattr(f, "message", "")
                if msg and msg not in out[part].details:
                    out[part].details.append(msg)
                continue
            out[part].worse(level, getattr(f, "message", ""), getattr(f, "advice", ""))
    return out


def summary(statuses: dict[str, Status]) -> tuple[str, str]:
    """(level, headline) for the whole machine."""
    bad = [s for s in statuses.values() if s.level != OK]
    if not bad:
        return OK, "All systems healthy"
    worst = CRIT if any(s.level == CRIT for s in bad) else WARN
    n = len(bad)
    return worst, f"{n} part{'s' if n != 1 else ''} need{'' if n != 1 else 's'} attention"


@dataclass(frozen=True)
class Fix:
    label: str
    kind: str        # "hog" (offer to close the heaviest program), "send" (send this sentence to the assistant)
    arg: str


def fixes(part: str, status: Status) -> list[Fix]:
    """One-click remedies for a part that is not OK. Each still asks before it changes anything."""
    if status.level == OK:
        return []
    return {
        "cpu": [Fix("Close the heaviest app", "hog", "cpu")],
        "fan": [Fix("Close the heaviest app", "hog", "cpu")],
        "gpu": [Fix("Close the heaviest app", "hog", "cpu")],
        "ram": [Fix("Close the biggest memory hog", "hog", "memory")],
        "ssd": [Fix("Clean my temp files", "send", "Clean my temp files")],
        "security": [Fix("Run a security check", "send", "Run a security check")],
        "battery": [Fix("Show what's draining it", "send", "What's using my CPU?")],
        "network": [Fix("Check my internet", "send", "Is my internet working?")],
    }.get(part, [])
