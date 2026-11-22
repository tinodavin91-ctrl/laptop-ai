"""find_files, notes and the morning report. Uses a fake home folder; nothing real is touched."""
import json
import os
import time
from datetime import datetime

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

import pytest  # noqa: E402

from laptop_ai import config, tools, tools_extra  # noqa: E402
from laptop_ai.scheduler import Scheduler  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_extra.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(config, "NOTES_FILE", tmp_path / "notes.json")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(tools, "SCHEDULER", Scheduler(tmp_path / "tasks.json"))
    return tmp_path


def run(name, **args):
    return json.loads(tools.REGISTRY[name].func(**args))


def make(path, age_days=0, size=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    t = time.time() - age_days * 86400
    os.utime(path, (t, t))


# ---------------------------------------------------------------- find_files
def test_find_files_by_name_newest_first_and_only_in_personal_folders(home):
    make(home / "Documents" / "CV_final.pdf", age_days=1)
    make(home / "Downloads" / "cv draft.docx", age_days=5)
    make(home / "Documents" / "budget.xlsx")
    make(home / "AppData" / "cv_secret.txt")                  # not a personal folder
    make(home / "Documents" / "node_modules" / "cv.js")       # skipped directory
    r = run("find_files", query="cv")
    assert r["found"] == 2
    assert [Path_name(x["path"]) for x in r["showing"]] == ["CV_final.pdf", "cv draft.docx"]


def Path_name(p):
    return os.path.basename(p)


def test_find_files_days_filter_folder_filter_and_limit(home):
    for i in range(30):
        make(home / "Downloads" / f"report{i}.txt", age_days=i)
    assert run("find_files", query="report", days=3.5)["found"] == 4
    assert len(run("find_files", query="report", limit=100)["showing"]) == 25        # hard cap
    assert run("find_files", query="report", folder="Documents")["status"] == "error"   # no such folder here
    make(home / "Documents" / "other.txt")
    assert run("find_files", query="report", folder="Documents")["found"] == 0
    assert run("find_files", query="report", folder="Windows")["status"] == "error"   # not an allowed folder
    assert run("find_files", query="")["status"] == "error"


def test_find_files_never_reads_contents_and_stops_when_too_big(home, monkeypatch):
    make(home / "Documents" / "a.txt")
    monkeypatch.setattr(tools_extra, "MAX_SCANNED", 0)
    assert run("find_files", query="a")["search_was_cut_short"] is True


# ---------------------------------------------------------------- notes
def test_notes_add_search_delete(home):
    a = run("add_note", text="call Kwame tomorrow")
    run("add_note", text="buy data bundle")
    assert run("add_note", text="  ")["status"] == "error"
    assert run("search_notes", query="kwame")["found"] == 1
    assert run("search_notes")["found"] == 2
    assert run("delete_note", note_id="nope")["status"] == "error"
    assert run("delete_note", note_id=a["id"])["deleted"] is True
    assert run("search_notes")["found"] == 1


def test_notes_survive_a_corrupt_file(home):
    config.NOTES_FILE.write_text("{not json", encoding="utf-8")
    assert run("search_notes")["found"] == 0
    assert run("add_note", text="still works")["status"] == "ok"


def test_delete_note_asks_first_but_adding_does_not():
    s = config.Settings()
    s.autonomy = "ask"
    s.obey_commands = False
    asked = []
    ex = tools.Executor(s, lambda t: asked.append(t) or False)
    ex.run("add_note", {"text": "hi"}, source="user")
    assert asked == []
    ex.run("delete_note", {"note_id": "x"}, source="user")
    assert len(asked) == 1


# ---------------------------------------------------------------- morning report
def test_morning_report_schedules_once_a_day_at_the_right_time(home):
    r = run("schedule_morning_report", time="07:30")
    assert r["status"] == "ok"
    run("schedule_morning_report", time="08:00")              # replaces, never duplicates
    tasks = [t for t in tools.get_scheduler().list() if t["kind"] == "morning_report"]
    assert len(tasks) == 1 and tasks[0]["every_s"] == 86400
    assert datetime.fromtimestamp(tasks[0]["next_run"]).strftime("%H:%M") == "08:00"
    for bad in ("25:00", "7", "soon", "07:61"):
        assert run("schedule_morning_report", time=bad)["status"] == "error"


def test_seconds_until_rolls_to_tomorrow():
    now = datetime(2026, 11, 5, 10, 0)
    assert tools_extra._seconds_until("11:00", now) == 3600
    assert tools_extra._seconds_until("09:00", now) == 23 * 3600


def test_morning_report_text():
    now = time.time()
    status = {"cpu_percent": 12, "ram_percent": 55, "disk_percent": 80, "disk_free": "40 GB",
              "battery_percent": 64, "plugged_in": True, "pc_running_since_last_restart_hours": 300.0}
    tasks = [{"kind": "remind", "message": "stretch", "next_run": now + 600},
             {"kind": "remind", "message": "far away", "next_run": now + 10 * 86400}]
    text = tools_extra.morning_report_text(status, tasks, notes=2, now=now)
    assert "Good morning" in text and "64% and charging" in text and "stretch" in text
    assert "far away" not in text and "12 days" in text and "2 saved notes" in text
    quiet = tools_extra.morning_report_text({**status, "battery_percent": None,
                                             "pc_running_since_last_restart_hours": 5}, [], 0, now)
    assert "desktop PC" in quiet and "No reminders" in quiet and "restarted" not in quiet
