"""Start with Windows, one copy at a time, the tray icon. Fakes and local sockets only."""
import os
import socket
import sys
import time
from types import SimpleNamespace

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

from laptop_ai import app, autostart, singleton, tray  # noqa: E402


# ---------------------------------------------------------------- start with windows
class FakeReg:
    HKEY_CURRENT_USER, KEY_SET_VALUE, REG_SZ = "HKCU", 2, 1

    def __init__(self, fail_open=False):
        self.values, self.fail_open = {}, fail_open

    def OpenKey(self, hive, path, reserved=0, access=0):
        if self.fail_open:
            raise PermissionError
        assert hive == "HKCU" and path == autostart.RUN_KEY          # only the user's own startup list
        return object()

    def CloseKey(self, key):
        pass

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError
        return self.values[name], 1

    def SetValueEx(self, key, name, _r, _t, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError
        del self.values[name]


def test_autostart_on_off_and_only_touches_its_own_value():
    reg = FakeReg()
    reg.values["Other"] = "keep me"
    assert autostart.is_enabled(reg) is False
    assert autostart.set_enabled(True, reg, cmd='"C:\\app\\LaptopAI.exe" --minimized') == (True, "")
    assert autostart.is_enabled(reg) and reg.values["LaptopAI"].endswith("--minimized")
    assert autostart.set_enabled(False, reg) == (True, "")
    assert autostart.set_enabled(False, reg) == (True, "")               # already off is fine
    assert reg.values == {"Other": "keep me"}


def test_autostart_failures_are_messages_not_crashes():
    ok_, msg = autostart.set_enabled(True, FakeReg(fail_open=True))
    assert not ok_ and "startup list" in msg
    assert autostart.is_enabled(FakeReg(fail_open=True)) is False
    assert autostart.set_enabled(True, None)[0] in (True, False)         # no winreg off Windows: a message


def test_autostart_command_is_quoted_and_minimized(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Laptop AI\LaptopAI.exe")
    assert autostart.command() == '"C:\\Program Files\\Laptop AI\\LaptopAI.exe" --minimized'
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    cmd = autostart.command()
    assert cmd.endswith('run.py" --minimized') and cmd.startswith('"')


# ---------------------------------------------------------------- single instance
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_second_copy_cannot_start_and_wakes_the_first():
    port, shown = free_port(), []
    first, second = singleton.Instance(port), singleton.Instance(port)
    assert first.acquire() is True
    first.listen(lambda: shown.append(1))
    assert second.acquire() is False
    end = time.time() + 3
    while not shown and time.time() < end:
        time.sleep(0.02)
    assert shown == [1]
    first.release()
    third = singleton.Instance(port)                                      # after the first quits, a new one may start
    assert third.acquire() is True
    third.release()


def test_instance_only_listens_on_this_pc():
    inst = singleton.Instance(free_port())
    assert inst.acquire()
    assert inst._server.getsockname()[0] == "127.0.0.1"
    inst.release()


def test_run_exits_quietly_when_already_running(monkeypatch):
    import pytest
    pytest.importorskip("tkinter")
    pytest.importorskip("customtkinter")
    from laptop_ai import gui
    launched = []
    monkeypatch.setattr(singleton.Instance, "acquire", lambda self: False)
    monkeypatch.setattr(gui, "main", lambda **k: launched.append(k))
    assert app.run([]) == 0 and launched == []


def test_run_passes_minimized_flag_and_instance(monkeypatch):
    import pytest
    pytest.importorskip("tkinter")
    pytest.importorskip("customtkinter")
    from laptop_ai import gui
    launched = []
    monkeypatch.setattr(singleton.Instance, "acquire", lambda self: True)
    monkeypatch.setattr(gui, "main", lambda **k: launched.append(k))
    assert app.run(["--minimized"]) == 0
    assert launched[0]["start_hidden"] is True and launched[0]["instance"] is not None
    assert app.run([]) == 0 and launched[1]["start_hidden"] is False


# ---------------------------------------------------------------- tray (fake pystray)
class FakeIcon:
    def __init__(self, name, image, title, menu):
        self.name, self.icon, self.title, self.menu = name, image, title, menu
        self.running, self.notes = False, []

    def run_detached(self):
        self.running = True

    def stop(self):
        self.running = False

    def notify(self, message, title):
        self.notes.append((title, message))


FakeLib = SimpleNamespace(Icon=FakeIcon, Menu=lambda *items: list(items),
                          MenuItem=lambda text, fn, default=False: SimpleNamespace(text=text, fn=fn, default=default))


def test_tray_menu_start_notify_and_stop():
    opened, quit_ = [], []
    t = tray.Tray(lambda: opened.append(1), lambda: quit_.append(1), FakeLib)
    assert t.start() is True and t.icon.running
    open_item, quit_item = t.icon.menu
    assert open_item.text == "Open Laptop AI" and open_item.default is True
    open_item.fn(), quit_item.fn()
    assert opened == [1] and quit_ == [1]
    t.notify("Reminder", "stretch")
    assert t.icon.notes == [("Reminder", "stretch")]
    t.set_mic(True)
    assert "microphone is live" in t.icon.title
    t.set_mic(False)
    assert t.icon.title == "Laptop AI"
    t.stop()
    assert t.icon is None
    t.notify("x", "y")                                                    # harmless after stop


def test_tray_without_pystray_is_a_quiet_no():
    t = tray.Tray(lambda: None, lambda: None, lib=None)
    t.lib = None
    assert t.start() is False
    t.notify("a", "b")
    t.set_mic(True)


def test_tray_survives_a_backend_that_cannot_start():
    class Broken(FakeIcon):
        def run_detached(self):
            raise RuntimeError("no tray here")
    lib = SimpleNamespace(Icon=Broken, Menu=FakeLib.Menu, MenuItem=FakeLib.MenuItem)
    assert tray.Tray(lambda: None, lambda: None, lib).start() is False


def test_mic_live_icon_differs_from_normal():
    assert tray.make_image(True).tobytes() != tray.make_image(False).tobytes()
