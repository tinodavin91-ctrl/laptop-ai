"""3.0 tests: welcome screen name, health-history gaps and speed, network scan and map, phone link, PC link."""
import json
import os
import time
from pathlib import Path

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

import pytest  # noqa: E402

from laptop_ai import config, intents, netmap, peers, phonelink, tools, tools_v4, trends, trendview, welcome  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "app" / "actions.log")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "app" / "settings.json")
    monkeypatch.setattr(tools, "SCHEDULER", None)
    return tmp_path / "app"


# ---------------------------------------------------------------- the welcome screen and the name
def test_a_fresh_install_has_no_name_so_it_never_says_tino(home):
    s = config.Settings.load()
    assert s.user_name == "" and s.setup_done is False
    assert welcome.greeting_for(s.user_name, 20) == "Good evening"


def test_an_old_install_with_the_leftover_hardcoded_tino_default_still_asks(home):
    # every pre-3.0 install saved user_name="Tino" by default, whether or not the person was actually called that,
    # so that alone must never count as "the welcome screen already ran" — otherwise everyone upgrading keeps "Tino"
    config.write_json(config.SETTINGS_FILE, {"user_name": "Tino"})
    s = config.Settings.load()
    assert s.setup_done is False and s.user_name == "Tino"       # kept so the welcome screen can pre-fill it


def test_an_old_install_with_a_real_name_already_chosen_by_hand_skips_the_welcome(home):
    config.write_json(config.SETTINGS_FILE, {"user_name": "Ama"})
    s = config.Settings.load()
    assert s.setup_done is True and s.user_name == "Ama"


def test_name_is_tidied_and_greeting_follows_the_time(home):
    assert welcome.clean_name("  Ama   Mensah <b>\n") == "Ama Mensah b"
    assert welcome.clean_name("x" * 90) == "x" * 24
    assert welcome.clean_name("???") == ""
    assert welcome.greeting_for("kofi", 8) == "Good morning, kofi"
    assert welcome.greeting_for("kofi", 14) == "Good afternoon, kofi"
    assert welcome.greeting_for("kofi", 21) == "Good evening, kofi"


def test_the_name_survives_a_save_and_load(home):
    s = config.Settings.load()
    s.user_name, s.setup_done = "Efua", True
    s.save()
    again = config.Settings.load()
    assert again.user_name == "Efua" and again.setup_done


# ---------------------------------------------------------------- health history: gaps and speed
def test_gaps_are_bridged_with_a_dashed_line_and_lone_points_become_dots():
    runs, dots, bridges = trendview.chart_segments([10, 20, None, None, 50, 60, None, 80, None], 300, 200, (40, 10, 10, 30))
    assert len(runs) == 2 and len(dots) == 1 and len(bridges) == 2
    x1, y1, x2, y2 = bridges[0]
    assert x1 < x2                                   # runs left to right: from the end of one stretch to the start of the next
    assert trendview.chart_segments([None, None], 300, 200, (0, 0, 0, 0)) == ([], [], [])
    assert trendview.chart_segments([5, None, 7], 300, 200, (0, 0, 0, 0))[2] != []


def test_trend_file_is_parsed_once_until_it_changes(home, monkeypatch):
    now = time.time()
    for i in range(50):
        trends.record({"cpu": 10, "ram": 50, "disk": 60, "battery": 90}, now - 300 * (50 - i), force=True)
    reads = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (reads.append(self.name), real(self, *a, **k))[1])
    trends.load(1), trends.buckets(1, 96), trends.summary(1), trends.load(7)
    assert reads.count("trends.csv") <= 1
    trends.record({"cpu": 11, "ram": 50, "disk": 60, "battery": 90}, now, force=True)    # the file changed: read again
    trends.load(1)
    assert reads.count("trends.csv") == 2


# ---------------------------------------------------------------- network scan
ARP_WIN = """
Interface: 192.168.1.50 --- 0x9
  Internet Address      Physical Address      Type
  192.168.1.1           aa-bb-cc-00-11-22     dynamic
  192.168.1.20          5a-11-22-33-44-55     dynamic
  192.168.1.255         ff-ff-ff-ff-ff-ff     static
  224.0.0.22            01-00-5e-00-00-16     static
"""
IPCONFIG = "   IPv4 Address. . . . . . . . . . . : 192.168.1.50\n   Default Gateway . . . . . . . . . : 192.168.1.1\n"


