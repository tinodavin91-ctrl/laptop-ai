"""The animated voice orb on the Voice Assistant page: a glowing core ringed by sound bars that react to what Trix is doing."""
from __future__ import annotations

import math
import random
import time
import tkinter as tk
from typing import Callable, Optional

from .holo import blend

BG = "#09101f"
COLOURS = {"idle": "#4f8cff", "listening": "#22c55e", "thinking": "#a78bfa", "speaking": "#3ddcff", "off": "#334155"}
STATES = tuple(COLOURS)


def bar_levels(state: str, t: float, n: int = 72, rnd: Optional[random.Random] = None) -> list[float]:
    """Height (0..1) of each of the n bars around the ring. Pure, so it is easy to test."""
    rnd = rnd or random
    out = []
    for i in range(n):
        a = 2 * math.pi * i / n
        if state == "listening":          # lively, like a live microphone level
            v = 0.30 + 0.28 * math.sin(a * 5 + t * 6) * math.sin(t * 2.3 + i) + 0.22 * rnd.random()
        elif state == "speaking":         # rhythmic, in waves
            v = 0.35 + 0.30 * math.sin(a * 3 - t * 7) + 0.18 * math.sin(a * 8 + t * 11) * rnd.random()
        elif state == "thinking":         # one bright bump chasing around the ring
            d = (a - t * 3.2) % (2 * math.pi)
            d = min(d, 2 * math.pi - d)
            v = 0.12 + 0.75 * math.exp(-(d * d) / 0.35)
        elif state == "off":
            v = 0.05
        else:                              # idle: a slow, calm breathing
            v = 0.12 + 0.07 * math.sin(a * 2 + t * 1.4)
        out.append(max(0.03, min(1.0, v)))
    return out


class VoiceOrb(tk.Canvas):
    def __init__(self, master, command: Callable[[], None] = lambda: None, size: int = 300, **kw):
        super().__init__(master, width=size, height=size, bg=BG, highlightthickness=0, bd=0, **kw)
        self.size, self.command = size, command
        self.state, self.enabled = "idle", True
        self._job = None
        self._t0 = time.time()
        self._rnd = random.Random(7)
        self._level = [0.1] * 72
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", lambda e: self.configure(cursor="hand2" if self._inside(e) and self.enabled else ""))

    # ---- public
    def set_state(self, state: str) -> None:
        if state in COLOURS:
            self.state = state

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        if not on:
            self.state = "off"
        elif self.state == "off":
            self.state = "idle"

    def start(self) -> None:
        if self._job is None:
            self._job = self.after(33, self._tick)

    def stop(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    # ---- input
    def _inside(self, e) -> bool:
        c = self.size / 2
        return math.hypot(e.x - c, e.y - c) <= self.size * 0.30

    def _click(self, e) -> None:
        if self.enabled and self._inside(e) and self.state in ("idle",):
            self.command()

    # ---- drawing
    def _tick(self) -> None:
        self._job = self.after(33 if self.winfo_ismapped() else 400, self._tick)
        if not self.winfo_ismapped():
            return
        try:
            self.draw(time.time() - self._t0)
        except tk.TclError:
            pass

    def draw(self, t: float) -> None:
        self.delete("orb")
        c, s = self.size / 2, self.size
        col = COLOURS[self.state]
        core = s * 0.20
        pulse = 0.5 + 0.5 * math.sin(t * (5 if self.state == "listening" else 2.2))
        # soft glow
        for i in range(7, 0, -1):
            r = core * (1.0 + i * 0.22) + (pulse * s * 0.012 if self.state != "off" else 0)
            self.create_oval(c - r, c - r, c + r, c + r, fill=blend(BG, col, 0.020 * (8 - i) + 0.02), outline="", tags="orb")
        # ring of bars (smoothed so they glide)
        target = bar_levels(self.state, t, len(self._level), self._rnd)
        self._level = [p + (q - p) * 0.35 for p, q in zip(self._level, target)]
        r0 = core * 1.55
        for i, v in enumerate(self._level):
            a = 2 * math.pi * i / len(self._level) - math.pi / 2
            r1 = r0 + v * s * 0.17
            x0, y0, x1, y1 = c + r0 * math.cos(a), c + r0 * math.sin(a), c + r1 * math.cos(a), c + r1 * math.sin(a)
            self.create_line(x0, y0, x1, y1, fill=blend(BG, col, 0.35 + 0.65 * v), width=max(2, s * 0.009), capstyle="round", tags="orb")
        # thin outer ring
        ro = r0 + s * 0.19
        self.create_oval(c - ro, c - ro, c + ro, c + ro, outline=blend(BG, col, 0.22), width=1, tags="orb")
        # the core
        self.create_oval(c - core, c - core, c + core, c + core, fill=blend(BG, col, 0.30), outline=col, width=2, tags="orb")
        self.create_oval(c - core * 0.78, c - core * 0.78, c + core * 0.78, c + core * 0.78, fill=blend(BG, col, 0.14), outline="", tags="orb")
        if self.state == "thinking":
            for k in range(3):
                st = (t * 240 + k * 120) % 360
                self.create_arc(c - core * 0.62, c - core * 0.62, c + core * 0.62, c + core * 0.62, start=st, extent=70,
                                style="arc", outline="#ffffff", width=3, tags="orb")
        elif self.state == "speaking":
            for k in range(5):
                h = core * (0.18 + 0.38 * abs(math.sin(t * 7 + k * 1.3)))
                x = c + (k - 2) * core * 0.26
                self.create_line(x, c - h, x, c + h, fill="#ffffff", width=max(3, s * 0.014), capstyle="round", tags="orb")
        else:
            self._mic(c, c, core * 0.5, "#ffffff" if self.state != "off" else "#64748b")

    def _mic(self, x: float, y: float, r: float, col: str) -> None:
        w = r * 0.62
        self.create_rectangle(x - w, y - r, x + w, y + r * 0.35, fill=col, outline=col, tags="orb")
        self.create_oval(x - w, y - r - w, x + w, y - r + w, fill=col, outline=col, tags="orb")
        self.create_oval(x - w, y + r * 0.35 - w, x + w, y + r * 0.35 + w, fill=col, outline=col, tags="orb")
        self.create_arc(x - r * 0.95, y - r * 0.55, x + r * 0.95, y + r * 0.95, start=200, extent=140, style="arc",
                        outline=col, width=max(2, r * 0.16), tags="orb")
        self.create_line(x, y + r * 0.95, x, y + r * 1.25, fill=col, width=max(2, r * 0.16), tags="orb")
