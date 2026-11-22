"""3D maths and the laptop's parts for the home page (no windows here, so it is testable).

World units are roughly centimetres. x runs left to right, y is up and z points at the viewer.
The laptop base sits on y = 0, its screen is hinged at the back edge (z = -31) and tilts back.
Rendering is done by holo.py with a simple painter's algorithm: that is plenty for ~15 solids.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

Vec = tuple[float, float, float]
LIGHT = (-0.35, 0.82, 0.45)


def add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a: Vec, k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def unit(a: Vec) -> Vec:
    n = math.sqrt(dot(a, a)) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


_L = unit(LIGHT)


def light(n: Vec) -> float:
    """0..1 brightness of a face with outward normal n."""
    return max(0.0, dot(unit(n), _L))


@dataclass
class Face:
    pts: list[Vec]
    n: Vec

    @property
    def centre(self) -> Vec:
        k = 1 / len(self.pts)
        return (sum(p[0] for p in self.pts) * k, sum(p[1] for p in self.pts) * k, sum(p[2] for p in self.pts) * k)


class Camera:
    """Orbit camera looking at `target`. yaw turns around the vertical, pitch tips it down."""

    def __init__(self, width: float = 700, height: float = 500):
        self.yaw, self.pitch, self.zoom = 0.0, 0.43, 1.0
        self.dist = 330.0
        self.target: Vec = (0.0, 22.0, 0.0)
        self.width, self.height = width, height

    @property
    def scale(self) -> float:
        """Pixels per world unit at the target distance."""
        return min(self.width / 168.0, self.height / 108.0) * self.zoom

    def _rot(self, x: float, y: float, z: float) -> Vec:
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        x, z = x * cy + z * sy, -x * sy + z * cy
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        return (x, y * cp - z * sp, y * sp + z * cp)

    def rot_point(self, p: Vec) -> Vec:
        return self._rot(p[0] - self.target[0], p[1] - self.target[1], p[2] - self.target[2])

    def project(self, p: Vec) -> tuple[float, float, float]:
        x, y, z = self.rot_point(p)
        depth = self.dist - z
        k = self.scale * self.dist / max(depth, 1.0)
        return self.width / 2 + x * k, self.height / 2 - y * k, depth

    def faces_toward_viewer(self, face: Face) -> bool:
        nx, ny, nz = self._rot(*face.n)
        x, y, z = self.rot_point(face.centre)
        return nx * (0 - x) + ny * (0 - y) + nz * (self.dist - z) > 0


# ------------------------------------------------------------------ solids
def box(o: Vec, ax: Vec, ay: Vec, az: Vec) -> list[Face]:
    """A (possibly rotated) box with corner o and edge vectors ax, ay, az."""
    def c(i: int, j: int, k: int) -> Vec:
        return add(add(add(o, mul(ax, i)), mul(ay, j)), mul(az, k))
    nx, ny, nz = unit(ax), unit(ay), unit(az)
    return [
        Face([c(1, 0, 0), c(1, 1, 0), c(1, 1, 1), c(1, 0, 1)], nx), Face([c(0, 0, 0), c(0, 0, 1), c(0, 1, 1), c(0, 1, 0)], mul(nx, -1)),
        Face([c(0, 1, 0), c(0, 1, 1), c(1, 1, 1), c(1, 1, 0)], ny), Face([c(0, 0, 0), c(1, 0, 0), c(1, 0, 1), c(0, 0, 1)], mul(ny, -1)),
        Face([c(0, 0, 1), c(1, 0, 1), c(1, 1, 1), c(0, 1, 1)], nz), Face([c(0, 0, 0), c(0, 1, 0), c(1, 1, 0), c(1, 0, 0)], mul(nz, -1)),
    ]


def aabb(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> list[Face]:
    return box((x0, y0, z0), (x1 - x0, 0, 0), (0, y1 - y0, 0), (0, 0, z1 - z0))


def cylinder(cx: float, cz: float, y0: float, y1: float, r: float, seg: int = 20) -> list[Face]:
    faces = []
    ring = [(cx + r * math.cos(2 * math.pi * i / seg), cz + r * math.sin(2 * math.pi * i / seg)) for i in range(seg)]
    for i in range(seg):
        (xa, za), (xb, zb) = ring[i], ring[(i + 1) % seg]
        am = 2 * math.pi * (i + 0.5) / seg
        faces.append(Face([(xa, y0, za), (xb, y0, zb), (xb, y1, zb), (xa, y1, za)], (math.cos(am), 0.0, math.sin(am))))
    faces.append(Face([(x, y1, z) for x, z in ring], (0.0, 1.0, 0.0)))
    return faces


def prism(poly: Sequence[tuple[float, float]], z0: float, z1: float) -> list[Face]:
    """An upright shape given in the x/y plane, extruded between z0 and z1."""
    faces = [Face([(x, y, z1) for x, y in poly], (0.0, 0.0, 1.0)), Face([(x, y, z0) for x, y in reversed(poly)], (0.0, 0.0, -1.0))]
    for i in range(len(poly)):
        (xa, ya), (xb, yb) = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = xb - xa, yb - ya
        faces.append(Face([(xa, ya, z0), (xb, yb, z0), (xb, yb, z1), (xa, ya, z1)], unit((dy, -dx, 0.0))))
    return faces


# ------------------------------------------------------------------ the laptop
BASE_X, BASE_Z0, BASE_Z1 = 50.0, -31.0, 45.0
HINGE_Z, TILT = -31.0, math.radians(11)
SCREEN_W, SCREEN_H, SCREEN_T = 100.0, 62.0, 2.4
UP = (0.0, math.cos(TILT), -math.sin(TILT))            # along the screen, hinge to top
NORMAL = (0.0, math.sin(TILT), math.cos(TILT))          # out of the screen face, toward the viewer
BOARD_Y = 0.6
SHIELD = [(0.0, 13.0), (9.0, 9.5), (9.0, -2.0), (0.0, -13.0), (-9.0, -2.0), (-9.0, 9.5)]
RING_CENTRE, RING_R = (66.0, 26.0, -29.0), 12.0
SHIELD_CENTRE = (-66.0, 26.0, -29.0)

# part -> (x0, x1, z0, z1, height) for the simple flat-topped ones
SOLIDS = {
    "cpu": (-6, 10, -10, 6, 4.2), "gpu": (16, 38, -14, 2, 2.6), "ssd": (-38, -18, -2, 9, 1.2),
    "battery": (-38, -2, 14, 25, 2.0), "network": (33, 41, 10, 21, 1.4), "bluetooth": (33, 41, 22.5, 26.5, 1.0),
    "touchpad": (-14, 14, 32, 42, 0.5),
}
# parts made of several boxes: (x0, x1, y0, y1, z0, z1). The side ports float just outside the case like exploded modules.
MODULES = {
    "usb": [(-57, -51, -2.6, 0.2, 7, 14), (-57, -51, -2.6, 0.2, 16, 23), (51, 57, -2.6, 0.2, 12, 17.5)],
    "hdmi": [(-57, -51, -2.6, 0.2, -9, 1)],
    "charger": [(51, 57, -2.6, 0.2, -5, 3)],
}
SPEAKERS = [(-46.0, -10.0), (-46.0, 10.0), (46.0, -10.0), (46.0, 10.0)]    # driver centres (x, z)
SPEAKER_R, SPEAKER_H = 3.3, 1.6
KB_Y = 8.5                                                    # the keyboard layer floats above the internals
KB_X, KB_Z0, KB_Z1 = 42.0, -28.0, 9.0
# key rows in key-widths (1 = one key); every row adds up to 15
KEY_ROWS = [[1] * 15, [1] * 13 + [2], [1.5] + [1] * 12 + [1.5], [1.75] + [1] * 11 + [2.25], [2.25] + [1] * 10 + [2.75],
            [1.25, 1.25, 1.25, 1.25, 6.25, 1.25, 1.25, 1.25]]
RAM_STICKS = [(-38, -18, -18, -14), (-38, -18, -11, -7)]
FAN = (24.0, 14.0, 7.5, 2.4)                             # centre x, centre z, radius, height
PART_IDS = ("display", "webcam", "mic", "board", "cpu", "gpu", "ram", "ssd", "fan", "battery", "network", "bluetooth",
            "speakers", "touchpad", "keyboard", "usb", "hdmi", "charger", "security")
BOOT_ORDER = ("board", "display", "webcam", "mic", "cpu", "gpu", "ram", "ssd", "battery", "fan", "network", "bluetooth",
              "speakers", "touchpad", "keyboard", "usb", "hdmi", "charger", "security")

TRACES = [      # owner, points as (x, z) on the board
    ("ram", [(-6, -4), (-12, -4), (-12, -12), (-18, -12)]), ("ssd", [(-6, 2), (-18, 2)]),
    ("gpu", [(10, -6), (16, -6)]), ("fan", [(4, 6), (4, 14), (16.5, 14)]),
    ("battery", [(-2, 6), (-2, 14)]), ("network", [(10, 4), (37, 4), (37, 10)]),
    ("display", [(2, -10), (2, -31)]), ("bluetooth", [(10, 5), (12, 5), (12, 24.5), (33, 24.5)]),
    ("speakers", [(-42, -10), (-46, -10)]), ("speakers", [(-42, 10), (-46, 10)]),
    ("speakers", [(42, -10), (46, -10)]), ("speakers", [(42, 10), (46, 10)]),
    ("usb", [(-42, 10.5), (-51, 10.5)]), ("usb", [(-42, 19.5), (-51, 19.5)]), ("usb", [(42, 14.75), (51, 14.75)]),
    ("hdmi", [(-42, -4), (-51, -4)]), ("charger", [(42, -1), (51, -1)]), ("touchpad", [(0, 27), (0, 32)]),
]


def screen_point(u: float, v: float, w: float = 0.0) -> Vec:
    """A point on the screen: u across (-50..50), v up from the hinge (0..62), w out of the face (negative = inside)."""
    return (u, v * UP[1] + w * NORMAL[1], HINGE_Z + v * UP[2] + w * NORMAL[2])


def screen_faces() -> list[Face]:
    o = screen_point(-SCREEN_W / 2, 0, -SCREEN_T)
    return box(o, (SCREEN_W, 0, 0), mul(UP, SCREEN_H), mul(NORMAL, SCREEN_T))


def display_quad(inset: float = 3.5) -> list[Vec]:
    return [screen_point(-SCREEN_W / 2 + inset, inset), screen_point(SCREEN_W / 2 - inset, inset),
            screen_point(SCREEN_W / 2 - inset, SCREEN_H - inset), screen_point(-SCREEN_W / 2 + inset, SCREEN_H - inset)]


def base_faces() -> list[Face]:
    return aabb(-BASE_X, BASE_X, -3.0, 0.0, BASE_Z0, BASE_Z1)


def board_faces() -> list[Face]:
    return aabb(-42, 42, 0.0, BOARD_Y, -24, 27)


def part_faces(pid: str, grow: float = 1.0) -> list[Face]:
    """The solid(s) of one part. grow (0..1) raises it up from the board, used for the start-up animation."""
    g = max(0.02, min(1.0, grow))
    if pid == "display":
        return screen_faces()
    if pid == "board":
        return board_faces()
    if pid == "ram":
        out: list[Face] = []
        for x0, x1, z0, z1 in RAM_STICKS:
            out += aabb(x0, x1, BOARD_Y, BOARD_Y + 1.8 * g, z0, z1)
        return out
    if pid == "fan":
        cx, cz, r, h = FAN
        return cylinder(cx, cz, BOARD_Y, BOARD_Y + h * g, r)
    if pid == "webcam":
        return box(screen_point(-1.3, SCREEN_H - 2.6, 0.0), (2.6, 0, 0), mul(UP, 1.7), mul(NORMAL, 0.5 * g))
    if pid == "mic":
        out = []
        for u in (-9.0, 9.0):
            out += box(screen_point(u - 0.6, SCREEN_H - 2.3, 0.0), (1.2, 0, 0), mul(UP, 1.0), mul(NORMAL, 0.35 * g))
        return out
    if pid == "speakers":
        out = []
        for cx, cz in SPEAKERS:
            out += cylinder(cx, cz, 0.0, SPEAKER_H * g, SPEAKER_R)
        return out
    if pid in MODULES:
        out = []
        for x0, x1, y0, y1, z0, z1 in MODULES[pid]:
            out += aabb(x0, x1, y0, y0 + (y1 - y0) * g, z0, z1)
        return out
    if pid == "keyboard":
        return aabb(-KB_X, KB_X, KB_Y - 0.3, KB_Y, KB_Z0, KB_Z1)
    if pid == "security":
        return prism([(SHIELD_CENTRE[0] + x, SHIELD_CENTRE[1] + y) for x, y in SHIELD], SHIELD_CENTRE[2], SHIELD_CENTRE[2] + 3.0)
    x0, x1, z0, z1, h = SOLIDS[pid]
    return aabb(x0, x1, BOARD_Y, BOARD_Y + h * g, z0, z1)


def top_of(pid: str) -> Vec:
    """The point on top of a part where its value and label are written."""
    if pid == "display":
        return screen_point(0, SCREEN_H - 5, 0.1)
    if pid == "webcam":
        return screen_point(0, SCREEN_H - 1.7, 0.6)
    if pid == "mic":
        return screen_point(9, SCREEN_H - 1.8, 0.5)
    if pid == "speakers":
        return (SPEAKERS[1][0], SPEAKER_H, SPEAKERS[1][1])
    if pid == "keyboard":
        return (0.0, KB_Y, (KB_Z0 + KB_Z1) / 2)
    if pid in MODULES:
        x0, x1, y0, y1, z0, z1 = MODULES[pid][0]
        return ((x0 + x1) / 2, y1, (z0 + z1) / 2)
    if pid == "board":
        return (0.0, BOARD_Y, 27.0)
    if pid == "ram":
        return (-28.0, BOARD_Y + 1.8, -12.5)
    if pid == "fan":
        return (FAN[0], BOARD_Y + FAN[3], FAN[1])
    if pid == "security":
        return (SHIELD_CENTRE[0], SHIELD_CENTRE[1], SHIELD_CENTRE[2] + 3.1)
    x0, x1, z0, z1, h = SOLIDS[pid]
    return ((x0 + x1) / 2, BOARD_Y + h, (z0 + z1) / 2)


def centre_of(pid: str) -> Vec:
    if pid in ("display", "security", "board", "ram", "fan", "webcam", "mic", "speakers", "keyboard") or pid in MODULES:
        return top_of(pid)
    x0, x1, z0, z1, h = SOLIDS[pid]
    return ((x0 + x1) / 2, BOARD_Y + h / 2, (z0 + z1) / 2)


def point_in_poly(x: float, y: float, poly: Sequence[float]) -> bool:
    """poly is a flat list x0, y0, x1, y1, ..."""
    inside, n = False, len(poly) // 2
    j = n - 1
    for i in range(n):
        xi, yi, xj, yj = poly[2 * i], poly[2 * i + 1], poly[2 * j], poly[2 * j + 1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi:
            inside = not inside
        j = i
    return inside


def pick(drawn: Sequence[tuple[str, list[float]]], x: float, y: float) -> Optional[str]:
    """drawn is in painting order (far to near); the last polygon under the cursor wins."""
    for pid, poly in reversed(drawn):
        if point_in_poly(x, y, poly):
            return pid
    return None


def key_quads() -> list[list[Vec]]:
    """The keys as flat outlines on the keyboard layer."""
    rows, quads = len(KEY_ROWS), []
    pitch_x, pitch_z = 2 * KB_X / 15.0, (KB_Z1 - KB_Z0) / rows
    for r, row in enumerate(KEY_ROWS):
        z0, x = KB_Z0 + r * pitch_z, -KB_X
        for w in row:
            x1 = x + w * pitch_x
            quads.append([(x + 0.3, KB_Y, z0 + 0.35), (x1 - 0.3, KB_Y, z0 + 0.35), (x1 - 0.3, KB_Y, z0 + pitch_z - 0.35), (x + 0.3, KB_Y, z0 + pitch_z - 0.35)])
            x = x1
    return quads
