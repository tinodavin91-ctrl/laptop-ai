"""The home-page laptop model: status logic (pure) and the drawing (needs a display)."""
import math
import os
import sys
import time

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

import pytest  # noqa: E402

from laptop_ai import config, health, holo_model as hm, intents  # noqa: E402


def F(key, level, area, msg="m", advice=""):
    return health.Finding(key, level, area, msg, advice)


def test_everything_ok_by_default():
    st = hm.compute({"cpu": 20, "ram": 40, "disk": 50, "battery": 80, "plugged": False}, [])
    assert all(s.level == hm.OK for s in st.values())
    assert st["cpu"].value == "20%" and st["battery"].value == "80%"
    assert hm.summary(st) == (hm.OK, "All systems healthy")


def test_threshold_warnings_light_the_right_parts():
    st = hm.compute({"cpu": 95, "ram": 97, "disk": 99, "battery": 10, "plugged": False}, [], config.Settings())
    assert {k for k, s in st.items() if s.level == hm.WARN} == {"cpu", "ram", "ssd", "battery"}
    assert hm.compute({"battery": 10, "plugged": True}, [])["battery"].level == hm.OK


def test_findings_map_to_parts_and_the_worst_level_wins():
    st = hm.compute({"cpu": 95}, [F("cpu_hot", "critical", "cpu", "Too hot", "Clean vents"),
                                 F("av_off", "critical", "malware"), F("whea", "warning", "motherboard"),
                                 F("gpu_hot", "critical", "gpu"), F("disk_errors", "warning", "disk")])
    assert st["cpu"].level == hm.CRIT and "Too hot" in st["cpu"].details and st["cpu"].advice == "Clean vents"
    assert st["fan"].level == hm.CRIT                          # heat also lights the fan
    assert st["security"].level == hm.CRIT and st["board"].level == hm.WARN
    assert st["gpu"].level == hm.CRIT and st["ssd"].level == hm.WARN
    assert st["display"].level == hm.OK and st["network"].level == hm.OK
    level, text = hm.summary(st)
    assert level == hm.CRIT and "attention" in text


def test_unknown_area_goes_to_the_board_and_single_issue_wording():
    st = hm.compute({}, [F("x", "warning", "weird")])
    assert st["board"].level == hm.WARN and hm.summary(st)[1] == "1 part needs attention"


def test_camera_projects_and_hides_faces_that_point_away():
    from laptop_ai import holo3d as g
    cam = g.Camera(700, 500)
    top = next(f for f in g.part_faces("cpu") if f.n[1] > 0.9)
    bottom = next(f for f in g.part_faces("cpu") if f.n[1] < -0.9)
    assert cam.faces_toward_viewer(top) and not cam.faces_toward_viewer(bottom)
    left, right = cam.project((-60, 0, 0)), cam.project((60, 0, 0))
    assert left[0] < 350 < right[0] and abs((left[0] + right[0]) / 2 - 350) < 40
    assert cam.project((0, 0, 30))[1] > cam.project((0, 0, -30))[1]          # nearer things are lower on screen
    assert cam.project((0, 0, 30))[2] < cam.project((0, 0, -30))[2]          # and closer to the camera
    cam.yaw = math.pi                                                         # seen from behind the top still shows,
    assert cam.faces_toward_viewer(next(f for f in g.screen_faces() if abs(f.n[2] + 0.98) < 0.05))   # and so does the screen's back


def test_every_part_has_geometry_and_a_label_spot():
    from laptop_ai import holo3d as g
    for pid in g.PART_IDS:
        assert len(g.part_faces(pid)) >= 6
        assert len(g.top_of(pid)) == 3 and len(g.centre_of(pid)) == 3
    assert set(g.PART_IDS) == {p.id for p in hm.PARTS}


def test_picking_uses_the_nearest_shape_under_the_cursor():
    from laptop_ai import holo3d as g
    square = [0, 0, 10, 0, 10, 10, 0, 10]
    small = [4, 4, 6, 4, 6, 6, 4, 6]
    drawn = [("board", square), ("cpu", small)]                              # cpu was painted after (in front of) the board
    assert g.pick(drawn, 5, 5) == "cpu" and g.pick(drawn, 1, 1) == "board" and g.pick(drawn, 20, 20) is None
    assert g.point_in_poly(1, 1, square) and not g.point_in_poly(11, 1, square)


@pytest.mark.parametrize("pid", [p.id for p in hm.PARTS])
def test_every_ask_button_is_understood_by_the_router(pid):
    got = intents.route(hm.BY_ID[pid].ask)
    assert got is not None, hm.BY_ID[pid].ask


# ---------------------------------------------------------------- drawing
NO_DISPLAY = sys.platform != "win32" and not os.environ.get("DISPLAY")


