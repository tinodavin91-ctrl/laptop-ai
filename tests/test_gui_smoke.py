"""Builds the real window and drives it. Skipped automatically where Tk or a display is missing."""
import json
import os
import sys
import threading
import time

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

import pytest  # noqa: E402

pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")
if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("no display available", allow_module_level=True)

from laptop_ai import config, tools   # noqa: E402
from laptop_ai import gui             # noqa: E402


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.01)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "TASKS_FILE", tmp_path / "tasks.json")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "actions.log")
    monkeypatch.setattr(tools, "SCHEDULER", None)
    config.write_json(tmp_path / "settings.json", {"setup_done": True})          # the welcome screen has its own tests
    a = gui.App()
    pump(a, 0.3)
    yield a
    a.monitor.stop()
    a.watchdog.stop()
    a.scheduler.stop()
    a.destroy()


@pytest.fixture
def fresh_app(tmp_path, monkeypatch):
    """A brand-new install: no settings file at all."""
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "TASKS_FILE", tmp_path / "tasks.json")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "actions.log")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(tools, "SCHEDULER", None)
    a = gui.App()
    pump(a, 0.3)
    yield a
    a.monitor.stop()
    a.watchdog.stop()
    a.scheduler.stop()
    a.destroy()


def test_every_page_opens(app):
    for key in app.pages:
        app.show(key)
        pump(app, 0.05)
        assert app.page == key


def test_send_shows_reply_and_new_chat_forgets(app):
    seen = []

    def fake_ask(text, on_tool=lambda n, a: None):
        seen.append(text)
        on_tool("get_system_status", {})
        app.brain.last_backend = "offline (Ollama)"
        app.brain.history.append({"role": "user", "content": text})
        return "All good."
    app.brain.ask = fake_ask

    assert app.send("how is my laptop?") is True
    assert app.page == "chat"
    pump(app, 0.6)
    texts = [lbl.cget("text") for lbl in app._labels]
    assert "how is my laptop?" in texts and "All good." in texts
    assert any("checking system status" in t for t in texts)
    assert app.busy is False and app._thinking is None
    assert app.send("   ") is False                      # blank messages are ignored

    app.new_chat()
    assert app.brain.history == []
    assert len(app.chat.winfo_children()) == 1


def test_second_message_while_busy_is_refused(app):
    app.busy = True
    assert app.send("hello") is False
    app.busy = False


def test_settings_controls_change_settings(app):
    app._set_autonomy("report_only")
    assert app.settings.autonomy == "report_only" and app.executor.settings.autonomy == "report_only"
    app._set_backend("offline")
    assert app.settings.backend == "offline"
    app.model_entry.delete(0, "end")
    app.model_entry.insert(0, "qwen2.5")
    app._set_model()
    assert app.settings.ollama_model == "qwen2.5"
    assert config.SETTINGS_FILE.exists()


def test_confirm_dialog_from_worker_thread(app, monkeypatch):
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: asked.append(a) or True)
    result = {}
    t = threading.Thread(target=lambda: result.update(ok=app.confirm_from_worker("Close Chrome?")))
    t.start()
    for _ in range(100):
        pump(app, 0.02)
        if not t.is_alive():
            break
    t.join(2)
    assert result.get("ok") is True and asked


def test_stats_cards_and_alert_badge(app):
    app._update_stats({"cpu": 95, "ram": 20, "disk": 50, "battery": 12, "plugged": False})
    assert app.tiles["cpu"].text == "95%" and app.tiles["cpu"].colour == gui.RED
    assert app.tiles["ram"].colour == gui.GREEN and app.tiles["battery"].colour == gui.RED
    assert len(app.series["cpu"]) >= 1
    app._update_stats({"cpu": 1, "ram": 1, "disk": 1, "battery": None, "plugged": None})
    assert app.tiles["battery"].text == "AC" and app.tiles["battery"].pct is None

    app.show("home")
    app.say("⚠ test alert", "alert")
    assert app.unread and "new" in app.nav_buttons["chat"].cget("text")
    app.show("chat")
    assert not app.unread


