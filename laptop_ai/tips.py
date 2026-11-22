"""A little rotating line under the Home greeting: a mix of feature tips (so people actually discover what's
in here) and a quiet lifetime stat. Pure and tiny on purpose — nothing here touches the network or the disk."""
from __future__ import annotations

import datetime as _dt

TIPS = [
    "Tip: say \"Trix\" from anywhere and I'll listen, even if the window is closed to the tray.",
    "Tip: click any part of the laptop model to ask about it directly.",
    "Tip: \"remind me in 20 minutes to stretch\" — I'll chime and say it out loud.",
    "Tip: set up scenes in Settings — \"movie mode\", \"study mode\" — one word runs them all.",
    "Tip: Devices lets you link your phone or another PC and send files between them.",
    "Tip: ask \"why is my internet slow\" for a quick diagnosis, not just a speed number.",
    "Tip: smart cleanup keeps a safe recoverable bin — nothing's gone for good right away.",
    "Tip: the Commands console runs any tool directly, with the same safety checks as chat.",
    "Tip: your own commands never ask for confirmation — only things I start on my own do.",
    "Tip: pick a voice and speaking rate for me in Settings > Voice.",
]


def stat_line(first_run_at: str) -> str:
    """A friendly one-liner about how long this has been looking after the PC. Empty string if there's nothing to say."""
    if not first_run_at:
        return ""
    try:
        days = (_dt.date.today() - _dt.date.fromisoformat(first_run_at)).days
    except ValueError:
        return ""
    if days <= 0:
        return "Looking after this PC since today."
    if days == 1:
        return "Looking after this PC for 1 day."
    return f"Looking after this PC for {days} days."


def rotation(first_run_at: str) -> list[str]:
    """The tip strip's cycle: the stat line first (if there is one), then the tips, in order."""
    stat = stat_line(first_run_at)
    return ([stat] if stat else []) + TIPS
