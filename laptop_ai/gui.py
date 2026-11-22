"""The desktop window (CustomTkinter): sidebar + Home, Chat, Voice, Files & Tools, Settings, Help.

Threading rule: widgets are only touched from the main thread. Worker threads (AI, monitor,
scheduler, voice, tool buttons) put callables on `self.ui_queue`, and the main thread runs
them every 100 ms. Confirmation dialogs work the same way: the worker waits on an Event
while the main thread shows the dialog.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk
from PIL import Image

from . import __version__, autostart, config, notify, sound, tools, tools_extra, tray, wakeword
from .brain import Brain
from . import dash, doctor, focus, health, hogs, holo, holo_model, models, nowplaying, orb, render, theme, tools_v2, trends, trendview, welcome, devicesview, tools_v4, updater, tips
from .monitor import Monitor, read_stats
from .voice import Voice

try:
    import winsound            # Windows only: the short "I'm listening" beep
except ImportError:
    winsound = None

ASSETS = Path(__file__).parent / "assets"

RECOG_TEXT = {
    "google": "google: more accurate. The short command clip is sent to Google to be turned into text.",
    "offline": "offline: stays on this PC (uses the downloaded voice model). Less accurate.",
}

# ---- palette ---------------------------------------------------------------
BG, SIDEBAR = "#070b14", "#0a0f1d"
CARD, CARD2, LINE = "#0f1730", "#142042", "#1b2a52"
ACCENT, ACCENT_HOVER, GLOW = theme.colours(config.Settings.load().theme)         # the chosen accent colour (see Settings)
TEXT, MUTED = "#e8eefc", "#8190b3"
GREEN, AMBER, RED = "#22c55e", "#f59e0b", "#ef4444"
RISK_COLORS = {"read": GREEN, "safe": "#38bdf8", "low": AMBER, "high": RED}

NAV = [("home", "home", "Home"), ("chat", "chat", "Chat"), ("voice", "mic", "Voice Assistant"),
       ("devices", "net", "Devices"), ("tools", "folder", "Files & Tools"), ("settings", "gear", "Settings"), ("help", "help", "Help & Support")]

TOOL_LABELS = {
    "hardware_health": "scanning for hardware and malware problems", "volume": "changing the volume",
    "media_control": "controlling media", "focus_mode": "starting focus mode", "stop_focus_mode": "ending focus mode",
    "focus_status": "checking focus mode", "play_local_music": "finding music on your PC", "remember": "remembering that", "forget": "forgetting", "list_memories": "recalling memories",
    "search_documents": "searching your documents", "run_diagnostics": "running a health check",
    "schedule_weekly_cleanup": "scheduling weekly cleanup",
    "get_system_status": "checking system status", "list_top_processes": "listing programs",
    "find_process": "looking for a program", "kill_process": "closing a program",
    "clean_temp_files": "cleaning temp files", "open_app": "opening an app",
    "add_reminder": "setting a reminder", "schedule_task": "scheduling a job",
    "list_scheduled_tasks": "listing scheduled jobs", "cancel_scheduled_task": "cancelling a job",
    "get_current_time": "checking the time", "find_files": "searching your files",
    "add_note": "saving a note", "search_notes": "looking through your notes", "delete_note": "deleting a note",
    "schedule_morning_report": "setting the morning report", "list_apps": "looking through installed apps",
    "open_website": "opening a website", "list_services": "listing Windows services",
    "run_command": "running a PowerShell command", "diagnose_internet": "testing the internet",
    "network_status": "checking the network", "list_connections": "looking at connections",
    "security_check": "running a security check", "find_large_files": "finding big files",
    "list_startup_items": "listing startup programs", "disable_startup_item": "changing a startup program",
    "enable_startup_item": "changing a startup program", "power_action": "changing the PC's power state",
    "take_screenshot": "taking a screenshot",
    "undo_last_action": "undoing the last action", "activity_history": "looking at what I did", "find_junk": "looking for junk files",
    "move_to_trash": "moving files to the safe bin", "restore_from_trash": "putting files back", "find_drives": "looking for drives",
    "backup_files": "backing up your files", "window_control": "moving windows", "brightness": "changing the brightness",
    "list_scenes": "listing scenes", "save_scene": "saving a scene", "run_scene": "starting a scene",
    "why_is_internet_slow": "testing why the internet is slow", "health_trend": "reading your PC's history",
    "scan_network": "looking at your network", "list_devices": "listing your devices", "phone_status": "looking at your phone",
    "phone_notifications": "reading your phone's notifications", "phone_screenshot": "taking a picture of your phone",
    "allow_pc_link": "opening the PC link", "link_pc": "linking to the other PC", "pc_status": "checking the other PC",
    "list_linked_pcs": "listing linked PCs",
}

# the buttons shown under /menu in Telegram: all of them only LOOK, none can change anything
PHONE_ACTIONS = {"status": ("get_system_status", {}), "top": ("list_top_processes", {"sort_by": "memory", "limit": 8}),
                 "internet": ("diagnose_internet", {"speed": False}), "security": ("security_check", {}),
                 "files": ("find_large_files", {"limit": 8}), "jobs": ("list_scheduled_tasks", {})}
PHONE_BUTTONS = [("📊 Status", "status"), ("🔥 Top programs", "top"), ("🌐 Internet", "internet"),
                 ("🛡 Security", "security"), ("📁 Biggest files", "files"), ("⏰ Reminders", "jobs"),
                 ("🎵 Now playing", "playing"), ("🔒 Lock PC", "ask_lock"), ("💤 Sleep PC", "ask_sleep")]
PHONE_POWER = {"lock": ("Lock", "🔒 Your PC is locked."), "sleep": ("Sleep", "💤 Your PC is going to sleep.")}

AUTONOMY_TEXT = {
    "ask": "Asks before things it starts on its own. What you tell it to do just happens (see Control below).",
    "auto_safe": "Does low-risk jobs on its own too. Always asks before closing programs itself.",
    "report_only": "Looks only. Never changes anything.",
}


def F(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


from .render import format_result  # noqa: E402,F401

def load_image(name: str, size: int) -> ctk.CTkImage | None:
    try:
        img = Image.open(ASSETS / name)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    except OSError:
        return None


def level_colour(pct: float, low_is_bad: bool = False) -> str:
    """Green / amber / red for the gauge cards."""
    if low_is_bad:                     # battery: a low number is the problem
        return GREEN if pct > 40 else AMBER if pct > 20 else RED
    return GREEN if pct < 70 else AMBER if pct < 90 else RED


class RoundButton(ctk.CTkFrame):
    """A true circle button (CTkButton adds its content width to the button, which makes ovals)."""

    def __init__(self, master, size: int, command, text: str = "", image=None, font=None,
                 fg: str = ACCENT, hover: str = ACCENT_HOVER):
        super().__init__(master, width=size, height=size, corner_radius=size // 2, fg_color=fg)
        self.command, self.fg, self.hover, self.enabled = command, fg, hover, True
        self.label = ctk.CTkLabel(self, text=text, image=image, font=font, text_color="white", fg_color="transparent")
        self.label.place(relx=0.5, rely=0.5, anchor="center")
        for w in (self, self.label):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", lambda _e: self._paint(self.hover))
            w.bind("<Leave>", lambda _e: self._paint(self.fg))
            w.configure(cursor="hand2")

    def _paint(self, colour: str) -> None:
        if self.enabled:
            self.configure(fg_color=colour)

    def _click(self, _event) -> None:
        if self.enabled:
            self.command()

    def set_state(self, fg: str, hover: str | None = None, enabled: bool = True) -> None:
        self.fg, self.hover, self.enabled = fg, hover or fg, enabled
        self.configure(fg_color=fg)


class InputBar(ctk.CTkFrame):
    """The rounded 'Type your request here...' bar with the round send button."""

    def __init__(self, master, on_send):
        super().__init__(master, corner_radius=32, fg_color=CARD, border_width=1, border_color=GLOW)
        self.on_send = on_send
        self.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(self, placeholder_text="Type your request here...", height=44,
                                  border_width=0, fg_color="transparent", text_color=TEXT, font=F(15))
        self.entry.grid(row=0, column=0, sticky="ew", padx=(22, 8), pady=10)
        self.entry.bind("<Return>", lambda _e: self._go())
        RoundButton(self, 46, self._go, text="➤", font=F(18)).grid(row=0, column=1, padx=(0, 9), pady=9)

    def _go(self) -> None:
        text = self.entry.get().strip()
        if text and self.on_send(text):
            self.entry.delete(0, "end")


class App(ctk.CTk):
    def __init__(self, start_hidden: bool = False, instance=None, tray_lib=None):
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=BG)
        self.title("Laptop AI")
        self.geometry("1100x700")
        self.minsize(900, 580)
        self.after(250, self._set_icon)

        self.settings = config.Settings.load()
        self.ui_queue: "queue.Queue" = queue.Queue()
        self.executor = tools.Executor(self.settings, self.confirm_from_worker)
        self.brain = Brain(self.settings, self.executor)
        self.voice = Voice()
        self.voice.name, self.voice.rate = self.settings.voice_name, self.settings.voice_rate
        self._in_follow = False
        self.busy = False
        self._claim_lock = threading.Lock()
        self.telegram = notify.Telegram(self.settings)
        self.phone: Optional[notify.PhoneQuestions] = None
        self.wake: Optional[wakeword.WakeListener] = None
        # the icon by the clock: lets the app keep working with its window closed
        self.instance = instance
        self.tray = tray.Tray(lambda: self.post(self.show_window), lambda: self.post(self.quit_app), tray_lib)
        self.tray_on = self.tray.start() if (tray_lib is not None or tray.available()) else False
        self._told_about_tray = False
        self._pending_update = None
        self._update_cancel = False
        if instance is not None:
            instance.listen(lambda: self.post(self.show_window))   # a second launch just shows this window
        if start_hidden and self.tray_on and self.settings.setup_done:
            self.after(100, self.withdraw)
        self.page = "home"
        self.unread = False
        self.wrap = 560
        self._labels: list[ctk.CTkLabel] = []
        self._thinking = None
        self._save_job = None
        self.speak_var = ctk.BooleanVar(value=self.settings.speak_replies and self.voice.can_speak)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.pages: dict[str, ctk.CTkFrame] = {}
        for key, builder in (("home", self._build_home), ("chat", self._build_chat),
                             ("voice", self._build_voice), ("devices", self._build_devices), ("tools", self._build_tools),
                             ("settings", self._build_settings), ("help", self._build_help)):
            frame = ctk.CTkFrame(self.content, fg_color="transparent")
            frame.grid(row=0, column=0, sticky="nsew")
            frame.grid_columnconfigure(0, weight=1)
            self.pages[key] = frame
            builder(frame)
        ctk.CTkButton(self.content, text="⚙", width=40, height=40, corner_radius=20, font=F(18),
                      fg_color="transparent", hover_color=CARD2, text_color=MUTED,
                      command=lambda: self.show("settings")).place(relx=1.0, x=-18, y=14, anchor="ne")

        self.add_message("Hi! I can check your laptop, close programs (with your OK), clean temp files "
                         "and set reminders. Try: \"What's using my memory?\"", "bot")
        self.show("home")
        self._welcome = None
        if not self.settings.setup_done:
            self._show_welcome()
        self.bind("<Configure>", self._on_resize)
        self.after(100, self._drain_queue)
        self.after(300, self._check_brain)

        self.monitor = Monitor(self.settings, lambda m: self.post(lambda: self.alert(m)),
                               lambda st: self.post(lambda: self._update_stats(st, record=False)))
        self.hogs = hogs.HogWatcher(self.settings, self.confirm_from_worker, self._close_program,
                                    note=lambda m: self.post(lambda: self.add_message("🧹 " + m, "note")), busy_now=focus.active)
        self.monitor.start()
        self.watchdog = health.Watchdog(self.settings, lambda m: self.post(lambda: self.alert(m)))
        self.watchdog.on_result = lambda fs: self.post(lambda: self._holo_findings(fs))
        threading.Thread(target=lambda: self.post(lambda fs=health.scan(quick=True): self._holo_findings(fs)),
                         daemon=True).start()
        self.monitor.on_stats = lambda st: (self.post(lambda: self._update_stats(st, record=False)), self.watchdog.feed(st),
                                            self.hogs.feed(st), trends.record(st))
        self.watchdog.start()
        self.scheduler = tools.get_scheduler()
        self.scheduler.start(lambda task: self.post(lambda: self.run_scheduled(task)))
        if self.settings.startup_check:
            self.after(9000, self._startup_check)
        if self.settings.update_check_auto and self.settings.update_manifest_url:
            self.after(12000, self._auto_check_updates)
        tools_v4.server().on_file = lambda name, fname: self.post(lambda: self.add_message(f"📎 {name} sent you a file: {fname}", "note"))
        if self.settings.allow_pc_link:                      # remembered from last time (needs a fresh code to pair again)
            threading.Thread(target=tools_v4.server().start, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._close)

        if self.settings.telegram_questions and self.telegram.configured:
            self._phone_on()
        self.after(1000, self._wake_poll)
        if self.settings.wake_word_enabled:                  # opt-in, remembered from last time
            self.after(1500, self._autostart_wake)

    def _set_icon(self) -> None:
        try:
            self.iconbitmap(str(ASSETS / "app.ico"))        # .ico only works on Windows
        except Exception:
            pass

    # ======================= sidebar =======================
    def _build_sidebar(self) -> None:
        side = ctk.CTkFrame(self, width=250, corner_radius=0, fg_color=SIDEBAR)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)
        side.grid_rowconfigure(8, weight=1)

        logo = load_image("logo_small.png", 84)
        ctk.CTkLabel(side, text="" if logo else "AI", image=logo, font=F(30, "bold")
                     ).grid(row=0, column=0, pady=(34, 30), padx=20)

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for i, (key, icon, label) in enumerate(NAV, start=1):
            b = ctk.CTkButton(side, text=f"  {label}", image=load_image(f"icon_{icon}.png", 24),
                              compound="left", anchor="w", height=48, corner_radius=14,
                              font=F(15), fg_color="transparent", hover_color=CARD2, text_color=TEXT,
                              command=lambda k=key: self.show(k))
            b.grid(row=i, column=0, sticky="ew", padx=14, pady=4)
            self.nav_buttons[key] = b

        card = ctk.CTkFrame(side, corner_radius=16, fg_color=CARD, border_width=1, border_color=LINE)
        card.grid(row=9, column=0, sticky="ew", padx=14, pady=18)
        self.dot = ctk.CTkLabel(card, text="●", text_color=AMBER, font=F(18), width=24)
        self.dot.grid(row=0, column=0, rowspan=2, padx=(14, 4), pady=12)
        self.status_title = ctk.CTkLabel(card, text="Checking...", font=F(15, "bold"), text_color=AMBER, anchor="w")
        self.status_title.grid(row=0, column=1, sticky="w", pady=(12, 0))
        self.status_detail = ctk.CTkLabel(card, text="", font=F(12), text_color=MUTED, anchor="w")
        self.status_detail.grid(row=1, column=1, sticky="w", pady=(0, 12))
        # always visible while the "Trix" microphone is on
        self.mic_note = ctk.CTkLabel(card, text="●  Microphone is live: say \"Trix\"", font=F(12, "bold"),
                                     text_color=RED, anchor="w", justify="left", wraplength=200)
        self.mic_note.grid(row=2, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 12))
        self.mic_note.grid_remove()

    def _set_mic_note(self, live: bool) -> None:
        self.tray.set_mic(live) if hasattr(self, "tray") else None
        if live:
            self.mic_note.grid()
        else:
            self.mic_note.grid_remove()

    def show(self, name: str) -> None:
        self.page = name
        self.pages[name].tkraise()
        for key, b in self.nav_buttons.items():
            b.configure(fg_color=ACCENT if key == name else "transparent")
        if name == "chat":
            self.unread = False
            self.nav_buttons["chat"].configure(text="  Chat")
            self._scroll_end()

    # ======================= Home =======================
    def _build_home(self, page) -> None:
        page.grid_rowconfigure(1, weight=1)
        page.grid_columnconfigure(0, weight=1)
        PANEL = "#09101f"

        # ---- header: greeting, health pill, scan button
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=26, pady=(12, 6))
        head.grid_columnconfigure(0, weight=1)
        left = ctk.CTkFrame(head, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        self.greeting = ctk.CTkLabel(left, text=self._greeting(), font=F(28, "bold"), text_color=TEXT, anchor="w")
        self.greeting.pack(anchor="w")
        self.subline = ctk.CTkLabel(left, text=datetime.now().strftime("%A, %d %B") + "   ·   Your laptop at a glance",
                                    font=F(13), text_color=MUTED, anchor="w")
        self.subline.pack(anchor="w")
        self.tip_label = ctk.CTkLabel(left, text="", font=F(12), text_color=ACCENT, anchor="w")
        self.tip_label.pack(anchor="w", pady=(2, 0))
        self._tip_rotation = tips.rotation(self.settings.first_run_at)
        self._tip_i = 0
        self._tip_tick()
        right = ctk.CTkFrame(head, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        self.health_chip = ctk.CTkLabel(right, text="  Checking your PC...", font=F(13, "bold"), corner_radius=18,
                                        fg_color=CARD, text_color=MUTED, height=38, width=200)
        self.health_chip.pack(side="left", padx=(0, 10))
        self.scan_button = ctk.CTkButton(right, text="Scan my PC", height=38, width=130, corner_radius=19, font=F(13, "bold"),
                                         fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white", command=self.scan_now)
        self.scan_button.pack(side="left")
        ctk.CTkButton(right, text="History", height=38, width=96, corner_radius=19, font=F(13, "bold"), fg_color=CARD2,
                      hover_color=LINE, text_color=TEXT, command=self.open_history).pack(side="left", padx=(8, 0))

        # ---- body: the system map and the selected-part card
        body = ctk.CTkFrame(page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=26, pady=(0, 8))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0, minsize=262)
        body.grid_rowconfigure(0, weight=1)
        panel = ctk.CTkFrame(body, corner_radius=20, fg_color=PANEL, border_width=1, border_color=LINE)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(panel, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 0))
        ctk.CTkLabel(bar, text="SYSTEM MAP", font=F(12, "bold"), text_color=MUTED).pack(side="left")
        for colour, word in ((RED, "Serious"), (AMBER, "Warning"), (GREEN, "Healthy")):
            ctk.CTkLabel(bar, text=f"● {word}", font=F(12), text_color=colour).pack(side="right", padx=(10, 0))
        self.holo = holo.HoloLaptop(panel, on_select=self._holo_select, width=100, height=100,
                                    active=lambda: self.page == "home" and not self.hidden())
        self.holo.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        # now-playing strip: song, previous / play-pause / next, volume
        np = ctk.CTkFrame(panel, corner_radius=14, fg_color=CARD, border_width=1, border_color=LINE, height=44)
        np.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
        np.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(np, text="♪", font=F(18, "bold"), text_color=ACCENT, width=34).grid(row=0, column=0, padx=(10, 0), pady=6)
        self.np_label = ctk.CTkLabel(np, text="Nothing playing. Say \"play some music\".", font=F(13), text_color=MUTED, anchor="w")
        self.np_label.grid(row=0, column=1, sticky="ew", padx=8)
        ctl = ctk.CTkFrame(np, fg_color="transparent")
        ctl.grid(row=0, column=2, padx=(0, 8))
        for glyph, act in (("⏮", "previous"), ("⏯", "play_pause"), ("⏭", "next")):
            ctk.CTkButton(ctl, text=glyph, width=34, height=28, corner_radius=14, font=F(14), fg_color=CARD2, hover_color=LINE,
                          text_color=TEXT, command=lambda a=act: self._media_click(a)).pack(side="left", padx=2)
        for glyph, act in (("−", "down"), ("+", "up")):
            ctk.CTkButton(ctl, text=glyph, width=28, height=28, corner_radius=14, font=F(15, "bold"), fg_color=CARD2, hover_color=LINE,
                          text_color=TEXT, command=lambda a=act: self._volume_click(a)).pack(side="left", padx=2)
        self._np_tick = 0

        side = ctk.CTkFrame(body, width=262, fg_color="transparent")
        side.grid(row=0, column=1, sticky="ns", padx=(14, 0))
        side.grid_propagate(False)
        card = ctk.CTkFrame(side, corner_radius=20, fg_color=CARD, border_width=1, border_color=LINE)
        card.pack(fill="both", expand=True)
        ctk.CTkLabel(card, text="SELECTED PART", font=F(11, "bold"), text_color=MUTED, anchor="w").pack(fill="x", padx=18, pady=(16, 0))
        self.part_title = ctk.CTkLabel(card, text="Pick a part", font=F(22, "bold"), text_color=TEXT, anchor="w")
        self.part_title.pack(fill="x", padx=18, pady=(2, 0))
        self.part_state = ctk.CTkLabel(card, text="", font=F(13, "bold"), text_color=MUTED, anchor="w")
        self.part_state.pack(fill="x", padx=18)
        self.part_text = ctk.CTkLabel(card, text="Click the CPU, GPU, memory, storage, battery or any other part of the "
                                                 "laptop to see how it is doing. Parts with a problem glow amber or red.",
                                      font=F(13), text_color=MUTED, anchor="nw", justify="left", wraplength=224)
        self.part_text.pack(fill="both", expand=True, padx=18, pady=(8, 0))
        self.part_ask = ctk.CTkButton(card, text="Ask about this", height=38, corner_radius=19, font=F(13, "bold"),
                                      fg_color=CARD2, hover_color=LINE, border_width=1, border_color=GLOW, text_color=TEXT,
                                      state="disabled")
        self.part_ask.pack(fill="x", padx=18, pady=(8, 16))
        self.part_fix = ctk.CTkButton(card, text="", height=38, corner_radius=19, font=F(13, "bold"), fg_color=ACCENT,
                                      hover_color=ACCENT_HOVER, text_color="white")

        # ---- live metrics: radial gauges with a sparkline each
        strip = ctk.CTkFrame(page, fg_color="transparent")
        strip.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 8))
        self.tiles: dict[str, dash.MetricTile] = {}
        for i, (key, label) in enumerate((("cpu", "CPU"), ("ram", "Memory"), ("disk", "Disk"), ("battery", "Battery"))):
            strip.grid_columnconfigure(i, weight=1, uniform="tiles")
            tile = dash.MetricTile(strip, label, bg=BG)
            tile.grid(row=0, column=i, sticky="ew", padx=4)
            self.tiles[key] = tile
        self.series = {k: deque(maxlen=60) for k in ("cpu", "ram", "disk", "battery")}

        # ---- ask bar with quick prompts
        low = ctk.CTkFrame(page, fg_color="transparent")
        low.grid(row=3, column=0, sticky="ew", padx=40, pady=(0, 16))
        low.grid_columnconfigure(0, weight=1)
        chips = ctk.CTkFrame(low, fg_color="transparent")
        chips.grid(row=0, column=0, sticky="w", pady=(0, 8))
        for text in ("What's using my memory?", "Is my PC healthy?", "Remind me in 10 minutes to stretch", "Play music from my laptop"):
            ctk.CTkButton(chips, text=text, height=30, corner_radius=15, font=F(12), fg_color=CARD, border_width=1,
                          border_color=LINE, hover_color=CARD2, text_color=TEXT, width=10,
                          command=lambda t=text: self.send(t)).pack(side="left", padx=(0, 8))
        self.home_bar = InputBar(low, self.send)
        self.home_bar.grid(row=1, column=0, sticky="ew")
        self.holo_part = None
        self.last_stats: dict = {}
        self.findings: list = []
        self.scanned = False
        self.holo.start()
        self.after(1500, self._ui_sample)

    def _build_devices(self, page) -> None:
        colours = dict(card=CARD, card2=CARD2, line=LINE, accent=ACCENT, accent_hover=ACCENT_HOVER, text=TEXT, muted=MUTED)
        page.grid_rowconfigure(0, weight=1)
        self.devices_page = devicesview.DevicesPage(page, self.post, F, colours,
                                                    lambda: os.environ.get("COMPUTERNAME") or "PC", lambda: self.settings)
        self.devices_page.grid(row=0, column=0, sticky="nsew")

    def _show_welcome(self) -> None:
        """First run on this PC: ask what to call the person before anything else (no account, no email)."""
        colours = dict(bg=BG, card=CARD, card2=CARD2, line=LINE, accent=ACCENT, accent_hover=ACCENT_HOVER, text=TEXT, muted=MUTED)
        self._welcome = welcome.Welcome(self, self._welcome_done, colours, F, existing_name=self.settings.user_name)
        self._welcome.place(x=0, y=0, relwidth=1, relheight=1)
        self._welcome.lift()
        self.after(250, self._welcome.focus_name)

    def _welcome_done(self, name: str) -> None:
        self.settings.user_name = welcome.clean_name(name)
        self.settings.setup_done = True
        self.settings.save()
        if self._welcome is not None:
            self._welcome.destroy()
            self._welcome = None
        self.greeting.configure(text=welcome.greeting_for(self.settings.user_name, datetime.now().hour))
        if self.settings.user_name:
            self.add_message(f"Nice to meet you, {self.settings.user_name}. Ask me anything about your PC.", "bot")

    def _set_name(self) -> None:
        name = welcome.clean_name(self.name_entry.get())
        if name != self.settings.user_name:
            self.settings.user_name = name
            self.settings.save()
        self.greeting.configure(text=welcome.greeting_for(name, datetime.now().hour))

    @staticmethod
    def _greeting() -> str:
        hour = datetime.now().hour
        word = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        return word

    def _ui_sample(self) -> None:
        """A light reading every 2 seconds keeps the gauges and the laptop model lively (alerts use the slower monitor)."""
        try:
            self._update_stats(read_stats())
        except Exception:
            pass
        self.greeting.configure(text=welcome.greeting_for(self.settings.user_name, datetime.now().hour))
        self._np_tick += 1
        if self._np_tick % 2 == 0 and self.page == "home" and not self.hidden():
            self._poll_now_playing()
        self.after(2000, self._ui_sample)

    def _tip_tick(self) -> None:
        if not self._tip_rotation:
            return
        self.tip_label.configure(text=self._tip_rotation[self._tip_i % len(self._tip_rotation)])
        self._tip_i += 1
        self.after(12000, self._tip_tick)

    # ---- the laptop model: state, selection, scanning
    def _refresh_holo(self) -> None:
        statuses = holo_model.compute(self.last_stats, self.findings, self.settings)
        self.holo.set_status(statuses, self.last_stats)
        level, text = holo_model.summary(statuses)
        if not self.scanned and level == holo_model.OK:
            text = "All good so far. Full scan soon."
        colour = {holo_model.OK: GREEN, holo_model.WARN: AMBER, holo_model.CRIT: RED}[level]
        self.health_chip.configure(text="  ● " + text, text_color=colour)
        self._statuses = statuses
        if self.holo_part:
            self._holo_select(self.holo_part)

    def _holo_select(self, part: str) -> None:
        self.holo_part = part
        info = holo_model.BY_ID[part]
        st = getattr(self, "_statuses", {}).get(part) or holo_model.Status()
        colour = {holo_model.OK: GREEN, holo_model.WARN: AMBER, holo_model.CRIT: RED}[st.level]
        label = {holo_model.OK: "Normal", holo_model.WARN: "Warning", holo_model.CRIT: "Serious problem"}[st.level]
        self.part_title.configure(text=info.label + (f"   {st.value}" if st.value else ""))
        self.part_state.configure(text="● " + label, text_color=colour)
        body = "\n\n".join(st.details) if st.details else info.blurb
        if st.advice:
            body += "\n\n" + st.advice
        self.part_text.configure(text=body)
        self.part_ask.configure(state="normal", command=lambda: self.send(info.ask))
        fix = (holo_model.fixes(part, st) or [None])[0]
        if fix:
            self.part_fix.configure(text=fix.label, command=lambda f=fix: self._run_fix(f))
            self.part_fix.pack(fill="x", padx=18, pady=(8, 0), before=self.part_ask)
        else:
            self.part_fix.pack_forget()

    def _run_fix(self, fix) -> None:
        if fix.kind == "hog":
            threading.Thread(target=lambda: self.hogs.offer(fix.arg, self.last_stats), daemon=True).start()
        else:
            self.send(fix.arg)

    def _media_click(self, action: str) -> None:
        threading.Thread(target=lambda: (tools_v2.media_control(action), self.post(self._poll_now_playing)), daemon=True).start()

    def _volume_click(self, action: str) -> None:
        threading.Thread(target=lambda: tools_v2.volume.press(action, 3), daemon=True).start()

    def _poll_now_playing(self) -> None:
        def work():
            info = nowplaying.current()

            def show():
                if not info or info["state"] == "stopped":
                    self.np_label.configure(text="Nothing playing. Say \"play some music\".", text_color=MUTED)
                else:
                    name = info["title"] or "Playing"
                    mark = "▶  " if info["state"] == "playing" else "⏸  "
                    self.np_label.configure(text=mark + name + (f"  ·  {info['artist']}" if info["artist"] and info["artist"] not in name else ""),
                                            text_color=TEXT)
            self.post(show)
        threading.Thread(target=work, daemon=True).start()

    def _holo_ask(self) -> None:
        if self.holo_part:
            self.send(holo_model.BY_ID[self.holo_part].ask)

    def _holo_findings(self, findings: list) -> None:
        self.findings, self.scanned = list(findings), True
        self._refresh_holo()

    def open_history(self) -> None:
        win = getattr(self, "_history_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return
        self._history_win = trendview.TrendWindow(self, F)

    def scan_now(self) -> None:
        if getattr(self, "_scanning", False):
            return
        self._scanning = True
        self.holo.set_scanning(True)
        self.scan_button.configure(text="Scanning...", state="disabled")

        def work():
            try:
                self.watchdog.tick()
            finally:
                self.post(self._scan_done)
        threading.Thread(target=work, daemon=True).start()

    def _scan_done(self) -> None:
        self._scanning = False
        self.holo.set_scanning(False)
        self.scan_button.configure(text="Scan my PC", state="normal")

    # ======================= Chat =======================
    def _build_chat(self, page) -> None:
        page.grid_rowconfigure(1, weight=1)
        self._header(page, "Chat", "Ask about your laptop, or tell me what to do.")
        self.chat = ctk.CTkScrollableFrame(page, fg_color="transparent")
        self.chat.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 8))
        self.chat_bar = InputBar(page, self.send)
        self.chat_bar.grid(row=2, column=0, sticky="ew", padx=40, pady=(4, 30))
        ctk.CTkButton(page, text="New chat", width=96, height=34, corner_radius=17, font=F(13), fg_color=CARD,
                      border_width=1, border_color=LINE, hover_color=CARD2, text_color=TEXT,
                      command=self.new_chat).place(relx=1.0, x=-72, y=34, anchor="ne")

    def new_chat(self) -> None:
        """Clear the screen and make the AI forget the conversation so far."""
        if self.busy:
            return
        for child in self.chat.winfo_children():
            child.destroy()
        self._labels.clear()
        self.brain.history.clear()
        self.add_message("New chat started. What would you like to do?", "bot")

    def add_message(self, text: str, kind: str = "bot") -> ctk.CTkFrame:
        row = ctk.CTkFrame(self.chat, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=5)
        if kind == "note":
            lbl = ctk.CTkLabel(row, text=text, text_color=MUTED, font=F(12), anchor="w",
                               justify="left", wraplength=self.wrap)
            lbl.pack(side="left", padx=6)
        else:
            bg, fg = {"you": (ACCENT, "white"), "bot": (CARD2, TEXT), "alert": ("#3a1520", "#ffb4b4")}[kind]
            bubble = ctk.CTkFrame(row, corner_radius=16, fg_color=bg)
            bubble.pack(side="right" if kind == "you" else "left")
            lbl = ctk.CTkLabel(bubble, text=text, text_color=fg, font=F(14), anchor="w",
                               justify="left", wraplength=self.wrap)
            lbl.pack(padx=14, pady=10)
        self._labels.append(lbl)
        self._scroll_end()
        return row

    def say(self, text: str, kind: str = "bot") -> None:
        self.add_message(text, kind)
        if self.page != "chat" and kind in ("bot", "alert"):
            self.unread = True
            self.nav_buttons["chat"].configure(text="  Chat   ●  new")

    def _scroll_end(self) -> None:
        def go():
            try:
                self.chat._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass
        self.after(60, go)

    def _on_resize(self, event) -> None:
        if event.widget is not self:
            return
        wrap = max(320, int((self.winfo_width() - 260) * 0.66))
        if abs(wrap - self.wrap) < 20:
            return
        self.wrap = wrap
        alive = []
        for lbl in self._labels:
            try:
                lbl.configure(wraplength=wrap)
                alive.append(lbl)
            except Exception:
                pass                                   # label was deleted
        self._labels = alive

    # ======================= Voice =======================
    def _build_voice(self, page) -> None:
        page.grid_rowconfigure(1, weight=1)
        page.grid_columnconfigure(0, weight=1)
        self._header(page, "Voice Assistant", "Talk to your laptop. Say \"Trix\" any time, or tap the orb.")
        panel = ctk.CTkFrame(page, corner_radius=22, fg_color="#09101f", border_width=1, border_color=LINE)
        panel.grid(row=1, column=0, sticky="nsew", padx=34, pady=(0, 22))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(0, weight=1)
        center = ctk.CTkFrame(panel, fg_color="transparent")
        center.grid(row=0, column=0)
        self.orb = orb.VoiceOrb(center, command=self.listen, size=290)
        self.orb.pack(pady=(14, 0))
        self.orb.set_enabled(self.voice.can_listen)
        self.orb.start()
        self.voice_status = ctk.CTkLabel(center, font=F(22, "bold"), text_color=TEXT, text="")
        self.voice_status.pack()
        self.voice_hint = ctk.CTkLabel(center, font=F(13), text_color=MUTED, text="", wraplength=520)
        self.voice_hint.pack(pady=(2, 8))
        self.wake_pill = ctk.CTkLabel(center, font=F(12, "bold"), corner_radius=14, height=28, width=210, fg_color=CARD, text_color=MUTED,
                                      text="")
        self.wake_pill.pack(pady=(0, 10))
        card = ctk.CTkFrame(center, corner_radius=16, fg_color=CARD, border_width=1, border_color=LINE)
        card.pack(fill="x", padx=30, pady=(0, 10))
        ctk.CTkLabel(card, text="LAST EXCHANGE", font=F(11, "bold"), text_color=MUTED, anchor="w").pack(fill="x", padx=16, pady=(10, 0))
        self.voice_you = ctk.CTkLabel(card, text="You: nothing yet", font=F(13), text_color=TEXT, anchor="w", justify="left", wraplength=500)
        self.voice_you.pack(fill="x", padx=16, pady=(2, 0))
        self.voice_reply = ctk.CTkLabel(card, text="", font=F(13), text_color=MUTED, anchor="w", justify="left", wraplength=500)
        self.voice_reply.pack(fill="x", padx=16, pady=(2, 10))
        row = ctk.CTkFrame(center, fg_color="transparent")
        row.pack(pady=(0, 6))
        ctk.CTkLabel(row, text="Try saying", font=F(12), text_color=MUTED).pack(side="left", padx=(0, 8))
        for text in ("Is my PC healthy?", "Pause in 30 seconds", "What's using my memory?"):
            ctk.CTkButton(row, text=text, height=28, corner_radius=14, font=F(12), fg_color=CARD, border_width=1, border_color=LINE,
                          hover_color=CARD2, text_color=TEXT, width=10, command=lambda t=text: self._voice_try(t)).pack(side="left", padx=3)
        ctk.CTkSwitch(center, text="Speak replies out loud", variable=self.speak_var, font=F(13),
                      progress_color=ACCENT, command=self._apply_voice_setting,
                      state="normal" if self.voice.can_speak else "disabled").pack(pady=(6, 0))
        note = ("Privacy: with the \"google\" setting, the clip you speak is sent to Google for recognition "
                "(choose \"offline\" in Settings to keep it on this PC). Spoken replies stay on your laptop.")
        if not self.voice.can_listen:
            note = "To enable the microphone:  pip install SpeechRecognition sounddevice"
        ctk.CTkLabel(center, text=note, font=F(11), text_color=MUTED, wraplength=520).pack(pady=(8, 14))
        self._voice_state("idle")
        self._refresh_wake_pill()

    def _refresh_wake_pill(self) -> None:
        on = bool(self.wake is not None and self.settings.wake_word_enabled)
        self.wake_pill.configure(text="●  \"Trix\" wake word is ON" if on else "○  \"Trix\" wake word is off",
                                 text_color=GREEN if on else MUTED)

    def _voice_state(self, state: str, title: str = "", hint: str = "") -> None:
        """One place that sets the orb and its words: idle | listening | thinking | speaking."""
        if not self.voice.can_listen and state == "idle":
            title, hint = "Microphone support is not installed", "You can still type commands on the Chat page."
        titles = {"idle": "Tap the orb and speak", "listening": "Listening...", "thinking": "Thinking...", "speaking": "Speaking..."}
        hints = {"idle": "Or just say \"Trix\" if the wake word is on.", "listening": "Speak now. I'll stop when you pause.",
                 "thinking": "Working on it.", "speaking": "Reading the answer out loud."}
        self.orb.set_state(state)
        self.voice_status.configure(text=title or titles[state])
        self.voice_hint.configure(text=hint or hints[state])

    def _voice_try(self, text: str) -> None:
        if self.busy:
            return
        self.voice_you.configure(text=f"You: {text}")
        self.send(text, stay=True)

    def listen(self) -> None:
        if self.busy or not self.voice.can_listen:
            return
        self._voice_state("listening")

        def work():
            text, error = self.voice.listen(mode=self.settings.recognition)

            def done():
                if text:
                    self._voice_state("thinking")
                    self.voice_you.configure(text=f"You: {text}")
                    self.voice_reply.configure(text="")
                    self.send(text, stay=True)
                else:
                    self._voice_state("idle", hint=error or "")
            self.post(done)
        threading.Thread(target=work, daemon=True).start()

    # ======================= Files & Tools =======================
    def _build_tools(self, page) -> None:
        page.grid_rowconfigure(1, weight=1)
        self._header(page, "Files & Tools", "Everything the assistant is allowed to do. Nothing else.")
        body = ctk.CTkScrollableFrame(page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 20))

        ctk.CTkLabel(body, text="Quick actions", font=F(17, "bold"), text_color=TEXT, anchor="w").pack(fill="x", padx=6)
        grid = ctk.CTkFrame(body, fg_color="transparent")
        grid.pack(fill="x", pady=8)
        actions = [("System status", "get_system_status", {}),
                   ("Internet check", "diagnose_internet", {"speed": True}),
                   ("Security check", "security_check", {}),
                   ("Biggest files", "find_large_files", {"limit": 10}),
                   ("Startup programs", "list_startup_items", {}),
                   ("Network", "list_connections", {}),
                   ("Screenshot", "take_screenshot", {}),
                   ("Top programs", "list_top_processes", {"sort_by": "memory", "limit": 8}),
                   ("Measure temp space", "clean_temp_files", {"dry_run": True}),
                   ("Clean temp files", "clean_temp_files", {"dry_run": False}),
                   ("Scheduled jobs", "list_scheduled_tasks", {})]
        for i, (label, name, args) in enumerate(actions):
            ctk.CTkButton(grid, text=label, height=40, corner_radius=12, font=F(14), fg_color=CARD2,
                          hover_color=ACCENT, text_color=TEXT,
                          command=lambda n=name, a=args: self.run_tool_button(n, a)
                          ).grid(row=i // 3, column=i % 3, padx=5, pady=5, sticky="ew")
            grid.grid_columnconfigure(i % 3, weight=1)
        self.tool_out = ctk.CTkTextbox(body, height=170, fg_color=CARD, border_width=1, border_color=LINE,
                                       text_color=TEXT, font=ctk.CTkFont(family="Consolas", size=13))
        self.tool_out.pack(fill="x", padx=6, pady=(4, 20))
        self._set_tool_out("Press a button. Anything that changes your laptop asks for your OK first.")

        ctk.CTkLabel(body, text="Commands", font=F(17, "bold"), text_color=TEXT, anchor="w").pack(fill="x", padx=6, pady=(18, 4))
        ctk.CTkLabel(body, text="Type something in plain words, or a tool name with JSON arguments like "
                                "clean_temp_files {\"dry_run\": true}. Runs the same way as typing it in Chat: the "
                                "same approvals apply, and everything is written to your activity history.",
                     font=F(12), text_color=MUTED, anchor="w", justify="left", wraplength=620).pack(fill="x", padx=6)
        crow = ctk.CTkFrame(body, fg_color="transparent")
        crow.pack(fill="x", pady=(8, 4))
        self.console_entry = ctk.CTkEntry(crow, height=38, placeholder_text="e.g. list_top_processes {\"limit\": 5}, or \"what's using my memory?\"",
                                         fg_color=CARD2, border_color=LINE, font=ctk.CTkFont(family="Consolas", size=13))
        self.console_entry.pack(side="left", fill="x", expand=True, padx=(6, 8))
        self.console_entry.bind("<Return>", lambda _e: self.run_console())
        ctk.CTkButton(crow, text="Run", width=70, height=38, corner_radius=12, font=F(14, "bold"), fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, command=self.run_console).pack(side="left")
        self.console_log = ctk.CTkTextbox(body, height=170, fg_color=CARD, border_width=1, border_color=LINE,
                                          text_color=TEXT, font=ctk.CTkFont(family="Consolas", size=12))
        self.console_log.pack(fill="x", padx=6, pady=(4, 20))
        self.console_log.configure(state="disabled")

        ctk.CTkLabel(body, text="What the AI can use", font=F(17, "bold"), text_color=TEXT, anchor="w").pack(fill="x", padx=6)
        order = {"read": 0, "safe": 1, "low": 2, "high": 3}
        for t in sorted(tools.REGISTRY.values(), key=lambda t: (order[t.risk], t.name)):
            row = ctk.CTkFrame(body, corner_radius=12, fg_color=CARD, border_width=1, border_color=LINE)
            row.pack(fill="x", padx=6, pady=4)
            ctk.CTkLabel(row, text=t.risk.upper(), width=58, font=F(11, "bold"), text_color=RISK_COLORS[t.risk]
                         ).pack(side="left", padx=(12, 6), pady=10)
            ctk.CTkLabel(row, text=t.name, font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
                         text_color=TEXT, width=190, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=t.description, font=F(12), text_color=MUTED, anchor="w", justify="left",
                         wraplength=430).pack(side="left", padx=8, pady=8, fill="x", expand=True)

    def _set_tool_out(self, text: str) -> None:
        self.tool_out.configure(state="normal")
        self.tool_out.delete("1.0", "end")
        self.tool_out.insert("1.0", text)
        self.tool_out.configure(state="disabled")

    def _console_print(self, line: str) -> None:
        self.console_log.configure(state="normal")
        self.console_log.insert("end", line.rstrip() + "\n")
        self.console_log.see("end")
        self.console_log.configure(state="disabled")

    def run_console(self) -> None:
        text = self.console_entry.get().strip()
        if not text:
            return
        self.console_entry.delete(0, "end")
        self._console_print(f"> {text}")
        m = re.match(r"^([a-z_][a-z0-9_]*)\s*(\{.*\})?\s*$", text, re.I)
        name = m.group(1) if m else None

        def work():
            if name and name in tools.REGISTRY:
                try:
                    args = json.loads(m.group(2)) if m.group(2) else {}
                except ValueError:
                    self.post(lambda: self._console_print("Bad JSON arguments."))
                    return
                raw = self.executor.run(name, args, source="user")
                self.post(lambda: self._console_print(format_result(raw)))
            else:
                reply = self.brain.ask(text, on_tool=lambda n, a: self.post(lambda: self._console_print(f"[running {n}]")))
                self.post(lambda: self._console_print(reply))
        threading.Thread(target=work, daemon=True).start()

    def run_tool_button(self, name: str, args: dict) -> None:
        self._set_tool_out("Working...")

        def work():
            raw = self.executor.run(name, dict(args), source="user")
            self.post(lambda: self._set_tool_out(format_result(raw)))
        threading.Thread(target=work, daemon=True).start()

    # ======================= Settings =======================
    def _build_settings(self, page) -> None:
        page.grid_rowconfigure(1, weight=1)
        self._header(page, "Settings", "Changes save automatically.")
        body = ctk.CTkScrollableFrame(page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 20))

        def card(title):
            c = ctk.CTkFrame(body, corner_radius=16, fg_color=CARD, border_width=1, border_color=LINE)
            c.pack(fill="x", padx=6, pady=8)
            ctk.CTkLabel(c, text=title, font=F(16, "bold"), text_color=TEXT, anchor="w"
                         ).pack(fill="x", padx=18, pady=(14, 6))
            inner = ctk.CTkFrame(c, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=(0, 14))
            return inner

        seg = dict(fg_color=CARD2, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                   unselected_color=CARD2, unselected_hover_color=LINE, font=F(14))

        c = card("Your name")
        nrow = ctk.CTkFrame(c, fg_color="transparent")
        nrow.pack(fill="x")
        ctk.CTkLabel(nrow, text="Call me", width=120, anchor="w", text_color=TEXT, font=F(14)).pack(side="left")
        self.name_entry = ctk.CTkEntry(nrow, width=200, fg_color=CARD2, border_color=LINE, placeholder_text="Your first name")
        self.name_entry.insert(0, self.settings.user_name)
        self.name_entry.pack(side="left", padx=8)
        self.name_entry.bind("<FocusOut>", lambda _e: self._set_name())
        self.name_entry.bind("<Return>", lambda _e: self._set_name())

        c = card("AI brain")
        self.backend_seg = ctk.CTkSegmentedButton(c, values=list(config.BACKENDS), command=self._set_backend, **seg)
        self.backend_seg.set(self.settings.backend)
        self.backend_seg.pack(anchor="w")
        ctk.CTkLabel(c, text="auto = offline first (Ollama), online (Claude) if offline isn't available.",
                     font=F(12), text_color=MUTED).pack(anchor="w", pady=(6, 8))
        row = ctk.CTkFrame(c, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text="Ollama model", width=120, anchor="w", text_color=TEXT, font=F(14)).pack(side="left")
        self.model_entry = ctk.CTkEntry(row, width=200, fg_color=CARD2, border_color=LINE)
        self.model_entry.insert(0, self.settings.ollama_model)
        self.model_entry.pack(side="left", padx=8)
        self.model_entry.bind("<FocusOut>", lambda _e: self._set_model())
        self.model_entry.bind("<Return>", lambda _e: self._set_model())
        self.pull_btn = ctk.CTkButton(c, text="Download Llama 3.2 (3B)  ~2 GB", height=34, corner_radius=17, font=F(13, "bold"),
                                      fg_color=ACCENT, hover_color=ACCENT_HOVER, command=lambda: self._pull_model("llama3.2:3b"))
        self.pull_btn.pack(anchor="w", pady=(10, 0))
        self.pull_bar = ctk.CTkProgressBar(c, height=8, progress_color=ACCENT, fg_color=CARD2)
        self.pull_bar.set(0)
        self.pull_status = ctk.CTkLabel(c, text=self._model_state_text(), font=F(12), text_color=MUTED, anchor="w", justify="left", wraplength=560)
        self.pull_status.pack(anchor="w", pady=(6, 0))

        c = card("How much can it do on its own?")
        self.autonomy_seg = ctk.CTkSegmentedButton(c, values=list(config.AUTONOMY), command=self._set_autonomy, **seg)
        self.autonomy_seg.set(self.settings.autonomy)
        self.autonomy_seg.pack(anchor="w")
        self.autonomy_text = ctk.CTkLabel(c, text=AUTONOMY_TEXT[self.settings.autonomy], font=F(12), text_color=MUTED)
        self.autonomy_text.pack(anchor="w", pady=(6, 0))

        c = card("Control")
        self.obey_var = ctk.BooleanVar(value=self.settings.obey_commands)
        ctk.CTkSwitch(c, text="Do what I tell you without asking (typed, or said with \"Trix\")", variable=self.obey_var, font=F(14),
                      progress_color=ACCENT, command=lambda: self._set_flag("obey_commands", self.obey_var)).pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(c, text="It still asks before it acts on its own (closing a heavy program, scheduled jobs) and before raw "
                             "PowerShell. Phone messages can never change anything except Lock and Sleep.", font=F(12), text_color=MUTED,
                     justify="left", anchor="w", wraplength=560).pack(anchor="w", pady=(0, 10))
        self.power_var = ctk.BooleanVar(value=self.settings.power_without_asking)
        ctk.CTkSwitch(c, text="Shut down, restart, sleep and lock when I ask, without asking again", variable=self.power_var, font=F(14),
                      progress_color=ACCENT, command=lambda: self._set_flag("power_without_asking", self.power_var)).pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(c, text="Shut down and restart wait 15 seconds so you can say \"cancel the shutdown\".", font=F(12),
                     text_color=MUTED).pack(anchor="w", pady=(0, 8))
        self.hog_var = ctk.BooleanVar(value=self.settings.suggest_close_apps)
        ctk.CTkSwitch(c, text="Ask to close a program that uses too much memory or CPU", variable=self.hog_var, font=F(14),
                      progress_color=ACCENT, command=lambda: self._set_flag("suggest_close_apps", self.hog_var)).pack(anchor="w", pady=(0, 8))
        prow = ctk.CTkFrame(c, fg_color="transparent")
        prow.pack(fill="x")
        ctk.CTkLabel(prow, text="Favourite music app", width=150, anchor="w", text_color=TEXT, font=F(14)).pack(side="left")
        self.player_menu = ctk.CTkOptionMenu(prow, values=["Default player", "VLC"], width=160, fg_color=CARD2, button_color=ACCENT,
                                             command=self._set_player)
        self.player_menu.set("VLC" if self.settings.music_player.lower().startswith("vlc") else "Default player")
        self.player_menu.pack(side="left", padx=8)
        self.startup_var = ctk.BooleanVar(value=self.settings.startup_check)
        ctk.CTkSwitch(c, text="Run a quiet self-check when Laptop AI starts (only speaks up if something is broken)",
                      variable=self.startup_var, font=F(14), progress_color=ACCENT,
                      command=lambda: self._set_flag("startup_check", self.startup_var)).pack(anchor="w", pady=(12, 0))
        trow = ctk.CTkFrame(c, fg_color="transparent")
        trow.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(trow, text="Colour theme", width=150, anchor="w", text_color=TEXT, font=F(14)).pack(side="left")
        self.theme_menu = ctk.CTkOptionMenu(trow, values=list(theme.LABELS.values()), width=160, fg_color=CARD2, button_color=ACCENT,
                                            command=self._set_theme)
        self.theme_menu.set(theme.LABELS.get(self.settings.theme, "Electric blue"))
        self.theme_menu.pack(side="left", padx=8)
        self.theme_note = ctk.CTkLabel(c, text="", font=F(12), text_color=MUTED, anchor="w", justify="left", wraplength=560)
        self.theme_note.pack(anchor="w", pady=(4, 0))

        c = card("Voice")
        ctk.CTkSwitch(c, text="Speak replies out loud", variable=self.speak_var, font=F(14), progress_color=ACCENT,
                      command=self._apply_voice_setting,
                      state="normal" if self.voice.can_speak else "disabled").pack(anchor="w")
        self.rsound_var = ctk.BooleanVar(value=self.settings.reminder_sound)
        self.rspeak_var = ctk.BooleanVar(value=self.settings.speak_reminders)
        self.asound_var = ctk.BooleanVar(value=self.settings.alert_sound)
        for text, var in (("Play a sound when a reminder is due", self.rsound_var),
                          ("Read reminders out loud", self.rspeak_var), ("Beep for PC warnings", self.asound_var)):
            ctk.CTkSwitch(c, text=text, variable=var, font=F(14), progress_color=ACCENT,
                          command=self._apply_sound_settings).pack(anchor="w", pady=(8, 0))
        ctk.CTkButton(c, text="Test the sound", width=140, height=32, corner_radius=16, font=F(12), fg_color=CARD2,
                      hover_color=LINE, command=lambda: sound.chime("reminder", repeats=1)).pack(anchor="w", pady=(10, 0))
        self.follow_var = ctk.BooleanVar(value=self.settings.conversation_mode)
        ctk.CTkSwitch(c, text="Conversation mode: after Trix answers out loud, keep listening a few seconds for a follow-up",
                      variable=self.follow_var, font=F(14), progress_color=ACCENT,
                      command=lambda: self._set_flag("conversation_mode", self.follow_var)).pack(anchor="w", pady=(12, 0))
        vrow = ctk.CTkFrame(c, fg_color="transparent")
        vrow.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(vrow, text="Trix's voice", width=110, anchor="w", text_color=TEXT, font=F(14)).pack(side="left")
        names = self.voice.voices()
        self.voice_menu = ctk.CTkOptionMenu(vrow, values=["PC default"] + names, width=260, fg_color=CARD2, button_color=ACCENT,
                                            command=self._set_voice_name, state="normal" if names else "disabled")
        self.voice_menu.set(next((n for n in names if self.settings.voice_name and self.settings.voice_name.lower() in n.lower()), "PC default"))
        self.voice_menu.pack(side="left", padx=8)
        ctk.CTkButton(vrow, text="Hear it", width=80, height=32, corner_radius=16, font=F(12), fg_color=CARD2, hover_color=LINE,
                      command=lambda: self.voice.speak("Hi, I'm Trix. This is how I sound.")).pack(side="left")
        rrow = ctk.CTkFrame(c, fg_color="transparent")
        rrow.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(rrow, text="Speed", width=110, anchor="w", text_color=TEXT, font=F(14)).pack(side="left")
        self.rate_slider = ctk.CTkSlider(rrow, from_=120, to=260, number_of_steps=14, width=260, progress_color=ACCENT, button_color=ACCENT,
                                         command=self._set_voice_rate)
        self.rate_slider.set(self.settings.voice_rate)
        self.rate_slider.pack(side="left", padx=8)

        c = card("Phone (Telegram)")
        ctk.CTkLabel(c, text="1) In Telegram, message @BotFather, send /newbot, and copy the token it gives you.\n"
                             "2) Paste it below and press Save. 3) Send your new bot \"hi\", then press Link my chat.",
                     font=F(12), text_color=MUTED, justify="left", anchor="w").pack(anchor="w", pady=(0, 8))
        row = ctk.CTkFrame(c, fg_color="transparent")
        row.pack(fill="x")
        self.token_entry = ctk.CTkEntry(row, width=300, show="•", fg_color=CARD2, border_color=LINE,
                                        placeholder_text="Bot token (saved in Windows Credential Manager)")
        self.token_entry.pack(side="left")
        for text, cmd in (("Save", self._tg_save), ("Link my chat", self._tg_link), ("Send test", self._tg_test)):
            ctk.CTkButton(row, text=text, width=96, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                          command=cmd).pack(side="left", padx=(8, 0))
        self.tg_status = ctk.CTkLabel(c, text=self._tg_state_text(), font=F(12), text_color=MUTED,
                                      anchor="w", justify="left", wraplength=560)
        self.tg_status.pack(anchor="w", pady=(8, 6))
        self.tg_vars = {}
        for attr, label in (("telegram_reminders", "Send my reminders and scheduled reports to my phone"),
                            ("telegram_alerts", "Send CPU / memory / disk / battery warnings to my phone"),
                            ("telegram_questions", "Let me ask questions from my phone (it can look; only Lock and Sleep can change anything, with a Yes tap)")):
            var = ctk.BooleanVar(value=getattr(self.settings, attr))
            self.tg_vars[attr] = var
            ctk.CTkSwitch(c, text=label, variable=var, font=F(14), progress_color=ACCENT,
                          command=lambda a=attr: self._tg_toggle(a)).pack(anchor="w", pady=3)
        self.pact_var = ctk.BooleanVar(value=self.settings.phone_actions)
        ctk.CTkSwitch(c, text="Show Lock and Sleep buttons on my phone (each asks you to tap Yes)", variable=self.pact_var, font=F(14),
                      progress_color=ACCENT, command=lambda: self._set_flag("phone_actions", self.pact_var)).pack(anchor="w", pady=3)

        c = card("Hands-free (\"Trix\")")
        ctk.CTkLabel(c, text="Off by default. When on, the microphone listens all the time, but only on this PC: a small "
                             "offline model waits for the one word \"Trix\". Nothing is saved or sent until you say it. "
                             "Then ONE command is recorded. Anything beyond a simple lookup asks for your OK first.",
                     font=F(12), text_color=MUTED, justify="left", anchor="w", wraplength=560).pack(anchor="w", pady=(0, 8))
        self.wake_var = ctk.BooleanVar(value=self.settings.wake_word_enabled)
        ctk.CTkSwitch(c, text="Listen for \"Trix\"", variable=self.wake_var, font=F(14), progress_color=ACCENT,
                      command=self._wake_toggle).pack(anchor="w")
        self.wake_status = ctk.CTkLabel(c, text=self._wake_state_text(), font=F(12), text_color=MUTED,
                                        anchor="w", justify="left", wraplength=560)
        self.wake_status.pack(anchor="w", pady=(6, 6))
        self.model_btn = ctk.CTkButton(c, text="Download voice model (~40 MB)", fg_color=ACCENT,
                                       hover_color=ACCENT_HOVER, command=self._download_model)
        self.model_btn.pack(anchor="w", pady=(0, 8))
        if wakeword.model_installed():
            self.model_btn.configure(state="disabled", text="Voice model installed ✓")
        ctk.CTkLabel(c, text="After you speak, turn it into text with:", font=F(14), text_color=TEXT).pack(anchor="w")
        self.recog_seg = ctk.CTkSegmentedButton(c, values=["google", "offline"], command=self._set_recog, **seg)
        self.recog_seg.set(self.settings.recognition)
        self.recog_seg.pack(anchor="w", pady=(4, 0))
        self.recog_text = ctk.CTkLabel(c, text=RECOG_TEXT[self.settings.recognition], font=F(12), text_color=MUTED,
                                       anchor="w", justify="left", wraplength=560)
        self.recog_text.pack(anchor="w", pady=(6, 0))

        c = card("App")
        self.autostart_var = ctk.BooleanVar(value=autostart.is_enabled())
        ctk.CTkSwitch(c, text="Start Laptop AI when Windows starts (hidden by the clock)", variable=self.autostart_var,
                      font=F(14), progress_color=ACCENT, command=self._set_autostart,
                      state="normal" if autostart.winreg is not None else "disabled").pack(anchor="w", pady=(0, 6))
        self.tray_var = ctk.BooleanVar(value=self.settings.close_to_tray)
        ctk.CTkSwitch(c, text="Keep running by the clock when I close the window", variable=self.tray_var,
                      font=F(14), progress_color=ACCENT, command=self._set_tray,
                      state="normal" if self.tray_on else "disabled").pack(anchor="w")
        self.app_note = ctk.CTkLabel(c, text="" if self.tray_on else "The system-tray icon needs:  pip install pystray",
                                     font=F(12), text_color=MUTED, anchor="w")
        self.app_note.pack(anchor="w", pady=(6, 0))

        c = card(f"Updates (you're on {__version__})")
        urow = ctk.CTkFrame(c, fg_color="transparent")
        urow.pack(fill="x")
        ctk.CTkLabel(urow, text="Update link", width=120, anchor="w", text_color=TEXT, font=F(14)).pack(side="left")
        self.update_url_entry = ctk.CTkEntry(urow, width=300, fg_color=CARD2, border_color=LINE,
                                             placeholder_text="https://.../update.json")
        self.update_url_entry.insert(0, self.settings.update_manifest_url)
        self.update_url_entry.pack(side="left", padx=8)
        self.update_url_entry.bind("<FocusOut>", lambda _e: self._set_update_url())
        self.update_url_entry.bind("<Return>", lambda _e: self._set_update_url())
        self.update_auto_var = ctk.BooleanVar(value=self.settings.update_check_auto)
        ctk.CTkSwitch(c, text="Check automatically once a day", variable=self.update_auto_var, font=F(13),
                      progress_color=ACCENT, command=lambda: self._set_flag("update_check_auto", self.update_auto_var)
                      ).pack(anchor="w", pady=(8, 8))
        self.update_btn = ctk.CTkButton(c, text="Check for updates", height=32, corner_radius=16, font=F(13, "bold"),
                                        fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.check_for_updates)
        self.update_btn.pack(anchor="w")
        self.update_bar = ctk.CTkProgressBar(c, height=8, progress_color=ACCENT, fg_color=CARD2)
        self.update_bar.set(0)
        self.update_status = ctk.CTkLabel(c, text="", font=F(12), text_color=MUTED, anchor="w", justify="left", wraplength=560)
        self.update_status.pack(anchor="w", pady=(8, 0))

        c = card("Advanced")
        self.cmd_var = ctk.BooleanVar(value=self.settings.allow_commands)
        ctk.CTkSwitch(c, text="Let the AI propose PowerShell commands", variable=self.cmd_var, font=F(14),
                      progress_color=RED, command=self._set_commands).pack(anchor="w")
        ctk.CTkLabel(c, text="Off by default. When on, the AI can suggest a command for jobs no other tool covers. "
                             "You always see the exact command and must press Yes every time. It never runs from "
                             "your phone, and a command you didn't read is a command you shouldn't approve.",
                     font=F(12), text_color=MUTED, justify="left", anchor="w", wraplength=560
                     ).pack(anchor="w", pady=(6, 0))

        c = card("Alerts")
        self.monitor_var = ctk.BooleanVar(value=self.settings.monitor_enabled)
        ctk.CTkSwitch(c, text="Warn me when something gets too high", variable=self.monitor_var, font=F(14),
                      progress_color=ACCENT, command=self._set_monitor).pack(anchor="w", pady=(0, 6))
        self._slider_row(c, "CPU above", "cpu_alert_pct", 50, 100)
        self._slider_row(c, "Memory above", "ram_alert_pct", 50, 100)
        self._slider_row(c, "Disk fuller than", "disk_alert_pct", 70, 100)
        self._slider_row(c, "Battery below", "battery_alert_pct", 5, 50)

    def _slider_row(self, parent, label: str, attr: str, lo: int, hi: int) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=4)
        ctk.CTkLabel(row, text=label, width=150, anchor="w", text_color=TEXT, font=F(14)).pack(side="left")
        val = ctk.CTkLabel(row, text=f"{getattr(self.settings, attr)}%", width=52, text_color=MUTED)

        def changed(v):
            v = int(round(v))
            setattr(self.settings, attr, v)
            val.configure(text=f"{v}%")
            self._save_soon()
        s = ctk.CTkSlider(row, from_=lo, to=hi, number_of_steps=hi - lo, command=changed,
                          progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER)
        s.set(getattr(self.settings, attr))
        s.pack(side="left", fill="x", expand=True, padx=10)
        val.pack(side="left")

    def _save_soon(self) -> None:
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(500, self.settings.save)

    def _set_backend(self, value: str) -> None:
        self.settings.backend = value
        self.settings.save()
        self._check_brain(repeat=False)

    def _set_autonomy(self, value: str) -> None:
        self.settings.autonomy = value
        self.autonomy_text.configure(text=AUTONOMY_TEXT[value])
        self.settings.save()

    def _set_model(self) -> None:
        name = self.model_entry.get().strip()
        if name and name != self.settings.ollama_model:
            self.settings.ollama_model = name
            self.settings.save()

    def _set_flag(self, attr: str, var) -> None:
        setattr(self.settings, attr, bool(var.get()))
        self.settings.save()

    def _set_update_url(self) -> None:
        url = self.update_url_entry.get().strip()
        if url != self.settings.update_manifest_url:
            self.settings.update_manifest_url = url
            self.settings.save()

    def check_for_updates(self, quiet: bool = False) -> None:
        """quiet=True is the once-a-day background check: it only ever speaks up when there IS something new,
        never to say "you're up to date" or to report a connection problem the user didn't ask about."""
        if not quiet:
            self.update_status.configure(text="Checking...")
            self.update_btn.configure(state="disabled")

        def work():
            info, message = updater.check(self.settings.update_manifest_url)
            self.post(lambda: self._update_checked(info, message, quiet))
        threading.Thread(target=work, daemon=True).start()

    def _update_checked(self, info, message: str, quiet: bool) -> None:
        if not quiet:
            self.update_btn.configure(state="normal")
            self.update_status.configure(text=message)
        if info is not None:
            self._pending_update = info
            self.update_status.configure(text=message + " Press Download & install to get it.")
            self.update_btn.configure(text="Download & install", command=lambda: self._download_update(info))
            self.add_message(f"🚀 Laptop AI {info.version} is available. Open Settings to update.", "note")

    def _download_update(self, info) -> None:
        self.update_btn.configure(state="disabled")
        self.update_bar.pack(fill="x", pady=(8, 0), before=self.update_status)
        self._update_cancel = False

        def progress(frac, status):
            self.post(lambda: (self.update_bar.set(max(0.0, frac)) if frac >= 0 else None))

        def work():
            ok, message, path = updater.download(info, progress=lambda f, s: progress(f, s),
                                                  cancelled=lambda: self._update_cancel)
            self.post(lambda: self._update_downloaded(ok, message, path))
        threading.Thread(target=work, daemon=True).start()

    def _update_downloaded(self, ok: bool, message: str, path) -> None:
        self.update_bar.pack_forget()
        self.update_status.configure(text=message)
        self.update_btn.configure(state="normal")
        if ok and path is not None:
            self.update_btn.configure(text="Install and restart", command=lambda: self._install_update(path))

    def _install_update(self, path) -> None:
        if updater.apply_and_restart(path, before_exit=lambda: self.post(self._close)):
            self.update_status.configure(text="Installing — Laptop AI will reopen on the new version.")
        else:
            self.update_status.configure(text="Couldn't start the installer. Run it yourself from the updates folder.")

    def _set_player(self, value: str) -> None:
        self.settings.music_player = "VLC" if value == "VLC" else ""
        self.settings.save()

    def _model_state_text(self) -> str:
        return (f"Current model: {self.settings.ollama_model}. The download keeps going in the background and resumes if the connection drops. "
                "Ollama must be running.")

    def _pull_model(self, name: str) -> None:
        if getattr(self, "_pulling", False):
            self._pull_cancel = True
            self.pull_btn.configure(text="Stopping...")
            return
        self._pulling, self._pull_cancel = True, False
        self.pull_btn.configure(text="Stop download", fg_color=RED, hover_color="#dc2626")
        self.pull_bar.pack(anchor="w", fill="x", pady=(8, 0), before=self.pull_status)
        self.pull_bar.configure(mode="indeterminate")
        self.pull_bar.start()

        def progress(frac: float, status: str) -> None:
            def ui():
                if frac >= 0:
                    self.pull_bar.stop()
                    self.pull_bar.configure(mode="determinate")
                    self.pull_bar.set(frac)
                    self.pull_status.configure(text=f"Downloading {name}: {frac * 100:.0f}%")
                else:
                    self.pull_status.configure(text=status.capitalize() + "...")
            self.post(ui)

        def work():
            ok, msg = models.pull(self.settings.ollama_url, name, progress, cancelled=lambda: self._pull_cancel)

            def done():
                self._pulling = False
                self.pull_bar.stop()
                self.pull_bar.pack_forget()
                self.pull_btn.configure(text="Download Llama 3.2 (3B)  ~2 GB", fg_color=ACCENT, hover_color=ACCENT_HOVER)
                if ok:
                    self.settings.ollama_model = name
                    self.settings.save()
                    self.model_entry.delete(0, "end")
                    self.model_entry.insert(0, name)
                    self.pull_status.configure(text=f"{name} is installed and selected. Trix will use it from the next message.", text_color=GREEN)
                    self._check_brain(repeat=False)
                else:
                    self.pull_status.configure(text=msg, text_color=AMBER)
            self.post(done)
        threading.Thread(target=work, daemon=True).start()

    def _set_monitor(self) -> None:
        self.settings.monitor_enabled = bool(self.monitor_var.get())
        self.settings.save()

    # ---- Telegram settings
    def _tg_state_text(self) -> str:
        if not self.telegram.has_token:
            return "Not set up yet."
        if not self.settings.telegram_chat_id:
            return "Token saved. Now send your bot \"hi\" in Telegram and press Link my chat."
        return "Connected. Your phone is linked."

    def _tg_say(self, text: str) -> None:
        self.post(lambda: self.tg_status.configure(text=text))

    def _tg_save(self) -> None:
        token = self.token_entry.get().strip()
        ok, err = notify.save_token(token)
        if not ok:
            self.tg_status.configure(text=err)
            return
        self.token_entry.delete(0, "end")
        self.tg_status.configure(text="Checking the token…")

        def work():
            ok2, name = self.telegram.bot_name()
            self._tg_say(f"Token saved. Your bot is {name}. Send it \"hi\", then press Link my chat." if ok2 else name)
        threading.Thread(target=work, daemon=True).start()

    def _tg_link(self) -> None:
        def work():
            ok, res = self.telegram.find_chat()
            if ok:
                self.settings.telegram_chat_id = res
                self.settings.save()
                self.telegram.send("✅ Laptop AI is linked to this chat.")
                self._tg_say("Connected. Your phone is linked.")
            else:
                self._tg_say(res)
        self.tg_status.configure(text="Looking for your chat…")
        threading.Thread(target=work, daemon=True).start()

    def _tg_test(self) -> None:
        def work():
            ok, err = self.telegram.send("🔔 Test message from Laptop AI.")
            self._tg_say("Sent. Check your phone." if ok else err)
        threading.Thread(target=work, daemon=True).start()

    def _tg_toggle(self, attr: str) -> None:
        setattr(self.settings, attr, bool(self.tg_vars[attr].get()))
        self.settings.save()
        if attr == "telegram_questions":
            if self.settings.telegram_questions and self.telegram.configured:
                self._phone_on()
            else:
                self._phone_off()
                if self.settings.telegram_questions:
                    self.tg_status.configure(text="Link your chat first, then switch this on.")
                    self.tg_vars[attr].set(False)
                    self.settings.telegram_questions = False
                    self.settings.save()

    def _apply_sound_settings(self) -> None:
        self.settings.reminder_sound = bool(self.rsound_var.get())
        self.settings.speak_reminders = bool(self.rspeak_var.get())
        self.settings.alert_sound = bool(self.asound_var.get())
        self.settings.save()

    # ---- wake word settings
    def _wake_state_text(self) -> str:
        if not wakeword.available():
            return "Needs:  pip install vosk sounddevice"
        if not wakeword.model_installed():
            return "Voice model not downloaded yet. Press the button below first."
        return "Ready." if not self.settings.wake_word_enabled else "Listening for \"Trix\"."

    def _wake_toggle(self) -> None:
        want = bool(self.wake_var.get())
        if want and not (wakeword.available() and wakeword.model_installed()):
            self.wake_var.set(False)
            self.wake_status.configure(text=self._wake_state_text())
            return
        self.settings.wake_word_enabled = want
        self.settings.save()
        self._wake_on() if want else self._wake_off()
        self.wake_status.configure(text=self._wake_state_text())

    def _download_model(self) -> None:
        self.model_btn.configure(state="disabled", text="Downloading…")

        def progress(p):
            self.post(lambda: self.model_btn.configure(text=f"Downloading… {p * 100:.0f}%"))

        def work():
            ok, err = wakeword.download_model(progress)

            def done():
                if ok:
                    self.model_btn.configure(state="disabled", text="Voice model installed ✓")
                else:
                    self.model_btn.configure(state="normal", text="Download voice model (~40 MB)")
                self.wake_status.configure(text="Downloaded. You can switch it on now." if ok else err)
            self.post(done)
        threading.Thread(target=work, daemon=True).start()

    def _set_recog(self, value: str) -> None:
        self.settings.recognition = value
        self.recog_text.configure(text=RECOG_TEXT[value])
        self.settings.save()

    def _set_autostart(self) -> None:
        want = bool(self.autostart_var.get())
        ok_, error = autostart.set_enabled(want)
        if not ok_:
            self.autostart_var.set(not want)
            self.app_note.configure(text=error)

    def _set_tray(self) -> None:
        self.settings.close_to_tray = bool(self.tray_var.get())
        self.settings.save()

    def _set_commands(self) -> None:
        self.settings.allow_commands = bool(self.cmd_var.get())
        self.settings.save()

    def _set_voice_name(self, value: str) -> None:
        self.settings.voice_name = "" if value == "PC default" else value
        self.voice.name = self.settings.voice_name
        self.settings.save()
        self.voice.speak("Hi, I'm Trix.")

    def _set_voice_rate(self, value: float) -> None:
        self.settings.voice_rate = int(value)
        self.voice.rate = self.settings.voice_rate
        self.settings.save()

    def _set_theme(self, label: str) -> None:
        self.settings.theme = theme.label_to_name(label)
        self.settings.save()
        self.theme_note.configure(text="Saved. The new colour appears after you close and reopen Laptop AI (Quit from the tray icon).")

    def _apply_voice_setting(self) -> None:
        self.settings.speak_replies = bool(self.speak_var.get())
        self.settings.save()

    # ======================= Help =======================
    def _build_help(self, page) -> None:
        page.grid_rowconfigure(1, weight=1)
        self._header(page, "Help & Support", f"Laptop AI {__version__}")
        body = ctk.CTkScrollableFrame(page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 20))

        def section(title, lines):
            c = ctk.CTkFrame(body, corner_radius=16, fg_color=CARD, border_width=1, border_color=LINE)
            c.pack(fill="x", padx=6, pady=8)
            ctk.CTkLabel(c, text=title, font=F(16, "bold"), text_color=TEXT, anchor="w"
                         ).pack(fill="x", padx=18, pady=(14, 4))
            for line in lines:
                ctk.CTkLabel(c, text="•  " + line, font=F(13), text_color=MUTED, anchor="w", justify="left",
                             wraplength=640).pack(fill="x", padx=18, pady=2)
            ctk.CTkFrame(c, height=10, fg_color="transparent").pack()

        section("Things to try", ["What's using my memory?", "Is my battery OK?",
                                  "Close Notepad  (it asks first)", "How much temp space could you free?",
                                  "Remind me in 30 minutes to drink water",
                                  "Every hour, give me a system report"])
        section("If it says it can't reach an AI brain",
                ["Offline: install Ollama, then run  ollama pull llama3.2  and leave Ollama running.",
                 "Online: set the ANTHROPIC_API_KEY environment variable, then restart this app.",
                 "The status box at the bottom-left shows whether a brain is ready."])
        section("How safety works",
                ["The AI can only use the tools listed on the Files & Tools page. It cannot run any command it likes.",
                 "Closing a program ALWAYS asks you first, and Windows system processes are protected.",
                 "Autonomy 'report_only' makes it look-only.",
                 "Every change is written to the action log."])

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=8)
        ctk.CTkButton(row, text="Open action log", height=40, corner_radius=12, fg_color=CARD2, hover_color=ACCENT,
                      command=lambda: self._open_path(config.LOG_FILE)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(row, text="Open settings folder", height=40, corner_radius=12, fg_color=CARD2,
                      hover_color=ACCENT, command=lambda: self._open_path(config.APP_DIR)).pack(side="left")

    def _open_path(self, path: Path) -> None:
        config.ensure_dir()
        if path == config.LOG_FILE and not path.exists():
            path.touch()
        if hasattr(os, "startfile"):
            os.startfile(str(path))                         # Windows
        else:
            messagebox.showinfo("Location", str(path), parent=self)

    # ======================= shared bits =======================
    def _header(self, page, title: str, subtitle: str) -> None:
        h = ctk.CTkFrame(page, fg_color="transparent")
        h.grid(row=0, column=0, sticky="ew", padx=34, pady=(26, 14))
        ctk.CTkLabel(h, text=title, font=F(28, "bold"), text_color=TEXT, anchor="w").pack(fill="x")
        ctk.CTkLabel(h, text=subtitle, font=F(14), text_color=MUTED, anchor="w").pack(fill="x")

    def post(self, fn) -> None:
        self.ui_queue.put(fn)

    def _drain_queue(self) -> None:
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def confirm_from_worker(self, text: str) -> bool:
        """Called from a worker thread. Shows a dialog on the main thread and waits."""
        done, answer = threading.Event(), {"ok": False}

        def ask():
            if self.hidden():                   # a question must never appear behind a hidden window
                self.show_window()
            answer["ok"] = messagebox.askyesno("Laptop AI needs your OK", text, icon="warning", parent=self)
            done.set()
        self.post(ask)
        done.wait(timeout=120)              # no answer in 2 minutes counts as "no"
        return answer["ok"]

    # ======================= sending / thinking =======================
    def claim_brain(self) -> bool:
        """Only one request (typed, spoken, wake word or phone) uses the AI at a time."""
        with self._claim_lock:
            if self.busy:
                return False
            self.busy = True
            return True

    def release_brain(self) -> None:
        with self._claim_lock:
            self.busy = False
            self.executor.override = None

    def send(self, text: str, stay: bool = False, origin: str = "gui") -> bool:
        text = text.strip()
        if not text:
            return False
        if not stay:
            self.show("chat")
        if not self.claim_brain():
            self.add_message("Still working on your last request...", "note")
            return False
        self.add_message(text, "you")
        self._thinking = self.add_message("thinking…", "note")
        threading.Thread(target=self._think, args=(text, origin), daemon=True).start()
        return True

    def _ask_brain(self, text: str, origin: str, on_tool=lambda n, a: None) -> str:
        """Run one request. Wake-word requests always ask first; phone requests are read-only."""
        self.executor.override = {"wake": "ask_all", "phone": "read_only"}.get(origin)
        try:
            return self.brain.ask(text, on_tool)
        except Exception as e:           # last-resort guard: show it, do not crash
            return f"Something went wrong: {e}"

    def _think(self, text: str, origin: str = "gui") -> None:
        def on_tool(name: str, _args: dict) -> None:
            self.post(lambda: self.add_message(f"({TOOL_LABELS.get(name, name)}...)", "note"))
        reply = self._ask_brain(text, origin, on_tool)
        backend = self.brain.last_backend

        def show():
            self.release_brain()
            if self._thinking is not None:
                self._thinking.destroy()
                self._thinking = None
            self.say(reply, "bot")
            if backend:
                self.add_message(f"answered by {backend}", "note")
            self.voice_reply.configure(text=f"Trix: {reply[:300]}")
            self._voice_state("idle")
            hidden = self.hidden()
            spoken = bool(self.speak_var.get() or (origin == "wake" and hidden))     # nobody can read the window: say it out loud
            follow = spoken and origin == "wake" and self.settings.conversation_mode and self.voice.can_speak
            if spoken:
                self._voice_state("speaking")
                self.voice.speak(reply, on_done=(lambda: self._wake_resume(follow_up=True)) if follow else None)
                self.after(min(12000, 1200 + 70 * len(reply)), lambda: self._voice_state("idle") if self.orb.state == "speaking" else None)
            if origin == "wake" and hidden:
                self._toast("Trix", reply[:200])
            if origin == "wake" and not follow:
                self._wake_resume()
        self.post(show)

    # ======================= phone (Telegram) =======================
    def notify_phone(self, text: str, kind: str) -> None:
        """kind: reminders | alerts. Respects the toggles; never blocks the window."""
        on = {"reminders": self.settings.telegram_reminders, "alerts": self.settings.telegram_alerts}.get(kind, False)
        if on and self.telegram.configured:
            self.telegram.send_async(text)

    def alert(self, message: str) -> None:
        if focus.active() and not message.startswith("Serious problem"):
            self.say("⚠ " + message, "alert")            # focus mode: no pop-ups, serious problems still get through
            return
        self.say("⚠ " + message, "alert")
        if self.settings.alert_sound:
            sound.chime("alert" if message.startswith("Serious problem") else "warn")
        self._toast("Laptop AI warning", message)
        self.notify_phone("⚠ " + message, "alerts")

    def phone_question(self, text: str) -> str:
        """Called from the Telegram polling thread. Read-only: nothing can be changed from the phone."""
        if not self.claim_brain():
            return "I'm busy with another request. Please try again in a moment."
        self.post(lambda: self.add_message(f"📱 From your phone: {text}", "note"))
        try:
            return self._ask_brain(text, "phone")
        finally:
            self.release_brain()

    def phone_button(self, key: str):
        """A tapped Telegram button. Most run one fixed LOOK-ONLY tool. Lock and Sleep first ask for a second tap on Yes."""
        if key == "playing":
            now = nowplaying.current()
            if not now:
                return "I can't see what's playing. I can only read VLC when Laptop AI started it."
            return f"{'▶' if now['state'] == 'playing' else '⏸'} {now['title'] or 'Unknown track'}" + (f" - {now['artist']}" if now["artist"] else "")
        if key.startswith(("ask_", "do_")) or key == "cancel":
            if key == "cancel":
                return "OK, cancelled."
            if not self.settings.phone_actions:
                return "Phone buttons for Lock and Sleep are switched off in Settings."
            action = key.split("_", 1)[1]
            if action not in PHONE_POWER:
                return "I don't know that button."
            verb, done = PHONE_POWER[action]
            if key.startswith("ask_"):
                return f"Really {verb.lower()} your PC? Tap Yes to confirm.", [(f"✅ Yes, {verb.lower()}", f"do_{action}"), ("✖ Cancel", "cancel")]
            res = tools.REGISTRY["power_action"].func(action=action)
            tools._log("phone", "power_action", {"action": action}, res)
            return done if '"status": "ok"' in res else render.problem(json.loads(res)) or "That didn't work."
        name, args = PHONE_ACTIONS[key]
        if not self.claim_brain():
            return "I'm busy with another request. Please try again in a moment."
        self.executor.override = "read_only"
        try:
            return format_result(self.executor.run(name, dict(args), source="phone"))
        finally:
            self.release_brain()

    def _phone_on(self) -> None:
        if self.phone is None:
            self.phone = notify.PhoneQuestions(self.telegram, self.phone_question,
                                               PHONE_BUTTONS, self.phone_button)
        self.phone.start()

    def _phone_off(self) -> None:
        if self.phone:
            self.phone.stop()

    # ======================= wake word ("Trix") =======================
    def _autostart_wake(self) -> None:
        if wakeword.available() and wakeword.model_installed():
            self._wake_on()
        else:
            self.settings.wake_word_enabled = False
            self.settings.save()

    def _wake_poll(self) -> None:
        """Show what the wake model last heard, so you can tell the microphone is working."""
        w = self.wake
        if w is not None and w.listening and self.settings.wake_word_enabled:
            heard = getattr(w.detector, "last", "") or "nothing yet"
            self.wake_status.configure(text=f"Listening for \"Trix\". Last heard: {heard}")
        self.after(1000, self._wake_poll)

    def _wake_on(self) -> None:
        if self.wake is None:
            self.wake = wakeword.WakeListener(
                on_wake=lambda: self.post(self._on_wake),
                on_command=lambda raw: threading.Thread(target=self._on_command, args=(raw,), daemon=True).start(),
                on_error=lambda m: self.post(lambda: self._wake_failed(m)),
                on_follow_up=self._on_follow_up)
        self.wake.start()
        self._set_mic_note(True)
        if hasattr(self, "wake_pill"):
            self._refresh_wake_pill()

    def _wake_off(self) -> None:
        if self.wake:
            self.wake.stop()
        self._set_mic_note(False)
        if hasattr(self, "wake_pill"):
            self._refresh_wake_pill()

    def _wake_failed(self, message: str) -> None:
        self.settings.wake_word_enabled = False
        self.settings.save()
        self._set_mic_note(False)
        if hasattr(self, "wake_var"):
            self.wake_var.set(False)
            self.wake_status.configure(text=self._wake_state_text())
        self.say("🎙 " + message, "alert")

    def _wake_resume(self, follow_up: bool = False) -> None:
        if self.wake:
            self.wake.resume(follow_up=follow_up)

    def _on_follow_up(self) -> None:
        """Trix finished speaking and is listening for a follow-up, with no wake word needed."""
        self._in_follow = True
        self.post(lambda: self._voice_state("listening", hint="Listening for a follow-up. Just speak, or stay quiet to stop."))
        self.post(lambda: self.after(int(self.wake.follow_up_s * 1000) + 1200,
                                     lambda: self._voice_state("idle") if self.orb.state == "listening" else None))

    def _on_wake(self) -> None:
        if winsound:
            try:
                winsound.Beep(880, 140)               # a clear tone, even if Windows system sounds are off
                winsound.Beep(1175, 140)
            except Exception:
                pass
        self._toast("Trix", "Listening... say your command.")
        self.add_message("🎙 I heard \"Trix\". Listening, say your command…", "note")
        self._voice_state("listening", hint="I heard \"Trix\". Say your command.")


    def _on_command(self, raw) -> None:            # worker thread
        if raw is None:
            self.post(lambda: (self._voice_state("idle"),
                               self.add_message("I didn't hear a command. After the two beeps, speak right away, "
                                                "clearly and close to the microphone.", "note")))
            self._wake_resume()
            return
        text, err = self.voice.transcribe(raw, self.settings.recognition)
        follow, self._in_follow = self._in_follow, False
        if not text:
            if follow:                                                   # background noise after an answer: say nothing
                self.post(lambda: self._voice_state("idle"))
            else:
                self.post(lambda: self.add_message(err or "I didn't catch that.", "note"))
            self._wake_resume()
            return
        self.post(lambda: self._wake_send(text))

    def _wake_send(self, text: str) -> None:
        if not self.send(text, stay=True, origin="wake"):
            self._wake_resume()

    # ======================= status, stats, scheduler =======================
    def _check_brain(self, repeat: bool = True) -> None:
        def work():
            mode = self.settings.backend
            off = mode in ("auto", "offline") and self.brain.offline.available()
            on = mode in ("auto", "online") and self.brain.online.available()
            self.post(lambda: self._set_status(off, on))
        threading.Thread(target=work, daemon=True).start()
        if repeat:
            self.after(20000, self._check_brain)

    def _set_status(self, offline_ok: bool, online_ok: bool) -> None:
        if offline_ok:
            colour, title, detail = GREEN, "System Online", "Offline brain ready (Ollama)"
        elif online_ok:
            colour, title, detail = GREEN, "System Online", "Online brain ready (Claude)"
        else:
            colour, title, detail = AMBER, "No AI brain", "See Help & Support"
        self.dot.configure(text_color=colour)
        self.status_title.configure(text=title, text_color=colour)
        self.status_detail.configure(text=detail)

    def _update_stats(self, st: dict, record: bool = True) -> None:
        self.last_stats = st
        if record:
            for key in ("cpu", "ram", "disk"):
                if st.get(key) is not None:
                    self.series[key].append(float(st[key]))
            if st.get("battery") is not None:
                self.series["battery"].append(float(st["battery"]))
        for key in ("cpu", "ram", "disk"):
            pct = float(st.get(key) or 0)
            self.tiles[key].set(pct, f"{pct:.0f}%", level_colour(pct), self.series[key])
        batt = st.get("battery")
        if batt is None:                                   # desktop PC without a battery
            self.tiles["battery"].set(None, "AC", GREEN, (), sub="No battery")
        else:
            plugged = bool(st.get("plugged"))
            self.tiles["battery"].set(float(batt), f"{batt:.0f}%" + (" ⚡" if plugged else ""),
                                      GREEN if plugged else level_colour(batt, low_is_bad=True), self.series["battery"])
        self._refresh_holo()

    def _close_program(self, name: str) -> bool:
        """You just said Yes in the question box, so close it (system programs are still refused)."""
        out = tools.kill_process(name=name)
        tools._log("hog-watch", "kill_process", {"name": name}, out)
        try:
            return json.loads(out).get("closed", 0) > 0
        except ValueError:
            return False

    def run_scheduled(self, task: dict) -> None:
        kind = task["kind"]
        if kind == "remind":
            self.say(f"⏰ Reminder: {task['message']}", "alert")
            self.notify_phone(f"⏰ Reminder: {task['message']}", "reminders")
            self._toast("Reminder", str(task["message"]))
            if self.settings.reminder_sound:
                sound.chime("reminder")
            if self.settings.speak_reminders and self.voice.can_speak:
                self.voice.speak(f"Reminder. {task['message']}")
        elif kind == "system_report":
            d = json.loads(self.executor.run("get_system_status", {}, source="scheduler"))
            report = (f"📊 Scheduled report: CPU {d.get('cpu_percent')}%, RAM {d.get('ram_percent')}%, "
                      f"disk {d.get('disk_percent')}%, battery {d.get('battery_percent')}%.")
            self.say(report, "bot")
            self.notify_phone(report, "reminders")
        elif kind == "morning_report":
            threading.Thread(target=self._morning_report, daemon=True).start()
        elif kind == "media":
            def go():
                action = (task.get("params") or {}).get("action", "play_pause")
                text = tools_v2.do_media(action)
                self.post(lambda: self.add_message(f"🎵 {text}", "note"))
                if self.settings.reminder_sound:
                    sound.chime("ok")
            threading.Thread(target=go, daemon=True).start()
        elif kind == "clean_temp":
            # runs through the Executor, so "ask" mode still asks before deleting anything
            threading.Thread(target=lambda: self._scheduled_clean(task), daemon=True).start()

    def _startup_check(self) -> None:
        """A quiet self-check a few seconds after start-up. It only speaks up when something that is switched on is broken."""
        def work():
            try:
                checks = doctor.run_all(self.settings)
            except Exception:
                return
            wake = self.settings.wake_word_enabled
            bad = [c for c in checks if c.status == doctor.FAIL or (wake and c.status == doctor.WARN and c.name.startswith(("Microphone", "Voice model")))]
            if not bad:
                return
            lines = ["🩺 Start-up check: " + (f"{len(bad)} thing{'s' if len(bad) != 1 else ''} to look at."), ""]
            for c in bad[:6]:
                lines.append(f"{doctor.ICON[c.status]} {c.name}: {c.detail}")
                if c.fix:
                    lines.append(f"     Fix: {c.fix}")
            self.post(lambda: self.add_message("\n".join(lines), "note"))
        threading.Thread(target=work, daemon=True).start()

    def _auto_check_updates(self) -> None:
        """Once a day at most — a quiet background check that only ever speaks up when there's something new."""
        marker = config.APP_DIR / "last_update_check.txt"
        try:
            last = float(marker.read_text().strip())
        except (OSError, ValueError):
            last = 0.0
        if time.time() - last < 86400:
            return
        marker.write_text(str(time.time()))
        self.check_for_updates(quiet=True)

    def _morning_report(self) -> None:
        d = json.loads(self.executor.run("get_system_status", {}, source="scheduler"))
        problems = [f.message for f in getattr(self, "findings", []) if getattr(f, "level", "") in ("critical", "warning")]
        text = tools_extra.morning_report_text(d, self.scheduler.list(), len(tools_extra._load_notes()),
                                               problems=problems, trend=trends.summary(7))
        self.post(lambda: self.say(text, "bot"))
        self.notify_phone(text, "reminders")
        self.post(lambda: self._toast("Good morning", text))

    def _scheduled_clean(self, task: dict) -> None:
        out = self.executor.run("clean_temp_files", task.get("params", {}), source="scheduler")
        self.post(lambda: self.add_message(f"🧹 Scheduled cleanup: {format_result(out)}", "note"))

    def _close(self) -> None:
        """The window's X button: hide to the tray if we can, otherwise quit."""
        if self.tray_on and self.settings.close_to_tray:
            self.withdraw()
            if not self._told_about_tray:
                self._told_about_tray = True
                self.tray.notify("Laptop AI is still running",
                                 "Reminders and your phone link keep working. Use the icon by the clock to open or quit.")
            return
        self.quit_app()

    def show_window(self) -> None:
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except Exception:
            pass

    def hidden(self) -> bool:
        try:
            return self.state() in ("withdrawn", "iconic")
        except Exception:
            return False

    def _toast(self, title: str, text: str) -> None:
        """A Windows pop-up, but only when the window is hidden (otherwise the chat already shows it)."""
        if self.tray_on and self.hidden():
            self.tray.notify(title, text)

    def quit_app(self) -> None:
        self.monitor.stop()
        self.watchdog.stop()
        self.holo.stop()
        self.orb.stop()
        self.scheduler.stop()
        self._wake_off()
        self._phone_off()
        self.tray.stop()
        if self.instance is not None:
            self.instance.release()
        self.destroy()


def _crash_log(exc_type, exc, tb) -> None:
    """A problem the rest of the app didn't catch should never just vanish the window with no clue why — write
    down what happened so it can actually be fixed, and keep the app alive if at all possible."""
    import traceback
    try:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        path = config.APP_DIR / "crash_log.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n--- {__version__} ---\n{text}")
    except OSError:
        pass


def main(start_hidden: bool = False, instance=None) -> None:
    import sys
    sys.excepthook = _crash_log                     # anything on the main thread that slips through is logged, not silent
    threading.excepthook = lambda args: _crash_log(args.exc_type, args.exc_value, args.exc_traceback)
    app = App(start_hidden, instance)
    app.report_callback_exception = lambda *exc_info: (_crash_log(*exc_info),
                                                        app.add_message("Something went wrong with that last action — "
                                                                        "it's written to crash_log.txt so it can be fixed. "
                                                                        "Everything else should keep working.", "note"))
    app.mainloop()
