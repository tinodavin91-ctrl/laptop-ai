"""Health check: looks at everything the assistant depends on and says, in plain words, what is wrong and
how to fix it. Every check is independent, so one failing probe never hides the others."""
from __future__ import annotations

import platform
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import __version__, autostart, config

OK, WARN, FAIL = "ok", "warn", "fail"
ICON = {OK: "✔", WARN: "!", FAIL: "✘"}


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def _guard(name: str, fn: Callable[[], Check]) -> Check:
    try:
        return fn()
    except Exception as e:                       # a broken probe is itself a finding
        return Check(name, WARN, f"Couldn't check this ({e.__class__.__name__}).")


def installed_models(settings: config.Settings, get=None) -> Optional[list[str]]:
    """Names of the Ollama models on this PC, or None when Ollama isn't reachable."""
    import requests
    try:
        r = (get or requests.get)(settings.ollama_url + "/api/tags", timeout=2)
        r.raise_for_status()
        return [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
    except Exception:
        return None


def run_all(settings: config.Settings, *, models: Optional[Callable[[], Optional[list[str]]]] = None,
            has_key: Optional[Callable[[], bool]] = None, mic: Optional[Callable[[], bool]] = None,
            voice_model: Optional[Callable[[], bool]] = None, phone: Optional[Callable[[], tuple[bool, bool]]] = None,
            home: Optional[Path] = None) -> list[Check]:
    home = Path(home) if home else config.APP_DIR
    models = models or (lambda: installed_models(settings))
    has_key = has_key or (lambda: bool(config.anthropic_key()))

    def offline() -> Check:
        found = models()
        if found is None:
            return Check("Offline brain (Ollama)", FAIL, "Ollama isn't running or isn't installed.",
                         "Install Ollama from ollama.com, open it once, then run:  ollama pull " + settings.ollama_model)
        base = settings.ollama_model.split(":")[0]
        if any(n == settings.ollama_model or n.split(":")[0] == base for n in found):
            return Check("Offline brain (Ollama)", OK, f"Running, model '{settings.ollama_model}' is installed.")
        if found and settings.auto_model:
            return Check("Offline brain (Ollama)", WARN,
                         f"'{settings.ollama_model}' isn't installed, so I'll use '{found[0]}' instead.",
                         f"For best results run:  ollama pull {settings.ollama_model}")
        return Check("Offline brain (Ollama)", FAIL, f"Ollama is running but has no model called '{settings.ollama_model}'.",
                     f"Run:  ollama pull {settings.ollama_model}")

    def online() -> Check:
        if has_key():
            return Check("Online brain (Claude)", OK, "An API key is set.")
        return Check("Online brain (Claude)", WARN, "No ANTHROPIC_API_KEY is set (optional).",
                     "Only needed if you want the online brain. Everyday commands work without it.")

    def mic_check() -> Check:
        try:
            ok = mic() if mic else _mic_present()
        except Exception:
            ok = False
        return Check("Microphone", OK if ok else WARN, "A microphone is available." if ok else "No microphone found.",
                     "" if ok else "Plug in or enable a microphone to use voice.")

    def wake() -> Check:
        try:
            ok = voice_model() if voice_model else _vosk_ready()
        except Exception:
            ok = False
        return Check("Voice model (\"Trix\")", OK if ok else WARN,
                     "Installed." if ok else "Not downloaded yet (needed only for the wake word).",
                     "" if ok else "Settings > Hands-free > Download voice model.")

    def tg() -> Check:
        token, chat = phone() if phone else _phone_state(settings)
        if token and chat:
            return Check("Phone (Telegram)", OK, "Linked.")
        if token:
            return Check("Phone (Telegram)", WARN, "Token saved but your chat isn't linked.",
                         "Send your bot 'hi', then Settings > Phone > Link my chat.")
        return Check("Phone (Telegram)", WARN, "Not set up (optional).")

    def start() -> Check:
        try:
            on = autostart.is_enabled()
        except Exception:
            on = False
        return Check("Starts with Windows", OK if on else WARN, "Yes." if on else "No.",
                     "" if on else "Settings > App > Start with Windows, so reminders work after a restart.")

    def disk() -> Check:
        free = shutil.disk_usage(home if home.exists() else home.anchor or ".").free
        gb = free / 1073741824
        if gb < 0.2:
            return Check("Disk space", FAIL, f"Only {gb:.2f} GB free.", "Free some space (try 'clean my temp files').")
        return Check("Disk space", OK if gb >= 1 else WARN, f"{gb:.1f} GB free.")

    def folder() -> Check:
        try:
            home.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=home, delete=True):
                pass
            return Check("Data folder", OK, f"Writable ({home}).")
        except OSError as e:
            return Check("Data folder", FAIL, f"Can't write to {home} ({e.__class__.__name__}).",
                         "Check the folder's permissions or your antivirus.")

    def files() -> Check:
        bad = sorted(p.name for p in home.glob("*.bad")) if home.exists() else []
        if bad:
            return Check("Saved files", WARN, "A damaged file was set aside: " + ", ".join(bad) + ".",
                         "Nothing to do: a fresh one was created. You can delete the .bad file.")
        big = [p.name for p in (home.glob("*.log") if home.exists() else []) if p.stat().st_size > 5_000_000]
        if big:
            return Check("Saved files", WARN, "Large log file: " + ", ".join(big) + ".", "It is safe to delete.")
        return Check("Saved files", OK, "Settings, reminders and notes look fine.")

    checks = [_guard(n, f) for n, f in (("Offline", offline), ("Online", online), ("Mic", mic_check), ("Wake", wake),
                                        ("Phone", tg), ("Start", start), ("Disk", disk), ("Folder", folder),
                                        ("Files", files))]
    checks.append(Check("App", OK, f"Laptop AI {__version__} on {platform.system()} {platform.release()}, "
                                   f"Python {sys.version.split()[0]}."))
    brain_ok = checks[0].status in (OK, WARN) or checks[1].status == OK
    if not brain_ok:
        checks.insert(0, Check("AI brain", WARN, "No AI brain is ready, but built-in commands still work "
                               "(battery, memory, reminders, open/close programs, notes, volume, files...).",
                               "Fix the Offline or Online brain below for open-ended questions."))
    return checks


def _mic_present() -> bool:
    import sounddevice as sd
    return any(d.get("max_input_channels", 0) > 0 for d in sd.query_devices())


def _vosk_ready() -> bool:
    from . import wakeword
    return wakeword.available() and wakeword.model_installed()


def _phone_state(settings: config.Settings) -> tuple[bool, bool]:
    from . import notify
    return bool(notify.get_token()), bool(settings.telegram_chat_id)


def summary(checks: list[Check]) -> str:
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    if not fails and not warns:
        return "Everything looks healthy."
    return f"{fails} problem(s), {warns} thing(s) worth a look."


def as_text(checks: list[Check]) -> str:
    lines = [summary(checks), ""]
    for c in checks:
        lines.append(f"{ICON[c.status]} {c.name}: {c.detail}")
        if c.fix and c.status != OK:
            lines.append(f"     Fix: {c.fix}")
    return "\n".join(lines)