def test_reminder_makes_a_sound_and_speaks(app, monkeypatch):
    from laptop_ai import sound
    chimes, spoken = [], []
    monkeypatch.setattr(sound, "chime", lambda kind="reminder", **k: chimes.append(kind))
    monkeypatch.setattr(app.voice, "speak", lambda t, on_done=None: spoken.append(t))
    monkeypatch.setattr(type(app.voice), "can_speak", property(lambda self: True))
    app.run_scheduled({"kind": "remind", "message": "stretch"})
    assert chimes == ["reminder"] and spoken == ["Reminder. stretch"]
    app.settings.reminder_sound = app.settings.speak_reminders = False
    app.run_scheduled({"kind": "remind", "message": "again"})
    assert chimes == ["reminder"] and len(spoken) == 1


def test_trix_command_while_hidden_is_spoken_and_shown_as_a_popup(app, monkeypatch):
    spoken, toasts = [], []
    monkeypatch.setattr(app.voice, "speak", lambda t, on_done=None: spoken.append(t))
    monkeypatch.setattr(app, "hidden", lambda: True)
    monkeypatch.setattr(app, "_toast", lambda title, text: toasts.append((title, text)))
    app.speak_var.set(False)                                  # spoken replies are off, but nobody can read the window
    app.claim_brain()
    app.brain.ask = lambda text, on_tool=None: "It is half past nine."
    app._think("what time is it in Accra", "wake")
    pump(app, 0.5)
    assert spoken == ["It is half past nine."] and toasts and toasts[-1][0] == "Trix"


def test_confirmation_brings_a_hidden_window_forward(app, monkeypatch):
    shown = []
    monkeypatch.setattr(app, "hidden", lambda: True)
    monkeypatch.setattr(app, "show_window", lambda: shown.append(1))
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)
    result = {}
    t = threading.Thread(target=lambda: result.update(ok=app.confirm_from_worker("Close Chrome?")))
    t.start()
    for _ in range(100):
        pump(app, 0.02)
        if not t.is_alive():
            break
    assert result.get("ok") is True and shown


def test_tool_button_runs_through_executor(app):
    app.show("tools")
    app.run_tool_button("get_current_time", {})
    pump(app, 0.6)
    assert "Weekday" in app.tool_out.get("1.0", "end")


def test_report_only_blocks_quick_action(app):
    app._set_autonomy("report_only")
    app.run_tool_button("add_reminder", {"message": "x", "minutes": 5})
    pump(app, 0.6)
    assert "Report-only" in app.tool_out.get("1.0", "end")


def test_format_result_pure():
    assert "declined" in gui.format_result('{"status": "declined_by_user"}')
    assert gui.format_result('{"status": "error", "message": "nope"}') == "Problem: nope"
    out = gui.format_result('{"status":"ok","programs":[{"name":"a.exe","memory_mb":5,"cpu_percent":1.0,"processes":2}]}')
    assert "a.exe: 5 MB" in out
    assert gui.level_colour(50) == gui.GREEN and gui.level_colour(95) == gui.RED
    assert gui.level_colour(10, low_is_bad=True) == gui.RED


# ---------------------------------------------------------------- phone + wake word
def _fake_brain(app, record):
    def ask(text, on_tool=lambda n, a: None):
        record.append((text, app.executor.override))
        app.brain.last_backend = "test"
        return "ok"
    app.brain.ask = ask


def test_phone_question_is_read_only_and_releases(app):
    seen = []
    _fake_brain(app, seen)
    assert app.phone_question("battery?") == "ok"
    assert seen == [("battery?", "read_only")]
    assert app.busy is False and app.executor.override is None


def test_phone_question_when_busy_does_not_run(app):
    seen = []
    _fake_brain(app, seen)
    assert app.claim_brain()
    assert "busy" in app.phone_question("hi")
    assert seen == [] and app.busy is True
    app.release_brain()


def test_wake_command_always_asks_first_and_resumes_listening(app):
    seen, resumed = [], []
    _fake_brain(app, seen)
    app.wake = type("W", (), {"resume": lambda s, follow_up=False: resumed.append(1), "stop": lambda s: None})()
    assert app.send("close notepad", stay=True, origin="wake")
    deadline = time.time() + 3
    while app.busy and time.time() < deadline:
        pump(app, 0.05)
    pump(app, 0.2)
    assert seen == [("close notepad", "ask_all")]
    assert resumed and app.executor.override is None and app.busy is False


