"""2.8 tests: undo and history, smart cleanup and the safe bin, backup, windows, scenes, slow-internet, health history."""
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

import pytest  # noqa: E402

from laptop_ai import config, history, tools, tools_v3, trends  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "app" / "actions.log")
    monkeypatch.setattr(config, "MEMORY_FILE", tmp_path / "app" / "memory.json")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "app" / "settings.json")
    monkeypatch.setattr(config, "TASKS_FILE", tmp_path / "app" / "tasks.json")
    monkeypatch.setattr(tools, "SCHEDULER", None)
    user = tmp_path / "user"
    for d in ("Documents", "Downloads", "Desktop", "Pictures"):
        (user / d).mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    return user


def ran(name, **args):
    result = tools.REGISTRY[name].func(**args)
    tools._log("user", name, args, result)
    return result


# ---------------------------------------------------------------- history + undo
def test_undo_forgets_a_saved_fact_and_is_not_repeated(home):
    ran("remember", text="my wifi is Home5G")
    assert "Home5G" in json.dumps(config.read_json(config.MEMORY_FILE, []))
    assert "Forget" in history.last_undoable()[1][2]
    assert '"undone"' in tools.REGISTRY["undo_last_action"].func()
    assert config.read_json(config.MEMORY_FILE, []) == []
    assert "nothing I can undo" in tools.REGISTRY["undo_last_action"].func()


def test_undo_cancels_a_reminder_and_history_lists_it(home):
    ran("add_reminder", message="stretch", minutes=10)
    assert tools.get_scheduler().list()
    shown = json.loads(tools.REGISTRY["activity_history"].func())
    assert shown["can_undo"] == "Cancel what I scheduled" and "set a reminder" in shown["activity"][0].lower()
    tools.REGISTRY["undo_last_action"].func()
    assert tools.get_scheduler().list() == []


def test_undo_has_nothing_for_unreversible_actions(home):
    tools._log("user", "kill_process", {"name": "x"}, '{"status": "ok"}')
    tools._log("user", "remember", {"text": "a"}, '{"status": "error", "message": "no"}')
    assert history.last_undoable() is None


# ---------------------------------------------------------------- smart cleanup
def make(path: Path, mb: float, days_old: float = 0, text: bytes = b"x"):
    path.write_bytes((text * int(mb * 1048576 / len(text)))[:int(mb * 1048576)])
    t = time.time() - days_old * 86400
    os.utime(path, (t, t))


def test_find_junk_finds_duplicates_old_installers_and_big_old_files(home):
    make(home / "Documents" / "a.bin", 2, 5, b"abc")
    make(home / "Downloads" / "copy of a.bin", 2, 1, b"abc")                  # newer duplicate -> offered
    make(home / "Downloads" / "setup.exe", 6, 40, b"q")                       # old installer
    make(home / "Documents" / "movie.mkv", 4, 200, b"m")                      # big and untouched (threshold lowered)
    make(home / "Documents" / "fresh.txt", 2, 0, b"f")                       # fine
    found = tools_v3.scan_junk(tools_v3._roots(), big_mb=3)
    why = {os.path.basename(c["path"]): c["why"] for c in found}
    assert "same as a.bin" in why["copy of a.bin"] and "installer" in why["setup.exe"] and "untouched" in why["movie.mkv"]
    assert "a.bin" not in why and "fresh.txt" not in why


def test_trash_moves_only_inside_personal_folders_and_undo_restores(home, tmp_path):
    make(home / "Downloads" / "old.exe", 6, 100, b"q")
    outside = tmp_path / "windows_file.sys"
    outside.write_bytes(b"x" * 100)
    tools_v3.LAST_JUNK[:] = [{"path": str(home / "Downloads" / "old.exe"), "size": 6 << 20, "why": "old"},
                             {"path": str(outside), "size": 100, "why": "evil"}]
    res = json.loads(ran("move_to_trash", which="last"))
    assert res["moved"] == 1 and not (home / "Downloads" / "old.exe").exists() and outside.exists()
    assert tools.REGISTRY["move_to_trash"].describe({"which": "last"}).startswith("Move 0 files")
    assert '"undone"' in tools.REGISTRY["undo_last_action"].func()
    assert (home / "Downloads" / "old.exe").exists()
    assert "no files" in tools.REGISTRY["move_to_trash"].func(which="last").lower()


