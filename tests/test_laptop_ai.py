import json
import os
import subprocess
import sys
import time

import pytest

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

from laptop_ai import config, tools                      # noqa: E402
from laptop_ai.brain import Brain, BrainError, ClaudeBackend, OllamaBackend  # noqa: E402
from laptop_ai.monitor import Monitor, check_thresholds  # noqa: E402
from laptop_ai.scheduler import Scheduler                # noqa: E402


def make_executor(mode="ask", answer=True, obey=False):
    s = config.Settings()
    s.autonomy = mode
    s.obey_commands = obey                 # these tests are about the asking machinery (used for things it starts by itself)
    asked = []

    def confirm(text):
        asked.append(text)
        return answer
    return tools.Executor(s, confirm), asked, s


# ---------------- safety gating ----------------
def test_read_tools_never_ask():
    ex, asked, _ = make_executor("ask")
    out = json.loads(ex.run("get_system_status", {}))
    assert out["status"] == "ok" and not asked


def test_kill_always_asks_even_in_auto_safe():
    ex, asked, _ = make_executor("auto_safe", answer=False)
    out = json.loads(ex.run("kill_process", {"name": "whatever.exe"}))
    assert out["status"] == "declined_by_user" and len(asked) == 1


def test_low_risk_asks_in_ask_mode_but_not_auto_safe():
    ex, asked, _ = make_executor("ask", answer=False)
    assert json.loads(ex.run("clean_temp_files", {"dry_run": True}))["status"] == "declined_by_user"
    ex2, asked2, _ = make_executor("auto_safe", answer=False)
    assert json.loads(ex2.run("clean_temp_files", {"dry_run": True}))["status"] == "ok" and not asked2


def test_report_only_blocks_actions_and_hides_them():
    ex, asked, _ = make_executor("report_only")
    assert json.loads(ex.run("add_reminder", {"message": "x", "minutes": 5}))["status"] == "error"
    assert json.loads(ex.run("kill_process", {"pid": 1}))["status"] == "error"
    assert {t.risk for t in ex.available()} == {tools.READ} and not asked


def test_unknown_tool_and_bad_args_do_not_crash():
    ex, _, _ = make_executor("auto_safe")
    assert json.loads(ex.run("format_c_drive", {}))["status"] == "error"
    assert json.loads(ex.run("find_process", {"bogus": 1}))["status"] == "error"


def test_no_arbitrary_command_tool_exists():
    names = " ".join(tools.REGISTRY).lower().replace("run_command", "")
    for bad in ("shell", "exec", "command", "powershell", "delete_file", "write_file"):
        assert bad not in names
    # the one command tool is off unless the user switched it on in Settings > Advanced
    assert tools.ADVANCED == {"run_command"}
    ex, _asked, s = make_executor("auto_safe")
    assert "run_command" not in [t.name for t in ex.available()]


# ---------------- kill_process on a real child ----------------
def test_kill_real_process_and_protections():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        ex, asked, _ = make_executor("ask", answer=True)
        out = json.loads(ex.run("kill_process", {"pid": p.pid}))
        assert out["status"] == "ok" and out["closed"] == 1 and asked
        p.wait(5)
        assert p.returncode is not None
    finally:
        if p.poll() is None:
            p.kill()
    # never kills itself or protected names
    assert json.loads(tools.kill_process(pid=os.getpid()))["status"] == "error"
    assert json.loads(tools.kill_process(pid=1))["status"] == "error"


def test_kill_describe_mentions_unsaved_work():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        text = tools.REGISTRY["kill_process"].describe({"pid": p.pid})
        assert "Unsaved work" in text
    finally:
        p.kill()


# ---------------- temp cleaning ----------------
def test_clean_temp_only_old_files(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "TEMP_DIR", tmp_path)
    old, new = tmp_path / "old.tmp", tmp_path / "new.tmp"
    old.write_text("x" * 1000)
    new.write_text("y")
    os.utime(old, (time.time() - 100 * 3600,) * 2)
    dry = json.loads(tools.clean_temp_files(24, dry_run=True))
    assert dry["files"] == 1 and old.exists()
    real = json.loads(tools.clean_temp_files(24))
    assert real["deleted"] == 1 and not old.exists() and new.exists()