def test_typed_message_uses_no_override(app):
    seen = []
    _fake_brain(app, seen)
    app.send("hello")
    deadline = time.time() + 3
    while app.busy and time.time() < deadline:
        pump(app, 0.05)
    assert seen == [("hello", None)]


def test_alerts_and_reminders_go_to_phone_only_when_enabled(app):
    sent = []
    app.telegram.send_async = sent.append
    app.settings.telegram_chat_id = "1"
    app.telegram.token_provider = lambda: "tok"
    app.notify_phone("a", "alerts")
    app.notify_phone("r", "reminders")
    assert sent == ["a", "r"]
    app.settings.telegram_alerts = False
    app.notify_phone("a2", "alerts")
    assert sent == ["a", "r"]


def test_mic_marker_and_wake_switch_refuses_without_model(app, monkeypatch):
    from laptop_ai import wakeword
    monkeypatch.setattr(wakeword, "model_installed", lambda: False)
    app.wake_var.set(True)
    app._wake_toggle()
    assert app.wake_var.get() is False and app.settings.wake_word_enabled is False
    assert not app.mic_note.winfo_ismapped()
    app._set_mic_note(True)
    pump(app, 0.1)
    assert app.mic_note.winfo_ismapped()
    app._set_mic_note(False)


def test_wake_failure_turns_it_off_visibly(app):
    app.settings.wake_word_enabled = True
    app._set_mic_note(True)
    app._wake_failed("The wake word stopped: OSError.")
    assert app.settings.wake_word_enabled is False and not app.wake_var.get()


def test_advanced_switch_defaults_off_and_controls_the_command_tool(app):
    assert app.settings.allow_commands is False and not app.cmd_var.get()
    assert "run_command" not in [t.name for t in app.executor.available()]
    app.cmd_var.set(True)
    app._set_commands()
    assert app.settings.allow_commands is True
    assert "run_command" in [t.name for t in app.executor.available()]


def test_phone_button_runs_read_only_and_releases(app):
    seen = []
    real = app.executor.run
    app.executor.run = lambda name, args, source="user": (seen.append((name, app.executor.override)) or
                                                          real(name, args, source))
    text = app.phone_button("status")
    assert seen == [("get_system_status", "read_only")] and "Cpu percent" in text
    assert app.busy is False and app.executor.override is None
    assert app.claim_brain()
    assert "busy" in app.phone_button("status")
    app.release_brain()


# ---------------------------------------------------------------- 2.8 pieces
def test_phone_lock_and_sleep_need_a_second_tap_and_stay_off_when_disabled(app, monkeypatch):
    calls = []
    monkeypatch.setattr(tools.REGISTRY["power_action"], "func", lambda action: calls.append(action) or tools.ok())
    text, buttons = app.phone_button("ask_lock")
    assert "Really lock" in text and [k for _l, k in buttons] == ["do_lock", "cancel"] and calls == []
    assert "locked" in app.phone_button("do_lock") and calls == ["lock"]
    assert app.phone_button("cancel") == "OK, cancelled." and calls == ["lock"]
    assert "don't know" in app.phone_button("do_shutdown") and calls == ["lock"]          # shutdown is never offered
    app.settings.phone_actions = False
    assert "switched off" in app.phone_button("ask_sleep") and "switched off" in app.phone_button("do_sleep")
    assert calls == ["lock"]