def test_old_trash_batches_are_purged(home):
    old = config.APP_DIR / "trash" / "b1"
    old.mkdir(parents=True)
    t = time.time() - 40 * 86400
    os.utime(old, (t, t))
    tools_v3._purge_old_trash()
    assert not old.exists()


# ---------------------------------------------------------------- backup
def test_backup_copies_new_files_only_and_needs_a_drive(home, tmp_path, monkeypatch):
    (home / "Documents" / "cv.txt").write_text("hello")
    (home / "Pictures" / "p.png").write_bytes(b"123")
    usb = tmp_path / "usb"
    usb.mkdir()
    monkeypatch.setattr(tools_v3, "DRIVES", lambda: [])
    assert "external drive" in tools_v3.backup_files()
    monkeypatch.setattr(tools_v3, "DRIVES", lambda: [str(usb)])
    first = json.loads(tools_v3.backup_files())
    assert first["copied"] == 2 and (usb / "Laptop_AI_Backup" / "Documents" / "cv.txt").read_text() == "hello"
    again = json.loads(tools_v3.backup_files())
    assert again["copied"] == 0 and again["already_there"] == 2
    (home / "Documents" / "cv.txt").write_text("hello world")
    os.utime(home / "Documents" / "cv.txt", (time.time() + 10, time.time() + 10))
    assert json.loads(tools_v3.backup_files(folders="Documents"))["copied"] == 1
    assert json.loads(tools_v3.find_drives())["drives"][0]["drive"] == str(usb)


# ---------------------------------------------------------------- windows
class FakeWin:
    def __init__(self):
        self.calls, self.keys = [], []

    def windows(self):
        return [{"hwnd": 1, "title": "Inbox - Google Chrome", "process": "chrome.exe"},
                {"hwnd": 2, "title": "main.py - Visual Studio Code", "process": "code.exe"},
                {"hwnd": 3, "title": "Laptop AI", "process": "python.exe", "pid": os.getpid()}]

    def do(self, hwnd, action):
        self.calls.append((hwnd, action))

    def hotkey(self, keys):
        self.keys.append(keys)


def test_window_control_matches_programs_and_protects_itself(monkeypatch):
    fake = FakeWin()
    monkeypatch.setattr(tools_v3, "WIN", fake)
    assert '"done"' in tools_v3.window_control("snap left", "chrome") and fake.calls == [(1, "snap_left")]
    tools_v3.window_control("maximise", "vs code")
    assert fake.calls[-1] == (2, "maximise")
    assert "can't see" in tools_v3.window_control("close", "spotify")
    assert "Which program" in tools_v3.window_control("close", "it")
    assert "can't see" in tools_v3.window_control("close", "laptop ai") and (3, "close") not in fake.calls
    tools_v3.window_control("minimise_all")
    assert fake.keys[-1] == [0x5B, 0x44]
    assert "action must be" in tools_v3.window_control("explode", "chrome")


# ---------------------------------------------------------------- scenes
def test_parse_steps_and_custom_scene(home):
    steps = tools_v3.parse_steps("open steam, volume 60, brightness 100, focus 45 minutes and play music")
    assert [s[0] for s in steps] == ["open_app", "volume", "brightness", "focus_mode", "play_local_music"]
    assert steps[3][1] == {"minutes": 45}
    saved = json.loads(tools_v3.save_scene("Gaming", "open steam, volume 60"))
    assert saved["saved"] == "gaming" and "gaming" in tools_v3.scenes()
    assert "need a name" in tools_v3.save_scene("x", "do a flip")


def test_run_scene_does_every_step_and_survives_a_failing_one(home, monkeypatch):
    calls = []
    for name in ("volume", "brightness", "focus_mode", "open_app"):
        monkeypatch.setattr(tools.REGISTRY[name], "func",
                            lambda _n=name, **a: calls.append((_n, a)) or (tools.err("no") if _n == "brightness" else tools.ok()))
    out = json.loads(tools_v3.run_scene("study mode"))
    assert [c[0] for c in calls] == ["focus_mode", "volume", "brightness"]
    assert len(out["done"]) == 2 and len(out["skipped"]) == 1
    assert "Start study mode" in tools.REGISTRY["run_scene"].describe({"name": "study"})
    assert "don't know a scene" in tools_v3.run_scene("flying")


