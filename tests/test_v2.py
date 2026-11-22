"""2.0 tests: command router, health watchdog, Ollama model fallback, saved-file recovery."""
import json
import os
from datetime import datetime
from types import SimpleNamespace

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

import pytest  # noqa: E402

from laptop_ai import brain, config, health, intents, tools  # noqa: E402

NOW = datetime(2026, 11, 17, 15, 0)


@pytest.mark.parametrize("text,tool", [
    ("how's my battery", "get_system_status"), ("What's the time?", "get_current_time"),
    ("remind me in 10 minutes to stretch", "add_reminder"), ("remind me to call mum at 5pm", "add_reminder"),
    ("close chrome", "kill_process"), ("please close the discord app", "kill_process"),
    ("volume up", "volume"), ("set volume to 40", "volume"), ("mute", "volume"),
    ("next song", "media_control"), ("pause the music", "media_control"),
    ("lock my laptop", "power_action"), ("restart the pc", "power_action"),
    ("take a screenshot", "take_screenshot"), ("clean my temp files", "clean_temp_files"),
    ("note: buy data bundle", "add_note"), ("remember that my exam is on Friday", "remember"),
    ("what do you remember about me", "list_memories"), ("forget my exam", "forget"),
    ("start focus mode for 45 minutes", "focus_mode"), ("stop focus mode", "stop_focus_mode"),
    ("is my internet working", "diagnose_internet"), ("run a security check", "security_check"),
    ("check my pc for viruses", "hardware_health"), ("is my gpu ok", "hardware_health"),
    ("is my laptop healthy", "hardware_health"), ("run a health check", "run_diagnostics"),
    ("what's using my memory", "list_top_processes"), ("search my documents for invoice", "search_documents"),
    ("clean my temp files every sunday at 9am", "schedule_weekly_cleanup"),
])
def test_router_picks_the_right_tool(text, tool):
    got = intents.route(text, NOW)
    assert got is not None and got.tool == tool, (text, got)


@pytest.mark.parametrize("text", [
    "write me a poem about Accra", "close it", "close explorer", "tell me a joke", "how do I fix a flat tyre",
    "stop", "explain what a battery is made of in chemistry class", "",
])
def test_router_leaves_other_things_to_the_ai(text):
    assert intents.route(text, NOW) is None


def test_reminder_arguments():
    got = intents.route("remind me in 2 hours to drink water", NOW)
    assert got.args == {"message": "drink water", "minutes": 120.0}
    got = intents.route("remind me to call mum at 5pm", NOW)
    assert got.args["minutes"] == 120.0 and got.args["message"] == "call mum"
    got = intents.route("remind me at 2pm to eat", NOW)       # 2pm already passed: tomorrow
    assert got.args["minutes"] == 23 * 60


def test_shutdown_phrases_do_not_trigger_on_chat():
    assert intents.route("shutdown the laptop", NOW).args == {"action": "shutdown"}
    assert intents.route("my laptop keeps shutting down randomly", NOW) is None


# ---------------------------------------------------------------- health watchdog
def fake_run(table):
    def run(script):
        for key, out in table.items():
            if key in script:
                return out
        return ""
    return run


def test_defender_off_and_detection_are_critical():
    run = fake_run({
        "Get-MpComputerStatus": '{"AntivirusEnabled":true,"RealTimeProtectionEnabled":false,"AntivirusSignatureAge":30}',
        "Get-MpThreatDetection": '{"ThreatID":2147,"ProcessName":"evil.exe"}'})
    keys = {f.key: f for f in health.check_defender(run)}
    assert keys["av_off"].level == health.CRITICAL and keys["av_old"].level == health.WARNING
    assert keys["threat_2147"].level == health.CRITICAL and "evil.exe" in keys["threat_2147"].message


