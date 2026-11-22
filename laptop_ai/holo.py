"""The 3D holographic laptop on the home page.

A rotating, glowing model of the laptop. Drag to turn it, scroll to zoom, double-click to reset, click a part
to inspect it. Parts rise up when the page opens. Healthy parts glow blue; a warning pulses amber; a serious
problem pulses red with a ground ring and a "!" badge. Colours and numbers come from holo_model.compute().

It is a small software renderer on a tk.Canvas (see holo3d.py for the maths): the whole scene is redrawn each
frame, which is cheap for a few dozen solids.
"""
from __future__ import annotations

import math
import random
import time
import tkinter as tk
from typing import Callable, Optional

from . import holo3d as g
from .holo_model import BY_ID, CRIT, OK, PARTS, WARN, Status

BG = "#09101f"
FILL, DIM, GRID, STEEL = "#0b1633", "#1d4a99", "#0f1c3a", "#101c3a"
CYAN, AMBER, RED, GREEN = "#3ddcff", "#f59e0b", "#ef4444", "#22c55e"
LEVEL = {OK: CYAN, WARN: AMBER, CRIT: RED}
HEX = "0123456789ABCDEF"
FPS_MS = 33
SHORT = {"network": "Wi-Fi", "battery": "Battery", "usb": "USB", "hdmi": "HDMI", "charger": "Charger", "webcam": "Camera",
         "mic": "Mic", "bluetooth": "Bluetooth"}
MINOR = {"webcam", "mic", "bluetooth", "speakers", "touchpad", "keyboard", "usb", "hdmi", "charger"}   # labelled only when hovered, selected or faulty
NO_RING = {"display", "board", "security", "webcam", "mic", "keyboard"}


