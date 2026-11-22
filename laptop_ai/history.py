"""The activity history (what Laptop AI did for you) and "undo that".

Every action that changes something is already written to ~/.laptop_ai/actions.log. This reads that log back and
works out, for the actions that CAN be reversed, what would reverse them. Nothing here changes anything by itself.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from . import config

# tools whose effect can be reversed, and how
UNDOABLE = {"disable_startup_item", "enable_startup_item", "remember", "add_reminder", "schedule_task", "schedule_media",
            "focus_mode", "power_action", "volume", "media_control", "move_to_trash", "add_note"}
OPPOSITE_VOLUME = {"up": "down", "down": "up", "mute": "mute"}


def read_log(limit: int = 200) -> list[dict]:
    """Newest last. Each row: time, source, tool, args (dict), outcome (text)."""
    try:
        lines = config.LOG_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[-limit:]:
        parts = line.split("\t", 4)
        if len(parts) < 5:
            continue
        try:
            args = json.loads(parts[3])
        except ValueError:
            args = {}
        rows.append({"time": parts[0], "source": parts[1], "tool": parts[2], "args": args if isinstance(args, dict) else {},
                     "outcome": parts[4]})
    return rows


def _found_id(outcome: str) -> str:
    m = re.search(r'"id":\s*"([^"]+)"', outcome)
    return m.group(1) if m else ""


def worked(row: dict) -> bool:
    out = row["outcome"]
    return '"status": "ok"' in out or '"status":"ok"' in out


def undo_plan(row: dict) -> Optional[tuple[str, dict, str]]:
    """(tool to run, its arguments, plain sentence) that reverses this action, or None."""
    tool, args = row["tool"], row["args"]
    if tool not in UNDOABLE or not worked(row):
        return None
    if tool == "disable_startup_item" and args.get("name"):
        return "enable_startup_item", {"name": args["name"]}, f"Turn {args['name']} back on at start-up"
    if tool == "enable_startup_item" and args.get("name"):
        return "disable_startup_item", {"name": args["name"]}, f"Stop {args['name']} from starting with Windows again"
    if tool == "remember" and _found_id(row["outcome"]):
        return "forget", {"text": _found_id(row["outcome"])}, "Forget the fact I saved"
    if tool == "add_note" and _found_id(row["outcome"]):
        return "delete_note", {"note_id": _found_id(row["outcome"])}, "Delete the note I saved"
    if tool in ("add_reminder", "schedule_task", "schedule_media") and _found_id(row["outcome"]):
        return "cancel_scheduled_task", {"task_id": _found_id(row["outcome"])}, "Cancel what I scheduled"
    if tool == "focus_mode":
        return "stop_focus_mode", {}, "End focus mode"
    if tool == "power_action" and args.get("action") in ("shutdown", "restart"):
        return "power_action", {"action": "cancel"}, f"Cancel the pending {args['action']}"
    if tool == "volume" and args.get("action") in OPPOSITE_VOLUME:
        return "volume", {"action": OPPOSITE_VOLUME[args["action"]]}, "Change the volume back"
    if tool == "media_control" and args.get("action") in ("next", "previous", "play_pause"):
        other = {"next": "previous", "previous": "next", "play_pause": "play_pause"}[args["action"]]
        return "media_control", {"action": other}, "Reverse the last music command"
    if tool == "move_to_trash":
        batch = re.search(r'"batch":\s*"([^"]+)"', row["outcome"])
        if batch:
            return "restore_from_trash", {"batch": batch.group(1)}, "Put the deleted files back"
    return None


def key(row: dict) -> str:
    return f"{row['time']}|{row['tool']}|{json.dumps(row['args'], sort_keys=True)}"


def _undone() -> set[str]:
    data = config.read_json(config.APP_DIR / "undone.json", [])
    return set(data) if isinstance(data, list) else set()


def mark_undone(row: dict) -> None:
    done = list(_undone())[-200:] + [key(row)]
    config.write_json(config.APP_DIR / "undone.json", done)


def last_undoable() -> Optional[tuple[dict, tuple[str, dict, str]]]:
    done = _undone()
    for row in reversed(read_log()):
        if row["source"] == "undo" or key(row) in done:
            continue
        plan = undo_plan(row)
        if plan:
            return row, plan
    return None


def describe(row: dict) -> str:
    """One readable line for the activity list."""
    tool, a = row["tool"], row["args"]
    names = {"kill_process": "Closed", "clean_temp_files": "Cleaned temporary files", "open_app": "Opened",
             "power_action": "Power:", "volume": "Volume", "media_control": "Music", "schedule_media": "Scheduled music change",
             "move_to_trash": "Moved files to the safe bin", "backup_files": "Backed up files", "run_scene": "Ran scene",
             "window_control": "Window:", "focus_mode": "Focus mode", "remember": "Remembered a fact",
             "add_reminder": "Set a reminder", "take_screenshot": "Took a screenshot"}
    base = names.get(tool, tool.replace("_", " ").capitalize())
    detail = ", ".join(str(v) for v in a.values() if isinstance(v, (str, int, float)))[:60]
    state = "" if worked(row) else (" (declined)" if "declined" in row["outcome"] else " (didn't work)")
    return f"{row['time'][5:16].replace('T', ' ')}  {base}{' ' + detail if detail else ''}{state}"