def test_parsing_arp_and_the_gateway():
    assert netmap.parse_arp(ARP_WIN) == {"192.168.1.1": "aa:bb:cc:00:11:22", "192.168.1.20": "5a:11:22:33:44:55"}
    assert netmap.parse_arp("192.168.1.7 dev wlan0 lladdr aa:bb:cc:dd:ee:ff REACHABLE") == {"192.168.1.7": "aa:bb:cc:dd:ee:ff"}
    assert netmap.parse_gateway(IPCONFIG) == "192.168.1.1"
    assert netmap.parse_gateway("default via 10.0.0.1 dev eth0") == "10.0.0.1"
    assert netmap.parse_gateway("nothing") == ""


def test_kinds_are_guessed_from_names_doors_and_private_addresses():
    D = netmap.Device
    assert netmap.classify(D("1.1.1.2", name="Galaxy-S23")) == "phone"
    assert netmap.classify(D("1.1.1.3", name="DESKTOP-4F2K")) == "pc"
    assert netmap.classify(D("1.1.1.4", ports=[9100])) == "printer"
    assert netmap.classify(D("1.1.1.5", ports=[netmap.PEER_PORT, 445])) == "laptop_ai"
    assert netmap.classify(D("1.1.1.6", mac="5a:11:22:33:44:55")) == "phone"          # private address, nothing open
    assert netmap.classify(D("1.1.1.7", mac="a4:11:22:33:44:55")) == "other"
    assert netmap.classify(D("1.1.1.1", is_gateway=True)) == "router"


def test_vendor_is_guessed_from_the_hardware_address_prefix_and_unknowns_show_nothing():
    assert netmap.vendor_for("B8:27:EB:11:22:33") == "Raspberry Pi"
    assert netmap.vendor_for("b8-27-eb-44-55-66") == "Raspberry Pi"           # dashes, lower case: still matches
    assert netmap.vendor_for("11:22:33:44:55:66") == ""


def test_risk_note_flags_old_insecure_doors_only():
    assert netmap.risk_note([80, 443]) == ""
    assert "Telnet" in netmap.risk_note([23, 445])
    assert "FTP" in netmap.risk_note([21])


@pytest.fixture
def fake_net(home, monkeypatch):
    monkeypatch.setattr(netmap, "local_ip", lambda: "192.168.1.50")
    monkeypatch.setattr(netmap, "RUN", lambda argv, *a, **k: IPCONFIG if argv[0] == "ipconfig" else ARP_WIN if argv[0] in ("arp", "ip") else "")
    monkeypatch.setattr(netmap.sys, "platform", "win32")
    monkeypatch.setattr(netmap, "PING", lambda ip: ip in ("192.168.1.1", "192.168.1.20", "192.168.1.30"))
    monkeypatch.setattr(netmap, "HOST", lambda ip: {"192.168.1.20": "Tinos-Galaxy-A14", "192.168.1.30": "DESKTOP-ABC"}.get(ip, ""))
    monkeypatch.setattr(netmap, "PORT", lambda ip, port: (ip, port) == ("192.168.1.30", 445))


def test_a_scan_finds_devices_and_sorts_me_then_router_then_the_rest(fake_net):
    seen = []
    devices = netmap.scan(lambda t, f: seen.append(f))
    assert [d.ip for d in devices] == ["192.168.1.50", "192.168.1.1", "192.168.1.20", "192.168.1.30"]
    assert devices[0].is_me and devices[1].is_gateway and devices[1].kind == "router"
    assert devices[2].kind == "phone" and devices[3].kind == "pc" and devices[3].ports == [445]
    assert seen[-1] == 1.0
    when, again = netmap.load()                                       # the last scan is remembered
    assert [d.ip for d in again] == [d.ip for d in devices] and when > 0


def test_naming_a_device_is_remembered_by_its_hardware_address(fake_net):
    devices = netmap.scan()
    netmap.rename(devices[2], "Ama's phone")
    _when, again = netmap.load()
    assert again[2].label == "Ama's phone"
    assert "Ama's phone" in netmap.describe(again)