def test_phone_confirm_keys_work_once_and_expire():
    from laptop_ai import notify
    sent = []
    tg = type("T", (), {"s": type("S", (), {"telegram_chat_id": "7"})(), "call": lambda self, *a, **k: (True, None),
                        "send": lambda self, text, buttons=None: sent.append((text, buttons))})()
    pq = notify.PhoneQuestions(tg, lambda t: "x", [("Lock", "ask_lock")],
                               lambda k: ("Sure?", [("Yes", "do_lock")]) if k == "ask_lock" else "done " + k)
    cb = lambda key: {"id": "1", "data": key, "message": {"chat": {"id": 7, "type": "private"}}}      # noqa: E731
    assert pq._button(cb("do_lock")) == 0                              # never offered: ignored
    assert pq._button(cb("ask_lock")) == 1 and sent[-1] == ("Sure?", [("Yes", "do_lock")])
    assert pq._button(cb("do_lock")) == 1 and sent[-1][0] == "done do_lock"
    assert pq._button(cb("do_lock")) == 0                              # a Yes works only once
    pq._button(cb("ask_lock"))
    pq.confirm_keys["do_lock"] = time.time() - 1                       # expired
    assert pq._button(cb("do_lock")) == 0


def test_history_window_opens_once_and_draws(app):
    app.show("home")
    app.open_history()
    pump(app, 0.3)
    first = app._history_win
    assert first.winfo_exists()
    app.open_history()
    assert app._history_win is first
    first.chart.set_days(7)
    pump(app, 0.2)
    first.destroy()


def test_settings_for_theme_voice_and_checks_save(app):
    app.show("settings")
    app._set_theme("Emerald")
    assert app.settings.theme == "emerald" and "reopen" in app.theme_note.cget("text")
    app._set_voice_rate(220)
    assert app.settings.voice_rate == 220 and app.voice.rate == 220
    app.settings.voice_name = ""
    app.follow_var.set(False)
    app._set_flag("conversation_mode", app.follow_var)
    assert app.settings.conversation_mode is False
    app.startup_var.set(False)
    app._set_flag("startup_check", app.startup_var)
    assert config.Settings.load().startup_check is False


def test_startup_check_is_quiet_unless_something_is_broken(app, monkeypatch):
    from laptop_ai import doctor
    ok_ = doctor.Check("Disk space", doctor.OK, "fine")
    notes = lambda: [lbl.cget("text") for lbl in app._labels]      # noqa: E731
    monkeypatch.setattr(doctor, "run_all", lambda s: [ok_, doctor.Check("Microphone", doctor.WARN, "none", "plug one in")])
    app.settings.wake_word_enabled = False
    app._startup_check()
    pump(app, 0.4)
    assert not any("Start-up check" in t for t in notes())            # optional things stay quiet
    monkeypatch.setattr(doctor, "run_all", lambda s: [ok_, doctor.Check("Offline brain", doctor.FAIL, "Ollama isn't running", "open it")])
    app._startup_check()
    pump(app, 0.4)
    assert any("Start-up check" in t and "Ollama isn't running" in t for t in notes())


def test_conversation_mode_waits_for_the_voice_to_finish_then_listens_for_a_follow_up(app, monkeypatch):
    resumed = []
    app.wake = type("W", (), {"resume": lambda self, follow_up=False: resumed.append(follow_up), "follow_up_s": 0.2})()
    app.settings.conversation_mode = True
    app.voice.speak = lambda text, on_done=None: (on_done() if on_done else None)         # a voice that finishes at once
    monkeypatch.setattr(type(app.voice), "can_speak", property(lambda self: True))
    app.brain.ask = lambda text, on_tool=(lambda n, a: None): "Done."
    app.speak_var.set(True)
    app.send("what time is it", stay=True, origin="wake")
    pump(app, 0.8)
    assert resumed == [True]                                           # follow-up listening, once, after the speech
    app.settings.conversation_mode = False
    resumed.clear()
    app.send("and now", stay=True, origin="wake")
    pump(app, 0.8)
    assert resumed == [False]


def test_a_noisy_follow_up_is_ignored_without_a_chat_message(app, monkeypatch):
    app.wake = type("W", (), {"resume": lambda self, follow_up=False: None, "follow_up_s": 0.2})()
    monkeypatch.setattr(app.voice, "transcribe", lambda raw, mode: (None, "Sorry, I couldn't understand that."))
    before = len(app._labels)
    app._on_follow_up()
    app._on_command(b"\x00" * 100)
    pump(app, 0.3)
    assert len(app._labels) == before and app._in_follow is False


