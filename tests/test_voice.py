"""Voice tests with a fake microphone and a fake recogniser (no hardware or internet needed)."""
import math
import struct

from laptop_ai import voice


def block(level, n=voice.BLOCK):
    """n samples of a sine wave with roughly the given loudness (rms)."""
    amp = level * math.sqrt(2)
    return struct.pack(f"<{n}h", *[int(amp * math.sin(i / 5)) for i in range(n)])


class FakeStream:
    def __init__(self, blocks):
        self.blocks = list(blocks)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        return (self.blocks.pop(0) if self.blocks else block(0)), False


class FakeSD:
    class PortAudioError(Exception):
        pass

    def __init__(self, blocks=None, error=None):
        self.blocks, self.error = blocks or [], error

    def RawInputStream(self, **kw):
        if self.error:
            raise self.error
        return FakeStream(self.blocks)


class FakeSR:
    class UnknownValueError(Exception):
        pass

    class RequestError(Exception):
        pass

    class AudioData:
        def __init__(self, raw, rate, width):
            self.raw, self.rate, self.width = raw, rate, width

    result = "open notepad"

    class Recognizer:
        def recognize_google(self, audio):
            if isinstance(FakeSR.result, Exception):
                raise FakeSR.result
            return FakeSR.result


def speech_session():
    quiet, loud = block(50), block(3000)
    return [quiet] * 4 + [quiet] * 5 + [loud] * 8 + [quiet] * 14        # room noise, wait, talk, stop


def test_records_a_phrase_and_stops_when_speaker_stops(monkeypatch):
    monkeypatch.setattr(voice, "sd", FakeSD(speech_session()))
    raw = voice.record_phrase()
    assert raw is not None
    blocks = len(raw) // (voice.BLOCK * 2)
    assert 8 <= blocks <= 8 + 3 + voice.QUIET_BLOCKS_TO_STOP          # speech + preroll + trailing quiet


def test_nobody_speaking_returns_none_after_timeout(monkeypatch):
    monkeypatch.setattr(voice, "sd", FakeSD([block(50)] * 200))
    assert voice.record_phrase(timeout=1.0) is None


def test_listen_success_and_all_error_messages(monkeypatch):
    monkeypatch.setattr(voice, "sr", FakeSR)
    monkeypatch.setattr(voice, "sd", FakeSD(speech_session()))
    v = voice.Voice()
    assert v.can_listen
    FakeSR.result = "open notepad"
    assert v.listen() == ("open notepad", "")

    monkeypatch.setattr(voice, "sd", FakeSD(speech_session()))
    FakeSR.result = FakeSR.UnknownValueError()
    assert "couldn't understand" in v.listen()[1]
    monkeypatch.setattr(voice, "sd", FakeSD(speech_session()))
    FakeSR.result = FakeSR.RequestError()
    assert "internet" in v.listen()[1]

    monkeypatch.setattr(voice, "sd", FakeSD([block(50)] * 200))
    assert "didn't hear" in v.listen(timeout=1.0)[1]

    fake = FakeSD(error=FakeSD.PortAudioError("no device"))
    monkeypatch.setattr(voice, "sd", fake)
    text, err = v.listen()
    assert text is None and "Privacy" in err                       # a helpful, accurate message
    FakeSR.result = "open notepad"


def test_missing_packages_are_reported_not_crashed(monkeypatch):
    monkeypatch.setattr(voice, "sd", None)
    v = voice.Voice()
    assert not v.can_listen
    assert "sounddevice" in v.listen()[1]
