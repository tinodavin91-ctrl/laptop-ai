"""Advanced pack tests: all fakes (no real registry, power commands, network or screen)."""
import json
import os
from types import SimpleNamespace

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

import psutil  # noqa: E402
import pytest  # noqa: E402

from laptop_ai import config, notify, tools, tools_extra, tools_pro  # noqa: E402


def call(tool_name, **args):
    return json.loads(tools.REGISTRY[tool_name].func(**args))


def executor(mode="ask", answer=True):
    s = config.Settings()
    s.autonomy = mode
    asked = []
    return tools.Executor(s, lambda t: asked.append(t) or answer), asked


# ---------------------------------------------------------------- internet
def test_interpretation_names_the_real_problem():
    assert "fine" in tools_pro.interpret(True, True, True)
    assert "DNS" in tools_pro.interpret(False, True, True) and "1.1.1.1" in tools_pro.interpret(False, True, True)
    assert "firewall" in tools_pro.interpret(True, False, True)
    assert "No internet" in tools_pro.interpret(False, False, True)
    assert "not connected" in tools_pro.interpret(False, False, False)


def test_diagnose_internet_uses_the_checks(monkeypatch):
    monkeypatch.setattr(psutil, "net_if_stats", lambda: {"WiFi": SimpleNamespace(isup=True, speed=72)})
    monkeypatch.setattr(tools_pro, "_dns_ok", lambda *a: False)
    monkeypatch.setattr(tools_pro, "_tcp_ok", lambda *a, **k: True)
    r = call("diagnose_internet")
    assert r["dns_works"] is False and r["can_reach_internet"] is True and "DNS" in r["diagnosis"]
    assert "download_speed" not in r
    monkeypatch.setattr(tools_pro, "_dns_ok", lambda *a: True)
    monkeypatch.setattr(tools_pro, "_speed_test", lambda: "70 KB/s")
    assert call("diagnose_internet", speed=True)["download_speed"] == "70 KB/s"


def test_speed_test_reports_kb_per_second_and_failures():
    class R:
        content = b"x" * 1_000_000
    assert "KB/s" in tools_pro._speed_test(lambda *a, **k: R())

    def boom(*a, **k):
        raise OSError("x")
    assert "could not measure" in tools_pro._speed_test(boom)


# ---------------------------------------------------------------- security
def test_judge_process():
    assert tools_pro.judge_process("svchost.exe", r"C:\Users\x\AppData\Local\Temp\svchost.exe")
    assert not tools_pro.judge_process("svchost.exe", r"C:\Windows\System32\svchost.exe")
    assert tools_pro.judge_process("update.exe", r"C:\Users\x\Downloads\update.exe")
    assert not tools_pro.judge_process("chrome.exe", r"C:\Program Files\Google\Chrome\chrome.exe")
    assert not tools_pro.judge_process("x", "")                       # unknown path is not a warning by itself
    assert len(tools_pro.judge_process("a.exe", "C:/Users/x/Downloads/a.exe", cpu=90)) == 2


def test_security_check_flags_only_suspicious(monkeypatch):
    def proc(pid, name, exe, cpu=0):
        return SimpleNamespace(pid=pid, info={"name": name, "exe": exe, "cpu_percent": cpu})
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [
        proc(1, "chrome.exe", r"C:\Program Files\Chrome\chrome.exe"),
        proc(2, "lsass.exe", r"C:\Users\Public\lsass.exe")])
    monkeypatch.setattr(psutil, "net_connections", lambda kind="inet": [])
    r = call("security_check")
    assert [w["program"] for w in r["warning_signs"]] == ["lsass.exe"] and "not proof" in r["note"]


def test_list_connections_handles_blocked_lookup(monkeypatch):
    def blocked(kind="inet"):
        raise psutil.AccessDenied()
    monkeypatch.setattr(psutil, "net_connections", blocked)
    assert call("list_connections")["status"] == "error"