# ---------------------------------------------------------------- internet
def test_slow_internet_names_the_cause(monkeypatch):
    monkeypatch.setattr(tools_v3, "_wifi", lambda: {"signal": 30, "name": "Home", "mbps": 20.0})
    monkeypatch.setattr(tools_v3, "_ping", lambda host="1.1.1.1": (210.0, 12.0))
    monkeypatch.setattr(tools_v3, "_download_mbps", lambda seconds=8: 1.2)
    monkeypatch.setattr(tools_v3, "_busy_programs", lambda limit=3: [("chrome.exe", 80)])
    monkeypatch.setattr(tools_v3, "_rate_now", lambda s=1.5: 3e6)
    r = json.loads(tools_v3.why_is_internet_slow())
    v = r["verdict"]
    assert "weak" in v and "lost" in v and "chrome.exe has 80" in v and "heavily" in v and r["facts"]["download"] == "1.2 Mbps"


def test_slow_internet_when_nothing_is_reachable_and_when_all_is_well(monkeypatch):
    monkeypatch.setattr(tools_v3, "_wifi", lambda: None)
    monkeypatch.setattr(tools_v3, "_ping", lambda host="1.1.1.1": None)
    assert "couldn't reach the internet" in tools_v3.why_is_internet_slow()
    monkeypatch.setattr(tools_v3, "_ping", lambda host="1.1.1.1": (20.0, 0.0))
    monkeypatch.setattr(tools_v3, "_download_mbps", lambda seconds=8: 80.0)
    monkeypatch.setattr(tools_v3, "_busy_programs", lambda limit=3: [])
    monkeypatch.setattr(tools_v3, "_rate_now", lambda s=1.5: 1000.0)
    assert "Nothing looks wrong" in tools_v3.why_is_internet_slow()


def test_ping_parser_reads_windows_and_linux_output(monkeypatch):
    linux = "4 packets transmitted, 4 received, 0% packet loss\n64 bytes: time=20.5 ms\n64 bytes: time=21.5 ms\n"
    monkeypatch.setattr(tools_v3, "RUN", lambda *a, **k: SimpleNamespace(stdout=linux))
    ms, loss = tools_v3._ping()
    assert 20 < ms < 22 and loss == 0


# ---------------------------------------------------------------- health history
def test_trends_record_summarise_and_spot_changes(home):
    now = time.time()
    for i in range(40):                                                    # the previous 7 days: calm
        trends.record({"cpu": 20, "ram": 40, "disk": 60, "battery": 80}, now - 13 * 86400 + i * 3600, force=True)
    for i in range(40):                                                    # the last 7 days: busier, disk filling
        trends.record({"cpu": 50, "ram": 75, "disk": 60 + i * 0.2, "battery": 70}, now - 6 * 86400 + i * 3600, force=True)
    text = trends.summary(7, now)
    assert "Memory averaged 75%" in text and "up from 40%" in text and "disk filled" in text
    b = trends.buckets(7, 10, now)
    assert len(b["cpu"]) == 10 and any(v for v in b["ram"] if v)
    assert trends.record({"cpu": 1}, now) is True and trends.record({"cpu": 1}, now + 10) is False
    assert "enough history" in trends.summary(7, now + 400 * 86400)


# ---------------------------------------------------------------- the router understands them
from laptop_ai import intents  # noqa: E402


