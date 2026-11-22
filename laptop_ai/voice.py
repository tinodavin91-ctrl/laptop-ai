"""Optional voice: speak replies (offline) and listen to the microphone.

  * Speaking uses the built-in Windows voices through pyttsx3: works offline.
  * Listening records from the default microphone with `sounddevice` (it ships its own audio
    library for Windows, so no compiler is needed and it works on new Python versions) and stops
    when you stop talking. The recording is then turned into text either by:
        - "google":  Google's free web recogniser (through SpeechRecognition). Your voice clip is
                     sent to Google. More accurate.
        - "offline": the Vosk model on this PC. Nothing leaves your laptop.

Both features are optional: if the packages are missing the app still works, text only.
"""
from __future__ import annotations

import math
import threading
from array import array
from typing import Callable, Optional

try:
    import pyttsx3
except ImportError:           # optional
    pyttsx3 = None

try:
    import speech_recognition as sr
except ImportError:           # optional
    sr = None

try:
    import sounddevice as sd
except (ImportError, OSError):    # OSError: the audio library could not be loaded
    sd = None

RATE = 16000                  # samples per second
BLOCK = 1600                  # 0.1 s of audio per read
QUIET_BLOCKS_TO_STOP = 12     # 1.2 s of quiet ends the phrase
MIC_HELP = ("No microphone found, or Windows is blocking it. Check that one is connected and that "
            "Settings > Privacy & security > Microphone allows desktop apps.")


def _rms(data: bytes) -> float:
    """Loudness of a block of 16-bit audio."""
    samples = array("h")
    samples.frombytes(data[: len(data) // 2 * 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def record_from(read: Callable[[], bytes], timeout: float = 6.0, phrase_limit: float = 12.0) -> Optional[bytes]:
    """Collect one spoken phrase from `read()` (which returns the next 0.1 s block of audio).
    Stops when the speaker stops. None means nobody spoke within `timeout` seconds."""
    room = [_rms(read()) for _ in range(4)]                                  # 0.4 s of room noise
    threshold = min(1500.0, max(400.0, 3.0 * min(room)))                     # quietest block = the real room, not a beep
    preroll: list[bytes] = []
    frames: list[bytes] = []
    started, quiet, waited, spoken = False, 0, 0.0, 0.0
    while True:
        data = read()
        loud = _rms(data) >= threshold
        if not started:
            preroll = (preroll + [data])[-3:]                                # keep the first syllable
            if loud:
                started, frames = True, list(preroll)
            else:
                waited += BLOCK / RATE
                if waited >= timeout:
                    return None
        else:
            frames.append(data)
            spoken += BLOCK / RATE
            quiet = 0 if loud else quiet + 1
            if quiet >= QUIET_BLOCKS_TO_STOP or spoken >= phrase_limit:
                return b"".join(frames)


def record_phrase(timeout: float = 6.0, phrase_limit: float = 12.0) -> Optional[bytes]:
    """Open the default microphone, record one phrase, close it."""
    with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK) as stream:
        return record_from(lambda: bytes(stream.read(BLOCK)[0]), timeout, phrase_limit)


class Voice:
    def __init__(self):
        self._lock = threading.Lock()
        self.name = ""                     # part of the name of the voice to use ("" = the PC's default)
        self.rate = 185                    # words per minute

    @property
    def can_speak(self) -> bool:
        return pyttsx3 is not None

    @property
    def can_listen(self) -> bool:
        return sr is not None and sd is not None

    def voices(self) -> list[str]:
        """Names of the voices installed on this PC (empty when speech isn't available)."""
        if not self.can_speak:
            return []
        try:
            engine = pyttsx3.init()
            names = [str(v.name) for v in engine.getProperty("voices")]
            engine.stop()
            return names
        except Exception:
            return []

    def _prepare(self, engine) -> None:
        try:
            engine.setProperty("rate", max(90, min(int(self.rate), 300)))
            if self.name:
                for v in engine.getProperty("voices"):
                    if self.name.lower() in str(v.name).lower():
                        engine.setProperty("voice", v.id)
                        break
        except Exception:
            pass                             # a voice that won't switch just means the default is used

    def speak(self, text: str, on_done: Optional[Callable[[], None]] = None) -> None:
        """Speak in a background thread so the window never freezes. on_done runs (in that thread) when the voice stops,
        or straight away when nothing could be spoken."""
        if not self.can_speak or not text:
            if on_done:
                on_done()
            return

        def work():
            with self._lock:                 # one voice at a time
                try:
                    engine = pyttsx3.init()
                    self._prepare(engine)
                    engine.say(text[:600])
                    engine.runAndWait()
                    engine.stop()
                except Exception:
                    pass
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass
        threading.Thread(target=work, daemon=True).start()

    def transcribe(self, raw: bytes, mode: str = "google") -> tuple[Optional[str], str]:
        """Turn a recording into text. Returns (text, error)."""
        if mode == "offline":
            from . import wakeword                  # imported here: wakeword also imports this module
            return wakeword.transcribe_offline(raw)
        if sr is None:
            return None, "Voice input needs:  pip install SpeechRecognition sounddevice"
        try:
            return sr.Recognizer().recognize_google(sr.AudioData(raw, RATE, 2)), ""
        except sr.UnknownValueError:
            return None, "Sorry, I couldn't understand that."
        except sr.RequestError:
            return None, "Speech recognition needs internet and could not connect."

    def listen(self, timeout: float = 6, phrase_limit: float = 12, mode: str = "google") -> tuple[Optional[str], str]:
        """Return (text, error). Blocks, so call it from a worker thread."""
        if sd is None or (mode == "google" and sr is None):
            return None, "Voice input needs:  pip install SpeechRecognition sounddevice"
        try:
            raw = record_phrase(timeout, phrase_limit)
        except (sd.PortAudioError, OSError):
            return None, MIC_HELP
        if raw is None:
            return None, "I didn't hear anything."
        return self.transcribe(raw, mode)