def blend(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


class HoloLaptop(tk.Canvas):
    def __init__(self, master, on_select: Callable[[str], None] = lambda p: None,
                 active: Callable[[], bool] = lambda: True, **kw):
        super().__init__(master, bg=BG, highlightthickness=0, bd=0, **kw)
        self.on_select, self.active = on_select, active
        self.status: dict[str, Status] = {p.id: Status() for p in PARTS}
        self.stats: dict = {}
        self.selected: Optional[str] = None
        self.hover: Optional[str] = None
        self.scanning = False
        self.cam = g.Camera()
        self.yaw0, self.pitch0 = 0.0, self.cam.pitch
        self.t0 = self.boot_t0 = self.last_drag = time.time()
        self.fan_angle = 0.0
        self.booted = False
        self._job = None
        self._drawn: list[tuple[str, list[float]]] = []
        self._mouse = (0, 0)
        self._press: Optional[tuple[int, int]] = None
        self._moved = False
        self._rain = [(random.Random(i).uniform(-47, 47), random.Random(i + 99).uniform(10, 26), random.Random(i + 7).uniform(0, 60))
                      for i in range(24)]
        self.frame_ms = 0.0
        self.bind("<Configure>", self._on_configure)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: setattr(self, "hover", None))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._reset_view)
        self.bind("<MouseWheel>", lambda e: self._zoom(1.08 if e.delta > 0 else 0.93))
        self.bind("<Button-4>", lambda e: self._zoom(1.08))
        self.bind("<Button-5>", lambda e: self._zoom(0.93))
        self._built = False

    # ------------------------------------------------------------ public
    def set_status(self, status: dict[str, Status], stats: Optional[dict] = None) -> None:
        self.status = status
        if stats is not None:
            self.stats = stats

    def select(self, part: Optional[str]) -> None:
        self.selected = part

    def set_scanning(self, on: bool) -> None:
        self.scanning = on

    def screen_pos(self, pid: str) -> tuple[float, float]:
        """Where a part currently is on the canvas (used by tests and the hint animation)."""
        x, y, _ = self.cam.project(g.centre_of(pid))
        return x, y

    def start(self) -> None:
        if self._job is None:
            self._job = self.after(FPS_MS, self._tick)

    def stop(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    # ------------------------------------------------------------ input
    def _on_configure(self, _e) -> None:
        self.cam.width, self.cam.height = max(self.winfo_width(), 120), max(self.winfo_height(), 120)
        self._built = True
        self._build_static()

    def _build_static(self) -> None:
        self.delete("hud")
        w, h = self.cam.width, self.cam.height
        arm = 26
        for px, py, dx, dy in ((10, 10, 1, 1), (w - 10, 10, -1, 1), (10, h - 10, 1, -1), (w - 10, h - 10, -1, -1)):
            self.create_line(px, py + dy * arm, px, py, px + dx * arm, py, fill=blend(BG, CYAN, 0.55), width=2, tags="hud")
        self.live_dot = self.create_oval(22, 22, 30, 30, fill=GREEN, outline="", tags="hud")
        self.create_text(38, 26, text="LIVE", anchor="w", fill=blend(BG, CYAN, 0.8), font=("Consolas", -12, "bold"), tags="hud")
        self.hud_time = self.create_text(w - 22, 26, text="", anchor="e", fill=blend(BG, CYAN, 0.8), font=("Consolas", -12, "bold"), tags="hud")
        self.create_text(w / 2, h - 18, text="DRAG TO ROTATE   ·   SCROLL TO ZOOM   ·   CLICK A PART   ·   DOUBLE-CLICK TO RESET",
                         fill=blend(BG, CYAN, 0.45), font=("Segoe UI", -11, "bold"), tags="hud")
        self.tag_lower("hud")

    def _on_press(self, e) -> None:
        self._press, self._moved = (e.x, e.y), False

    def _on_drag(self, e) -> None:
        if self._press is None:
            return
        dx, dy = e.x - self._press[0], e.y - self._press[1]
        if not self._moved and abs(dx) + abs(dy) < 5:
            return
        self._moved = True
        self.yaw0 += (e.x - self._mouse[0] if self._mouse != (0, 0) else dx) * 0.009
        self.pitch0 = max(0.1, min(1.25, self.pitch0 + (e.y - self._mouse[1]) * 0.006))
        self._mouse = (e.x, e.y)
        self.last_drag = time.time()
        self.configure(cursor="fleur")

    def _on_release(self, e) -> None:
        if not self._moved:
            pid = g.pick(self._drawn, e.x, e.y)
            if pid:
                self.select(pid)
                self.on_select(pid)
        self._press, self._moved = None, False
        self._mouse = (e.x, e.y)
        self.configure(cursor="")

    def _on_motion(self, e) -> None:
        self._mouse = (e.x, e.y)
        self.hover = g.pick(self._drawn, e.x, e.y)
        self.configure(cursor="hand2" if self.hover else "")

    def _zoom(self, k: float) -> None:
        self.cam.zoom = max(0.6, min(2.2, self.cam.zoom * k))

    def _reset_view(self, _e=None) -> None:
        self.yaw0, self.pitch0, self.cam.zoom = 0.0, 0.43, 1.0

    # ------------------------------------------------------------ drawing helpers
    def _k(self, p: g.Vec) -> float:
        """Pixels per world unit at p (perspective), for sizing text."""
        _, _, depth = self.cam.project(p)
        return self.cam.scale * self.cam.dist / max(depth, 1.0)

    def _poly(self, pts, fill: str, outline: str, width: float = 1.2, tags: str = "scene") -> list[float]:
        flat: list[float] = []
        for p in pts:
            x, y, _ = self.cam.project(p)
            flat += [x, y]
        self.create_polygon(flat, fill=fill, outline=outline, width=width, tags=tags)
        return flat

    def _line(self, pts, colour: str, width: float = 1.0, dash=None) -> None:
        flat: list[float] = []
        for p in pts:
            x, y, _ = self.cam.project(p)
            flat += [x, y]
        self.create_line(flat, fill=colour, width=width, dash=dash, tags="scene")

    def _solid(self, faces, colour: str, pid: Optional[str] = None, pulse: float = 0.0, boost: float = 1.0,
               edge: Optional[str] = None, width: float = 1.3) -> None:
        vis = [f for f in faces if self.cam.faces_toward_viewer(f)]
        vis.sort(key=lambda f: -self.cam.project(f.centre)[2])
        for f in vis:
            t = (0.10 + 0.34 * g.light(f.n)) * boost + 0.12 * pulse
            flat = self._poly(f.pts, blend(FILL, colour, t), edge or blend(colour, "#ffffff", 0.25 * pulse), width)
            if pid:
                self._drawn.append((pid, flat))

    # ------------------------------------------------------------ the frame
    def _tick(self) -> None:
        self._job = self.after(FPS_MS if self.active() else 400, self._tick)
        if not self._built or not self.active():
            return
        t0 = time.time()
        try:
            self._frame(t0)
        except tk.TclError:
            return
        self.frame_ms = (time.time() - t0) * 1000

    def _frame(self, now: float) -> None:
        t = now - self.t0
        dt = min(0.1, now - getattr(self, "_last", now))
        self._last = now
        cam = self.cam
        idle = min(1.0, (now - self.last_drag) / 4.0)
        cam.yaw = self.yaw0 + 0.34 * idle * math.sin(t * 0.32)
        cam.pitch = self.pitch0 + 0.025 * math.sin(t * 0.21)
        cpu = float(self.stats.get("cpu") or 0) / 100
        self.fan_angle += dt * (2.0 + cpu * 14 + (6 if self.status["fan"].level != OK else 0))
        self.delete("scene")
        self._drawn = []
        grow = {pid: 1.0 for pid in g.PART_IDS}
        if not self.booted:
            for i, pid in enumerate(g.BOOT_ORDER):
                grow[pid] = ease((now - self.boot_t0 - 0.06 * i) / 0.75)
            self.booted = now - self.boot_t0 > 0.06 * len(g.BOOT_ORDER) + 0.9
        self._floor(t)
        # base, board, ground rings and traces
        self._solid(g.base_faces(), CYAN, boost=0.5, edge=blend(BG, DIM, 1.0), width=1.4)
        board_c = LEVEL[self.status["board"].level]
        bp = self._pulse(self.status["board"], t)
        self._solid(g.board_faces(), board_c, "board", bp, boost=0.55, edge=blend(board_c, BG, 0.2 + 0.3 * (1 - bp)), width=1.6)
        for pid, st in self.status.items():
            if st.level != OK and pid not in NO_RING:
                self._ground_ring(pid, st, t)
        self._traces(t)
        self._scan(t)
        # solids, far to near
        order = sorted((p for p in g.PART_IDS if p not in ("board", "keyboard")), key=lambda p: -cam.project(g.centre_of(p))[2])
        for pid in order:
            self._part(pid, grow[pid], t)
        if grow["keyboard"] > 0.3:
            self._keyboard(grow["keyboard"], t)
        self._ring(t)
        self._labels(grow, t)
        self._hud(t)
        self._tip()

    def _pulse(self, st: Status, t: float) -> float:
        if st.level == OK:
            return 0.0
        rate = 2.2 if st.level == CRIT else 1.2
        return 0.5 + 0.5 * math.sin(t * math.pi * 2 * rate)

    def _floor(self, t: float) -> None:
        y = -3.6
        col = blend(BG, DIM, 0.35)
        for i in range(-6, 7):
            self._line([(i * 20, y, -120), (i * 20, y, 120)], col, 1)
            self._line([(-120, y, i * 20), (120, y, i * 20)], col, 1)
        for r in (80, 105, 130):
            pts = [(r * math.cos(a / 40 * 2 * math.pi), y, r * math.sin(a / 40 * 2 * math.pi)) for a in range(41)]
            self._line(pts, blend(BG, CYAN, 0.28), 1, dash=(2, 6))

    def _ground_ring(self, pid: str, st: Status, t: float) -> None:
        x, y, z = g.centre_of(pid)
        ph = (t * (1.0 if st.level == CRIT else 0.6)) % 1.0
        r = 6 + 12 * ph
        pts = [(x + r * math.cos(a / 24 * 2 * math.pi), g.BOARD_Y + 0.2, z + r * math.sin(a / 24 * 2 * math.pi)) for a in range(25)]
        self._line(pts, blend(LEVEL[st.level], BG, ph), 2)

    def _traces(self, t: float) -> None:
        cpu = float(self.stats.get("cpu") or 0) / 100
        speed = 0.18 + cpu * 0.7 + (0.5 if self.scanning else 0)
        for ti, (owner, pts) in enumerate(g.TRACES):
            st = self.status[owner]
            colour = LEVEL[st.level]
            line = [(x, g.BOARD_Y + 0.12, z) for x, z in pts]
            self._line(line, blend(BG, colour, 0.4 if st.level == OK else 0.85), 1.6)
            lens = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
            total = sum(lens) or 1
            for j in range(3):
                u = (t * speed * (0.55 if st.level == OK else 1.4) + j / 3 + ti * 0.17) % 1.0
                d, k = u * total, 0
                while k < len(lens) - 1 and d > lens[k]:
                    d -= lens[k]
                    k += 1
                f = d / lens[k] if lens[k] else 0
                x = pts[k][0] + (pts[k + 1][0] - pts[k][0]) * f
                z = pts[k][1] + (pts[k + 1][1] - pts[k][1]) * f
                sx, sy, _ = self.cam.project((x, g.BOARD_Y + 0.4, z))
                r = max(2.0, 0.85 * self._k((x, 1, z)) * (1.3 if st.level != OK else 1))
                self.create_oval(sx - r, sy - r, sx + r, sy + r, fill=colour, outline="", tags="scene")

    def _scan(self, t: float) -> None:
        u = (t * (0.5 if self.scanning else 0.2)) % 1.0
        z = -24 + 51 * u
        self._line([(-42, g.BOARD_Y + 0.15, z), (42, g.BOARD_Y + 0.15, z)], "#ffffff" if self.scanning else blend(BG, CYAN, 0.55),
                   2.2 if self.scanning else 1.6)

    # ---- each part
    def _part(self, pid: str, grow: float, t: float) -> None:
        st = self.status[pid]
        colour = LEVEL[st.level]
        pulse = self._pulse(st, t)
        hot = pid == self.selected or pid == self.hover
        edge = "#ffffff" if pid == self.selected else blend(colour, "#ffffff", 0.35 if hot else 0.25 * pulse)
        width = 2.6 if hot else 1.0 + 1.4 * pulse + 0.4
        self._solid(g.part_faces(pid, grow), colour, pid, pulse, 1.0 + (0.25 if hot else 0), edge, width)
        if grow < 0.9:
            return
        top = g.BOARD_Y
        if pid == "cpu":
            h = g.SOLIDS["cpu"][4]
            self._solid(g.aabb(-2, 6, top + h, top + h + 0.8, -6, 2), colour, None, pulse, 1.3, blend(colour, "#ffffff", 0.3), 1)
        elif pid == "gpu":
            h = g.SOLIDS["gpu"][4]
            for cx in (23.0, 31.0):
                self._solid(g.cylinder(cx, -6.0, top + h, top + h + 0.6, 3.8, 12), colour, None, pulse, 0.7, blend(colour, BG, 0.3), 1)
                self._blades(cx, -6.0, top + h + 0.7, 3.0, self.fan_angle * 0.6, colour, 3)
        elif pid == "ram":
            for x0, x1, z0, z1 in g.RAM_STICKS:
                for i in range(3):
                    cx = x0 + 3 + i * 6.4
                    self._solid(g.aabb(cx, cx + 4.6, top + 1.8, top + 2.4, z0 + 0.6, z1 - 0.6), colour, None, pulse, 0.9, blend(colour, BG, 0.3), 1)
        elif pid == "ssd":
            h = g.SOLIDS["ssd"][4]
            for z0 in (0.0, 4.5):
                self._solid(g.aabb(-35, -21, top + h, top + h + 0.5, z0, z0 + 3.6), colour, None, pulse, 0.9, blend(colour, BG, 0.3), 1)
        elif pid == "battery":
            x0, x1, z0, z1, h = g.SOLIDS["battery"]
            batt = self.stats.get("battery")
            frac = 1.0 if batt is None else max(0.03, min(1.0, float(batt) / 100))
            low = batt is not None and batt <= 20 and not self.stats.get("plugged")
            fc = AMBER if low else GREEN
            self._solid(g.aabb(x0 + 1.5, x0 + 1.5 + (x1 - x0 - 3) * frac, top + h, top + h + 0.6, z0 + 1.8, z1 - 1.8), fc, None, 0, 1.6, fc, 1)
        elif pid == "fan":
            cx, cz, r, h = g.FAN
            self._blades(cx, cz, top + h + 0.05, r - 1.2, self.fan_angle, colour, 5)
        elif pid == "network":
            x0, x1, z0, z1, h = g.SOLIDS["network"]
            cx = (x0 + x1) / 2
            for rr in (2.4, 4.4, 6.4):
                pts = [(cx + rr * math.cos(math.radians(a)), top + h + 0.8 + rr * math.sin(math.radians(a)), z1 + 0.5) for a in range(40, 141, 10)]
                self._line(pts, colour, 2)
        elif pid == "display":
            self._screen(st, colour, t)
        elif pid == "webcam":
            if self._screen_faces_viewer():
                x, y, _ = self.cam.project(g.screen_point(0, g.SCREEN_H - 1.75, 0.6))
                r = max(2.0, 0.55 * self._k(g.screen_point(0, g.SCREEN_H - 1.75)))
                self.create_oval(x - r, y - r, x + r, y + r, fill=BG, outline=colour, width=1.5, tags="scene")
                self.create_oval(x - r * 0.45, y - r * 0.45, x + r * 0.45, y + r * 0.45, fill=colour if st.level != OK else GREEN, outline="", tags="scene")
        elif pid == "speakers":
            y = g.SPEAKER_H + 0.05
            for cx, cz in g.SPEAKERS:
                for rr in (2.3, 1.2):
                    self._line([(cx + rr * math.cos(a / 14 * 2 * math.pi), y, cz + rr * math.sin(a / 14 * 2 * math.pi)) for a in range(15)], colour, 1)
        elif pid == "touchpad":
            x0, x1, z0, z1, h = g.SOLIDS["touchpad"]
            y = top + h + 0.05
            self._line([(x0 + 1, y, z0 + 1), (x1 - 1, y, z0 + 1), (x1 - 1, y, z1 - 1), (x0 + 1, y, z1 - 1), (x0 + 1, y, z0 + 1)], colour, 1)
            self._line([(0, y, z1 - 3.2), (0, y, z1 - 1)], colour, 1)
        elif pid == "bluetooth":
            x0, x1, z0, z1, h = g.SOLIDS["bluetooth"]
            cx, cz, y = (x0 + x1) / 2, (z0 + z1) / 2, top + h + 0.05
            glyph = [(-1.0, 0.9), (1.0, -0.9), (0.0, -1.7), (0.0, 1.7), (1.0, 0.9), (-1.0, -0.9)]
            self._line([(cx + a * 1.1, y, cz + b * 0.9) for a, b in glyph], colour, 1.6)
        elif pid in g.MODULES:
            for x0, x1, y0, y1, z0, z1 in g.MODULES[pid]:
                self._poly([(x0 + 1.2, y1 + 0.03, z0 + 1.4), (x1 - 1.2, y1 + 0.03, z0 + 1.4), (x1 - 1.2, y1 + 0.03, z1 - 1.4),
                            (x0 + 1.2, y1 + 0.03, z1 - 1.4)], BG, colour, 1)

    def _screen_faces_viewer(self) -> bool:
        front = next((f for f in g.screen_faces() if abs(f.n[1] - g.NORMAL[1]) < 1e-6 and abs(f.n[2] - g.NORMAL[2]) < 1e-6), None)
        return bool(front) and self.cam.faces_toward_viewer(front)

    def _keyboard(self, grow: float, t: float) -> None:
        """The keyboard is a see-through layer of key outlines hovering over the parts, so nothing is hidden under it."""
        st = self.status["keyboard"]
        colour = LEVEL[st.level]
        pulse = self._pulse(st, t)
        hot = "keyboard" in (self.selected, self.hover)
        strength = (0.30 + 0.55 * pulse + (0.35 if hot else 0)) * ease(grow)
        line = blend(BG, colour, min(1.0, strength))
        for quad in g.key_quads():
            self._line(quad + [quad[0]], line, 1)
        y = g.KB_Y
        frame = [(-g.KB_X, y, g.KB_Z0), (g.KB_X, y, g.KB_Z0), (g.KB_X, y, g.KB_Z1), (-g.KB_X, y, g.KB_Z1)]
        flat: list[float] = []
        for p in frame:
            x, yy, _ = self.cam.project(p)
            flat += [x, yy]
        self.create_line(flat + flat[:2], fill="#ffffff" if "keyboard" == self.selected else blend(BG, colour, min(1.0, strength + 0.2)),
                         width=2 if hot else 1.4, tags="scene")
        self._drawn.insert(0, ("keyboard", flat))          # first = lowest priority: any part under the cursor wins over the keys

    def _blades(self, cx: float, cz: float, y: float, r: float, ang: float, colour: str, n: int) -> None:
        for i in range(n):
            a = ang + i * 2 * math.pi / n
            self._line([(cx + math.cos(a) * 0.8, y, cz + math.sin(a) * 0.8), (cx + math.cos(a) * r, y, cz + math.sin(a) * r)], colour, 3)

    def _screen(self, st: Status, colour: str, t: float) -> None:
        faces = g.screen_faces()
        front = [f for f in faces if f.n == g.NORMAL or (abs(f.n[1] - g.NORMAL[1]) < 1e-6 and abs(f.n[2] - g.NORMAL[2]) < 1e-6)]
        if not front or not self.cam.faces_toward_viewer(front[0]):
            return
        quad = g.display_quad()
        self._poly(quad, "#050b1c", colour, 1.6)
        size = max(8, int(2.1 * self._k(g.screen_point(0, 30))))
        font = ("Consolas", -size, "normal")
        for i, (u, speed, off) in enumerate(self._rain):
            head = (off + t * speed * 2.2) % 70
            for k in range(4):
                v = 54 - ((head - k * 4.5) % 70)
                if 4.5 < v < 57.5:
                    sx, sy, _ = self.cam.project(g.screen_point(u, v, 0.1))
                    self.create_text(sx, sy, text=random.choice(HEX) if k == 0 or random.random() < 0.15 else "0", font=font,
                                     fill=blend("#050b1c", CYAN, 0.62 * (1 - k / 4) ** 1.4 + 0.06), tags="scene")
        tx, ty, _ = self.cam.project(g.screen_point(0, 36, 0.2))
        self.create_text(tx, ty, text="LAPTOP AI", fill=blend("#9fb4e8", "#ffffff", 0.5 + 0.5 * math.sin(t * 1.6)),
                         font=("Segoe UI", -max(10, int(7.2 * self._k(g.screen_point(0, 36)))), "bold"), tags="scene")
        bad = sum(1 for s in self.status.values() if s.level != OK)
        sx, sy, _ = self.cam.project(g.screen_point(0, 27, 0.2))
        self.create_text(sx, sy, text="ALL SYSTEMS NOMINAL" if not bad else f"{bad} PART{'S' if bad > 1 else ''} NEED ATTENTION",
                         fill=CYAN if not bad else (RED if any(s.level == CRIT for s in self.status.values()) else AMBER),
                         font=("Consolas", -max(8, int(2.7 * self._k(g.screen_point(0, 27)))), "bold"), tags="scene")

    # ---- overlays
    def _ring(self, t: float) -> None:
        """The overall-health ring floats beside the screen and always faces you."""
        levels = [s.level for s in self.status.values()]
        worst = CRIT if CRIT in levels else WARN if WARN in levels else OK
        bad = sum(1 for lv in levels if lv != OK)
        colour = GREEN if worst == OK else LEVEL[worst]
        cx, cy, _ = self.cam.project(g.RING_CENTRE)
        k = self._k(g.RING_CENTRE)
        r = g.RING_R * k
        self.create_oval(cx - r * 1.12, cy - r * 1.12, cx + r * 1.12, cy + r * 1.12, outline=blend(BG, colour, 0.3), width=1, tags="scene")
        self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=colour, width=max(2.0, 0.42 * k), tags="scene")
        self.create_text(cx, cy, text="OK" if not bad else str(bad), fill=colour, font=("Segoe UI", -max(12, int(8 * k)), "bold"), tags="scene")
        self.create_text(cx, cy + r + 12, text="SYSTEM" if not bad else ("ISSUE" if bad == 1 else "ISSUES"), fill=colour if bad else CYAN,
                         font=("Segoe UI", -max(9, int(3.0 * k)), "bold"), tags="scene")

    def _labels(self, grow: dict, t: float) -> None:
        for pid in g.PART_IDS:
            if grow[pid] < 0.95 or pid in ("display",):
                continue
            st = self.status[pid]
            colour = LEVEL[st.level]
            x, y, z = g.top_of(pid)
            if pid in MINOR and st.level == OK and pid not in (self.hover, self.selected):
                continue
            if self.cam.scale < 3.8 and st.level == OK and pid not in (self.hover, self.selected):
                continue                      # a small window: label only what needs attention (hover shows the rest)
            if pid == "board":
                px, py, _ = self.cam.project((0, g.BOARD_Y, 29.5))
                self.create_text(px, py, text="MOTHERBOARD", fill=colour, font=("Segoe UI", -max(9, int(2.9 * self._k((0, 1, 29)))), "bold"), tags="scene")
                continue
            k = self._k((x, y, z))
            fs = max(9, int(3.0 * k))
            lift = 3.4 if pid != "security" else 17
            px, py, _ = self.cam.project((x, y + lift, z))
            name = SHORT.get(pid, BY_ID[pid].label).upper()
            text = f"{name} {st.value}" if st.value else name
            self.create_text(px, py, text=text, fill=colour if st.level != OK else "#d5f3ff", font=("Segoe UI", -fs, "bold"), tags="scene")
            if st.level != OK:
                bx, by, _ = self.cam.project((x, y + (9 if pid != "security" else 21), z))
                rr = max(7.0, 2.1 * k)
                self.create_oval(bx - rr, by - rr, bx + rr, by + rr, fill=colour, outline=BG, width=2, tags="scene")
                self.create_text(bx, by, text="!", fill="white", font=("Segoe UI", -int(rr * 1.4), "bold"), tags="scene")

    def _hud(self, t: float) -> None:
        self.itemconfigure(self.live_dot, fill=blend(BG, GREEN, 0.45 + 0.55 * (0.5 + 0.5 * math.sin(t * 3))))
        clock = time.strftime("%a %d %b  %H:%M:%S").upper()
        self.itemconfigure(self.hud_time, text=clock)

    def _tip(self) -> None:
        pid = self.hover
        if not pid or self._press is not None and self._moved:
            return
        st = self.status[pid]
        head = BY_ID[pid].label + (f"  {st.value}" if st.value else "")
        body = st.details[0] if st.details else "Normal"
        body = body if len(body) < 70 else body[:67] + "..."
        colour = LEVEL[st.level]
        w = max(len(head), len(body)) * 7.4 + 24
        x = min(self._mouse[0] + 16, max(10, self.cam.width - w - 6))
        y = max(6, self._mouse[1] - 54)
        self.create_rectangle(x, y, x + w, y + 46, fill="#0f1a38", outline=colour, tags="scene")
        self.create_text(x + 10, y + 13, text=head, anchor="w", fill="white", font=("Segoe UI", -13, "bold"), tags="scene")
        self.create_text(x + 10, y + 32, text=body, anchor="w", fill=colour, font=("Segoe UI", -12), tags="scene")
