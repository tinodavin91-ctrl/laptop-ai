"""Telegram link: push reminders and alerts to your phone, and (optionally) answer
READ-ONLY questions you send from it.

Safety design:
  * The bot token is stored in Windows Credential Manager (through `keyring`), never in
    settings.json. The TELEGRAM_BOT_TOKEN environment variable also works.
  * Only ONE chat is ever listened to: the chat id saved in settings. Messages from anyone
    else are ignored without a reply.
  * Questions from the phone run with the executor in read-only mode: the AI is not even told
    about tools that change things, so nobody can close programs or delete files from the phone.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Callable, Optional

import requests

from . import config

try:
    import keyring
except Exception:            # not installed, or no usable backend
    keyring = None

API = "https://api.telegram.org"
SERVICE, ACCOUNT = "laptop-ai", "telegram_bot_token"
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
MAX_TEXT = 4000                                   # Telegram's limit is 4096


# ---------------------------------------------------------------- token storage
def get_token() -> str:
    env = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if env:
        return env
    if keyring is None:
        return ""
    try:
        return (keyring.get_password(SERVICE, ACCOUNT) or "").strip()
    except Exception:
        return ""


def looks_like_token(token: str) -> bool:
    return bool(TOKEN_RE.match(token.strip()))


def save_token(token: str) -> tuple[bool, str]:
    if not looks_like_token(token):
        return False, "That doesn't look like a bot token. It looks like 123456789:ABC... (copy it from @BotFather)."
    if keyring is None:
        return False, ("Secure storage isn't installed. Run  pip install keyring  "
                       "(or set the TELEGRAM_BOT_TOKEN environment variable).")
    try:
        keyring.set_password(SERVICE, ACCOUNT, token.strip())
        return True, ""
    except Exception as e:
        return False, f"Couldn't save the token securely ({e.__class__.__name__}). Set TELEGRAM_BOT_TOKEN instead."


def forget_token() -> None:
    if keyring is not None:
        try:
            keyring.delete_password(SERVICE, ACCOUNT)
        except Exception:
            pass


# ---------------------------------------------------------------- Telegram client
class Telegram:
    def __init__(self, settings: config.Settings, post=requests.post, token_provider: Callable[[], str] = get_token):
        self.s, self.post, self.token_provider = settings, post, token_provider
        self.last_error = ""

    @property
    def has_token(self) -> bool:
        return bool(self.token_provider())

    @property
    def configured(self) -> bool:
        return self.has_token and bool(self.s.telegram_chat_id)

    def call(self, method: str, payload: Optional[dict] = None, timeout: float = 15):
        """Returns (ok, result_or_error_text). Never raises."""
        token = self.token_provider()
        if not token:
            return False, "No bot token saved yet."
        try:
            r = self.post(f"{API}/bot{token}/{method}", json=payload or {}, timeout=timeout)
        except requests.RequestException as e:
            return False, f"Couldn't reach Telegram ({e.__class__.__name__}). Check your internet connection."
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 401:
            return False, "Telegram rejected the bot token. Copy it again from @BotFather."
        if not data.get("ok"):
            return False, str(data.get("description") or f"Telegram error {r.status_code}.")
        return True, data.get("result")

    def bot_name(self) -> tuple[bool, str]:
        ok, res = self.call("getMe")
        return (True, "@" + str(res.get("username", "your bot"))) if ok else (False, res)

    def find_chat(self) -> tuple[bool, str]:
        """Find the private chat that last messaged the bot (you, after sending it 'hi')."""
        ok, res = self.call("getUpdates", {"timeout": 0, "limit": 20})
        if not ok:
            return False, res
        for update in reversed(res or []):
            chat = (update.get("message") or {}).get("chat") or {}
            if chat.get("type") == "private" and "id" in chat:
                return True, str(chat["id"])
        return False, "No messages yet. Open your bot in Telegram, send it  hi , then press this button again."

    def send(self, text: str, buttons: Optional[list[tuple[str, str]]] = None) -> tuple[bool, str]:
        """buttons: [(label, key), ...] shown as tappable buttons under the message (two per row)."""
        if not self.s.telegram_chat_id:
            return False, "No chat is linked yet."
        payload = {"chat_id": self.s.telegram_chat_id, "text": text[:MAX_TEXT], "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [
                [{"text": label, "callback_data": key} for label, key in buttons[i:i + 2]]
                for i in range(0, len(buttons), 2)]}
        ok, res = self.call("sendMessage", payload)
        self.last_error = "" if ok else str(res)
        return (True, "") if ok else (False, str(res))

    def send_async(self, text: str) -> None:
        """Fire and forget (reminders must never freeze the window)."""
        if self.configured:
            threading.Thread(target=self.send, args=(text,), daemon=True).start()


# ---------------------------------------------------------------- questions from the phone
class PhoneQuestions:
    """Long-polls Telegram and hands text from the ONE linked chat to `handler`, then sends the reply."""

    def __init__(self, tg: Telegram, handler: Callable[[str], str],
                 buttons: Optional[list[tuple[str, str]]] = None,
                 button_handler: Optional[Callable[[str], str]] = None):
        self.tg, self.handler = tg, handler
        self.buttons, self.button_handler = buttons or [], button_handler
        self.offset: Optional[int] = None
        self.confirm_keys: dict[str, float] = {}           # one-tap "Yes" keys we just offered -> when they expire
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _skip_old_messages(self) -> None:
        """Messages sent while the app was closed must never run now."""
        ok, res = self.tg.call("getUpdates", {"offset": -1, "timeout": 0})
        if ok and res:
            self.offset = res[-1]["update_id"] + 1

    def poll_once(self, timeout: int = 0) -> int:
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if self.offset is not None:
            payload["offset"] = self.offset
        ok, res = self.tg.call("getUpdates", payload, timeout=timeout + 10)
        if not ok:
            return -1
        handled = 0
        for update in res or []:
            self.offset = update["update_id"] + 1
            if update.get("callback_query"):
                handled += self._button(update["callback_query"])
                continue
            msg = update.get("message") or {}
            chat = msg.get("chat") or {}
            text = (msg.get("text") or "").strip()
            if str(chat.get("id")) != str(self.tg.s.telegram_chat_id) or chat.get("type") != "private":
                continue                                    # strangers get no reply at all
            if not text:
                continue
            if text.lower() in ("/start", "/help", "/menu"):
                self.tg.send("Hi! Tap a button, or just type a question (battery, what's using memory, disk space). "
                             "I can only look, except Lock and Sleep, which ask you to tap Yes first.", self.buttons or None)
                continue
            try:
                reply = self.handler(text)
            except Exception as e:
                reply = f"Something went wrong: {e}"
            self.tg.send(reply or "(no answer)")
            handled += 1
        return handled

    def _button(self, cb: dict) -> int:
        """A tapped button. Only the linked private chat counts, and only keys we offered."""
        chat = ((cb.get("message") or {}).get("chat")) or {}
        if str(chat.get("id")) != str(self.tg.s.telegram_chat_id) or chat.get("type") != "private":
            return 0
        self.tg.call("answerCallbackQuery", {"callback_query_id": cb.get("id")})     # stops the spinner
        key = cb.get("data")
        offered = key in [k for _label, k in self.buttons]
        confirmed = self.confirm_keys.pop(key, 0) > time.time()      # a Yes/Cancel we offered a moment ago works once
        if not self.button_handler or not (offered or confirmed):
            return 0
        extra = None
        try:
            reply = self.button_handler(key)
            if isinstance(reply, tuple):                              # (text, [(label, key), ...]): ask for a confirming tap
                reply, extra = reply
                self.confirm_keys = {k: time.time() + 120 for _label, k in extra}
        except Exception as e:
            reply = f"Something went wrong: {e}"
        self.tg.send(reply or "(no answer)", extra)
        return 1

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        self._skip_old_messages()
        while not self._stop.is_set():
            if self.poll_once(timeout=25) < 0:
                self._stop.wait(10)                          # offline: wait, then try again

    def stop(self) -> None:
        self._stop.set()