def test_the_map_layout_never_overlaps_and_handles_big_networks(fake_net):
    devs = [netmap.Device("192.168.1.50", is_me=True, kind="me"), netmap.Device("192.168.1.1", is_gateway=True, kind="router")]
    devs += [netmap.Device(f"192.168.1.{i}", kind="phone") for i in range(2, 40)]
    lay = netmap.layout(devs, 640, 420)
    pts = [(x, y) for _id, x, y, _k, _l in lay["nodes"]]
    assert len(lay["edges"]) == len(devs)
    for i, a in enumerate(pts):
        for b in pts[i + 1:]:
            assert abs(a[0] - b[0]) > 8 or abs(a[1] - b[1]) > 8
    assert all(0 <= x <= 640 and 0 <= y <= 420 for x, y in pts)


def test_scan_tool_and_router_phrases(fake_net):
    r = json.loads(tools.REGISTRY["scan_network"].func())
    assert r["status"] == "ok" and r["count"] == 3
    assert "DESKTOP-ABC" in r["text"]
    for phrase in ("scan my network", "who is on my wifi", "what devices are connected", "show my devices", "network map"):
        assert intents.route(phrase).tool == "scan_network", phrase
    assert intents.route("why is my wifi slow").tool == "why_is_internet_slow"


def test_scan_fills_in_latency_and_flags_a_risky_open_port(home, monkeypatch):
    monkeypatch.setattr(netmap, "local_ip", lambda: "192.168.1.50")
    monkeypatch.setattr(netmap, "RUN", lambda argv, *a, **k: IPCONFIG if argv[0] == "ipconfig" else ARP_WIN if argv[0] in ("arp", "ip") else "")
    monkeypatch.setattr(netmap.sys, "platform", "win32")
    monkeypatch.setattr(netmap, "PING", lambda ip: ip in ("192.168.1.1", "192.168.1.20"))
    monkeypatch.setattr(netmap, "HOST", lambda ip: {"192.168.1.20": "old-nas"}.get(ip, ""))
    monkeypatch.setattr(netmap, "PORT", lambda ip, port: (ip, port) == ("192.168.1.20", 23))
    devices = netmap.scan()
    nas = next(d for d in devices if d.ip == "192.168.1.20")
    assert nas.latency_ms is not None and nas.latency_ms >= 0
    assert "Telnet" in nas.risk
    assert nas.vendor == netmap.vendor_for(nas.mac)
    assert "Worth a look" in netmap.describe(devices) and nas.label in netmap.describe(devices)


def test_a_device_is_flagged_new_only_the_first_time_its_ever_seen(fake_net):
    first = netmap.scan()
    assert all(d.is_new for d in first if not d.is_me and not d.is_gateway)
    second = netmap.scan()                                            # same devices, second scan: no longer new
    assert not any(d.is_new for d in second if not d.is_me and not d.is_gateway)
    r = json.loads(tools.REGISTRY["scan_network"].func())
    assert r["new_count"] == 0


WIFI_TEXT = """
    Name                   : Wi-Fi
    SSID                   : HomeNet-5G
    BSSID                  : 11:22:33:44:55:66
    Signal                 : 78%
    Receive rate (Mbps)    : 433
    State                  : connected
"""


def test_wifi_parsing_never_confuses_ssid_with_bssid():
    info = netmap.parse_wifi(WIFI_TEXT)
    assert info == {"ssid": "HomeNet-5G", "signal_pct": 78, "radio": "", "mbps": 433.0, "connected": True}
    assert netmap.parse_wifi("State : disconnected") == {"ssid": "", "signal_pct": None, "radio": "", "mbps": None, "connected": False}


def test_wifi_status_tool_and_phrase(home, monkeypatch):
    monkeypatch.setattr(netmap, "RUN", lambda argv: WIFI_TEXT)
    monkeypatch.setattr(netmap.sys, "platform", "win32")
    r = json.loads(tools.REGISTRY["wifi_status"].func())
    assert r["status"] == "ok" and "HomeNet-5G" in r["text"] and "78%" in r["text"]
    assert intents.route("what's my wifi signal").tool == "wifi_status"
    monkeypatch.setattr(netmap, "RUN", lambda argv: "State : disconnected")
    r2 = json.loads(tools.REGISTRY["wifi_status"].func())
    assert "isn't on Wi-Fi" in r2["text"]