def pump(w, secs):
    end = time.time() + secs
    while time.time() < end:
        w.update()
        time.sleep(0.01)


@pytest.mark.skipif(NO_DISPLAY, reason="no display available")
def test_canvas_draws_animates_and_reacts():
    tk = pytest.importorskip("tkinter")
    from laptop_ai.holo import HoloLaptop
    root = tk.Tk()
    picked = []
    h = HoloLaptop(root, on_select=picked.append, width=560, height=380)
    h.pack(fill="both", expand=True)
    h.start()
    pump(root, 2.2)                                    # boot-in finishes, a few animation frames run
    assert h._built and h.booted
    st = hm.compute({"cpu": 99, "ram": 10, "disk": 10, "battery": 50}, [F("gpu_hot", "critical", "gpu")])
    h.set_status(st, {"cpu": 99, "battery": 50})
    pump(root, 0.4)
    assert h.frame_ms > 0 and h.status["gpu"].level == hm.CRIT
    x, y = h.screen_pos("cpu")                         # hover then click the CPU
    h.event_generate("<Motion>", x=int(x), y=int(y))
    pump(root, 0.15)
    assert h.hover == "cpu"
    h.event_generate("<ButtonPress-1>", x=int(x), y=int(y))
    h.event_generate("<ButtonRelease-1>", x=int(x), y=int(y))
    pump(root, 0.2)
    assert picked == ["cpu"] and h.selected == "cpu"
    yaw = h.yaw0                                       # dragging turns the model instead of selecting
    h.event_generate("<ButtonPress-1>", x=100, y=100)
    h.event_generate("<B1-Motion>", x=160, y=110)
    h.event_generate("<ButtonRelease-1>", x=160, y=110)
    pump(root, 0.1)
    assert h.yaw0 != yaw and picked == ["cpu"]
    h._reset_view()
    assert h.yaw0 == 0.0
    h.set_scanning(True)
    pump(root, 0.2)
    root.geometry("420x300")                           # resizing re-fits the model without errors
    pump(root, 0.5)
    assert h.cam.width <= 420 and h._built
    h.stop()
    root.destroy()


def test_device_faults_light_the_matching_component():
    st = hm.compute({}, [F("dev_cam", "warning", "camera"), F("dev_snd", "warning", "audio"), F("dev_usb", "warning", "usb"),
                         F("dev_kb", "warning", "input")])
    lit = {k for k, v in st.items() if v.level != hm.OK}
    assert lit == {"webcam", "mic", "speakers", "usb", "keyboard", "touchpad"}
    assert hm.summary(st)[1] == "6 parts need attention"


def test_windows_device_classes_map_to_areas():
    from laptop_ai import health
    out = health.check_devices(lambda cmd: '[{"Name":"Integrated Camera","PNPClass":"Camera","ConfigManagerErrorCode":10},'
                                           '{"Name":"Bluetooth Radio","PNPClass":"Bluetooth","ConfigManagerErrorCode":43}]')
    assert [f.area for f in out] == ["camera", "bluetooth"]


def test_faulty_parts_offer_a_fix_and_healthy_ones_do_not():
    st = hm.compute({"cpu": 99, "ram": 97, "disk": 99, "battery": 50}, [])
    assert [f.kind for f in hm.fixes("cpu", st["cpu"])] == ["hog"]
    assert hm.fixes("ram", st["ram"])[0].arg == "memory"
    assert hm.fixes("ssd", st["ssd"])[0].arg == "Clean my temp files"
    assert hm.fixes("display", st["display"]) == [] and hm.fixes("battery", st["battery"]) == []
    for f in (x for p in hm.PARTS for x in hm.fixes(p.id, hm.Status(level=hm.WARN)) if x.kind == "send"):
        assert intents.route(f.arg) is not None, f.arg


def test_voice_orb_bars_change_with_the_state():
    pytest.importorskip("tkinter")
    import random
    from laptop_ai import orb
    rnd = lambda: random.Random(1)
    quiet, busy = orb.bar_levels("idle", 1.0, 72, rnd()), orb.bar_levels("listening", 1.0, 72, rnd())
    assert len(quiet) == len(busy) == 72 and all(0 < v <= 1 for v in quiet + busy)
    assert max(quiet) < 0.3 < sum(busy) / 72
    think = orb.bar_levels("thinking", 0.5, 72, rnd())
    assert max(think) > 0.7 and min(think) < 0.2                        # one bright bump travels round the ring
    assert orb.bar_levels("thinking", 0.9, 72, rnd()) != think
    assert set(orb.COLOURS) == {"idle", "listening", "thinking", "speaking", "off"}
