"""Wake word tests: fake microphone, fake Vosk, fake download. No hardware, model or internet needed."""
import io
import json
import math
import os
import struct
import time
import zipfile

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

import pytest   # noqa: E402
import requests  # noqa: E402

from laptop_ai import config, voice, wakeword  # noqa: E402


def block(level, n=voice.BLOCK):
    amp = level * math.sqrt(2)
    return struct.pack(f"<{n}h", *[int(amp * math.sin(i / 5)) for i in range(n)])


QUIET, LOUD = block(50), block(3000)


# ---------------------------------------------------------------- phrase matching and defaults
def test_wake_phrase_matching():
    assert wakeword.matches("tricks") and wakeword.matches("  Trix ") and wakeword.matches("hey trix")
    assert not wakeword.matches("") and not wakeword.matches("hello laptop") and not wakeword.matches(None)
    assert not wakeword.matches("matrix")


def test_off_by_default():
    assert config.Settings().wake_word_enabled is False
    assert config.Settings().recognition == "google"


# ---------------------------------------------------------------- the listener
class FakeStream:
    def __init__(self, blocks):
        self.blocks = list(blocks)
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        self.reads += 1
        return (self.blocks.pop(0) if self.blocks else QUIET), False


class TriggerDetector:
    """Says 'wake!' on the Nth block it is shown (stands in for Vosk)."""

    def __init__(self, on_feed):
        self.on_feed, self.seen = on_feed, []

    def feed(self, data):
        self.seen.append(data)
        return self.on_feed(len(self.seen))


def run_listener(blocks, trigger_at, **kw):
    events, commands = [], []
    stream = FakeStream(blocks)
    det = TriggerDetector(lambda n: n in trigger_at)
    ls = wakeword.WakeListener(lambda: events.append("wake"), commands.append, lambda m: events.append("error:" + m),
                               detector_factory=lambda: det, stream_factory=lambda: stream, cooldown=0, **kw)
    return ls, events, commands, det, stream


def wait(cond, secs=3):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_wake_then_one_command_then_paused_until_resume():
    command = [QUIET] * 4 + [QUIET] * 3 + [LOUD] * 8 + [QUIET] * 14    # room noise, wait, speech, stop
    blocks = [QUIET] * 5 + [QUIET] + command + [QUIET] * 5 + [QUIET] * 3
    ls, events, commands, det, stream = run_listener(blocks, trigger_at={6, 100})
    ls.start()
    assert wait(lambda: len(commands) == 1)
    assert events == ["wake"] and commands[0] is not None and len(commands[0]) > voice.BLOCK * 2 * 8
    seen_after_command = len(det.seen)
    time.sleep(0.15)
    assert len(det.seen) == seen_after_command             # paused: audio is drained, not analysed
    ls.stop()


def test_silence_after_wake_gives_none_and_resume_allows_the_next_wake():
    blocks = [QUIET] * 6 + [QUIET] * 80                      # wake, then nobody speaks
    ls, events, commands, det, _ = run_listener(blocks, trigger_at={6, 3 + 6}, command_timeout=1.0)
    ls.start()
    assert wait(lambda: commands == [None])
    ls.resume()
    ls.stop()


def test_no_command_is_recorded_before_the_wake_word():
    ls, events, commands, det, stream = run_listener([LOUD] * 30, trigger_at=set())
    ls.start()
    assert wait(lambda: stream.reads > 20)
    ls.stop()
    assert events == [] and commands == []                   # loud talking without the wake word does nothing


def test_cooldown_blocks_a_second_wake_right_after():
    blocks = [QUIET] * 6 + [QUIET] * 80
    events, commands = [], []
    det = TriggerDetector(lambda n: True)                    # would fire on every block
    ls = wakeword.WakeListener(lambda: events.append("wake"), commands.append, lambda m: None,
                               detector_factory=lambda: det, stream_factory=lambda: FakeStream(blocks),
                               cooldown=60, command_timeout=0.5)
    ls.start()
    assert wait(lambda: len(commands) == 1)
    ls.resume()
    time.sleep(0.3)
    ls.stop()
    assert events == ["wake"]


SPEECH = [LOUD] * 8 + [QUIET] * 14


def test_follow_up_listens_without_the_wake_word_after_an_answer():
    ls, events, commands, det, stream = run_listener([QUIET] * 6 + [QUIET] * 3 + SPEECH, trigger_at={6}, follow_up_s=3.0)
    heard = []
    ls.on_follow_up = lambda: heard.append("listening")
    ls.start()
    assert wait(lambda: len(commands) == 1)
    time.sleep(0.2)
    ls.resume(follow_up=True)
    stream.blocks.extend([QUIET] * 2 + SPEECH)                  # the user answers the answer, no "Trix"
    assert wait(lambda: len(commands) == 2)
    assert events == ["wake"] and heard == ["listening"] and commands[1] is not None
    ls.stop()


def test_follow_up_silence_goes_quietly_back_to_the_wake_word():
    ls, events, commands, det, stream = run_listener([QUIET] * 6 + [QUIET] * 3 + SPEECH, trigger_at={6}, follow_up_s=0.5)
    ls.start()
    assert wait(lambda: len(commands) == 1)
    ls.resume(follow_up=True)
    time.sleep(1.2)                                              # nobody speaks
    assert len(commands) == 1                                    # no "I didn't hear a command" for the follow-up silence
    seen = len(det.seen)
    time.sleep(0.2)
    assert len(det.seen) > seen                                  # the detector is listening for "Trix" again
    ls.stop()