@pytest.mark.parametrize("text,tool", [
    ("undo", "undo_last_action"), ("undo that", "undo_last_action"), ("put them back", "undo_last_action"),
    ("what did you do", "activity_history"), ("show my recent activity", "activity_history"),
    ("why is my internet so slow", "why_is_internet_slow"), ("my wifi is lagging", "why_is_internet_slow"),
    ("find junk", "find_junk"), ("smart cleanup", "find_junk"), ("find duplicate files", "find_junk"), ("free up some space", "find_junk"),
    ("delete those", "move_to_trash"), ("yes, delete them", "move_to_trash"),
    ("back up my documents to my flash drive", "backup_files"), ("backup my files", "backup_files"),
    ("is my flash drive plugged in", "find_drives"),
    ("minimize everything", "window_control"), ("show desktop", "window_control"), ("snap chrome to the left", "window_control"),
    ("put vs code on the right", "window_control"), ("maximize chrome", "window_control"), ("switch to spotify", "window_control"),
    ("brightness 40", "brightness"), ("dim the screen", "brightness"), ("make the screen brighter", "brightness"),
    ("study mode", "run_scene"), ("start movie mode", "run_scene"), ("goodnight", "run_scene"), ("run the work scene", "run_scene"),
    ("what scenes do you have", "list_scenes"),
    ("create a scene called gaming: open steam, volume 60", "save_scene"),
    ("how has my pc been this week", "health_trend"), ("show my health history", "health_trend"),
])
def test_router_sends_new_commands_to_the_right_tool(text, tool, home):
    got = intents.route(text)
    assert got is not None and got.tool == tool, (text, got and got.tool)


@pytest.mark.parametrize("text", ["focus mode", "start focus mode for 45 minutes", "close chrome", "go to settings", "switch to chat",
                                  "play some music", "open spotify", "undo my life choices please explain", "dark mode"])
def test_router_leaves_old_and_unrelated_commands_alone(text, home):
    got = intents.route(text)
    assert got is None or got.tool not in {"run_scene", "window_control", "undo_last_action", "find_junk"}, (text, got and got.tool)


def test_router_arguments_for_new_commands(home):
    assert intents.route("back up my documents and pictures to a usb drive").args == {"folders": "documents, pictures"}
    assert intents.route("snap chrome to the left").args == {"action": "snap_left", "app": "chrome"}
    assert intents.route("brightness 140").args == {"level": 100}
    assert intents.route("how has my pc been this month").args == {"days": 30}
    c = intents.route("create a scene called gaming: open steam, volume 60")
    assert c.args == {"name": "gaming", "steps": "open steam, volume 60"}
    ran_ = json.loads(tools_v3.save_scene(**c.args))
    assert intents.route("gaming mode").tool == "run_scene" and ran_["saved"] == "gaming"


# ---------------------------------------------------------------- the history chart and themes
def test_chart_points_keep_gaps_as_gaps():
    from laptop_ai import trendview
    runs = trendview.chart_points([10, 20, None, None, 50, 60, 70], 300, 200, (40, 10, 10, 30))
    assert len(runs) == 2 and len(runs[0]) == 4 and len(runs[1]) == 6
    assert runs[0][1] > runs[0][3]                                   # 20% is drawn higher up than 10%
    assert trendview.chart_points([None, 5, None], 300, 200, (0, 0, 0, 0)) == []     # one lone point isn't a line


def test_themes_fall_back_to_blue_and_have_every_colour():
    from laptop_ai import theme
    assert theme.colours("nonsense") == theme.PRESETS["blue"] and theme.label_to_name("Emerald") == "emerald"
    assert all(len(v) == 3 and all(c.startswith("#") and len(c) == 7 for c in v) for v in theme.PRESETS.values())
    assert set(theme.LABELS) == set(theme.PRESETS) and config.Settings().theme == "blue"


def test_morning_report_includes_problems_and_history_only_when_there_is_something_to_say():
    from laptop_ai import tools_extra
    status = {"cpu_percent": 10, "ram_percent": 40, "disk_percent": 50, "disk_free": "100 GB", "battery_percent": 80, "plugged_in": False}
    plain = tools_extra.morning_report_text(status, [], 0, now=1_700_000_000)
    assert "Needs attention" not in plain
    rich = tools_extra.morning_report_text(status, [], 0, now=1_700_000_000, problems=["GPU is hot"],
                                           trend="Over the last 7 days (50 readings):\n- Memory averaged 70%.")
    assert "Needs attention:" in rich and "GPU is hot" in rich and "Memory averaged 70%" in rich
    assert "enough history" not in tools_extra.morning_report_text(status, [], 0, trend="I don't have enough history yet.")