# ---------------------------------------------------------------- phone link
BATTERY = "Current Battery Service state:\n  AC powered: false\n  USB powered: true\n  status: 2\n  level: 83\n  scale: 100\n  temperature: 301\n"
DF = "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/block/dm-9 112000000 48000000 64000000 43% /data\n"
FG = "  mResumedActivity: ActivityRecord{abc u0 com.whatsapp/.Main t12}\n"
NOTES = ("NotificationRecord(0x1 pkg=com.whatsapp user=UserHandle{0}\n  android.title=String (Ama)\n  android.text=secret words\n"
         "NotificationRecord(0x2 pkg=android user=UserHandle{0}\n  android.title=String (System)\n"
         "NotificationRecord(0x3 pkg=com.instagram.android user=UserHandle{0}\n  android.title=String (New follower)\n")


def test_parsing_what_adb_prints():
    assert phonelink.parse_devices("List of devices attached\nR58M123 device usb:1 product:a model:SM_A146 device:a\nXYZ unauthorized\n") == [
        {"serial": "R58M123", "state": "device", "model": "SM A146"}, {"serial": "XYZ", "state": "unauthorized", "model": ""}]
    assert phonelink.parse_battery(BATTERY) == {"percent": 83, "charging": True, "temp_c": 30.1}
    assert phonelink.parse_storage(DF) == {"total_gb": 114.7, "free_gb": 65.5, "used_pct": 43}
    assert phonelink.parse_foreground(FG) == "com.whatsapp"
    notes = phonelink.parse_notifications(NOTES)
    assert notes == [{"app": "WhatsApp", "title": "Ama"}, {"app": "Instagram", "title": "New follower"}]      # no message text, no system noise


@pytest.fixture
def fake_adb(home, monkeypatch):
    monkeypatch.setattr(phonelink, "adb_path", lambda: "adb")
    state = {"devices": "List of devices attached\nR58M123 device model:SM_A146\n"}

    def run(cmd, timeout=15, binary=False):
        text = " ".join(cmd)
        if binary:
            return b"\x89PNG fake"
        if "devices" in cmd:
            return state["devices"]
        if "ro.product.manufacturer" in text:
            return "samsung"
        if "ro.product.model" in text:
            return "SM-A146"
        if "ro.build.version.release" in text:
            return "14"
        if "dumpsys battery" in text:
            return BATTERY
        if "df /data" in text:
            return DF
        if "mResumedActivity" in text:
            return FG
        if "mWakefulness" in text:
            return "mWakefulness=Awake"
        if "dumpsys notification" in text:
            return NOTES
        return ""
    monkeypatch.setattr(phonelink, "RUN", run)
    return state


def test_phone_status_in_plain_words(fake_adb):
    r = json.loads(tools.REGISTRY["phone_status"].func())
    assert r["status"] == "ok"
    assert "Samsung SM-A146" in r["text"] and "83%" in r["text"] and "charging" in r["text"] and "WhatsApp" in r["text"]
    n = json.loads(tools.REGISTRY["phone_notifications"].func())
    assert "WhatsApp: Ama" in n["text"] and "secret" not in n["text"]


def test_phone_problems_are_explained_kindly(fake_adb):
    fake_adb["devices"] = "List of devices attached\nR58M123 unauthorized\n"
    assert "tap Allow" in json.loads(tools.REGISTRY["phone_status"].func())["message"]
    fake_adb["devices"] = "List of devices attached\n"
    assert "USB debugging" in json.loads(tools.REGISTRY["phone_status"].func())["message"]


def test_phone_screenshot_is_saved(fake_adb, monkeypatch):
    monkeypatch.setattr(tools_v4.sys, "platform", "linux")
    r = json.loads(tools.REGISTRY["phone_screenshot"].func())
    assert r["status"] == "ok" and Path(r["path"]).read_bytes().startswith(b"\x89PNG")


