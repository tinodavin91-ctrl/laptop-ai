"""Start Laptop AI when you sign in to Windows (a normal entry in your own startup list).

It only touches ONE value, "LaptopAI", under your user's Run key. No administrator rights are needed
and removing it is just turning the switch off again.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

try:
    import winreg
except ImportError:
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "LaptopAI"


def command() -> str:
    """What Windows should run: the installed .exe, or pythonw + run.py when running from source."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")                       # no console window
    runner = Path(__file__).resolve().parent.parent / "run.py"
    return f'"{pythonw if pythonw.exists() else exe}" "{runner}" --minimized'


def is_enabled(reg=None) -> bool:
    reg = reg or winreg
    if reg is None:
        return False
    try:
        key = reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY)
    except OSError:
        return False
    try:
        reg.QueryValueEx(key, VALUE)
        return True
    except OSError:
        return False
    finally:
        reg.CloseKey(key)


def set_enabled(on: bool, reg=None, cmd: Optional[str] = None) -> tuple[bool, str]:
    reg = reg or winreg
    if reg is None:
        return False, "Starting with Windows only works on Windows."
    try:
        key = reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE)
    except OSError as e:
        return False, f"Couldn't open your startup list ({e.__class__.__name__})."
    try:
        if on:
            reg.SetValueEx(key, VALUE, 0, reg.REG_SZ, cmd or command())
        else:
            try:
                reg.DeleteValue(key, VALUE)
            except FileNotFoundError:
                pass                                           # already off
        return True, ""
    except OSError as e:
        return False, f"Couldn't change your startup list ({e.__class__.__name__})."
    finally:
        reg.CloseKey(key)
