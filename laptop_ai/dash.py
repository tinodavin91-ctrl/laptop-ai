"""Home-page metric tiles: a radial gauge with a live sparkline, drawn on a canvas.

The arc glides to each new value, and the line shows the last couple of minutes, so the numbers feel alive.
"""
from __future__ import annotations

import tkinter as tk
from typing import Optional, Sequence

PANEL, LINE, TRACK = "#0b1329", "#1b2a52", "#16213f"
MUTED, TEXT = "#8190b3", "#e8eefc"


def blend(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def spark_points(history: Sequence[float], x0: float, y0: float, w: float, h: float, top: float = 100.0) -> list[float]:
    """Canvas coordinates for a sparkline (oldest on the left). Fewer than 2 samples gives nothing to draw."""
    vals = [max(0.0, min(float(v), top)) for v in history if v is not None]
    if len(vals) < 2:
        return []
    step = w / (len(vals) - 1)
    pts: list[float] = []
    for i, v in enumerate(vals):
        pts += [x0 + i * step, y0 + h - (v / top) * h]
    return pts


class MetricTile(tk.Canvas):
    def __init__(self, master, label: str, height: int = 90, bg: str = "#070b14", **kw):
        super().__init__(master, height=height, bg=bg, highlightthickness=0, bd=0, **kw)
        self.label = label
        self.pct: Optional[float] = None
        self.text, self.sub, self.colour = "--", "", "#22c55e"
        self.history: list[float] = []
        self._shown = 0.0                     # the arc's current (animated) value
        self._job = None
        self._parent_bg = bg
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, pct: Optional[float], text: str, colour: str, history: Sequence[float] = (), sub: str = "") -> None:
        self.pct, self.text, self.colour, self.sub = pct, text, colour, sub
        self.history = list(history)
        self._draw()
        if self._job is None:
            self._job = self.after(30, self._glide)

    def _glide(self) -> None:
        self._job = None
        target = 100.0 if self.pct is None else float(self.pct)
        if abs(target - self._shown) < 0.4:
            self._shown = target
        else:
            self._shown += (target - self._shown) * 0.25
            self._job = self.after(30, self._glide)
        self._draw()

    def _round(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw(self) -> None:
        self.delete("all")
        w, h = max(self.winfo_width(), 120), max(self.winfo_height(), 60)
        self._round(1, 1, w - 1, h - 1, 16, fill=PANEL, outline=LINE)
        d = min(h - 28, 70)
        ax, ay = 16, (h - d) / 2
        self.create_arc(ax, ay, ax + d, ay + d, start=225, extent=-270, style="arc", outline=TRACK, width=7)
        val = max(0.0, min(self._shown, 100.0))
        if val > 0.5:
            self.create_arc(ax, ay, ax + d, ay + d, start=225, extent=-270 * val / 100, style="arc", outline=self.colour, width=7)
        self.create_text(ax + d / 2, ay + d / 2 + 1, text="AC" if self.pct is None else f"{val:.0f}", fill=TEXT,
                         font=("Segoe UI", -16, "bold"))
        tx = ax + d + 14
        self.create_text(tx, 20, text=self.label.upper(), anchor="w", fill=MUTED, font=("Segoe UI", -11, "bold"))
        self.create_text(tx, 42, text=self.text, anchor="w", fill=TEXT, font=("Segoe UI", -22, "bold"))
        sw = w - tx - 16
        pts = spark_points(self.history, tx, h - 34, sw, 20) if sw > 20 else []
        if pts:
            area = pts + [pts[-2], h - 14, pts[0], h - 14]
            self.create_polygon(area, fill=blend(self.colour, PANEL, 0.82), outline="")
            self.create_line(pts, fill=self.colour, width=2, smooth=True)
            self.create_oval(pts[-2] - 3, pts[-1] - 3, pts[-2] + 3, pts[-1] + 3, fill=self.colour, outline="")
        elif self.sub:
            self.create_text(tx, h - 24, text=self.sub, anchor="w", fill=MUTED, font=("Segoe UI", -11))