def test_a_file_can_be_pushed_to_the_phones_download_folder(fake_adb, monkeypatch, tmp_path):
    f = tmp_path / "pic.jpg"
    f.write_bytes(b"x")
    # reuse the existing fake_adb responder for the status() probe, only special-casing "push"
    base = phonelink.RUN
    def layered(cmd, timeout=15, binary=False):
        if "push" in cmd:
            return "1 file pushed."
        return base(cmd, timeout, binary)
    monkeypatch.setattr(phonelink, "RUN", layered)
    worked, message = phonelink.push_file(f)
    assert worked and "Sent pic.jpg" in message


def test_pushing_a_missing_file_fails_clearly(fake_adb, tmp_path):
    worked, message = phonelink.push_file(tmp_path / "nope.jpg")
    assert not worked and "doesn't exist" in message


def test_a_file_can_be_pulled_from_the_phone(fake_adb, monkeypatch, tmp_path):
    base = phonelink.RUN
    def layered(cmd, timeout=15, binary=False):
        if "pull" in cmd:
            Path(cmd[-1]).write_bytes(b"photo-bytes")
            return "1 file pulled."
        return base(cmd, timeout, binary)
    monkeypatch.setattr(phonelink, "RUN", layered)
    worked, message, path = phonelink.pull_file("/sdcard/Download/photo.jpg", tmp_path / "out")
    assert worked and path.read_bytes() == b"photo-bytes"


def test_listing_and_launching_phone_apps(fake_adb, monkeypatch):
    base = phonelink.RUN
    def layered(cmd, timeout=15, binary=False):
        text = " ".join(cmd)
        if "pm list packages" in text:
            return "package:com.whatsapp\npackage:com.spotify.music\n"
        if "monkey" in text:
            return "Events injected: 1"
        return base(cmd, timeout, binary)
    monkeypatch.setattr(phonelink, "RUN", layered)
    apps = phonelink.list_apps()
    assert {"package": "com.whatsapp", "label": "WhatsApp"} in apps
    worked, message = phonelink.launch_app("com.whatsapp")
    assert worked and "WhatsApp" in message


def test_launching_a_bad_package_name_is_refused_before_touching_the_phone(fake_adb):
    worked, message = phonelink.launch_app("com.whatsapp; rm -rf /")
    assert not worked and "package name" in message


def test_the_new_phone_and_pc_tools_are_registered_and_work(fake_adb, monkeypatch, tmp_path):
    for name in ("phone_send_file", "phone_pull_file", "phone_apps", "phone_launch_app", "send_file_to_pc"):
        assert name in tools.REGISTRY

    f = tmp_path / "doc.txt"
    f.write_text("hi")
    base = phonelink.RUN
    def layered(cmd, timeout=15, binary=False):
        text = " ".join(cmd)
        if "push" in cmd:
            return "1 file pushed."
        if "pm list packages" in text:
            return "package:com.whatsapp\n"
        if "monkey" in text:
            return "Events injected: 1"
        return base(cmd, timeout, binary)
    monkeypatch.setattr(phonelink, "RUN", layered)

    r = json.loads(tools.REGISTRY["phone_send_file"].func(path=str(f)))
    assert r["status"] == "ok" and "Sent doc.txt" in r["text"]

    r = json.loads(tools.REGISTRY["phone_apps"].func())
    assert r["status"] == "ok" and r["count"] == 1

    r = json.loads(tools.REGISTRY["phone_launch_app"].func(package="com.whatsapp"))
    assert r["status"] == "ok" and "WhatsApp" in r["text"]


def test_send_file_to_pc_tool_wraps_peers_send_file(server, tmp_path):
    code = server.new_code()
    peers.pair("127.0.0.1", code, "MY-LAPTOP", server.port)
    f = tmp_path / "report.pdf"
    f.write_text("hi")
    r = json.loads(tools.REGISTRY["send_file_to_pc"].func(name="OFFICE-PC", path=str(f)))
    assert r["status"] == "ok" and "Sent report.pdf" in r["text"]


def test_downloading_the_phone_tool_never_writes_outside_its_folder(home):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("platform-tools/adb.exe", "x")
        z.writestr("../evil.txt", "bad")
    problem = phonelink.download_adb(get=lambda url: buf.getvalue())
    assert not (home / "evil.txt").exists() and not (home.parent / "evil.txt").exists()
    assert (home / "adb" / "platform-tools" / "adb.exe").exists() or "adb" in problem


