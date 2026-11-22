"""The built-in command router.

Everyday requests ("how's my battery", "remind me in 10 minutes to stretch", "close chrome", "volume up")
are understood here with plain rules, so they work instantly and reliably, even with no AI brain or with a
small model that fumbles tools. Anything the router isn't sure about returns None and goes to the AI.

The router only DECIDES what to run. Every action still goes through tools.Executor, so risk levels,
confirmations, report-only mode and the phone's read-only rule apply exactly as before.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from . import config, render

Formatter = Callable[[dict, dict], str]          # (arguments, tool result) -> sentence


@dataclass
class Intent:
    tool: str
    args: dict
    fmt: Formatter


UNITS = {"s": 1 / 60, "sec": 1 / 60, "secs": 1 / 60, "second": 1 / 60, "seconds": 1 / 60,
         "m": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1,
         "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60, "d": 1440, "day": 1440, "days": 1440}
_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20,
             "thirty": 30, "forty": 40, "half": 0.5}
_NUM = r"(\d+(?:\.\d+)?|an?|one|two|three|five|ten|fifteen|twenty|thirty|forty|half an?|half)"
_UNIT = r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)"
_IN = re.compile(rf"\bin\s+{_NUM}\s*{_UNIT}\b", re.I)

ALIASES = {"google chrome": "chrome", "chrome": "chrome", "microsoft edge": "msedge", "edge": "msedge",
           "task manager": "taskmgr", "vs code": "code", "visual studio code": "code", "word": "winword",
           "microsoft word": "winword", "excel": "excel", "powerpoint": "powerpnt", "teams": "ms-teams",
           "file explorer": "explorer", "notepad": "notepad", "calculator": "calculatorapp", "spotify": "spotify",
           "discord": "discord", "firefox": "firefox", "steam": "steam", "whatsapp": "whatsapp", "zoom": "zoom"}
NEVER_CLOSE = {"explorer", "file explorer", "windows", "everything", "all", "it", "this", "that", "tab", "tabs",
               "window", "the window", "app", "program", "laptop ai", "yourself", "you", "the app", "chat", "focus",
               "focus mode"}


def _clean(text: str) -> str:
    t = " ".join((text or "").strip().split())
    t = re.sub(r"^(hey\s+)?(trix|laptop)[,:]?\s+", "", t, flags=re.I)
    t = re.sub(r"^(please|pls|kindly)\s+", "", t, flags=re.I)
    t = re.sub(r"^(can|could|would|will)\s+you\s+(please\s+)?", "", t, flags=re.I)
    t = re.sub(r"^i\s+(want|need|would like)\s+(you\s+)?to\s+", "", t, flags=re.I)
    t = re.sub(r"\s+(please|pls|for me|now)$", "", t, flags=re.I)
    return t.strip().rstrip(".!?").strip()


def _minutes(num: str, unit: str) -> float:
    n = _WORD_NUM.get(num.lower().replace("half an", "half").replace("half a", "half"), None)
    if n is None:
        n = float(num)
    if num.lower().startswith("half") and unit.lower().startswith("hour"):
        return 30.0
    return n * UNITS[unit.lower()]


def _first(fmt_ok: str) -> Formatter:
    return lambda a, r: fmt_ok


def _sentence_or(ok_text) -> Formatter:
    """A formatter that shows tool problems as-is and otherwise the given text (str or callable)."""
    def f(a: dict, r: dict) -> str:
        return render.problem(r) or (ok_text(a, r) if callable(ok_text) else ok_text)
    return f


def _status(which: str) -> Formatter:
    return lambda a, r: render.problem(r) or render.status_sentence(r, which)


def _reminder(message: str, minutes: float) -> Intent:
    message = message.strip(" .,") or "your reminder"
    return Intent("add_reminder", {"message": message, "minutes": round(minutes, 2)},
                  _sentence_or(lambda a, r: f"OK, I'll remind you to {message} at {str(r.get('due', ''))[-5:]}."))


def _clock_to_minutes(h: int, m: int, ap: str, now: datetime) -> float:
    ap = (ap or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    target = now.replace(hour=h % 24, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds() / 60


def _remind(t: str, now: datetime) -> Optional[Intent]:
    m = re.match(r"^remind me\b(.*)$", t, re.I)
    if not m:
        return None
    rest = m.group(1).strip()
    # remind me in 10 minutes to stretch
    mm = re.match(rf"^in\s+{_NUM}\s*{_UNIT}\s*(?:to|that|about)?\s*(.+)$", rest, re.I)
    if mm:
        return _reminder(mm.group(3), _minutes(mm.group(1), mm.group(2)))
    # remind me to stretch in 10 minutes
    mm = re.match(rf"^(?:to|that|about)\s+(.+?)\s+in\s+{_NUM}\s*{_UNIT}$", rest, re.I)
    if mm:
        return _reminder(mm.group(1), _minutes(mm.group(2), mm.group(3)))
    # remind me at 5pm to call mum
    mm = re.match(r"^at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:to|that|about)?\s*(.+)$", rest, re.I)
    if mm:
        h, mi = int(mm.group(1)), int(mm.group(2) or 0)
        if h <= 23 and mi <= 59:
            return _reminder(mm.group(4), _clock_to_minutes(h, mi, mm.group(3), now))
    # remind me to call mum at 5pm
    mm = re.match(r"^(?:to|that|about)\s+(.+?)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", rest, re.I)
    if mm:
        h, mi = int(mm.group(2)), int(mm.group(3) or 0)
        if h <= 23 and mi <= 59:
            return _reminder(mm.group(1), _clock_to_minutes(h, mi, mm.group(4), now))
    return None


def _close_name(raw: str) -> Optional[str]:
    n = raw.lower().strip().strip("'\"")
    n = re.sub(r"^(the|my)\s+", "", n)
    n = re.sub(r"\s+(app|program|application|window)$", "", n)
    if not n or n in NEVER_CLOSE or len(n) > 40:
        return None
    return ALIASES.get(n, n.replace(" ", ""))


def route(text: str, now: Optional[datetime] = None) -> Optional[Intent]:
    now = now or datetime.now()
    t = _clean(text)
    low = t.lower()
    if not low or len(low) > 220:
        return None
    words = low.split()
    v3 = _route_v3(low) or _route_v4(low)
    if v3:
        return v3

    # ---- help / health
    if re.fullmatch(r"(help|what can you do|what are your features|what are your abilities|commands|what do you do)", low):
        return Intent("get_current_time", {}, lambda a, r: HELP_TEXT)
    if re.search(r"\b(health check|run (a )?diagnostics?|diagnose (yourself|the app|laptop ai)|check yourself|"
                 r"what('s| is) wrong with you|are you ok(ay)?|check your setup)\b", low):
        return Intent("run_diagnostics", {}, lambda a, r: render.problem(r) or _diag_text(r))

    if re.search(r"\b(hardware|malware|virus(es)?|infected|hacked|gpu|graphics card|motherboard|overheat\w*|too hot|running hot)\b", low) and \
            re.search(r"\b(check|scan|any|is|are|problems?|issues?|wrong|damaged|healthy|ok|okay|safe|test|status|health|temperature)\b", low) and \
            not re.search(r"\b(remind|schedule|every)\b", low):
        area = next((a for pat, a in (("gpu|graphics", "gpu"), ("cpu|processor|overheat|too hot|running hot", "cpu"),
                                      ("virus|malware|infected|hacked", "malware"), ("motherboard", "motherboard")) if re.search(pat, low)), "")
        return Intent("hardware_health", {"area": area} if area else {}, lambda a, r: render.problem(r) or r.get("report", ""))
    if re.search(r"\b(is|how is) (my )?(pc|laptop|computer) (healthy|ok|okay|fine|in good shape)\b|^(full )?(pc )?health scan$", low):
        return Intent("hardware_health", {}, lambda a, r: render.problem(r) or r.get("report", ""))

    # ---- memory
    m = re.match(r"^remember\s+(?:that\s+)?(.+)$", t, re.I)
    if m:
        return Intent("remember", {"text": m.group(1)}, _sentence_or(lambda a, r: f"OK, I'll remember that {r.get('saved')}."))
    if re.search(r"\b(what do you (remember|know) about me|what have i told you|show (me )?(my )?memor(y|ies)|"
                 r"list (my )?memor(y|ies))\b", low):
        return Intent("list_memories", {}, _sentence_or(lambda a, r: ("I remember: " + "; ".join(x["text"] for x in r["memories"]) + ".")
                                                      if r.get("memories") else "I'm not remembering anything yet. Say \"remember that ...\"."))
    m = re.match(r"^forget\s+(?:that\s+|about\s+)?(.+)$", t, re.I)
    if m and not low.startswith("forget it"):
        return Intent("forget", {"text": m.group(1)}, _sentence_or(lambda a, r: f"Forgotten ({r.get('forgotten')})."))

    # ---- reminders
    r = _remind(t, now)
    if r:
        return r
    if re.search(r"\b(what|which|show|list|my)\b.*\breminders?\b", low) and not low.startswith("remind"):
        return Intent("list_scheduled_tasks", {}, lambda a, r: render.problem(r) or render.format_result(json.dumps(r)))

    # ---- focus mode
    if re.search(r"\b(stop|end|turn off|disable|leave|cancel)\b.*\bfocus\b", low):
        return Intent("stop_focus_mode", {}, _sentence_or("Focus mode is off."))
    m = re.search(r"\bfocus(?: mode)?\b(?:.*?\bfor\s+" + _NUM + r"\s*" + _UNIT + r")?", low)
    if m and re.search(r"\b(start|turn on|enable|begin|go into|enter|activate)\b.*\bfocus\b|^focus\b|^i need to focus", low):
        mins = _minutes(m.group(1), m.group(2)) if m.group(1) else 30
        return Intent("focus_mode", {"minutes": mins},
                      _sentence_or(lambda a, r: f"Focus mode is on for {r.get('minutes')} minutes"
                                   + (f". I closed {', '.join(r['closed'])}." if r.get("closed") else ".")))
    if re.fullmatch(r"(is )?focus mode (on|active|running)\??|how long is (left|focus left) (on )?focus( mode)?", low):
        return Intent("focus_status", {}, _sentence_or(lambda a, r: f"Focus mode is on, {r['minutes_left']} minutes left."
                                                       if r.get("focus_on") else "Focus mode is off."))

    # ---- sound and media
    m = re.fullmatch(r"(?:set |turn |put )?(?:the )?(?:volume|sound)(?: to| at)?\s*(\d{1,3})\s*%?", low) or \
        re.fullmatch(r"(?:set |turn |put )(?:the )?(?:volume|sound) to\s*(\d{1,3})\s*%?", low)
    if m:
        return Intent("volume", {"action": "set", "level": min(100, int(m.group(1)))}, _sentence_or(lambda a, r: f"Volume set to {r.get('level')}%."))
    if re.fullmatch(r"(?:turn |put )?(?:the )?(?:volume|sound)?\s*up|louder|turn it up|raise the volume|increase (the )?volume|volume up", low):
        return Intent("volume", {"action": "up"}, _sentence_or("Turned the volume up."))
    if re.fullmatch(r"(?:turn |put )?(?:the )?(?:volume|sound)?\s*down|quieter|softer|turn it down|lower the volume|decrease (the )?volume|volume down", low):
        return Intent("volume", {"action": "down"}, _sentence_or("Turned the volume down."))
    if re.fullmatch(r"(un)?mute( (the )?(sound|volume|pc|laptop|audio))?", low):
        return Intent("volume", {"action": "mute"}, _sentence_or("Done. (Mute is a toggle, so say it again to switch back.)"))
    _mv, _mo = r"(pause|stop|play|resume|skip|next|mute)", r"(?: the)?(?: music| songs?| video| media| track| sound| playing| playback)?"
    m1 = re.fullmatch(rf"{_mv}{_mo}\s+(?:in|after)\s+{_NUM}\s*{_UNIT}", low)
    m2 = re.fullmatch(rf"(?:in|after)\s+{_NUM}\s*{_UNIT},?\s*{_mv}{_mo}", low)
    if m1 or m2:
        verb, num, unit = m1.groups() if m1 else (m2.group(3), m2.group(1), m2.group(2))
        word = {"stop": "pause", "resume": "play", "skip": "next"}.get(verb, verb)
        mins = _minutes(num, unit)
        when = f"{round(mins * 60)} seconds" if mins < 1 else f"{mins:g} minute{'s' if mins != 1 else ''}"
        return Intent("schedule_media", {"action": word, "minutes": round(mins, 3)},
                      _sentence_or(f"OK, I'll {word if word != 'next' else 'skip to the next song'}"
                                   f"{' the music' if word in ('pause', 'play') else ''} in {when}."))
    if re.fullmatch(r"(pause|play|resume|stop)( the)?( music| song| video| media| playing)?|play/pause", low) and \
            low not in ("stop", "play"):
        return Intent("media_control", {"action": "play_pause"}, _sentence_or("Sent play/pause."))
    m = re.match(r"^(?:play|shuffle)\s+(?:some\s+|my\s+|any\s+)?(?:(.{2,60}?)\s+)?(?:music|songs?|tracks?)?\s*(?:from|on|in|off)\s+(?:my\s+|this\s+|the\s+)?"
                 r"(?:local\s+)?(?:laptop|pc|computer|disk|drive|hard ?drive|device|folder|files|music folder|downloads)$", low)
    if m or re.fullmatch(r"(?:play|shuffle) my (?:local |offline |downloaded )?(?:music|songs|playlist)|play (?:music|songs) offline", low):
        q = (m.group(1) if m else "") or ""
        q = re.sub(r"^(?:some|any|my)\s+|\s*(?:music|songs?)$", "", q).strip()
        return Intent("play_local_music", {"query": q},
                      _sentence_or(lambda a, r: f"Playing {r.get('playing')} (and {max(0, r.get('queued', 1) - 1)} more from your PC)."))
    if re.fullmatch(r"(you )?(just )?play( some| any| my)?( music| songs?| a song| something| audio)|start (the )?music|put (some )?music on", low):
        fav = config.Settings.load().music_player.strip()
        if fav:                                               # a favourite app is set: open it with your own songs
            return Intent("play_local_music", {"query": "", "player": fav},
                          _sentence_or(lambda a, r: f"Playing {r.get('playing')} in {r.get('player') or fav} (and {max(0, r.get('queued', 1) - 1)} more)."))
    if re.fullmatch(r"(you )?(just )?play( some| any| my)?( music| songs?| a song| something| audio)?( please)?|start (the )?music|put (some )?music on", low):
        return Intent("media_control", {"action": "play_pause"},
                      _sentence_or("Sent play. If nothing started, no music app is open yet. Say \"open spotify\" first."))
    m = re.match(r"^open\s+(?:the\s+)?(.{2,30}?)\s+(?:and|then)\s+play\s+(?:some\s+|my\s+)?(?:music|songs?)(?:\s+(?:from|on)\s+(?:my\s+)?(?:laptop|pc|computer|disk|drive))?$", low)
    if m:
        player = re.sub(r"\s+(media player|player|app)$", "", m.group(1)).strip()
        return Intent("play_local_music", {"query": "", "player": player},
                      _sentence_or(lambda a, r: f"Playing {r.get('playing')} in {r.get('player') or 'your default player'} "
                                                f"(and {max(0, r.get('queued', 1) - 1)} more)."))
    m = re.match(r"^play\s+(.{2,80}?)(?:\s+(?:on|in)\s+youtube)?$", low)
    if m and not re.match(r"^(the )?(music|song|video|media|audio)$", m.group(1)):
        from urllib.parse import quote_plus
        what = m.group(1)
        return Intent("open_website", {"url": "https://www.youtube.com/results?search_query=" + quote_plus(what)},
                      _sentence_or(f"Opened YouTube search results for \"{what}\"."))
    if re.fullmatch(r"(next|skip)( the)?( song| track)?( please)?|skip", low):
        return Intent("media_control", {"action": "next"}, _sentence_or("Skipped to the next track."))
    if re.fullmatch(r"(previous|go back)( song| track)?|last song", low):
        return Intent("media_control", {"action": "previous"}, _sentence_or("Went back a track."))

    # ---- power
    if re.search(r"\block\b.*\b(pc|laptop|computer|screen)\b|^lock$", low):
        return Intent("power_action", {"action": "lock"}, _sentence_or("Locked."))
    if re.search(r"\b(put|send|make)\b.*\b(pc|laptop|computer|it)\b.*\bsleep\b|^(go to )?sleep( now| please)?$|^(sleep|suspend) (the |my )?(pc|laptop|computer)$", low):
        return Intent("power_action", {"action": "sleep"}, _sentence_or("Going to sleep."))
    m = re.search(r"\b(restart|reboot|shut ?down|shutdown|turn off|power off)\b.*\b(pc|laptop|computer)\b|"
                  r"^(restart|reboot|shut ?down|power off|turn (it )?off)( now| please)?$|^(shut|power|turn) (it )?(down|off)( now| please)?$", low)
    if m:
        act = "restart" if re.search(r"restart|reboot", low) else "shutdown"
        return Intent("power_action", {"action": act}, _sentence_or(lambda a, r: f"OK. It will {act} in {r.get('in_seconds', 15)} seconds. "
                                                                  "Say \"cancel the shutdown\" to stop it."))
    if re.search(r"\b(cancel|stop|abort)\b.*\b(shut ?down|restart|reboot)\b", low):
        return Intent("power_action", {"action": "cancel"}, _sentence_or("Cancelled."))

    # ---- screenshot, temp files
    if re.search(r"\b(screenshot|screen shot|screen capture|capture (my )?screen)\b", low):
        return Intent("take_screenshot", {}, _sentence_or(lambda a, r: f"Saved your screenshot to {r.get('saved')}."))
    every = re.search(r"\bevery\s+(week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", low)
    if re.search(r"\b(clean|clear|delete)\b.*\btemp", low) and every:
        day = "sunday" if every.group(1) == "week" else every.group(1)
        tm = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", low)
        hhmm = "10:00"
        if tm:
            h = int(tm.group(1)) % 24 + (12 if (tm.group(3) or "").lower() == "pm" and int(tm.group(1)) < 12 else 0)
            hhmm = f"{h % 24:02d}:{int(tm.group(2) or 0):02d}"
        return Intent("schedule_weekly_cleanup", {"day": day, "time": hhmm},
                      _sentence_or(lambda a, r: f"Done. I'll clean your temp files every {day.capitalize()} at {hhmm} (first run {r.get('first_run')})."))
    if re.search(r"\b(how much|measure|check|see)\b.*\b(temp|temporary)\b", low):
        return Intent("clean_temp_files", {"dry_run": True}, _sentence_or(lambda a, r: f"About {r.get('could_free')} could be freed from {r.get('files')} old temp files."))
    if re.search(r"\b(clean|clear|delete)\b.*\b(temp|temporary)\b|free up (some )?(disk )?space|clean (up )?my (pc|laptop|computer)", low):
        return Intent("clean_temp_files", {"dry_run": False}, _sentence_or(lambda a, r: f"Cleaned up {r.get('deleted')} files and freed {r.get('freed')}."))

    # ---- notes
    m = re.match(r"^(?:add |save |take |make |write )?(?:a |down )?note\s*(?:that|to self|:|,)?\s*(.+)$", t, re.I)
    if m and not re.match(r"^(?:show|list|read|my)\b", m.group(1), re.I):
        return Intent("add_note", {"text": m.group(1)}, _sentence_or("Saved your note."))
    if re.search(r"^(show|list|read|what are)( me)?( all)?( my| the)? notes$|^my notes$", low):
        return Intent("search_notes", {}, lambda a, r: render.problem(r) or _notes_text(r))

    # ---- stop / close a program
    m = re.match(r"^(?:close|quit|kill|exit|end|stop)\s+(.+)$", low)
    if m:
        name = _close_name(m.group(1))
        if name:
            return Intent("kill_process", {"name": name}, _close_fmt(m.group(1)))

    # ---- searching documents and files
    m = re.search(r"^(?:search|look|find|check)\s+(?:through |in |inside )?(?:all )?(?:my )?(?:documents?|files|notes|folders?)\s+(?:for|about)\s+(.+)$", t, re.I) or \
        re.search(r"^what did i (?:write|say|note|save)\s+(?:about|on|regarding)\s+(.+)$", t, re.I) or \
        re.search(r"^(?:find|search for|look for)\s+(?:documents?|files|notes)\s+(?:about|on|mentioning|containing|with)\s+(.+)$", t, re.I)
    if m:
        return Intent("search_documents", {"query": m.group(1)}, lambda a, r: render.problem(r) or _docs_text(r, a["query"]))
    m = re.match(r"^(?:find|locate|search for|look for|where is|where's)\s+(?:my |the |a )?(?:files? |file called |file named )?(.+?)(?:\s+files?)?$", t, re.I)
    if m and (re.search(r"\bfiles?\b", low) or re.search(r"\.[a-z0-9]{2,4}$", low)):
        return Intent("find_files", {"query": re.sub(r"\.[a-z0-9]{2,4}$", "", m.group(1)) if False else m.group(1)},
                      lambda a, r: render.problem(r) or _files_text(r))

    # ---- the PC itself
    if re.search(r"\b(biggest|largest|big) files?\b|what('s| is) (taking|using|eating) (up )?(my )?(disk|space|storage)", low):
        return Intent("find_large_files", {"limit": 8}, lambda a, r: render.problem(r) or render.format_result(json.dumps(r)))
    if re.search(r"\b(startup|start up|start-up)\b.*\b(programs?|apps?|items?)\b|what (runs|starts) (at|on|when).*(start|boot|log)", low):
        return Intent("list_startup_items", {}, lambda a, r: render.problem(r) or render.format_result(json.dumps(r)))
    if re.search(r"\b(is )?(my |the )?(internet|wi-?fi|network)\b", low) and re.search(
            r"\b(working|down|slow|ok|okay|connected|check|test|speed|fast|problem|issue|why)\b", low):
        speed = bool(re.search(r"speed|slow|fast", low))
        return Intent("diagnose_internet", {"speed": speed}, lambda a, r: render.problem(r) or render.format_result(json.dumps(r)))
    if re.search(r"\b(security check|virus|suspicious|scan my (pc|laptop|computer)|is my (pc|laptop|computer) safe|am i (safe|hacked))\b", low):
        return Intent("security_check", {}, lambda a, r: render.problem(r) or render.format_result(json.dumps(r)))
    if re.search(r"\b(what('s| is)|which)\b.*\b(using|eating|hogging|taking up|slowing|draining)\b|\b(top|heaviest)\s+(programs?|apps?|processes)\b|why is my (pc|laptop|computer) (slow|lagging|hanging)", low):
        by = "cpu" if re.search(r"\b(cpu|processor)\b", low) else "memory"
        return Intent("list_top_processes", {"sort_by": by, "limit": 6}, lambda a, r: render.problem(r) or render.top_sentence(r))
    if re.fullmatch(r"(what('s| is) the |what )?(time|date)( is it)?( now| today)?|what day is it|what time is it", low):
        return Intent("get_current_time", {}, lambda a, r: render.problem(r) or render.clock_sentence(r))
    for which, pat in (("battery", r"\b(battery|charging|charge level|plugged in)\b"), ("cpu", r"\b(cpu|processor)\b"),
                       ("ram", r"\b(ram|memory)\b"), ("disk", r"\b(disk|storage|hard drive|free space|space left)\b")):
        if re.search(pat, low) and len(words) <= 9 and re.search(
                r"\b(how|check|what|level|usage|used|free|left|status|percent|is|are|show|much|many)\b", low) and \
                not re.search(r"\b(remind|set|alert|warn|schedule|clean|delete)\b", low):
            return Intent("get_system_status", {}, _status(which))
    if re.search(r"\b(system|laptop|pc|computer|machine)\b.*\b(status|health|stats|doing|ok|okay)\b|\bhow('s| is) (my|the) (laptop|pc|computer|machine|system)\b|^status$|^system status$|^check my (laptop|pc|computer|system)$", low):
        return Intent("get_system_status", {}, _status("all"))

    # ---- websites ("open youtube.com" that is not an installed app)
    m = re.match(r"^(?:go to|open|visit|browse to)\s+((?:https?://)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?)$", low)
    if m:
        return Intent("open_website", {"url": m.group(1)}, _sentence_or(lambda a, r: f"Opened {r.get('opened')}."))
    return None


# ---------------------------------------------------------------- the 2.8 commands
def _activity_text(a: dict, r: dict) -> str:
    rows = r.get("activity") or []
    if not rows:
        return "I haven't done anything for you yet. Actions that change something show up here."
    text = "What I did lately:\n" + "\n".join("- " + x for x in rows[:10])
    return text + (f"\nI can undo the last one: {r['can_undo']}. Just say \"undo\"." if r.get("can_undo") else "")


def _internet_text(a: dict, r: dict) -> str:
    facts = r.get("facts") or {}
    lines = [r.get("verdict", "")]
    lines += [f"- {k.replace('_', ' ').capitalize()}: {v}" for k, v in facts.items()]
    return "\n".join(lines)


def _junk_text(a: dict, r: dict) -> str:
    rows = r.get("candidates") or []
    if not rows:
        return "I didn't find big old files, old installers or duplicates. Your folders look tidy."
    lines = [f"I found {r.get('found')} things you could remove (about {r.get('could_free')} in all):"]
    lines += [f"{c['n']}. {c['file']} ({c['size']}): {c['why']}" for c in rows]
    return "\n".join(lines) + "\n\nSay \"delete those\" and I'll move them to a safe bin. You can undo for 30 days."


def _scene_names() -> list[str]:
    try:
        from . import tools_v3
        return sorted(tools_v3.scenes(), key=len, reverse=True)
    except Exception:
        return ["study", "movie", "work", "relax", "goodnight"]


def _text_of(a: dict, r: dict) -> str:
    return r.get("text") or "Done."


def _route_v4(low: str) -> Optional[Intent]:
    """3.0: devices on the network, the phone link and linked PCs."""
    if re.search(r"\b(wi-?fi|wifi)\b.*\b(signal|strength|speed|connected|which network)\b|"
                 r"\bwhat('s| is) my (wi-?fi|wifi|network) (signal|called|name)\b", low) and not re.search(r"\bslow|lag|drop", low):
        return Intent("wifi_status", {}, _sentence_or(_text_of))
    if re.search(r"\b(scan|check|look at|search)\b.*\b(my )?(network|wi-?fi|wifi|router)\b|"
                 r"\b(who|what|which)\b.*\b(is |are )?(on|connected to|using|joined)\b.*\b(my )?(wi-?fi|wifi|network|router)\b|"
                 r"\b(what|which|show|list)\b.*\bdevices\b|\bnetwork (map|topology)\b|\bwho('s| is) on my (wi-?fi|wifi|network)\b", low) \
            and not re.search(r"\bslow|speed|lag|drop", low):
        return Intent("scan_network", {}, _sentence_or(_text_of))
    if re.search(r"\bphone\b.*\b(notifications?|messages|alerts)\b|\b(notifications?|messages) (on|from) my phone\b", low):
        return Intent("phone_notifications", {}, _sentence_or(_text_of))
    if re.search(r"\b(screenshot|screen ?shot|picture|see the screen|show the screen)\b.*\bphone\b|\bphone('s)? screen\b", low):
        return Intent("phone_screenshot", {}, _sentence_or(_text_of))
    if re.search(r"\b(my )?phone\b.*\b(status|battery|storage|doing|going on|happening|connected|linked)\b|"
                 r"\b(what('s| is)|check|see|how('s| is))\b.*\b(on |happening on |going on )?(my )?phone\b|\b(link|connect) (to )?(my )?(android )?phone\b", low) \
            and not re.search(r"\b(play|open|call|text|send|remind|lock|sleep)\b", low):
        return Intent("phone_status", {}, _sentence_or(_text_of))
    if re.search(r"\b(let|allow)\b.*\b(other )?(pcs?|computers?|laptops?)\b.*\b(link|connect)\b|\bpairing code\b|\bshow (me )?(a |the )?link code\b", low):
        return Intent("allow_pc_link", {"on": True}, _sentence_or(_text_of))
    m = re.search(r"\blink (?:to |with )?(?:the )?(?:other )?(?:pc|computer|laptop)?\s*((?:\d{1,3}\.){3}\d{1,3})\D+(\d{6})\b", low)
    if m:
        return Intent("link_pc", {"host": m.group(1), "code": m.group(2)}, _sentence_or(_text_of))
    if re.fullmatch(r"(list |show )?(my )?(linked|connected) (pcs?|computers?|laptops?)", low):
        return Intent("list_linked_pcs", {}, _sentence_or(_text_of))
    try:
        from . import peers
        names = list(peers.linked())
    except Exception:
        names = []
    for n in names:
        if n.lower() in low and re.search(r"\b(status|how is|how's|check|doing|health|what's going on)\b", low):
            return Intent("pc_status", {"name": n}, _sentence_or(_text_of))
    return None


def _route_v3(low: str) -> Optional[Intent]:
    if re.fullmatch(r"(what (have you|did you|have you been) (do|done|doing)( lately| recently| today)?|"
                    r"(show|list|see)( me)?( my| the)?( recent| latest)? (activity|history|actions)( log| list)?|activity (log|history)|"
                    r"what did you do( for me)?)", low):
        return Intent("activity_history", {}, _sentence_or(_activity_text))
    if re.fullmatch(r"undo( that| it| the last (thing|action|one))?|(please )?undo|put (them|it|the files|those) back|revert that|"
                    r"restore (the |my )?(deleted )?files", low):
        return Intent("undo_last_action", {}, _sentence_or(lambda a, r: f"Done. {r.get('undone')}."))
    if re.search(r"\b(why|how come)\b.*\b(internet|wi-?fi|network|connection)\b.*\b(slow|bad|lag\w*|poor|dropping|weak|unstable)\b|"
                 r"\b(internet|wi-?fi|network|connection)\b (is |has been |keeps |feels )?(so |very |really )?(slow|lagging|laggy|dropping)\b|"
                 r"\b(speed|check) (test|my (internet|wi-?fi) speed)\b|why is (it|youtube|everything) (so )?slow online", low):
        return Intent("why_is_internet_slow", {}, _sentence_or(_internet_text))
    if re.search(r"\b(find|show|look for|scan for|list)\b.*\b(junk|duplicates?|duplicate files|old installers|big old files|unused files)\b|"
                 r"\bsmart (clean ?up|clean)\b|\bclean (up )?my (pc|laptop|computer|disk|files|storage)( smartly| properly)?$|"
                 r"\bfree up (some )?(disk )?(space|storage)\b|\bwhat can i (delete|remove)\b", low):
        return Intent("find_junk", {}, _sentence_or(_junk_text))
    if re.fullmatch(r"(yes,? )?(delete|remove|trash|clear|get rid of|move) (those|them|all of them|the junk|these|the duplicates|all)( to the (safe )?bin)?", low):
        return Intent("move_to_trash", {"which": "last"},
                      _sentence_or(lambda a, r: f"Moved {r.get('moved')} files ({r.get('freed')}) to the safe bin. Say \"undo\" to put them back."))
    m = re.fullmatch(r"(?:back ?up|backup)\s+(?:all )?(?:of )?(?:my\s+)?(.*?)(?:\s+(?:to|on|onto|in)\s+(?:my\s+|the\s+|a\s+)?"
                     r"(?:flash ?drive|usb(?: drive| stick)?|external(?: hard)?(?: drive| disk)?|hard ?drive|drive|disk|pen ?drive|memory stick))?", low)
    if m:
        want = [w for w in ("documents", "desktop", "pictures", "downloads", "videos", "music") if w in m.group(1)]
        return Intent("backup_files", {"folders": ", ".join(want)},
                      _sentence_or(lambda a, r: f"Backed up {r.get('copied')} new file{'s' if r.get('copied') != 1 else ''} "
                                                f"({r.get('size')}) to {r.get('to')}. {r.get('already_there')} were already there."
                                                + (f" {r['note']}" if r.get("note") else "")))
    if re.search(r"\b(is my|any|what|which)\b.*\b(flash ?drive|usb (drive|stick)|external (hard )?(drive|disk)|drives)\b.*\b(plugged|connected|in|there|attached|available)?\b", low) and \
            re.search(r"flash|usb|external|drives", low) and not re.search(r"\b(play|open|backup|back up)\b", low):
        return Intent("find_drives", {}, _sentence_or(lambda a, r: "External drives: " + ", ".join(f"{d['drive']} ({d['free']} free)" for d in r.get("drives", []))
                                                      if r.get("drives") else "I can't see any external drive. Plug one in and ask again."))
    if re.fullmatch(r"(minimi[sz]e|hide) (all|everything|all windows|every window|all my windows)|show (the |my )?desktop|clear (the |my )?screen", low):
        return Intent("window_control", {"action": "minimise_all"}, _sentence_or("Showing the desktop."))
    if re.fullmatch(r"(restore|bring back|show) (all |my |the )?windows( back)?|undo show desktop", low):
        return Intent("window_control", {"action": "restore_all"}, _sentence_or("Brought your windows back."))
    m = re.fullmatch(r"snap\s+(.+?)\s+(?:to\s+)?(?:the\s+)?(left|right)(?: side)?|put\s+(.+?)\s+on\s+the\s+(left|right)(?: side)?", low)
    if m:
        app, side = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        return Intent("window_control", {"action": f"snap_{side}", "app": app}, _sentence_or(f"Snapped {app} to the {side}."))
    m = re.fullmatch(r"(minimi[sz]e|maximi[sz]e|focus on|focus|switch to|go to|bring up)\s+(?:the\s+)?(.{2,30})", low)
    if m and not re.search(r"\b(mode|music|volume|tab|page|chat|home|settings|voice|tools|help)\b", m.group(2)) and \
            m.group(2) not in ("it", "this", "that", "window", "app"):
        verb = m.group(1)
        act = "minimise" if verb.startswith("minim") else "maximise" if verb.startswith("maxim") else "focus"
        return Intent("window_control", {"action": act, "app": m.group(2)},
                      _sentence_or(lambda a, r: {"minimise": f"Minimised {m.group(2)}.", "maximise": f"Maximised {m.group(2)}.",
                                                 "focus": f"Switched to {m.group(2)}."}[act]))
    m = re.fullmatch(r"(?:set )?(?:the )?(?:screen )?brightness(?: to)?\s*(\d{1,3})\s*%?", low)
    if m:
        return Intent("brightness", {"level": min(100, int(m.group(1)))}, _sentence_or(lambda a, r: f"Brightness set to {r.get('brightness')}%."))
    if re.fullmatch(r"(dim|lower|reduce)( the)?( screen| brightness| display)( a (bit|little))?|too bright", low):
        return Intent("brightness", {"level": 30}, _sentence_or("Dimmed the screen to 30%."))
    if re.fullmatch(r"(brighten|raise|increase)( the)?( screen| brightness| display)|(make )?(the )?screen brighter|(max|full) brightness", low):
        return Intent("brightness", {"level": 100 if "max" in low or "full" in low else 80},
                      _sentence_or(lambda a, r: f"Brightness set to {r.get('brightness')}%."))
    m = re.fullmatch(r"(?:make|create|save|add)(?: me)? (?:a |new |another )?scene (?:called |named )?([a-z0-9 ]{2,20}?)\s*(?::| to | that | which |with )\s*(.+)", low)
    if m:
        return Intent("save_scene", {"name": m.group(1), "steps": m.group(2)},
                      _sentence_or(lambda a, r: f"Saved the {r.get('saved')} scene: {r.get('does')}. Say \"{r.get('saved')} mode\" to use it."))
    if re.fullmatch(r"(list|show|what) (my |the |are my |are the )?scenes|what scenes (do you|can you)( know| have| run)?|what modes (do you|can you) (know|have)", low):
        return Intent("list_scenes", {}, _sentence_or(lambda a, r: "Scenes I know:\n" + "\n".join(f"- {s['name']}: {s['does']}" for s in r.get("scenes", []))))
    names = "|".join(re.escape(n) for n in _scene_names())
    m = re.fullmatch(rf"(?:(?:start|run|activate|turn on|enter|switch to|go into|go to|begin)\s+)?(?:the\s+)?({names})\s+(?:mode|scene)", low) or \
        re.fullmatch(rf"(?:(?:start|run|activate|turn on|enter|begin)\s+)(?:the\s+)?(?:scene\s+)?({names})", low) or \
        (re.fullmatch(r"(good ?night)( trix| laptop)?", low) and re.fullmatch(r"(good ?night).*", low))
    if m and not re.fullmatch(r"focus", m.group(1) or ""):
        name = m.group(1).replace(" ", "")
        return Intent("run_scene", {"name": "goodnight" if name.startswith("goodnight") else name},
                      _sentence_or(lambda a, r: f"{a['name'].capitalize()} mode is on: " + ", ".join(r.get("done", [])) + "."
                                                + (f" Couldn't do: {', '.join(r['skipped'])}." if r.get("skipped") else "")))
    m = re.search(r"how (has|have|is|are) (my )?(pc|laptop|computer|battery|disk|memory|cpu|ram)( been)?( doing)?(?: (?:this|over the|in the|for the|the)?\s*(last )?(\w+))?|"
                  r"\b(health|pc|laptop) (history|trends?)\b|^(show|give me) (my )?(pc |laptop )?(history|trends?)$", low)
    if m:
        days = 30 if "month" in low else 1 if re.search(r"today|24|day\b", low) and "days" not in low else 7
        return Intent("health_trend", {"days": days}, _sentence_or(lambda a, r: r.get("report", "")))
    return None


# ---------------------------------------------------------------- sentences
HELP_TEXT = ("I can do these without any AI brain: check battery, CPU, memory and disk; show what's using your PC; "
             "open and close programs; set reminders (\"remind me in 10 minutes to stretch\"); take notes; "
             "remember facts (\"remember that ...\"); find files and search inside your documents; volume and "
             "media; focus mode; clean temp files (also every week); screenshot; lock, sleep, restart; check "
             "your internet and security; find junk and duplicates (\"smart cleanup\"); back up to a flash drive; "
             "minimise, maximise and snap windows; brightness; scenes (\"study mode\", \"movie mode\"); \"why is my internet slow\"; "
             "see what is on your Wi-Fi (\"scan my network\"); check a linked Android phone; link other PCs running Laptop AI; "
             "\"undo\" and \"what did you do\"; and run a health check on me. For anything else, just ask in your own words.")


def _close_fmt(raw: str) -> Formatter:
    label = raw.strip()

    def f(a: dict, r: dict) -> str:
        bad = render.problem(r)
        if bad:
            return f"I couldn't find {label} running." if "No matching process" in bad else bad
        return f"Closed {label}."
    return f


def _diag_text(r: dict) -> str:
    lines = [r.get("summary", "")]
    for c in r.get("checks", []):
        if c["result"] != "ok":
            lines.append(f"- {c['check']}: {c['detail']}" + (f" Fix: {c['fix']}" if c.get("fix") else ""))
    return "\n".join(lines)


def _notes_text(r: dict) -> str:
    notes = r.get("notes") or r.get("showing") or []
    if not notes:
        return "You have no notes yet. Say \"note: ...\" to add one."
    return "Your notes:\n" + "\n".join(f"- {n.get('text', n)}" for n in notes[:10])


def _files_text(r: dict) -> str:
    rows = r.get("showing") or []
    if not rows:
        return "I couldn't find a file like that in your personal folders."
    return f"Found {r.get('found')} file(s):\n" + "\n".join(f"- {x['path']}" for x in rows[:6])


def _docs_text(r: dict, query: str) -> str:
    rows = r.get("showing") or []
    if not rows:
        return f"I didn't find \"{query}\" inside your documents (looked at {r.get('searched', 0)} files)."
    out = [f"Found \"{query}\" in {r.get('found')} file(s):"]
    out += [f"- {x['file']} ({x['folder']}): {x['match']}" for x in rows[:5]]
    if r.get("cut_short"):
        out.append("(I stopped early to stay quick, so narrow it down for more.)")
    return "\n".join(out)