def test_first_run_asks_for_a_name_and_never_says_tino(fresh_app):
    a = fresh_app
    assert a._welcome is not None and a._welcome.winfo_exists()
    assert "Tino" not in a.greeting.cget("text")
    a._welcome.entry.insert(0, "  Ama   Mensah ")
    a._welcome._go()
    pump(a, 0.1)
    assert a._welcome is None
    assert a.settings.user_name == "Ama Mensah" and a.settings.setup_done
    assert config.Settings.load().user_name == "Ama Mensah"
    assert a.greeting.cget("text").endswith(", Ama Mensah")


def test_an_upgrade_from_before_3_0_with_leftover_tino_default_asks_once_prefilled(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "TASKS_FILE", tmp_path / "tasks.json")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "actions.log")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(tools, "SCHEDULER", None)
    config.write_json(tmp_path / "settings.json", {"user_name": "Tino"})       # what every pre-3.0 install had saved
    a = gui.App()
    pump(a, 0.3)
    assert a._welcome is not None and a._welcome.entry.get() == "Tino"         # asks again, but pre-filled so one click does it
    a._welcome._go()
    pump(a, 0.1)
    assert a.settings.setup_done and a.settings.user_name == "Tino"
    a.monitor.stop(); a.watchdog.stop(); a.scheduler.stop(); a.destroy()


def test_welcome_needs_a_name_or_a_skip_and_the_name_can_be_changed_later(fresh_app):
    a = fresh_app
    a._welcome._go()                                    # empty: stays open
    assert a._welcome is not None and "Type a name" in a._welcome.note.cget("text")
    a._welcome.on_done("")                              # Skip
    pump(a, 0.1)
    assert a._welcome is None and a.settings.setup_done and a.settings.user_name == ""
    assert a.greeting.cget("text") in ("Good morning", "Good afternoon", "Good evening")
    a.show("settings")
    a.name_entry.delete(0, "end")
    a.name_entry.insert(0, "Kofi")
    a._set_name()
    assert config.Settings.load().user_name == "Kofi" and a.greeting.cget("text").endswith(", Kofi")


def test_the_second_launch_does_not_show_the_welcome_again(app):
    assert app._welcome is None


def test_devices_page_scans_selects_renames_and_links(app, monkeypatch):
    from laptop_ai import netmap
    devs = [netmap.Device("10.0.0.5", name="MY-PC", is_me=True, kind="me"), netmap.Device("10.0.0.1", is_gateway=True, kind="router"),
            netmap.Device("10.0.0.7", mac="5a:00:00:00:00:01", name="Galaxy-A14", kind="phone"),
            netmap.Device("10.0.0.9", name="OFFICE", kind="laptop_ai", ports=[netmap.PEER_PORT])]
    monkeypatch.setattr(netmap, "scan", lambda progress=None: (progress and progress("Asking...", 0.5), devs)[1])
    app.show("devices")
    page = app.devices_page
    page.scan()
    pump(app, 0.6)
    assert page.scan_btn.cget("state") == "normal" and "Found 3" in page.scan_text.cget("text")
    pump(app, 0.3)
    page.map.redraw()
    assert len(page.map.hit) == 4                                       # every node but the internet can be clicked
    page._select(devs[3])
    assert "OFFICE" in page.detail.cget("text") and page.link_btn.winfo_manager() == "pack"
    page._link_selected()
    assert page.host_entry.get() == "10.0.0.9"
    page._select(devs[2])
    assert page.link_btn.winfo_manager() == ""
    page.rename_entry.delete(0, "end")
    page.rename_entry.insert(0, "Ama's phone")
    page._rename()
    assert devs[2].alias == "Ama's phone"
    pump(app, 0.3)                                                      # the moving light-dots run without errors


def test_device_detail_shows_vendor_latency_and_risk(app):
    from laptop_ai import netmap
    page = app.devices_page
    risky = netmap.Device("10.0.0.20", name="old-cam", kind="camera", mac="b8:27:eb:11:22:33",
                          ports=[23, 554], latency_ms=4.2, risk=netmap.risk_note([23, 554]),
                          vendor=netmap.vendor_for("b8:27:eb:11:22:33"))
    page._select(risky)
    text = page.detail.cget("text")
    assert "Raspberry Pi" in text and "4 ms" in text and "Telnet" in text