def test_phone_phrases():
    assert intents.route("check my phone").tool == "phone_status"
    assert intents.route("what is happening on my phone").tool == "phone_status"
    assert intents.route("phone notifications").tool == "phone_notifications"
    assert intents.route("take a screenshot of my phone").tool == "phone_screenshot"


# ---------------------------------------------------------------- PC to PC link (real sockets on this machine)
@pytest.fixture
def server(home):
    srv = peers.PeerServer(lambda: {"cpu": 12.0, "ram": 40.0, "disk": 70.0, "battery": 55.0, "top": ["chrome.exe"]},
                           lambda: "OFFICE-PC", port=0, host="127.0.0.1")
    assert srv.start()
    srv.port = srv._httpd.server_address[1]
    yield srv
    srv.stop()


def test_pairing_then_reading_a_linked_pcs_health(server):
    assert peers.hello("127.0.0.1", server.port)["name"] == "OFFICE-PC"
    code = server.new_code()
    worked, msg = peers.pair("127.0.0.1", code, "MY-LAPTOP", server.port)
    assert worked and "OFFICE-PC" in msg
    assert "OFFICE-PC" in peers.linked()
    d = peers.fetch_status("office")                                    # partial, any-case name works
    assert d["cpu"] == 12.0 and d["top"] == ["chrome.exe"]
    assert "CPU 12%" in peers.describe(d) and "chrome.exe" in peers.describe(d)
    assert intents.route("how is OFFICE-PC doing").tool == "pc_status"


def test_a_wrong_code_a_used_code_and_no_key_are_all_refused(server):
    code = server.new_code()
    ok1, _ = peers.pair("127.0.0.1", "000000" if code != "000000" else "111111", "X", server.port)
    assert ok1 is False
    assert peers.pair("127.0.0.1", code, "X", server.port)[0] is True
    assert peers.pair("127.0.0.1", code, "Y", server.port)[0] is False        # a code works once
    import urllib.error
    import urllib.request
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{server.port}/status", timeout=3)
    assert e.value.code == 401


def test_five_wrong_guesses_kill_the_code(server):
    code = server.new_code()
    for n in range(5):
        peers.pair("127.0.0.1", f"{n:06d}" if f"{n:06d}" != code else "999999", "X", server.port)
    assert peers.pair("127.0.0.1", code, "X", server.port)[0] is False


def test_an_unreachable_or_unlinked_pc_gives_a_clear_message(home):
    assert "not linked" in peers.fetch_status("nobody")["error"]
    assert peers.pair("127.0.0.1", "12345", "X")[0] is False
    config.write_json(config.APP_DIR / "peers_out.json", {"GHOST": {"host": "127.0.0.1", "key": "k", "port": 9}})
    assert "isn't answering" in peers.fetch_status("ghost")["error"]
    assert peers.unlink("GHOST") is True


def test_a_request_from_outside_the_home_network_is_refused(server):
    assert peers.is_private_address("192.168.1.5") and peers.is_private_address("127.0.0.1")
    assert peers.is_private_address("10.0.0.5") and peers.is_private_address("169.254.1.1")
    assert not peers.is_private_address("8.8.8.8") and not peers.is_private_address("not-an-ip")
    code = server.new_code()
    peers.pair("127.0.0.1", code, "X", server.port)
    peers.list_incoming()               # sanity: doesn't crash on a freshly-paired key
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{server.port}/hello")
    with urllib.request.urlopen(req, timeout=3) as r:
        assert json.loads(r.read())["app"] == "laptop-ai"          # loopback itself is allowed


def test_a_stale_or_replayed_signature_is_refused(server):
    code = server.new_code()
    worked, _ = peers.pair("127.0.0.1", code, "X", server.port)
    assert worked
    key = list(peers._read_incoming())[0]
    import urllib.error
    import urllib.request
    old_ts = f"{time.time() - 120:.3f}"                             # two minutes old: outside the 60s window
    req = urllib.request.Request(f"http://127.0.0.1:{server.port}/status",
                                 headers={"X-Key": key, "X-Time": old_ts, "X-Sig": peers.sign(key, "GET", "/status", old_ts)})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=3)
    assert e.value.code == 401
    req2 = urllib.request.Request(f"http://127.0.0.1:{server.port}/status", headers={"X-Key": key})  # no signature at all
    with pytest.raises(urllib.error.HTTPError) as e2:
        urllib.request.urlopen(req2, timeout=3)
    assert e2.value.code == 401


