"""The first-run welcome screen: asks what Laptop AI should call you. No account, no email: the name stays on this PC."""
from __future__ import annotations

import re
import tkinter as tk
from typing import Callable

import customtkinter as ctk

MAX_NAME = 24


def clean_name(text: str) -> str:
    """Tidy what was typed: no control characters or odd symbols, single spaces, at most 24 letters. Empty if nothing usable."""
    text = re.sub(r"[^\w .'\-]", "", str(text or ""), flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip(" .'-")
    return text[:MAX_NAME].strip()


def greeting_for(name: str, hour: int) -> str:
    word = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    name = clean_name(name)
    return f"{word}, {name}" if name else word


class Welcome(ctk.CTkFrame):
    """Full-window panel shown over the app until a name is chosen. `on_done(name)` gets the cleaned name ('' if skipped)."""

    def __init__(self, master, on_done: Callable[[str], None], colours: dict, fonts, existing_name: str = ""):
        super().__init__(master, fg_color=colours["bg"], corner_radius=0)
        self.on_done = on_done
        c = colours
        box = ctk.CTkFrame(self, fg_color=c["card"], corner_radius=24, border_width=1, border_color=c["line"])
        box.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(box, text="LAPTOP AI", font=fonts(13, "bold"), text_color=c["accent"]).pack(padx=70, pady=(44, 2))
        ctk.CTkLabel(box, text="Welcome", font=fonts(36, "bold"), text_color=c["text"]).pack(padx=70)
        ask = "Still happy to be called {}?".format(existing_name) if existing_name else "I live on this PC and help you run it."
        ctk.CTkLabel(box, text=f"{ask}\nWhat should I call you?", font=fonts(15),
                     text_color=c["muted"], justify="center").pack(padx=70, pady=(8, 22))
        self.entry = ctk.CTkEntry(box, width=300, height=46, corner_radius=23, font=fonts(16), justify="center",
                                  fg_color=c["card2"], border_color=c["line"], placeholder_text="Your first name")
        if existing_name:
            self.entry.insert(0, existing_name)
        self.entry.pack(padx=70)
        self.entry.bind("<Return>", lambda _e: self._go())
        self.entry.bind("<KeyRelease>", lambda _e: self._live())
        self.note = ctk.CTkLabel(box, text="", font=fonts(12), text_color=c["muted"])
        self.note.pack(pady=(8, 0))
        self.button = ctk.CTkButton(box, text="Continue", width=300, height=46, corner_radius=23, font=fonts(15, "bold"),
                                    fg_color=c["accent"], hover_color=c["accent_hover"], command=self._go)
        self.button.pack(padx=70, pady=(12, 6))
        ctk.CTkButton(box, text="Skip for now", width=120, height=28, fg_color="transparent", hover_color=c["card2"],
                      text_color=c["muted"], font=fonts(12), command=lambda: self.on_done("")).pack(pady=(0, 14))
        ctk.CTkLabel(box, text="No account or email needed. Your name stays on this PC.", font=fonts(11),
                     text_color=c["muted"]).pack(padx=70, pady=(0, 32))

    def focus_name(self) -> None:
        try:
            self.entry.focus_set()
        except tk.TclError:
            pass

    def _live(self) -> None:
        raw = self.entry.get()
        cleaned = clean_name(raw)
        self.note.configure(text=f"I'll say: Good evening, {cleaned}" if cleaned else "")

    def _go(self) -> None:
        name = clean_name(self.entry.get())
        if not name:
            self.note.configure(text="Type a name, or press Skip.")
            return
        self.on_done(name)