def test_devices_page_phone_and_pc_messages(app, monkeypatch):
    from laptop_ai import phonelink, tools_v4
    app.show("devices")
    page = app.devices_page
    monkeypatch.setattr(phonelink, "status", lambda serial="": {"error": "I don't see a phone."})
    page.check_phone()
    pump(app, 0.4)
    assert "don't see a phone" in page.phone_text.cget("text")
    page.link_manual()                                                  # nothing typed
    assert "address" in page.pc_text.cget("text")
    monkeypatch.setattr(tools_v4, "allow_pc_link",
                        lambda on=True: json.dumps({"status": "ok", "code": "123456", "seconds_left": 600, "text": "x"}))
    page.allow_var.set(True)
    page.toggle_allow()
    pump(app, 0.4)
    assert "123456" in page.code_label.cget("text") and "left" in page.code_label.cget("text")


def test_home_page_shows_a_rotating_tip(app):
    app.show("home")
    pump(app, 0.1)
    first = app.tip_label.cget("text")
    assert first
    app._tip_tick()
    pump(app, 0.1)
    assert app.tip_label.cget("text")      # still something sensible after rotating


def test_phone_send_file_and_apps_buttons_work(app, monkeypatch, tmp_path):
    from laptop_ai import phonelink
    app.show("devices")
    page = app.devices_page
    monkeypatch.setattr("tkinter.filedialog.askopenfilename", lambda **k: str(tmp_path / "x.jpg"))
    (tmp_path / "x.jpg").write_bytes(b"1")
    monkeypatch.setattr(phonelink, "push_file", lambda path, **k: (True, "Sent x.jpg to the phone's Download folder."))
    page.phone_send_file()
    pump(app, 0.3)
    assert "Sent x.jpg" in page.phone_text.cget("text")

    monkeypatch.setattr(phonelink, "list_apps", lambda **k: [{"package": "com.whatsapp", "label": "WhatsApp"}])
    page.phone_apps_list()
    pump(app, 0.3)      # opens a Toplevel with one button; just confirm it doesn't crash


def test_received_files_are_listed_and_can_be_sent_to_a_linked_pc(app, monkeypatch, tmp_path):
    from laptop_ai import peers
    app.show("devices")
    page = app.devices_page
    monkeypatch.setattr(peers, "linked", lambda: {"OFFICE-PC": {"host": "127.0.0.1", "key": "k", "port": 1}})
    page._show_linked()
    pump(app, 0.1)
    sent = []
    monkeypatch.setattr(peers, "send_file", lambda name, path: (sent.append((name, path)), (True, f"Sent to {name}"))[1])
    monkeypatch.setattr("tkinter.filedialog.askopenfilename", lambda **k: str(tmp_path / "pic.png"))
    page._send_file("OFFICE-PC")
    pump(app, 0.4)
    assert sent == [("OFFICE-PC", str(tmp_path / "pic.png"))]
    assert "Sent to OFFICE-PC" in page.pc_text.cget("text")
    monkeypatch.setattr(peers, "list_received", lambda: [{"name": "notes.txt", "path": "/x/notes.txt", "bytes": 2048, "when": 0}])
    page._show_received()
    pump(app, 0.1)

    def all_text(widget):
        bits = []
        if hasattr(widget, "cget") and "text" in widget.keys():
            bits.append(str(widget.cget("text")))
        for child in widget.winfo_children():
            bits.extend(all_text(child))
        return bits
    assert "notes.txt" in " ".join(all_text(page.received_box))


def test_console_runs_a_tool_by_name_with_json_args_without_asking(app):
    app.show("tools")
    app.console_entry.insert(0, 'get_system_status {}')
    app.run_console()
    pump(app, 1.5)
    out = app.console_log.get("1.0", "end").lower()
    assert "cpu" in out or "ram" in out or "memory" in out or "problem" in out


def test_console_falls_back_to_plain_words_for_anything_else(app):
    app.brain.ask = lambda text, on_tool=lambda n, a: None: "All good."
    app.show("tools")
    app.console_entry.insert(0, "how is my laptop doing?")
    app.run_console()
    pump(app, 0.3)
    assert "All good." in app.console_log.get("1.0", "end")


