"""Health history: a reading of CPU, memory, disk and battery every few minutes, kept for 30 days.

Saved as one small CSV in ~/.laptop_ai. The home page's "Health history" window and the health_trend tool read it.
"""
from __future__ import annotations

import time
from typing import Optional

from . import config

FIELDS = ("cpu", "ram", "disk", "battery")
EVERY_S = 300                      # one reading per 5 minutes
KEEP_S = 30 * 86400
_last_saved = 0.0
_cache: dict = {"key": None, "rows": []}     # the parsed file, so opening the history window doesn't re-read it again and again


def _file():
    return config.APP_DIR / "trends.csv"


def record(stats: dict, now: Optional[float] = None, force: bool = False) -> bool:
    """Save a reading if 5 minutes have passed. Returns True when one was written."""
    global _last_saved
    now = now or time.time()
    if not force and now - _last_saved < EVERY_S:
        return False
    _last_saved = now
    row = [f"{now:.0f}"] + ["" if stats.get(k) is None else f"{float(stats[k]):.1f}" for k in FIELDS]
    try:
        config.ensure_dir()
        with open(_file(), "a", encoding="utf-8") as f:
            f.write(",".join(row) + "\n")
        if int(now) % 17 == 0:                       # now and then, drop readings older than 30 days
            _trim(now)
    except OSError:
        return False
    return True


def _trim(now: float) -> None:
    rows = load(30, now)
    try:
        _file().write_text("".join(",".join([f"{r['t']:.0f}"] + ["" if r[k] is None else f"{r[k]:.1f}" for k in FIELDS]) + "\n"
                                   for r in rows), encoding="utf-8")
    except OSError:
        pass


def _all_rows() -> list[dict]:
    """Every reading in the file, parsed once and reused until the file changes."""
    path = _file()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    if _cache["key"] == key:
        return _cache["rows"]
    rows = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) != 5:
            continue
        try:
            t = float(parts[0])
            vals = [float(p) if p else None for p in parts[1:]]
        except ValueError:
            continue
        rows.append({"t": t, **dict(zip(FIELDS, vals))})
    _cache["key"], _cache["rows"] = key, rows
    return rows


def load(days: float, now: Optional[float] = None) -> list[dict]:
    now = now or time.time()
    return [r for r in _all_rows() if now - r["t"] <= days * 86400]


def buckets(days: float, n: int = 60, now: Optional[float] = None) -> dict[str, list[Optional[float]]]:
    """Average the readings into n equal slices of the last `days` days (None where there is no data)."""
    now = now or time.time()
    span = days * 86400
    rows = load(days, now)
    out: dict[str, list[Optional[float]]] = {k: [None] * n for k in FIELDS}
    for k in FIELDS:
        sums, counts = [0.0] * n, [0] * n
        for r in rows:
            if r[k] is None:
                continue
            i = min(n - 1, int((r["t"] - (now - span)) / span * n))
            sums[i] += r[k]
            counts[i] += 1
        out[k] = [sums[i] / counts[i] if counts[i] else None for i in range(n)]
    return out


def _avg(rows: list[dict], key: str) -> Optional[float]:
    vals = [r[key] for r in rows if r[key] is not None]
    return sum(vals) / len(vals) if vals else None


def summary(days: int = 7, now: Optional[float] = None) -> str:
    """Plain words: averages over the period, and what changed compared with the period before it."""
    now = now or time.time()
    now_rows = load(days, now)
    if len(now_rows) < 6:
        return "I don't have enough history yet. Keep the app running and ask again in a day or two."
    before = [r for r in load(days * 2, now) if now - r["t"] > days * 86400]
    names = {"cpu": "CPU", "ram": "memory", "disk": "disk", "battery": "battery"}
    lines = [f"Over the last {days} day{'s' if days != 1 else ''} ({len(now_rows)} readings):"]
    for k in FIELDS:
        a = _avg(now_rows, k)
        if a is None:
            continue
        peak = max(r[k] for r in now_rows if r[k] is not None)
        line = f"- {names[k][0].upper() + names[k][1:]} averaged {a:.0f}% (peak {peak:.0f}%)"
        b = _avg(before, k) if len(before) >= 6 else None
        if b is not None and abs(a - b) >= 5:
            line += f", {'up' if a > b else 'down'} from {b:.0f}% the {days} days before"
        lines.append(line + ".")
    disk = [r["disk"] for r in now_rows if r["disk"] is not None]
    if len(disk) >= 6 and disk[-1] - disk[0] >= 3:
        lines.append(f"Your disk filled by {disk[-1] - disk[0]:.0f} points in that time. Try \"smart cleanup\".")
    return "\n".join(lines)