def test_clean_temp_ignores_symlinks(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "TEMP_DIR", tmp_path)
    target = tmp_path.parent / "precious.txt"
    target.write_text("keep me")
    link = tmp_path / "link"
    link.symlink_to(target)
    os.utime(target, (time.time() - 100 * 3600,) * 2)
    tools.clean_temp_files(1)
    assert target.exists()


# ---------------- scheduler ----------------
def test_scheduler_runs_once_and_repeats(tmp_path):
    now = [1000.0]
    s = Scheduler(tmp_path / "t.json", clock=lambda: now[0])
    ran = []
    s._runner = ran.append
    s.add("remind", 60, message="once")
    s.add("system_report", 60, every_s=300)
    assert s.run_due() == 0
    now[0] += 61
    assert s.run_due() == 2 and len(ran) == 2
    assert [t["kind"] for t in s.list()] == ["system_report"]      # one-off gone, repeat kept
    now[0] += 301
    assert s.run_due() == 1


def test_scheduler_persists_and_survives_bad_runner(tmp_path):
    now = [0.0]
    s = Scheduler(tmp_path / "t.json", clock=lambda: now[0])
    s._runner = lambda t: 1 / 0
    t = s.add("remind", 1, message="m")
    assert Scheduler(tmp_path / "t.json").list()[0]["id"] == t["id"]
    now[0] = 5
    assert s.run_due() == 1                                          # exception swallowed


def test_reminder_validation():
    assert json.loads(tools.add_reminder("x", 0))["status"] == "error"
    assert json.loads(tools.schedule_task("clean_temp", 1))["status"] == "error"
    assert json.loads(tools.schedule_task("rm -rf", 10))["status"] == "error"


# ---------------- monitor ----------------
def test_thresholds_and_cooldown():
    s = config.Settings()
    stats = {"cpu": 95, "ram": 50, "disk": 50, "battery": 10, "plugged": False}
    keys = [k for k, _ in check_thresholds(stats, s)]
    assert keys == ["cpu", "battery"]
    assert check_thresholds({**stats, "plugged": True}, s)[0][0] == "cpu"
    got = []
    m = Monitor(s, got.append)
    assert len(m.tick(stats, now=1000)) == 2
    assert m.tick(stats, now=1100) == []                             # cooldown
    assert len(m.tick(stats, now=1000 + Monitor.COOLDOWN_S)) == 2


def test_first_reading_updates_gauges_without_alerting():
    s = config.Settings()
    got, seen = [], []
    m = Monitor(s, got.append, seen.append)
    stats = {"cpu": 100, "ram": 10, "disk": 10, "battery": None, "plugged": None}
    assert m.tick(stats, now=5, alert=False) == [] and got == [] and seen == [stats]


# ---------------- brain (fake HTTP) ----------------
class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


def test_ollama_tool_loop():
    ex, asked, s = make_executor("ask")
    calls = []

    def post(url, **kw):
        calls.append(kw["json"])
        if len(calls) == 1:
            return FakeResp({"message": {"content": "", "tool_calls": [
                {"function": {"name": "get_current_time", "arguments": {}}}]}})
        return FakeResp({"message": {"content": "It is time."}})
    brain = Brain(s, ex, offline=OllamaBackend(s, post))
    seen = []
    assert brain.ask("tell me what hour it is in Tokyo", lambda n, a: seen.append(n)) == "It is time."
    assert seen == ["get_current_time"]
    assert calls[1]["messages"][-1]["role"] == "tool"
    assert "get_system_status" in {t["function"]["name"] for t in calls[0]["tools"]}


