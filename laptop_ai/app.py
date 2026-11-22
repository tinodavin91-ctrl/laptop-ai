"""Entry point used by both `python run.py` and the packaged .exe.

A windowed .exe has no console, so a crash would just vanish. Any startup error is written to
~/.laptop_ai/crash.log and shown in a message box instead."""
import sys
import traceback

from . import config


def _report(exc: BaseException) -> None:
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        config.APP_DIR.mkdir(parents=True, exist_ok=True)
        (config.APP_DIR / "crash.log").write_text(text, encoding="utf-8")
    except OSError:
        pass
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("Laptop AI couldn't start",
                             f"{exc.__class__.__name__}: {exc}\n\nDetails were saved to:\n{config.APP_DIR / 'crash.log'}")
        root.destroy()
    except Exception:
        print(text, file=sys.stderr)


def run(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        from . import singleton
        from .gui import main
        instance = singleton.Instance()
        if not instance.acquire():          # already running: it was asked to show itself, so just leave
            return 0
        main(start_hidden="--minimized" in argv, instance=instance)
        return 0
    except Exception as e:
        _report(e)
        return 1