def test_incoming_keys_can_be_listed_and_revoked(server):
    code = server.new_code()
    peers.pair("127.0.0.1", code, "MY-LAPTOP", server.port)
    entries = peers.list_incoming()
    assert len(entries) == 1 and entries[0]["name"] == "MY-LAPTOP"
    assert peers.revoke_incoming(entries[0]["key"]) is True
    assert peers.list_incoming() == []
    assert peers.describe({"error": "gone"}) == "gone"


def test_an_idle_key_expires_on_its_own(server, monkeypatch):
    code = server.new_code()
    peers.pair("127.0.0.1", code, "OLD-PC", server.port)
    stale = {k: {**v, "last_seen": time.time() - 31 * 86400} for k, v in peers._read_incoming().items()}
    config.write_json(peers._incoming(), stale)
    assert peers.list_incoming() == []


def test_an_unreachable_host_is_reported_as_unreachable_not_a_bad_code(home):
    worked, message = peers.pair("127.0.0.1", "123456", "X", port=1)   # nothing listens on port 1
    assert worked is False and "can't find a Laptop AI" in message


def test_files_can_be_sent_between_linked_pcs(server, tmp_path):
    code = server.new_code()
    assert peers.pair("127.0.0.1", code, "MY-LAPTOP", server.port)[0]
    f = tmp_path / "notes.txt"
    f.write_text("hello from the other pc")
    worked, msg = peers.send_file("OFFICE-PC", f)
    assert worked and "Sent" in msg
    received = peers.list_received()
    assert len(received) == 1 and received[0]["name"] == "notes.txt"
    assert Path(received[0]["path"]).read_text() == "hello from the other pc"


def test_sending_to_an_unlinked_pc_or_a_missing_file_fails_clearly(tmp_path):
    real = tmp_path / "x.txt"
    real.write_text("hi")
    ok1, msg1 = peers.send_file("NOBODY", real)
    assert ok1 is False and "Not linked" in msg1
    ok2, msg2 = peers.send_file("NOBODY", tmp_path / "missing.txt")
    assert ok2 is False and "doesn't exist" in msg2


def test_a_file_too_big_is_refused_before_sending(server, tmp_path, monkeypatch):
    code = server.new_code()
    peers.pair("127.0.0.1", code, "MY-LAPTOP", server.port)
    monkeypatch.setattr(peers, "MAX_FILE_BYTES", 10)
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 100)
    worked, message = peers.send_file("OFFICE-PC", big)
    assert worked is False and "50 MB" in message


def test_an_unsafe_filename_from_a_sender_is_sanitised(server):
    assert peers._safe_filename("../../evil.sh") == "evil.sh"
    assert peers._safe_filename("..\\..\\windows\\system32\\x.exe") == "x.exe"
    assert peers._safe_filename("") == "file"


def test_the_pairing_code_shows_how_long_it_has_left(server):
    server.new_code()
    assert 595 <= server.code_seconds_left() <= 600
    assert peers.CODE_LIFETIME_S == 600


def test_allow_pc_link_tool_returns_a_code_and_can_stop(home, monkeypatch):
    srv = peers.PeerServer(lambda: {}, lambda: "ME", port=0, host="127.0.0.1")
    monkeypatch.setattr(tools_v4, "_server", srv)
    monkeypatch.setattr(tools_v4.netmap, "local_ip", lambda: "192.168.1.50")
    r = json.loads(tools.REGISTRY["allow_pc_link"].func(on=True))
    assert r["status"] == "ok" and len(r["code"]) == 6 and "192.168.1.50" in r["text"]
    assert config.Settings.load().allow_pc_link is True
    assert json.loads(tools.REGISTRY["allow_pc_link"].func(on=False))["status"] == "ok"
    assert not srv.running and config.Settings.load().allow_pc_link is False
    assert intents.route("link to pc 192.168.1.20 code 123456").args == {"host": "192.168.1.20", "code": "123456"}