def test_event_log_maps_hardware_trouble():
    run = fake_run({"Get-WinEvent": json.dumps([
        {"Name": "Microsoft-Windows-WHEA-Logger, 18", "Count": 4},
        {"Name": "Microsoft-Windows-Kernel-Power, 41", "Count": 1},
        {"Name": "disk, 7", "Count": 5}, {"Name": "nvlddmkm, 14", "Count": 2}])})
    f = {x.key: x for x in health.check_event_log(run)}
    assert f["whea"].level == health.CRITICAL and f["whea"].area == "motherboard"
    assert f["power_loss"].level == health.INFO                 # one unexpected shutdown is only a note
    assert f["disk_errors"].level == health.WARNING and f["gpu_driver"].area == "gpu"


def test_failing_disk_and_broken_device():
    run = fake_run({"Get-PhysicalDisk": '{"FriendlyName":"Samsung SSD","HealthStatus":"Warning"}',
                    "Win32_PnPEntity": '{"Name":"NVIDIA GeForce","PNPClass":"Display","ConfigManagerErrorCode":43}'})
    assert health.check_disks(run)[0].level == health.CRITICAL
    dev = health.check_devices(run)[0]
    assert dev.area == "gpu" and "43" in dev.message
    assert health.check_disks(fake_run({"Get-PhysicalDisk": '{"FriendlyName":"X","HealthStatus":"Healthy"}'})) == []


def test_gpu_and_cpu_temperature():
    assert health.check_gpu(lambda s: "", smi="RTX 3050, 93, Not Active, 0")[0].level == health.CRITICAL
    assert health.check_gpu(lambda s: "", smi="RTX 3050, 60, Not Active, 0") == []
    hot = {"coretemp": [SimpleNamespace(current=97.0)]}
    assert health.check_temperatures(hot)[0].level == health.CRITICAL
    assert health.check_temperatures({"coretemp": [SimpleNamespace(current=55.0)]}) == []


def test_fake_system_program_is_flagged_but_downloads_alone_is_not():
    bad = [{"name": "svchost.exe", "exe": "C:\\Users\\Tino\\AppData\\Local\\Temp\\svchost.exe", "cpu_percent": 1}]
    assert health.check_processes(bad)[0].area == "malware"
    ok_ = [{"name": "setup.exe", "exe": "C:\\Users\\Tino\\Downloads\\setup.exe", "cpu_percent": 1}]
    assert health.check_processes(ok_) == []


def test_sustained_cpu_not_a_spike():
    w = health.CpuWatch(samples=4)
    assert [w.feed(c) for c in (99, 99, 20, 99)] == [None] * 4
    w = health.CpuWatch(samples=3)
    assert w.feed(99) is None and w.feed(98) is None and w.feed(97).key == "cpu_pinned"


def test_watchdog_alerts_once_per_cooldown():
    sent, t = [], [1000.0]
    run = fake_run({"Get-PhysicalDisk": '{"FriendlyName":"D","HealthStatus":"Unhealthy"}'})
    wd = health.Watchdog(config.Settings(), sent.append, run=run, clock=lambda: t[0])
    wd.tick()
    wd.tick()
    assert len(sent) == 1 and sent[0].startswith("Serious problem")
    t[0] += 3601
    wd.tick()
    assert len(sent) == 2


def test_watchdog_can_be_turned_off_and_survives_a_failing_probe():
    s = config.Settings()
    s.watchdog_enabled = False
    assert health.Watchdog(s, lambda m: None).tick() == []
    boom = lambda script: (_ for _ in ()).throw(RuntimeError("x"))     # noqa: E731
    assert isinstance(health.scan(boom, quick=False), list)


def test_report_text():
    assert "No signs" in health.report([])
    assert "[CRITICAL]" in health.report([health.Finding("k", health.CRITICAL, "disk", "Bad drive.")])


# ---------------------------------------------------------------- brain: models and history
class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body or {}

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


def make_brain(post, installed, model="llama3.2:3b", tmp=None):
    s = config.Settings()
    s.ollama_model = model
    off = brain.OllamaBackend(s, post=post)
    off.installed = lambda: installed
    ex = tools.Executor(s, lambda t: True)
    b = brain.Brain(s, ex, offline=off, online=brain.ClaudeBackend(s))
    b.history = []
    return b


