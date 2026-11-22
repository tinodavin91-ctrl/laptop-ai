"""Settings and file locations. Nothing secret is ever written to disk here."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path(os.environ.get("LAPTOP_AI_HOME", str(Path.home() / ".laptop_ai")))
SETTINGS_FILE = APP_DIR / "settings.json"
TASKS_FILE = APP_DIR / "tasks.json"
LOG_FILE = APP_DIR / "actions.log"
NOTES_FILE = APP_DIR / "notes.json"
MEMORY_FILE = APP_DIR / "memory.json"      # things you told the assistant to remember
HISTORY_FILE = APP_DIR / "history.json"    # the last conversation, so it survives a restart
FOCUS_FILE = APP_DIR / "focus.json"
APP_LOG = APP_DIR / "app.log"              # technical log for troubleshooting (rotated, small)

BACKENDS = ("auto", "offline", "online")
AUTONOMY = ("ask", "auto_safe", "report_only")


def ensure_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


def anthropic_key() -> str:
    """The API key is read from the environment only (never saved in settings.json)."""
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


@dataclass
class Settings:
    backend: str = "auto"                 # auto = offline first, online if offline fails
    autonomy: str = "ask"                 # ask | auto_safe | report_only
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"       # the full llama3.2 (about 2 GB)
    claude_model: str = "claude-sonnet-5-5"
    speak_replies: bool = False
    monitor_enabled: bool = True
    monitor_interval_s: int = 30
    cpu_alert_pct: int = 90
    ram_alert_pct: int = 90
    disk_alert_pct: int = 92
    battery_alert_pct: int = 15
    # phone (Telegram). The bot token is NOT here: it lives in Windows Credential Manager.
    telegram_chat_id: str = ""
    telegram_reminders: bool = True       # send reminders and scheduled reports to the phone
    telegram_alerts: bool = True          # send CPU / memory / disk / battery warnings
    telegram_questions: bool = False      # answer READ-ONLY questions sent from the phone
    # app behaviour
    close_to_tray: bool = True            # closing the window keeps the app running by the clock
    # advanced: lets the AI propose PowerShell commands. Off by default; EVERY command still asks first.
    allow_commands: bool = False
    # hands-free
    wake_word_enabled: bool = False       # "Trix": always-listening, opt-in
    recognition: str = "google"           # how a spoken command is turned into text: google | offline
    # v2
    auto_model: bool = True               # if the chosen Ollama model is missing, use one that IS installed
    reminder_sound: bool = True            # chime when a reminder is due
    speak_reminders: bool = True           # also read the reminder out loud
    alert_sound: bool = True               # beep for PC warnings
    user_name: str = ""                   # what Laptop AI calls you (asked on first run)
    allow_pc_link: bool = False           # other PCs running Laptop AI may link to this one (read-only, needs a pairing code)
    setup_done: bool = False              # the welcome screen has been completed (or skipped)
    watchdog_enabled: bool = True         # background scan for malware signs and hardware trouble
    watchdog_interval_min: int = 15
    suggest_close_apps: bool = True       # ask to close a program that hogs memory or the processor
    power_without_asking: bool = True     # "shut down", "sleep", "lock", "restart" run as soon as you ask (shutdown waits 15 s so you can cancel)
    music_player: str = ""                # "play some music" opens this app (empty = the PC's default player)
    obey_commands: bool = True            # do what I type or say without asking; only ask when it wants to act on its own
    startup_check: bool = True            # a quiet self-check when the app opens; speaks up only if something is broken
    phone_actions: bool = True            # phone buttons for Lock / Sleep (each one asks you to tap Yes on the phone)
    theme: str = "blue"                   # accent colour: blue | emerald | violet | gold | rose  (applies after a restart)
    voice_name: str = ""                  # part of the name of the voice Trix speaks with (empty = the PC's default)
    voice_rate: int = 185                 # words per minute
    conversation_mode: bool = True        # after Trix answers, keep listening a few seconds for a follow-up
    scenes: dict = field(default_factory=dict)   # your own scenes: name -> list of [tool, arguments]
    restore_chat: bool = True             # bring back the last conversation after a restart
    focus_apps: list = field(default_factory=lambda: ["discord.exe", "steam.exe", "whatsapp.exe", "telegram.exe"])
    update_manifest_url: str = ""         # where "is there a new version" is checked (a small JSON file you host)
    update_check_auto: bool = True        # quietly check once a day on startup; never installs anything by itself
    first_run_at: str = ""                # the date Laptop AI first ran on this PC (set automatically, for the Home tip strip)

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        data_keys: set = set()
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("settings must be an object")
            data_keys = set(data)
            for key, value in data.items():
                if hasattr(s, key) and isinstance(value, type(getattr(s, key))):    # ignore wrong-typed values
                    setattr(s, key, value)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            _set_aside(SETTINGS_FILE)        # corrupt file: keep a copy for troubleshooting, start clean
        if "setup_done" not in data_keys and s.user_name and s.user_name != "Tino":
            # an install from before the welcome screen existed, with a real name already set by hand (not the old
            # hardcoded "Tino" default every pre-3.0 install had, which must never count as "already set up")
            s.setup_done = True
        if s.backend not in BACKENDS:
            s.backend = "auto"
        if s.autonomy not in AUTONOMY:
            s.autonomy = "ask"
        s.monitor_interval_s = max(5, int(s.monitor_interval_s))
        s.watchdog_interval_min = max(5, int(s.watchdog_interval_min))
        if s.ollama_model in ("llama3.2:1b", "llama3.2:1b-instruct-q8_0"):
            s.ollama_model = "llama3.2:3b"     # upgrade: Brain falls back to the 1b if 3b isn't installed yet
        if not s.first_run_at:
            import datetime as _dt
            s.first_run_at = _dt.date.today().isoformat()
            s.save()
        return s

    def save(self) -> None:
        write_json(SETTINGS_FILE, asdict(self))


def write_json(path: Path, data) -> None:
    """Write a file so a crash or power cut can never leave it half-written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        _set_aside(Path(path))
        return default


def _set_aside(path: Path) -> None:
    try:
        path = Path(path)
        if path.exists():
            os.replace(path, path.with_suffix(path.suffix + ".bad"))
    except OSError:
        pass
