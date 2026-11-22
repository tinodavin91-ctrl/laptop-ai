"""3.5: the Home-page rotating tip strip."""
import datetime as _dt

from laptop_ai import tips


def test_no_first_run_date_means_no_stat_line():
    assert tips.stat_line("") == ""
    assert tips.stat_line("not-a-date") == ""


def test_stat_line_counts_days_since_first_run():
    assert tips.stat_line(_dt.date.today().isoformat()) == "Looking after this PC since today."
    yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    assert tips.stat_line(yesterday) == "Looking after this PC for 1 day."
    week_ago = (_dt.date.today() - _dt.timedelta(days=7)).isoformat()
    assert tips.stat_line(week_ago) == "Looking after this PC for 7 days."


def test_rotation_puts_the_stat_first_then_all_the_tips():
    today = _dt.date.today().isoformat()
    rot = tips.rotation(today)
    assert rot[0].startswith("Looking after this PC")
    assert rot[1:] == tips.TIPS


def test_rotation_with_no_stat_is_just_the_tips():
    assert tips.rotation("") == tips.TIPS
