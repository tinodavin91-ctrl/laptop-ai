"""Open any installed app, and the opt-in PowerShell tool. Fakes only: nothing is launched or run."""
import json
import os
import subprocess

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

import pytest  # noqa: E402

from laptop_ai import apps, brain, config, tools, tools_extra  # noqa: E402

CATALOG = [{"name": "Xbox", "kind": "store", "target": "Microsoft.GamingApp_8wekyb3d8bbwe!Microsoft.Xbox"},
           {"name": "Spotify", "kind": "shortcut", "target": r"C:\x\Spotify.lnk"},
           {"name": "Visual Studio Code", "kind": "shortcut", "target": r"C:\x\Code.lnk"},
           {"name": "Visual Studio Installer", "kind": "shortcut", "target": r"C:\x\vsi.lnk"}]


# ---------------------------------------------------------------- discovery
def test_scan_shortcuts_skips_uninstallers_and_help(tmp_path):
    d = tmp_path / "Programs" / "Vendor"
    d.mkdir(parents=True)
    for n in ("Spotify.lnk", "Uninstall Spotify.lnk", "Spotify Help.lnk", "readme.lnk", "notes.txt"):
        (d / n).write_text("x")
    assert [a["name"] for a in apps.scan_shortcuts([tmp_path / "Programs", tmp_path / "missing"])] == ["Spotify"]


def test_installed_apps_merges_dedupes_and_sorts(tmp_path):
    (tmp_path / "Zed.lnk").write_text("x")
    (tmp_path / "Xbox.lnk").write_text("x")
    store = lambda: [{"name": "xbox", "kind": "store", "target": "id"}, {"name": "Alarms", "kind": "store", "target": "a"}]  # noqa: E731
    names = [a["name"] for a in apps.installed_apps(dirs=[tmp_path], store=store)]
    assert names == ["Alarms", "Xbox", "Zed"]                  # the shortcut wins the duplicate


def test_find_ranks_exact_then_prefix_then_words():
    assert [a["name"] for a in apps.find("xbox", CATALOG)] == ["Xbox"]
    assert [a["name"] for a in apps.find("visual studio", CATALOG)] == ["Visual Studio Code", "Visual Studio Installer"]
    assert [a["name"] for a in apps.find("code studio", CATALOG)] == ["Visual Studio Code"]
    assert apps.find("", CATALOG) == [] and apps.find("zzz", CATALOG) == []