def test_devices_live_views_open_and_poll(app, monkeypatch):
    from laptop_ai import devicesview, peers, phonelink
    app.show("devices")
    page = app.devices_page
    monkeypatch.setattr(peers, "fetch_status", lambda name: {"cpu": 40.0, "ram": 50.0, "disk": 60.0, "battery": 70.0, "top": ["x.exe"]})
    win = devicesview.LivePCView(page, "OFFICE-PC", page.F, page.c, page.post)
    pump(app, 1.0)
    assert win.status.cget("text") == "Live"
    assert win.labels["cpu"].cget("text") == "40%"
    win.close()
    monkeypatch.setattr(phonelink, "status", lambda: {"error": "no phone"})
    pwin = devicesview.LivePhoneView(page, page.F, page.c, page.post)
    pump(app, 1.0)
    assert "no phone" in pwin.text.cget("text")
    pwin.close()


def test_phone_live_view_mirrors_the_actual_screen(app, monkeypatch, tmp_path):
    from PIL import Image

    from laptop_ai import devicesview, phonelink
    shot = tmp_path / "shot.png"
    Image.new("RGB", (40, 80), "blue").save(shot)
    app.show("devices")
    page = app.devices_page
    monkeypatch.setattr(phonelink, "status", lambda: {"name": "Pixel", "android": "14", "battery": {"percent": 50},
                                                       "storage": {}, "screen_on": True, "app": "WhatsApp"})
    monkeypatch.setattr(phonelink, "screenshot", lambda path, serial="": (__import__("shutil").copy(shot, path), "")[1])
    win = devicesview.LivePhoneView(page, page.F, page.c, page.post, shot_path=tmp_path / "live.png")
    pump(app, 1.0)
    assert win._ctk_image is not None and "Pixel" in win.text.cget("text")
    win.close()


def test_devices_wifi_card_and_keep_watching(app, monkeypatch):
    from laptop_ai import netmap
    app.show("devices")
    page = app.devices_page
    monkeypatch.setattr(netmap, "wifi_info", lambda: {"connected": True, "ssid": "HomeNet", "signal_pct": 80, "mbps": 400.0})
    page._refresh_wifi()
    pump(app, 0.4)
    assert "HomeNet" in page.wifi_label.cget("text") and "80%" in page.wifi_label.cget("text")
    calls = []
    monkeypatch.setattr(page, "scan", lambda: calls.append(1))
    page.watch_var.set(True)
    page._watch_tick()
    assert calls == [1]
    page.watch_var.set(False)
    page._watch_tick()
    assert calls == [1]                     # stays at 1: watching is off, so no new scan


def test_devices_shows_and_revokes_incoming_links(app, monkeypatch):
    from laptop_ai import peers
    app.show("devices")
    page = app.devices_page
    monkeypatch.setattr(peers, "list_incoming", lambda: [{"key": "abc", "name": "FRIEND-PC"}])
    revoked = []
    monkeypatch.setattr(peers, "revoke_incoming", lambda k: revoked.append(k) or True)
    page._show_incoming()
    pump(app, 0.1)
    page._revoke_incoming("abc")
    assert revoked == ["abc"]


