"""The little icon by the clock (system tray). Lets the app keep running (reminders, phone, "Trix")
while its window is closed, and shows pop-up notifications.

Needs `pystray`. If it is missing, closing the window simply quits, as before.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

try:
    import pystray
except Exception:                      # not installed, or no display/backend
    pystray = None

ICON = Path(__file__).parent / "assets" / "logo_small.png"


def available() -> bool:
    return pystray is not None


def make_image(mic_live: bool = False) -> Image.Image:
    try:
        img = Image.open(ICON).convert("RGBA").resize((64, 64))
    except OSError:
        img = Image.new("RGBA", (64, 64), (37, 99, 235, 255))
    if mic_live:                                           # a red dot: the microphone is on
        ImageDraw.Draw(img).ellipse((38, 38, 62, 62), fill=(239, 68, 68, 255), outline=(255, 255, 255, 255), width=3)
    return img


class Tray:
    def __init__(self, on_open: Callable[[], None], on_quit: Callable[[], None], lib=None):
        self.on_open, self.on_quit = on_open, on_quit
        self.lib = lib or pystray
        self.icon = None
        self.mic_live = False

    def start(self) -> bool:
        if self.lib is None or self.icon is not None:
            return self.icon is not None
        menu = self.lib.Menu(
            self.lib.MenuItem("Open Laptop AI", lambda *_: self.on_open(), default=True),
            self.lib.MenuItem("Quit", lambda *_: self.on_quit()))
        self.icon = self.lib.Icon("LaptopAI", make_image(), self._title(), menu)
        try:
            self.icon.run_detached()                       # runs in its own thread beside the window
        except Exception:
            self.icon = None
            return False
        return True

    def _title(self) -> str:
        return "Laptop AI" + (" - microphone is live" if self.mic_live else "")

    def set_mic(self, live: bool) -> None:
        self.mic_live = live
        if self.icon is not None:
            self.icon.icon = make_image(live)
            self.icon.title = self._title()

    def notify(self, title: str, message: str) -> None:
        if self.icon is not None:
            try:
                self.icon.notify(message[:240], title)
            except Exception:
                pass

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