def test_claude_tool_loop_and_format(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    ex, _, s = make_executor("ask")
    sent = []

    def post(url, headers, json, timeout):
        sent.append((headers, json))
        if len(sent) == 1:
            return FakeResp({"content": [{"type": "tool_use", "id": "t1", "name": "get_current_time", "input": {}}]})
        return FakeResp({"content": [{"type": "text", "text": "Done."}]})
    brain = Brain(s, ex, online=ClaudeBackend(s, post))
    s.backend = "online"
    assert brain.ask("clock question?") == "Done."
    assert sent[0][0]["x-api-key"] == "k" and sent[0][1]["tools"][0]["input_schema"]
    last = sent[1][1]["messages"][-1]
    assert last["role"] == "user" and last["content"][0]["type"] == "tool_result"


def test_auto_falls_back_to_online(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    ex, _, s = make_executor("ask")
    import requests

    def dead(*a, **k):
        raise requests.ConnectionError()
    online_post = lambda url, headers, json, timeout: FakeResp({"content": [{"type": "text", "text": "online!"}]})  # noqa
    brain = Brain(s, ex, OllamaBackend(s, dead), ClaudeBackend(s, online_post))
    assert brain.ask("hi") == "online!" and "online" in brain.last_backend


def test_no_backend_gives_helpful_message(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ex, _, s = make_executor("ask")
    import requests

    def dead(*a, **k):
        raise requests.ConnectionError()
    reply = Brain(s, ex, OllamaBackend(s, dead), ClaudeBackend(s, dead)).ask("hi")
    assert "ollama pull" in reply and "ANTHROPIC_API_KEY" in reply


def test_api_key_never_saved_to_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-123")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    config.Settings().save()
    assert "sk-secret" not in (tmp_path / "s.json").read_text()


def test_missing_ollama_model_message():
    ex, _, s = make_executor("ask")
    be = OllamaBackend(s, lambda url, **k: FakeResp({}, 404))
    with pytest.raises(BrainError, match="ollama pull"):
        be.run([{"role": "user", "content": "x"}], "sys", [], ex, lambda n, a: None)


# ---------------- small-model tool-call mistakes (seen on a real llama3.2) ----------------
SCHEMA_ECHO = {"type": "object", "properties": {}}


def test_schema_echo_args_are_accepted():
    ex, asked, _ = make_executor("ask")
    out = json.loads(ex.run("get_system_status", dict(SCHEMA_ECHO)))
    assert out["status"] == "ok" and "cpu_percent" in out and not asked


def test_schema_echo_on_changing_tool_still_asks_first():
    ex, asked, _ = make_executor("ask", answer=False)
    out = json.loads(ex.run("clean_temp_files", dict(SCHEMA_ECHO)))
    assert out["status"] == "declined_by_user" and len(asked) == 1


def test_nested_real_values_are_unwrapped_and_fragments_dropped():
    t = tools.REGISTRY["list_top_processes"]
    assert tools.clean_args(t, {"type": "object", "properties": {"sort_by": "cpu", "limit": "3"}}) == \
        {"sort_by": "cpu", "limit": 3}
    assert tools.clean_args(t, {"properties": {"sort_by": {"type": "string"}}}) == {}
    assert tools.clean_args(t, {"limit": "5", "bogus": 1}) == {"limit": 5}


def test_string_booleans_and_bad_numbers():
    t = tools.REGISTRY["clean_temp_files"]
    assert tools.clean_args(t, {"dry_run": "false"}) == {"dry_run": False}
    assert tools.clean_args(t, {"dry_run": "true", "older_than_hours": "abc"}) == {"dry_run": True}


def test_error_messages_guide_the_model_and_name_real_tools():
    ex, _, _ = make_executor("auto_safe")
    unknown = json.loads(ex.run("get_cpu_usage", {}))["message"]
    assert "get_system_status" in unknown and "Do not invent" in unknown
    missing = json.loads(ex.run("find_process", {}))["message"]
    assert "needs: name" in missing


def test_whole_conversation_with_schema_echo_ends_in_a_real_answer():
    ex, _, s = make_executor("ask")
    seen = []

    def post(url, **kw):
        seen.append(kw["json"])
        if len(seen) == 1:
            return FakeResp({"message": {"content": "", "tool_calls": [
                {"function": {"name": "get_system_status", "arguments": SCHEMA_ECHO}}]}})
        tool_msg = json.loads(kw["json"]["messages"][-1]["content"])
        assert tool_msg["status"] == "ok"                       # the model got real data, not an error
        return FakeResp({"message": {"content": "CPU is fine."}})
    brain = Brain(s, ex, offline=OllamaBackend(s, post))
    assert brain.ask("summarise the situation for me") == "CPU is fine."
    assert seen[0]["options"]["temperature"] == 0.2


def test_system_status_labels_are_unambiguous():
    d = json.loads(tools.get_system_status())
    assert isinstance(d["battery"], str) and d["battery"]            # one plain sentence about the battery
    assert "uptime_hours" not in d and "pc_running_since_last_restart_hours" in d
    assert "battery" not in "pc_running_since_last_restart_hours"


# ---------------- overrides for voice and phone requests ----------------
def test_phone_override_is_read_only_even_when_user_allows_everything():
    ex, asked, s = make_executor("auto_safe")
    ex.override = "read_only"
    assert {t.risk for t in ex.available()} == {tools.READ}
    assert json.loads(ex.run("kill_process", {"pid": 1}))["status"] == "error"
    assert json.loads(ex.run("add_reminder", {"message": "x", "minutes": 5}))["status"] == "error"
    assert json.loads(ex.run("get_system_status", {}))["status"] == "ok" and not asked
    ex.override = None
    assert len(ex.available()) == len(tools.REGISTRY) - len(tools.ADVANCED)      # back to normal afterwards


def test_wake_word_override_asks_even_in_auto_safe():
    ex, asked, _ = make_executor("auto_safe", answer=False)
    assert json.loads(ex.run("clean_temp_files", {"dry_run": True}))["status"] == "ok" and not asked
    ex.override = "ask_all"
    assert json.loads(ex.run("clean_temp_files", {"dry_run": True}))["status"] == "declined_by_user"
    assert len(asked) == 1
    assert json.loads(ex.run("add_reminder", {"message": "x", "minutes": 1}))["status"] == "ok"   # SAFE stays free


# ---------------------------------------------------------------- "do what I tell you, ask only when it acts on its own"
def test_commands_the_person_gives_run_without_a_question():
    ex, asked, _ = make_executor("ask", answer=False, obey=True)
    for source in ("user",):
        assert json.loads(ex.run("clean_temp_files", {"dry_run": True}, source=source))["status"] == "ok"
        assert json.loads(ex.run("play_local_music", {"query": "zzzz"}, source=source))["status"] in ("ok", "error")
    ex.override = "ask_all"                                         # said out loud with "Trix": still their own command
    assert json.loads(ex.run("clean_temp_files", {"dry_run": True}, source="user"))["status"] == "ok"
    assert asked == []


def test_things_it_starts_by_itself_still_ask():
    ex, asked, _ = make_executor("ask", answer=False, obey=True)
    for source in ("scheduler", "watchdog"):
        assert json.loads(ex.run("clean_temp_files", {"dry_run": True}, source=source))["status"] == "declined_by_user"
    assert len(asked) == 2


def test_actions_the_model_picks_for_your_request_run_without_asking():
    ex, asked, _ = make_executor("ask", answer=False, obey=True)
    assert json.loads(ex.run("clean_temp_files", {"dry_run": True}, source="ai"))["status"] == "ok"
    assert json.loads(ex.run("kill_process", {"name": "whatever.exe"}, source="ai"))["status"] != "declined_by_user"   # HIGH: still no question
    assert asked == []


def test_raw_powershell_and_the_phone_never_skip_the_rules():
    ex, asked, s = make_executor("ask", answer=False, obey=True)
    s.allow_commands = True
    assert json.loads(ex.run("run_command", {"command": "echo hi"}, source="user"))["status"] == "declined_by_user"
    ex.override = "read_only"                                       # a phone message can still change nothing
    assert json.loads(ex.run("clean_temp_files", {"dry_run": True}, source="user"))["status"] == "error"
    s.autonomy = "report_only"
    ex.override = None
    assert json.loads(ex.run("clean_temp_files", {"dry_run": True}, source="user"))["status"] == "error"


def test_switching_it_off_brings_the_questions_back():
    ex, asked, s = make_executor("ask", answer=False, obey=False)
    assert json.loads(ex.run("clean_temp_files", {"dry_run": True}, source="user"))["status"] == "declined_by_user"
    assert config.Settings().obey_commands is True                  # on by default