def test_history_window_opens_fast_from_a_big_file(app, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    now = time.time()
    with open(config.APP_DIR / "trends.csv", "w") as f:               # 30 days of readings
        for i in range(8640):
            f.write(f"{now - i * 300:.0f},{10 + i % 30},50,60,80\n")
    app.show("home")
    t0 = time.time()
    app.open_history()
    pump(app, 0.01)
    assert time.time() - t0 < 1.0                                       # the window itself appears at once
    pump(app, 0.5)
    assert "Over the last 1 day" in app._history_win.summary.cget("text")
    app._history_win.destroy()


# ---------------------------------------------------------------- tray, hiding, start with Windows
class _Icon:
    def __init__(self, name, image, title, menu):
        self.title, self.notes, self.running, self.icon = title, [], False, image

    def run_detached(self):
        self.running = True

    def stop(self):
        self.running = False

    def notify(self, message, title):
        self.notes.append((title, message))


import types as _types  # noqa: E402

FAKE_TRAY = _types.SimpleNamespace(Icon=_Icon, Menu=lambda *i: list(i),
                                   MenuItem=lambda text, fn, default=False: _types.SimpleNamespace(text=text, fn=fn))


def test_home_page_laptop_model_shows_problems_and_selection(app):
    from laptop_ai import health
    app.show("home")
    app._update_stats({"cpu": 40, "ram": 50, "disk": 30, "battery": 90, "plugged": True})
    pump(app, 0.3)
    assert "healthy" in app.health_chip.cget("text").lower()
    app._holo_findings([health.Finding("gpu_hot", health.CRITICAL, "gpu", "The GPU is very hot.", "Close games.")])
    pump(app, 0.3)
    assert "attention" in app.health_chip.cget("text")
    app._holo_select("gpu")
    assert "very hot" in app.part_text.cget("text") and "Serious" in app.part_state.cget("text")
    app.scan_now()                                     # runs the (fake-free) watchdog scan in a thread; must not crash
    pump(app, 3.0)
    assert app.scan_button.cget("state") == "normal"


@pytest.fixture
def tray_app(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "TASKS_FILE", tmp_path / "tasks.json")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "actions.log")
    monkeypatch.setattr(tools, "SCHEDULER", None)
    config.write_json(tmp_path / "settings.json", {"setup_done": True})
    released = []
    inst = _types.SimpleNamespace(listen=lambda cb: None, release=lambda: released.append(1))
    a = gui.App(instance=inst, tray_lib=FAKE_TRAY)
    a.released = released
    pump(a, 0.3)
    yield a
    try:
        a.quit_app()
    except Exception:
        pass


def test_closing_the_window_hides_it_and_keeps_running(tray_app):
    a = tray_app
    assert a.tray_on and a.tray.icon.running
    a._close()
    pump(a, 0.1)
    assert a.state() == "withdrawn" and a.monitor is not None
    assert a.tray.icon.notes and "still running" in a.tray.icon.notes[0][0]
    a._close()
    assert len(a.tray.icon.notes) == 1                           # told only once
    a.show_window()
    pump(a, 0.1)
    assert a.state() == "normal"


def test_toasts_only_when_hidden(tray_app):
    a = tray_app
    a.alert("CPU is at 99%")
    assert a.tray.icon.notes == []                               # window is visible: the chat shows it
    a.withdraw()
    pump(a, 0.1)
    a.alert("CPU is at 99%")
    assert a.tray.icon.notes[-1] == ("Laptop AI warning", "CPU is at 99%")


def test_tray_quit_stops_everything_and_frees_the_lock(tray_app):
    a = tray_app
    a.quit_app()
    assert a.tray.icon is None and a.released == [1]


def test_close_really_quits_when_tray_is_switched_off(tray_app):
    a = tray_app
    a.settings.close_to_tray = False
    a._close()
    assert a.tray.icon is None and a.released == [1]


def test_mic_marker_is_mirrored_on_the_tray_icon(tray_app):
    tray_app._set_mic_note(True)
    assert "microphone is live" in tray_app.tray.icon.title
    tray_app._set_mic_note(False)
    assert tray_app.tray.icon.title == "Laptop AI"


def test_start_with_windows_switch_writes_and_reverts_on_failure(app, monkeypatch):
    calls = []
    monkeypatch.setattr(gui.autostart, "set_enabled", lambda on: (calls.append(on) or (True, "")))
    app.autostart_var.set(True)
    app._set_autostart()
    assert calls == [True] and app.autostart_var.get() is True
    monkeypatch.setattr(gui.autostart, "set_enabled", lambda on: (False, "Couldn't open your startup list"))
    app.autostart_var.set(False)
    app._set_autostart()
    assert app.autostart_var.get() is True and "startup list" in app.app_note.cget("text")


def test_without_a_tray_closing_just_quits(app, monkeypatch):
    assert app.tray_on is False
    destroyed = []
    monkeypatch.setattr(app, "destroy", lambda: destroyed.append(1))
    app._close()
    assert destroyed == [1]
