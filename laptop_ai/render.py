"""Turn tool answers into readable text: one version for the Tools page, friendly sentences for replies."""
from __future__ import annotations

import json
from datetime import datetime


def format_result(raw: str) -> str:
    """Turn a tool's JSON answer into short readable text (used by the Files & Tools page)."""
    try:
        d = json.loads(raw)
    except ValueError:
        return raw
    if d.get("status") == "declined_by_user":
        return "You declined this action."
    if d.get("status") == "error":
        return "Problem: " + str(d.get("message", "unknown error"))
    lines = []
    for key, value in d.items():
        if key == "status":
            continue
        if key == "programs" and isinstance(value, list):
            lines.append("Programs:")
            lines += [f"  • {p['name']}: {p['memory_mb']} MB, CPU {p['cpu_percent']}%, "
                      f"{p['processes']} process(es)" for p in value]
        elif isinstance(value, list) and value and isinstance(value[0], dict) and key != "tasks":
            lines.append(key.replace("_", " ").capitalize() + ":")
            lines += ["  • " + ", ".join(f"{k}: {v}" for k, v in item.items() if v not in (None, "", []))
                      for item in value]
        elif isinstance(value, list) and key != "tasks":
            lines.append(f"{key.replace('_', ' ').capitalize()}: " + (", ".join(map(str, value)) or "none"))
        elif key == "tasks" and isinstance(value, list):
            lines.append("Scheduled jobs:" if value else "No scheduled jobs.")
            lines += [f"  • [{t['id']}] {t['task']} {t.get('message') or ''}  next: {t['next_run']}"
                      for t in value]
        else:
            lines.append(f"{key.replace('_', ' ').capitalize()}: {value}")
    return "\n".join(lines) or "Done."


def problem(d: dict) -> str | None:
    """The sentence to show when a tool did not succeed, or None when it did."""
    if d.get("status") == "declined_by_user":
        return "OK, I won't do that."
    if d.get("status") == "error":
        return str(d.get("message") or "That didn't work.")
    return None


def status_sentence(d: dict, which: str) -> str:
    if which == "battery":
        b = d.get("battery_percent")
        if b is None:
            return "This PC has no battery."
        return f"Battery is at {b:.0f}%, {'plugged in' if d.get('plugged_in') else 'running on battery'}."
    if which == "cpu":
        return f"CPU is at {d.get('cpu_percent')}% across {d.get('cpu_threads')} threads."
    if which == "ram":
        return f"Memory is {d.get('ram_percent')}% used ({d.get('ram_used')} of {d.get('ram_total')})."
    if which == "disk":
        return f"Disk is {d.get('disk_percent')}% full, {d.get('disk_free')} free."
    batt = d.get("battery_percent")
    parts = [f"CPU {d.get('cpu_percent')}%", f"memory {d.get('ram_percent')}%", f"disk {d.get('disk_percent')}% full"]
    parts.append("no battery" if batt is None else f"battery {batt:.0f}%" + (" (plugged in)" if d.get("plugged_in") else ""))
    return "Right now: " + ", ".join(parts) + "."


def top_sentence(d: dict) -> str:
    rows = d.get("programs") or []
    if not rows:
        return "I couldn't read the list of programs."
    by = "CPU" if d.get("sorted_by") == "cpu" else "memory"
    parts = [f"{r['name']} ({r['cpu_percent']}% CPU)" if by == "CPU" else f"{r['name']} ({r['memory_mb']:,} MB)"
             for r in rows[:5]]
    return f"Biggest users of {by}: " + ", ".join(parts) + "."


def clock_sentence(d: dict) -> str:
    try:
        t = datetime.strptime(d["local_time"], "%Y-%m-%d %H:%M:%S")
        return f"It's {t.strftime('%H:%M')} on {t.strftime('%A')} {t.strftime('%d %B %Y')}."
    except (KeyError, ValueError):
        return format_result(json.dumps(d))