# ---------------------------------------------------------------- big files
def test_find_large_files(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_extra.Path, "home", classmethod(lambda cls: tmp_path))
    for name, size in (("a.bin", 5000), ("b.bin", 90000), ("c.bin", 300)):
        p = tmp_path / "Downloads" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
    r = call("find_large_files", limit=2)
    assert [os.path.basename(x["path"]) for x in r["largest"]] == ["b.bin", "a.bin"]
    assert call("find_large_files", folder="Music")["status"] == "error"


# ---------------------------------------------------------------- startup manager
class FakeReg:
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, KEY_SET_VALUE, REG_SZ = "HKCU", "HKLM", 2, 1

    def __init__(self):
        self.data = {"HKCU": {"Spotify": r"C:\s\spotify.exe", "Teams": r"C:\t\teams.exe"},
                     "HKLM": {"Defender": r"C:\d\def.exe"}}

    def OpenKey(self, hive, path, reserved=0, access=0):
        return SimpleNamespace(hive=hive)

    def CloseKey(self, key):
        pass

    def EnumValue(self, key, i):
        items = list(self.data[key.hive].items())
        if i >= len(items):
            raise OSError
        return items[i][0], items[i][1], 1

    def DeleteValue(self, key, name):
        del self.data[key.hive][name]

    def SetValueEx(self, key, name, _r, _t, value):
        self.data[key.hive][name] = value


@pytest.fixture
def reg(monkeypatch, tmp_path):
    fake = FakeReg()
    monkeypatch.setattr(tools_pro, "winreg", fake)
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    return fake


def test_startup_list_marks_what_can_be_changed(reg):
    items = {i["name"]: i for i in call("list_startup_items")["items"]}
    assert items["Spotify"]["can_disable"] and not items["Defender"]["can_disable"]


def test_disable_then_restore_a_startup_item(reg):
    assert call("disable_startup_item", name="spotify")["disabled"] == "Spotify"
    assert "Spotify" not in reg.data["HKCU"] and "Teams" in reg.data["HKCU"]
    assert "Spotify" in call("list_startup_items")["turned_off_by_this_app"]
    assert call("enable_startup_item", name="Spotify")["enabled"] == "Spotify"
    assert reg.data["HKCU"]["Spotify"] == r"C:\s\spotify.exe"
    assert call("enable_startup_item", name="Spotify")["status"] == "error"        # nothing left to restore


def test_cannot_disable_all_users_items_or_unknown_ones(reg):
    assert call("disable_startup_item", name="Defender")["status"] == "error"
    assert reg.data["HKLM"] == {"Defender": r"C:\d\def.exe"}
    assert call("disable_startup_item", name="nope")["status"] == "error"


def test_disable_always_asks(reg):
    ex, asked = executor("auto_safe")
    ex.run("disable_startup_item", {"name": "Spotify"}, source="scheduler")
    assert len(asked) == 1 and "Spotify" in asked[0]


# ---------------------------------------------------------------- power
def test_power_commands_delays_and_confirmation(monkeypatch):
    seen = []
    monkeypatch.setattr(tools_pro, "RUNNER", lambda argv, **k: seen.append(argv))
    assert call("power_action", action="restart", delay_seconds=2)["in_seconds"] == 10           # never less than 10 s
    assert call("power_action", action="shutdown", delay_seconds=99999)["in_seconds"] == 3600
    assert call("power_action", action="cancel")["done"] == "cancel"
    assert call("power_action", action="lock")["done"] == "lock"
    assert call("power_action", action="format c:")["status"] == "error"
    assert seen[0] == ["shutdown", "/r", "/t", "10"] and seen[2] == ["shutdown", "/a"]
    assert len(seen) == 4
    ex, asked = executor("auto_safe")
    ex.run("power_action", {"action": "shutdown", "delay_seconds": 60}, source="scheduler")
    assert asked and "60 seconds" in asked[0] and "Unsaved" in asked[0]