def test_missing_model_falls_back_to_an_installed_one():
    seen = []

    def post(url, **kw):
        seen.append(kw["json"]["model"])
        return Resp(body={"message": {"content": "hello there"}})
    b = make_brain(post, ["llama3.2:1b"])
    reply = b.ask("tell me a joke")
    assert seen == ["llama3.2:1b"] and "hello there" in reply and "isn't installed" in reply


def test_model_without_tool_support_retries_as_plain_chat():
    calls = []

    def post(url, **kw):
        calls.append("tools" in kw["json"])
        return Resp(400) if "tools" in kw["json"] else Resp(body={"message": {"content": "plain answer"}})
    b = make_brain(post, ["llama3.2:3b"])
    assert b.ask("tell me a joke") == "plain answer" and calls == [True, False]


def test_router_answers_without_any_model(monkeypatch):
    def post(*a, **k):
        raise AssertionError("the AI should not be called")
    b = make_brain(post, [])
    assert "time" in b.ask("what time is it").lower() or b.last_backend == "built-in"
    assert b.last_backend == "built-in"


def test_history_survives_restart():
    b = make_brain(lambda *a, **k: Resp(body={"message": {"content": "ok"}}), ["llama3.2:3b"])
    b.ask("hello model")
    again = brain.Brain.load_history()
    assert again[-2]["content"] == "hello model" and again[-1]["content"] == "ok"
    b.clear()
    assert brain.Brain.load_history() == []


