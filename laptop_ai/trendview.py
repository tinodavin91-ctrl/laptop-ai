"""The Health history window: CPU, memory, disk and battery over the last day, week or month."""
from __future__ import annotations

import time
import tkinter as tk
from typing import Optional

import customtkinter as ctk

from . import trends

LINES = (("cpu", "CPU", "#38bdf8"), ("ram", "Memory", "#a78bfa"), ("disk", "Disk", "#f59e0b"), ("battery", "Battery", "#22c55e"))
BG, GRID, TEXT, MUTED = "#09101f", "#1b2a52", "#e8eefc", "#8190b3"
RANGES = {"Last 24 hours": 1, "Last 7 days": 7, "Last 30 days": 30}


def _xy(i: int, n: int, v: float, w: float, h: float, pad) -> tuple[float, float]:
    left, top, right, bottom = pad
    return (left + (w - left - right) * (i / max(1, n - 1)),
            top + (h - top - bottom) * (1 - max(0.0, min(100.0, v)) / 100))


def chart_segments(values: list[Optional[float]], w: float, h: float, pad: tuple[float, float, float, float]):
    """(runs, dots, bridges) for one line (pure, testable).
    runs: unbroken stretches as flat (x, y, x, y...) lists; dots: lone readings; bridges: (x1, y1, x2, y2) joining the end of
    one stretch to the start of the next, drawn dashed to show the app wasn't running in between."""
    n = len(values)
    groups: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    for i, v in enumerate(values):
        if v is None:
            if cur:
                groups.append(cur)
            cur = []
            continue
        cur.append(_xy(i, n, v, w, h, pad))
    if cur:
        groups.append(cur)
    runs = [[c for pt in g for c in pt] for g in groups if len(g) >= 2]
    dots = [g[0] for g in groups if len(g) == 1]
    bridges = [(a[-1][0], a[-1][1], b[0][0], b[0][1]) for a, b in zip(groups, groups[1:])]
    return runs, dots, bridges


def chart_points(values: list[Optional[float]], w: float, h: float, pad: tuple[float, float, float, float]) -> list[list[float]]:
    """Just the unbroken runs (kept for older callers)."""
    return chart_segments(values, w, h, pad)[0]


def _dim(colour: str, bg: str = BG, mix: float = 0.7) -> str:
    c = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    d = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(x * mix + y * (1 - mix)):02x}" for x, y in zip(c, d))


class TrendCanvas(tk.Canvas):
    def __init__(self, master, **kw):
        super().__init__(master, bg=BG, highlightthickness=0, **kw)
        self.days = 1
        self.shown = {k: True for k, _n, _c in LINES}
        self._job = None
        self._data_key = None
        self._data = None
        self.bind("<Configure>", lambda e: self._soon())

    def _soon(self) -> None:
        """Window being dragged or opened sends many resize events: redraw once when they stop."""
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
        self._job = self.after(60, self.redraw)

    def set_days(self, days: int) -> None:
        self.days = days
        self._data_key = None
        self.redraw()

    def toggle(self, key: str, on: bool) -> None:
        self.shown[key] = on
        self.redraw()

    def redraw(self) -> None:
        self._job = None
        if not self.winfo_exists():
            return
        self.delete("all")
        w, h = max(self.winfo_width(), 300), max(self.winfo_height(), 200)
        pad = (44, 14, 14, 30)
        for pct in (0, 25, 50, 75, 100):
            y = pad[1] + (h - pad[1] - pad[3]) * (1 - pct / 100)
            self.create_line(pad[0], y, w - pad[2], y, fill=GRID)
            self.create_text(pad[0] - 8, y, text=f"{pct}%", fill=MUTED, anchor="e", font=("Segoe UI", 9))
        if self._data_key != self.days:                  # work out the lines once per range, not on every resize
            self._data, self._data_key = trends.buckets(self.days, 96), self.days
        data = self._data
        if not any(v is not None for k in data for v in data[k]):
            self.create_text(w / 2, h / 2, text="No history yet.\nKeep the app running and it fills in by itself.",
                             fill=MUTED, font=("Segoe UI", 12), justify="center")
            return
        now = time.time()
        for i in range(5):
            frac = i / 4
            when = now - self.days * 86400 * (1 - frac)
            fmt = "%H:%M" if self.days <= 1 else "%d %b"
            self.create_text(pad[0] + (w - pad[0] - pad[2]) * frac, h - 14, text=time.strftime(fmt, time.localtime(when)),
                             fill=MUTED, font=("Segoe UI", 9), anchor="e" if i == 4 else "center")
        for key, _name, colour in LINES:
            if not self.shown.get(key):
                continue
            runs, dots, bridges = chart_segments(data[key], w, h, pad)
            for x1, y1, x2, y2 in bridges:                  # dashed = the app was closed, so no readings
                self.create_line(x1, y1, x2, y2, fill=_dim(colour), width=1, dash=(4, 3))
            for run in runs:
                self.create_line(*run, fill=colour, width=2, smooth=True)
            for x, y in dots:
                self.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill=colour, outline="")


class TrendWindow(ctk.CTkToplevel):
    def __init__(self, master, fonts):
        super().__init__(master)
        self.title("Health history")
        self.geometry("820x560")
        self.configure(fg_color="#070b14")
        self.transient(master)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=22, pady=(18, 6))
        ctk.CTkLabel(head, text="Health history", font=fonts(22, "bold"), text_color=TEXT).pack(side="left")
        self.range = ctk.CTkSegmentedButton(head, values=list(RANGES), command=self._range, font=fonts(12))
        self.range.set("Last 24 hours")
        self.range.pack(side="right")
        self.chart = TrendCanvas(self)
        self.chart.pack(fill="both", expand=True, padx=22, pady=6)
        legend = ctk.CTkFrame(self, fg_color="transparent")
        legend.pack(fill="x", padx=22)
        self.vars = {}
        for key, name, colour in LINES:
            var = ctk.BooleanVar(value=True)
            self.vars[key] = var
            ctk.CTkCheckBox(legend, text=name, variable=var, text_color=colour, font=fonts(13, "bold"), width=90,
                            command=lambda k=key: self.chart.toggle(k, self.vars[k].get())).pack(side="left", padx=(0, 14))
        ctk.CTkLabel(legend, text="dashed line = Laptop AI was closed", font=fonts(11), text_color=MUTED).pack(side="right")
        self.summary = ctk.CTkLabel(self, text="Reading your history...", font=fonts(13), text_color=MUTED, justify="left", anchor="w", wraplength=760)
        self.summary.pack(fill="x", padx=22, pady=(10, 18))
        self.after(30, lambda: self._range("Last 24 hours"))        # open the window first, fill it a moment later

    def _range(self, label: str) -> None:
        if not self.winfo_exists():
            return
        days = RANGES[label]
        self.chart.set_days(days)
        self.summary.configure(text=trends.summary(days))