def test_power_is_hidden_from_the_phone(monkeypatch):
    ex, asked = executor()
    ex.override = "read_only"
    assert "power_action" not in [t.name for t in ex.available()]
    assert "can't make changes" in json.loads(ex.run("power_action", {"action": "shutdown"}))["message"]
    assert asked == []


def test_power_refuses_off_windows():
    assert call("power_action", action="lock")["status"] == "error"


# ---------------------------------------------------------------- screenshot
def test_screenshot_saves_a_png(tmp_path, monkeypatch):
    from PIL import Image, ImageGrab
    monkeypatch.setattr(tools_extra.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(ImageGrab, "grab", lambda *a, **k: Image.new("RGB", (20, 10), "blue"))
    r = call("take_screenshot")
    assert r["status"] == "ok" and r["saved"].endswith(".png") and os.path.getsize(r["saved"]) > 0


def test_screenshot_failure_is_an_error_not_a_crash(monkeypatch):
    from PIL import ImageGrab

    def boom(*a, **k):
        raise OSError("no screen")
    monkeypatch.setattr(ImageGrab, "grab", boom)
    assert call("take_screenshot")["status"] == "error"


# ---------------------------------------------------------------- phone buttons
TOKEN = "123456789:" + "A" * 35


class Resp:
    def __init__(self, data):
        self._d, self.status_code = data, 200

    def json(self):
        return self._d


class FakePost:
    def __init__(self):
        self.sent, self.updates = [], []

    def __call__(self, url, json=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        self.sent.append((method, json))
        if method == "getUpdates":
            return Resp({"ok": True, "result": self.updates})
        return Resp({"ok": True, "result": {}})


def phone(chat="42"):
    s = config.Settings()
    s.telegram_chat_id = chat
    fp = FakePost()
    got = []
    p = notify.PhoneQuestions(notify.Telegram(s, fp, lambda: TOKEN), lambda t: "ai", [("📊 Status", "status"),
                              ("🌐 Internet", "internet"), ("🛡 Security", "security")],
                              lambda k: got.append(k) or f"result for {k}")
    return p, fp, got


def tap(update_id, chat, data, kind="private"):
    return {"update_id": update_id, "callback_query": {"id": "cb1", "data": data,
                                                       "message": {"chat": {"id": chat, "type": kind}}}}


def test_menu_command_shows_buttons_two_per_row():
    p, fp, _ = phone()
    fp.updates = [{"update_id": 1, "message": {"chat": {"id": 42, "type": "private"}, "text": "/menu"}}]
    p.poll_once()
    send = [j for m, j in fp.sent if m == "sendMessage"][0]
    rows = send["reply_markup"]["inline_keyboard"]
    assert [len(r) for r in rows] == [2, 1] and rows[0][0]["callback_data"] == "status"


def test_tapping_a_button_runs_the_handler_and_replies():
    p, fp, got = phone()
    fp.updates = [tap(1, 42, "internet")]
    assert p.poll_once() == 1
    assert got == ["internet"]
    methods = [m for m, _ in fp.sent]
    assert "answerCallbackQuery" in methods
    assert [j["text"] for m, j in fp.sent if m == "sendMessage"] == ["result for internet"]


def test_buttons_from_strangers_or_unknown_keys_do_nothing():
    p, fp, got = phone()
    fp.updates = [tap(1, 999, "status"), tap(2, 42, "status", kind="group"), tap(3, 42, "format_c")]
    p.poll_once()
    assert got == [] and not [1 for m, _ in fp.sent if m == "sendMessage"]


# ---------------------------------------------------------------- gui wiring (no window needed)
def test_every_phone_button_is_a_look_only_tool():
    pytest.importorskip("tkinter")
    pytest.importorskip("customtkinter")
    from laptop_ai import gui
    for key, (name, _args) in gui.PHONE_ACTIONS.items():
        assert tools.REGISTRY[name].risk == tools.READ, key
    assert {k for _l, k in gui.PHONE_BUTTONS} == set(gui.PHONE_ACTIONS) | {"playing", "ask_lock", "ask_sleep"}   # the extras are handled by hand