def test_follow_up_is_off_when_set_to_zero():
    ls, *_ = run_listener([QUIET] * 8, trigger_at=set(), follow_up_s=0)
    ls.resume(follow_up=True)
    assert ls._follow_until == 0.0


def test_microphone_failure_is_reported_with_help():
    errors = []

    def broken():
        raise OSError("no device")
    ls = wakeword.WakeListener(lambda: None, lambda r: None, errors.append,
                               detector_factory=lambda: TriggerDetector(lambda n: False), stream_factory=broken)
    ls.start()
    assert wait(lambda: errors)
    assert "Privacy" in errors[0] and ls.listening is False


# ---------------------------------------------------------------- Vosk wrappers (fake Vosk)
class FakeVosk:
    text, final = "", False
    instances = []

    class Model:
        def __init__(self, path):
            self.path = path

    class KaldiRecognizer:
        def __init__(self, model, rate, grammar=None):
            self.model, self.rate, self.grammar, self.got = model, rate, grammar, b""
            FakeVosk.instances.append(self)

        def AcceptWaveform(self, data):
            self.got += data
            return FakeVosk.final

        def Result(self):
            return json.dumps({"text": FakeVosk.text})

        def PartialResult(self):
            return json.dumps({"partial": FakeVosk.text})

        def FinalResult(self):
            return json.dumps({"text": FakeVosk.text})


@pytest.fixture
def fake_vosk(monkeypatch, tmp_path):
    monkeypatch.setattr(wakeword, "vosk", FakeVosk)
    monkeypatch.setattr(wakeword, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(wakeword, "MODEL_DIR", tmp_path / wakeword.MODEL_NAME)
    monkeypatch.setattr(wakeword, "_MODEL", None)
    FakeVosk.instances.clear()
    FakeVosk.text, FakeVosk.final = "", False
    return tmp_path


def install_fake_model(root):
    for sub in ("am", "conf", "graph"):
        (root / wakeword.MODEL_NAME / sub).mkdir(parents=True, exist_ok=True)


def test_detector_only_listens_for_the_wake_phrase(fake_vosk):
    install_fake_model(fake_vosk)
    det = wakeword.VoskDetector()
    assert json.loads(det.rec.grammar) == ["tricks", "[unk]"]          # nothing else is even recognised
    FakeVosk.text = "[unk]"
    assert det.feed(QUIET) is False
    FakeVosk.text = "tricks"                                                # seen as a partial result
    assert det.feed(QUIET) is True
    assert len(FakeVosk.instances) == 2                                    # a fresh recogniser after each wake


def test_offline_transcription_paths(fake_vosk, monkeypatch):
    assert "isn't downloaded" in wakeword.transcribe_offline(b"x")[1]
    install_fake_model(fake_vosk)
    FakeVosk.text = "open notepad"
    assert wakeword.transcribe_offline(b"audio") == ("open notepad", "")
    FakeVosk.text = ""
    assert wakeword.transcribe_offline(b"audio")[0] is None
    monkeypatch.setattr(wakeword, "vosk", None)
    assert "pip install vosk" in wakeword.transcribe_offline(b"x")[1]


def test_voice_transcribe_routes_offline(fake_vosk):
    install_fake_model(fake_vosk)
    FakeVosk.text = "close notepad"
    assert voice.Voice().transcribe(b"audio", "offline") == ("close notepad", "")


# ---------------------------------------------------------------- model download
class FakeGet:
    def __init__(self, payload=b"", error=None, length=True):
        self.payload, self.error, self.length = payload, error, length

    def __call__(self, url, stream=True, timeout=30):
        if self.error:
            raise self.error
        outer = self

        class R:
            headers = {"content-length": str(len(outer.payload))} if outer.length else {}

            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def raise_for_status(s):
                pass

            def iter_content(s, chunk_size):
                for i in range(0, len(outer.payload), 100):
                    yield outer.payload[i:i + 100]
        return R()


def make_zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return buf.getvalue()


def test_download_installs_the_model_and_reports_progress(fake_vosk):
    name = wakeword.MODEL_NAME
    payload = make_zip([f"{name}/am/final.mdl", f"{name}/conf/mfcc.conf", f"{name}/graph/HCLG.fst"])
    steps = []
    assert wakeword.download_model(steps.append, FakeGet(payload)) == (True, "")
    assert wakeword.model_installed() and steps and steps[-1] == 1.0
    assert not list(fake_vosk.glob("*.part"))                              # temporary file cleaned up


def test_download_refuses_files_that_escape_the_folder(fake_vosk):
    ok, err = wakeword.download_model(lambda p: None, FakeGet(make_zip(["../evil.txt"])))
    assert not ok and "unsafe" in err
    assert not (fake_vosk.parent / "evil.txt").exists()


def test_download_errors_give_manual_instructions(fake_vosk):
    ok, err = wakeword.download_model(lambda p: None, FakeGet(error=requests.ConnectionError()))
    assert not ok and wakeword.MODEL_URL in err
    ok, err = wakeword.download_model(lambda p: None, FakeGet(b"not a zip"))
    assert not ok and "incomplete" in err
    ok, err = wakeword.download_model(lambda p: None, FakeGet(make_zip(["other/file"])))
    assert not ok and "wasn't found" in err