def test_store_apps_failure_is_just_empty(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError
    monkeypatch.setattr(apps.subprocess, "run", boom)
    assert apps.powershell_start_apps() == []


# ---------------------------------------------------------------- open_app
@pytest.fixture
def windows(monkeypatch):
    launched = []
    monkeypatch.setattr(tools.platform, "system", lambda: "Windows")
    monkeypatch.setattr(apps, "installed_apps", lambda *a, **k: CATALOG)
    monkeypatch.setattr(apps, "launch", lambda app: launched.append(app["name"]))
    monkeypatch.setattr(tools.os, "startfile", lambda t: launched.append(t), raising=False)
    return launched


def call(tool_name, **args):
    return json.loads(tools.REGISTRY[tool_name].func(**args))


def test_open_any_installed_app(windows):
    assert call("open_app", name="xbox")["opened"] == "Xbox"
    assert call("open_app", name="spotify")["status"] == "ok"
    assert windows == ["Xbox", "Spotify"]
    assert call("open_app", name="notepad")["status"] == "ok" and windows[-1] == "notepad.exe"   # built-ins still fast


def test_open_app_unknown_and_ambiguous(windows):
    assert "couldn't find" in call("open_app", name="nonexistent thing")["message"]
    r = call("open_app", name="visual studio")
    assert r["status"] == "error" and "Visual Studio Code" in r["message"] and windows == []


def test_list_apps(windows):
    assert call("list_apps", query="studio")["apps"] == ["Visual Studio Code", "Visual Studio Installer"]
    assert call("list_apps")["found"] == 4


def test_open_website_only_http(monkeypatch):
    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", opened.append)
    assert call("open_website", url="youtube.com")["opened"] == "https://youtube.com"
    assert call("open_website", url="file:///C:/Windows/System32/cmd.exe")["status"] == "error"
    assert call("open_website", url="javascript:alert(1)")["status"] == "error"
    assert call("open_website", url="localhost:8080")["opened"] == "https://localhost:8080"
    assert opened == ["https://youtube.com", "https://localhost:8080"]


class FakeExec:
    def __init__(self):
        self.calls = []

    def run(self, name, args, source="user"):
        self.calls.append((name, args))
        return json.dumps({"status": "ok"})


def test_quick_open_handles_any_unambiguous_installed_app(windows):
    ex = FakeExec()
    assert brain.quick_open("open xbox", ex, lambda n, a: None) == "Opened Xbox."
    assert brain.quick_open("open visual studio", ex, lambda n, a: None) is None      # ambiguous: let the AI ask
    assert brain.quick_open("open my bank account", ex, lambda n, a: None) is None
    assert ex.calls == [("open_app", {"name": "Xbox"})]


# ---------------------------------------------------------------- run_command
class Fake:
    def __init__(self, stdout="hi\n", stderr="", code=0, error=None):
        self.stdout, self.stderr, self.returncode, self.error, self.seen = stdout, stderr, code, error, []

    def __call__(self, argv, **kw):
        self.seen.append((argv, kw))
        if self.error:
            raise self.error
        return self


def executor(allow=True, mode="ask", answer=True):
    s = config.Settings()
    s.allow_commands, s.autonomy = allow, mode
    asked = []
    return tools.Executor(s, lambda t: asked.append(t) or answer), asked


def test_run_command_is_hidden_and_refused_until_switched_on():
    ex, asked = executor(allow=False)
    assert "run_command" not in [t.name for t in ex.available()]
    assert "switched off" in json.loads(ex.run("run_command", {"command": "dir"}))["message"]
    assert asked == []
    ex2, _ = executor(allow=True)
    assert "run_command" in [t.name for t in ex2.available()]


def test_run_command_always_asks_even_in_auto_safe_and_shows_the_command(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(tools_extra, "RUNNER", fake)
    ex, asked = executor(mode="auto_safe")
    r = json.loads(ex.run("run_command", {"command": "Get-Date"}, source="ai"))
    assert r["status"] == "ok" and r["output"] == "hi\n" and r["exit_code"] == 0
    assert len(asked) == 1 and "Get-Date" in asked[0]
    assert fake.seen[0][1]["timeout"] == 60 and fake.seen[0][1]["stdin"] == subprocess.DEVNULL


def test_declined_command_never_runs(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(tools_extra, "RUNNER", fake)
    ex, _ = executor(answer=False)
    assert json.loads(ex.run("run_command", {"command": "Get-Date"}))["status"] == "declined_by_user"
    assert fake.seen == []


def test_run_command_blocked_from_phone_and_report_only(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(tools_extra, "RUNNER", fake)
    ex, asked = executor()
    ex.override = "read_only"                                     # a message from the phone
    assert "run_command" not in [t.name for t in ex.available()]
    assert "can't make changes" in json.loads(ex.run("run_command", {"command": "dir"}))["message"]
    assert fake.seen == [] and asked == []


def test_run_command_limits(monkeypatch):
    monkeypatch.setattr(tools_extra, "RUNNER", Fake(stdout="x" * 9000))
    assert call("run_command", command="big")["output"].endswith("…(cut off)")
    assert call("run_command", command="  ")["status"] == "error"
    assert call("run_command", command="a" * 3000)["status"] == "error"
    monkeypatch.setattr(tools_extra, "RUNNER", Fake(error=subprocess.TimeoutExpired("x", 60)))
    assert "longer than 60" in call("run_command", command="sleep")["message"]
    monkeypatch.setattr(tools_extra, "RUNNER", Fake(error=FileNotFoundError()))
    assert call("run_command", command="x")["status"] == "error"


def test_powershell_is_started_without_a_profile_or_prompts(monkeypatch):
    monkeypatch.setattr(tools_extra.os, "name", "nt")
    assert tools_extra._argv("Get-Date") == ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Date"]


def test_list_services_off_windows_is_a_clear_error():
    r = call("list_services")
    assert r["status"] in ("ok", "error")