def test_old_1b_setting_is_upgraded_and_corrupt_settings_recover(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", f)
    f.write_text(json.dumps({"ollama_model": "llama3.2:1b", "cpu_alert_pct": "lots"}))
    s = config.Settings.load()
    assert s.ollama_model == "llama3.2:3b" and s.cpu_alert_pct == 90
    f.write_text("{not json")
    s = config.Settings.load()
    assert s.backend == "auto" and (tmp_path / "settings.json.bad").exists()


@pytest.mark.parametrize("text,tool", [("play music", "media_control"), ("you just play music", "media_control"),
                                       ("play some music please", "media_control"), ("play burna boy last last", "open_website")])
def test_play_requests_do_something_real(text, tool):
    got = intents.route(text, NOW)
    assert got is not None and got.tool == tool


def test_youtube_search_url_is_encoded():
    got = intents.route("play burna boy last last", NOW)
    assert got.args["url"].endswith("search_query=burna+boy+last+last")


def test_invented_tool_json_is_never_shown_as_the_answer():
    def post(url, **kw):
        return Resp(body={"message": {"content": '{"name": "play_music", "parameters": {"command": "music"}}'}})
    b = make_brain(post, ["llama3.2:3b"])
    reply = b.ask("sing me something in the shower")
    assert "play_music" in reply and "isn't one of my tools" in reply and "{" not in reply


@pytest.mark.parametrize("text,query", [("play music from my laptop", ""), ("play some music from my pc", ""),
                                        ("play my music", ""), ("play burna boy from my laptop", "burna boy"),
                                        ("shuffle songs from my computer", ""), ("play wizkid songs from my disk", "wizkid")])
def test_local_music_requests(text, query):
    got = intents.route(text, NOW)
    assert got is not None and got.tool == "play_local_music" and got.args == {"query": query}, (text, got)


def test_local_music_finds_files_and_opens_a_playlist(tmp_path, monkeypatch):
    from laptop_ai import tools_extra, tools_v2
    music = tmp_path / "Music"
    (music / "Burna Boy").mkdir(parents=True)
    for n in ("Last Last.mp3", "Ye.mp3", "notes.txt"):
        (music / "Burna Boy" / n).write_text("x")
    (music / "Wizkid - Essence.flac").write_text("x")
    monkeypatch.setattr(tools_extra, "_roots", lambda folder="": [music])
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    opened = []
    monkeypatch.setattr(tools_v2, "OPENER", opened.append)
    r = json.loads(tools.REGISTRY["play_local_music"].func(query="burna"))
    assert r["status"] == "ok" and r["queued"] == 2
    assert "Last Last.mp3" in opened[0].read_text(encoding="utf-8") and len(opened) == 1
    r = json.loads(tools.REGISTRY["play_local_music"].func(query=""))
    assert r["queued"] == 3
    bad = json.loads(tools.REGISTRY["play_local_music"].func(query="nonexistent artist"))
    assert bad["status"] == "error" and "no music" in bad["message"]


# ---------------------------------------------------------------- 2.3: sounds, notes, areas, compound requests
def test_single_disk_error_is_a_quiet_note_that_never_alerts():
    run = fake_run({"Get-WinEvent": json.dumps([{"Name": "disk, 7", "Count": 1}])})
    f = health.check_event_log(run)[0]
    assert f.level == health.INFO and "harmless" in f.message
    sent = []
    wd = health.Watchdog(config.Settings(), sent.append, run=run)
    wd.tick()
    assert sent == [] and wd.latest[0].key == "disk_errors"
    from laptop_ai import holo_model as hm
    st = hm.compute({}, wd.latest)
    assert st["ssd"].level == hm.OK and "disk error" in st["ssd"].details[0]


def test_report_answers_about_the_part_asked():
    note = health.Finding("disk_errors", health.INFO, "disk", "1 disk error.")
    assert health.report([note], "gpu").startswith("No signs of GPU trouble found.")
    assert "[NOTE]" in health.report([note], "disk") and "Elsewhere" not in health.report([note], "disk")
    assert "Elsewhere: 1 other" in health.report([note], "gpu")
    assert intents.route("is my gpu ok", NOW).args == {"area": "gpu"}
    assert intents.route("check my pc for viruses", NOW).args == {"area": "malware"}


def test_open_vlc_and_play_music_is_understood():
    got = intents.route("open  VLC media player and play some music", NOW)
    assert got.tool == "play_local_music" and got.args == {"query": "", "player": "vlc"}


def test_chime_plays_the_pattern_without_blocking_or_crashing(monkeypatch):
    from laptop_ai import sound
    played = []
    monkeypatch.setattr(sound, "BEEP", lambda f, ms: played.append(f))
    monkeypatch.setattr(sound, "SLEEP", lambda s: None)
    sound.chime("reminder", wait=True)
    assert len(played) == 4 * 3 and played[3] == 1319
    sound.chime("nonsense", wait=True)
    monkeypatch.setattr(sound, "BEEP", lambda f, ms: (_ for _ in ()).throw(RuntimeError("no device")))
    sound.chime("alert", wait=True)                    # a missing sound card must not raise


def test_model_fallback_note_is_shown_once_and_never_saved_in_history():
    b = make_brain(lambda url, **kw: Resp(body={"message": {"content": "answer"}}), ["llama3.2:1b"])
    first = b.ask("tell me a joke")
    second = b.ask("another one")
    assert "isn't installed" in first and "isn't installed" not in second
    assert all("isn't installed" not in m["content"] for m in b.history)


def test_media_control_talks_to_vlc_directly_when_it_is_running(monkeypatch):
    from laptop_ai import tools_v2
    sent, keys = [], []
    monkeypatch.setattr(tools_v2, "_vlc_running", lambda: True)
    monkeypatch.setattr(tools_v2, "_vlc_http", lambda c: sent.append(c) or True)
    monkeypatch.setattr(tools_v2.volume, "SENDER", keys.append)
    assert '"via": "VLC"' in tools_v2.media_control("next") and sent == ["pl_next"] and keys == []
    tools_v2.media_control("play_pause")
    tools_v2.media_control("previous")
    assert sent == ["pl_next", "pl_pause", "pl_previous"]


def test_media_control_falls_back_to_window_message_then_media_keys(monkeypatch):
    from laptop_ai import tools_v2
    keys, posted = [], []
    monkeypatch.setattr(tools_v2.volume, "SENDER", keys.append)
    monkeypatch.setattr(tools_v2, "_vlc_running", lambda: True)
    monkeypatch.setattr(tools_v2, "_vlc_http", lambda c: False)            # VLC was opened by hand, no web remote
    monkeypatch.setattr(tools_v2, "_vlc_appcommand", lambda a: posted.append(a) or True)
    tools_v2.media_control("next")
    assert posted == ["next"] and keys == []
    monkeypatch.setattr(tools_v2, "_vlc_running", lambda: False)           # no VLC: the keyboard media keys, as before
    tools_v2.media_control("next")
    assert keys == [0xB0]


def test_vlc_is_started_with_its_local_remote(monkeypatch):
    from laptop_ai import tools_v2
    calls = []
    monkeypatch.setattr(tools_v2, "_vlc_path", lambda: "C:/vlc.exe")
    monkeypatch.setattr("subprocess.Popen", lambda args, **k: calls.append(args))
    assert tools_v2._open_in_vlc("x.m3u8")
    assert "--extraintf=http" in calls[0] and "--http-host=127.0.0.1" in calls[0] and calls[0][-1] == "x.m3u8"


# ---------------------------------------------------------------- 2.7: power without prompts, close-the-hog prompts
def _power_executor(monkeypatch, **settings):
    from laptop_ai import config, tools, tools_pro
    seen, asked = [], []
    monkeypatch.setattr(tools_pro, "RUNNER", lambda argv, **k: seen.append(argv))
    s = config.Settings()
    for k, v in settings.items():
        setattr(s, k, v)
    return tools.Executor(s, lambda text: asked.append(text) or True), seen, asked


@pytest.mark.parametrize("text,action", [("shut down", "shutdown"), ("shutdown the pc", "shutdown"), ("turn off my laptop", "shutdown"),
                                          ("turn it off", "shutdown"), ("sleep", "sleep"), ("put the laptop to sleep", "sleep"),
                                          ("restart the pc", "restart"), ("cancel the shutdown", "cancel"), ("lock my laptop", "lock")])
def test_power_phrases_are_understood(text, action):
    got = intents.route(text)
    assert got is not None and got.tool == "power_action" and got.args["action"] == action


def test_power_commands_you_give_run_without_a_question(monkeypatch):
    ex, seen, asked = _power_executor(monkeypatch)
    for action in ("sleep", "lock", "shutdown", "restart", "cancel"):
        ex.run("power_action", {"action": action}, source="user")
    assert asked == [] and len(seen) == 5
    assert seen[2] == ["shutdown", "/s", "/t", "15"]                       # shutdown waits 15 s so it can be cancelled


def test_the_ais_own_power_guesses_and_the_phone_still_cannot_skip_asking(monkeypatch):
    ex, seen, asked = _power_executor(monkeypatch)
    ex.run("power_action", {"action": "shutdown"}, source="scheduler")
    assert len(asked) == 1                                                  # something it starts by itself still asks
    ex.override = "read_only"
    assert "can't make changes" in json.loads(ex.run("power_action", {"action": "shutdown"}, source="user"))["message"]
    ex2, _, asked2 = _power_executor(monkeypatch, power_without_asking=False)
    ex2.run("power_action", {"action": "sleep"}, source="user")
    assert len(asked2) == 1                                                 # the switch in Settings turns the shortcut off


ROWS = {"memory": [{"name": "System", "memory_mb": 9000, "pids": [4]},
                   {"name": "chrome.exe", "memory_mb": 3400, "cpu_percent": 5.0, "processes": 30, "pids": [11]},
                   {"name": "code.exe", "memory_mb": 900, "cpu_percent": 1.0, "processes": 3, "pids": [12]}],
        "cpu": [{"name": "handbrake.exe", "memory_mb": 400, "cpu_percent": 71.0, "processes": 1, "pids": [21]}]}


def _watcher(monkeypatch, answer=True, **settings):
    from laptop_ai import config, hogs
    s = config.Settings()
    for k, v in settings.items():
        setattr(s, k, v)
    asked, closed, notes = [], [], []
    w = hogs.HogWatcher(s, lambda q: asked.append(q) or answer, lambda n: closed.append(n) or True, notes.append,
                        rows_by=lambda by: ROWS[by])
    return w, asked, closed, notes


def test_high_memory_asks_before_closing_the_biggest_program(monkeypatch):
    w, asked, closed, notes = _watcher(monkeypatch)
    assert w.check({"ram": 95, "cpu": 10}, now=1000) == "closed"
    assert closed == ["chrome.exe"] and "3.3 GB" in asked[0] and "95%" in asked[0] and "Close chrome.exe?" in asked[0]
    assert "Closed chrome.exe" in notes[0]
    assert w.check({"ram": 95, "cpu": 10}, now=1100) is None                # not asked again straight away


def test_saying_no_closes_nothing_and_waits_an_hour(monkeypatch):
    w, asked, closed, _ = _watcher(monkeypatch, answer=False)
    assert w.check({"ram": 96, "cpu": 5}, now=0) == "declined" and closed == []
    assert w.check({"ram": 96, "cpu": 5}, now=3000) is None
    assert w.check({"ram": 96, "cpu": 5}, now=3700) == "declined"


def test_cpu_must_stay_high_for_two_readings_and_a_blameless_pc_is_left_alone(monkeypatch):
    w, asked, closed, _ = _watcher(monkeypatch)
    assert w.check({"ram": 20, "cpu": 97}, now=0) is None                   # a single spike is normal
    assert w.check({"ram": 20, "cpu": 97}, now=30) == "closed" and closed == ["handbrake.exe"]
    w2, asked2, _, _ = _watcher(monkeypatch)
    assert w2.check({"ram": 50, "cpu": 40}, now=0) is None and asked2 == []
    w3, asked3, _, _ = _watcher(monkeypatch, suggest_close_apps=False)
    assert w3.check({"ram": 99, "cpu": 5}, now=0) is None and asked3 == []  # switched off in Settings


@pytest.mark.parametrize("text,action,minutes", [
    ("pause in 30 seconds", "pause", 0.5), ("pause the music in 2 minutes", "pause", 2), ("stop the music in 10 minutes", "pause", 10),
    ("in 5 minutes pause the music", "pause", 5), ("after half an hour, stop the music", "pause", 30), ("skip the song in a minute", "next", 1),
    ("after a minute pause", "pause", 1), ("mute in 1 hour", "mute", 60), ("play in 20 seconds", "play", 0.333)])
def test_timed_media_commands_are_scheduled(text, action, minutes):
    got = intents.route(text)
    assert got is not None and got.tool == "schedule_media", text
    assert got.args["action"] == action and abs(got.args["minutes"] - minutes) < 0.01


def test_timed_media_task_is_stored_and_runs(monkeypatch, tmp_path):
    from laptop_ai import tools_v2, scheduler
    pressed = []
    monkeypatch.setattr(tools_v2.volume, "SENDER", pressed.append)
    monkeypatch.setattr(tools_v2, "_vlc_running", lambda: False)
    sch = scheduler.Scheduler(tmp_path / "s.json")
    monkeypatch.setattr(tools_v2, "get_scheduler", lambda: sch)
    out = json.loads(tools_v2.schedule_media("pause", 0.5))
    task = sch.list()[0]
    assert out["status"] == "ok" and task["kind"] == "media" and task["params"] == {"action": "play_pause"}
    assert tools_v2.do_media(task["params"]["action"]).startswith("Paused") and pressed == [0xB3]
    assert json.loads(tools_v2.schedule_media("dance", 1))["status"] == "error"
    assert json.loads(tools_v2.schedule_media("pause", 0))["status"] == "error"


def test_play_some_music_uses_your_favourite_app_when_one_is_set(monkeypatch):
    plain = intents.route("play some music")
    assert plain.tool == "media_control"                                  # nothing set: just press play
    monkeypatch.setattr(config.Settings, "load", classmethod(lambda cls: SimpleNamespace(music_player="VLC")))
    fav = intents.route("play some music")
    assert fav.tool == "play_local_music" and fav.args == {"query": "", "player": "VLC"}
    assert intents.route("play").tool == "media_control"                  # a bare "play" is still the play key


def test_map_button_offers_the_top_program_and_still_asks(monkeypatch):
    w, asked, closed, notes = _watcher(monkeypatch)
    assert w.offer("memory", {"ram": 40, "cpu": 5}) == "closed" and closed == ["chrome.exe"] and asked   # skips protected "System"
    w2, asked2, closed2, _ = _watcher(monkeypatch, answer=False)
    assert w2.offer("cpu", {"ram": 10, "cpu": 20}) == "declined" and closed2 == []
