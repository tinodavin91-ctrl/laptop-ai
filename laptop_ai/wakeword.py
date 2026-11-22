"""Hands-free "Trix": an opt-in, ALWAYS-LISTENING microphone, kept as private as possible.

Privacy design (the tests check the key ones: off by default, a wake-phrase-only grammar, nothing recorded before the phrase, paused during a command):
  * OFF by default. You switch it on in Settings, and the window shows a "microphone is live"
    marker for as long as it is on.
  * Everything is analysed on THIS PC by Vosk, a small offline speech model. While waiting for the
    wake word nothing is saved to disk and nothing is sent anywhere. Audio is thrown away block by
    block, and the model is only listening for the one word "Trix".
  * Only AFTER you say "Trix" is one short command recorded. It is turned into text offline
    (Vosk) or, if you chose "google" in Settings, sent to Google for recognition. Never saved.
  * A command that arrives this way can never act silently: anything beyond a plain lookup asks
    for your OK first, even in auto_safe mode.

One-time setup: `pip install vosk`, then download the ~40 MB English model (Settings has a button).
"""
from __future__ import annotations

import json
import threading
import time
import zipfile
from typing import Callable, Optional

import requests

from . import config, voice

try:
    import vosk
except (ImportError, OSError):
    vosk = None

MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODELS_DIR = config.APP_DIR / "models"
MODEL_DIR = MODELS_DIR / MODEL_NAME
# The small English model has no word spelled "trix"; it hears the same sound as "tricks", so that is
# the word it listens for (its vocabulary decides what a grammar may contain). Both spellings match.
SETTLE_BLOCKS = 7                  # 0.7 s of audio thrown away after the wake word (the confirmation beeps)
WAKE_PHRASE = "tricks"
WAKE_WORDS = ("tricks", "trix")
WAKE_NAME = "Trix"
MANUAL_HELP = (f"Download it yourself from {MODEL_URL} , unzip it, and put the folder "
               f"'{MODEL_NAME}' inside {MODELS_DIR}")


def available() -> bool:
    """Can the wake word run at all on this PC (library + microphone library installed)?"""
    return vosk is not None and voice.sd is not None


def model_installed() -> bool:
    return (MODEL_DIR / "am").is_dir() and (MODEL_DIR / "conf").is_dir()


def matches(text: str) -> bool:
    return any(w in (text or "").lower().split() for w in WAKE_WORDS)


# ---------------------------------------------------------------- model download
def download_model(progress: Callable[[float], None] = lambda p: None, get=requests.get) -> tuple[bool, str]:
    """Download and unpack the model (~40 MB). Returns (ok, error)."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    archive = MODELS_DIR / (MODEL_NAME + ".zip.part")
    try:
        with get(MODEL_URL, stream=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with open(archive, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        progress(min(done / total, 1.0))
        with zipfile.ZipFile(archive) as z:
            root = MODELS_DIR.resolve()
            for member in z.namelist():                                   # refuse paths that escape the folder
                if not (root / member).resolve().is_relative_to(root):
                    return False, "The downloaded file looked unsafe, so it was not unpacked."
            z.extractall(MODELS_DIR)
    except (requests.RequestException, OSError) as e:
        return False, f"Download failed ({e.__class__.__name__}). {MANUAL_HELP}"
    except zipfile.BadZipFile:
        return False, f"The download was incomplete. Try again. {MANUAL_HELP}"
    finally:
        archive.unlink(missing_ok=True)
    return (True, "") if model_installed() else (False, f"The model folder wasn't found after unpacking. {MANUAL_HELP}")


# ---------------------------------------------------------------- offline recognition
_MODEL = None
_MODEL_LOCK = threading.Lock()


def _get_model():
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            _MODEL = vosk.Model(str(MODEL_DIR))
        return _MODEL


def transcribe_offline(raw: bytes) -> tuple[Optional[str], str]:
    """Turn a recording into text with the offline model. Returns (text, error)."""
    if vosk is None:
        return None, "Offline recognition needs:  pip install vosk"
    if not model_installed():
        return None, "The offline voice model isn't downloaded yet (Settings > Hands-free)."
    try:
        rec = vosk.KaldiRecognizer(_get_model(), voice.RATE)
        rec.AcceptWaveform(raw)
        text = json.loads(rec.FinalResult()).get("text", "").strip()
    except Exception as e:
        return None, f"Offline recognition failed ({e.__class__.__name__})."
    return (text, "") if text else (None, "Sorry, I couldn't understand that.")


class VoskDetector:
    """Listens for the wake phrase only (the model is limited to the wake word and 'unknown')."""

    def __init__(self):
        self.last = ""                                   # what the model last heard (only the wake word or [unk])
        self._new()

    def _new(self) -> None:
        self.rec = vosk.KaldiRecognizer(_get_model(), voice.RATE, json.dumps([WAKE_PHRASE, "[unk]"]))

    def feed(self, data: bytes) -> bool:
        if self.rec.AcceptWaveform(data):
            text = json.loads(self.rec.Result()).get("text", "")
        else:
            text = json.loads(self.rec.PartialResult()).get("partial", "")
        if text:
            self.last = text
        if matches(text):
            self._new()                                  # start clean for the next time
            return True
        return False


# ---------------------------------------------------------------- the listener
class WakeListener:
    """Reads the microphone in a background thread. When the wake phrase is heard it calls
    on_wake(), records ONE command from the same stream, and hands the recording to on_command(raw)
    (raw is None if nobody spoke). Detection then stays paused until resume() is called."""

    def __init__(self, on_wake: Callable[[], None], on_command: Callable[[Optional[bytes]], None],
                 on_error: Callable[[str], None], detector_factory: Callable[[], object] = None,
                 stream_factory: Callable[[], object] = None, cooldown: float = 3.0,
                 command_timeout: float = 6.0, phrase_limit: float = 12.0, follow_up_s: float = 7.0,
                 on_follow_up: Optional[Callable[[], None]] = None):
        self.on_wake, self.on_command, self.on_error = on_wake, on_command, on_error
        self.follow_up_s = follow_up_s                       # how long to keep listening after an answer, without "Trix"
        self.on_follow_up = on_follow_up
        self._follow_until = 0.0
        self.detector_factory = detector_factory or VoskDetector
        self.stream_factory = stream_factory or (lambda: voice.sd.RawInputStream(
            samplerate=voice.RATE, channels=1, dtype="int16", blocksize=voice.BLOCK))
        self.cooldown, self.command_timeout, self.phrase_limit = cooldown, command_timeout, phrase_limit
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.listening = False
        self.detector = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._paused.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.listening = False

    def resume(self, follow_up: bool = False) -> None:
        """Wake word listening again. With follow_up=True, the next few seconds are treated as a follow-up command
        (no "Trix" needed); if nobody speaks, it quietly goes back to waiting for the wake word."""
        if follow_up and self.follow_up_s > 0:
            self._follow_until = time.time() + self.follow_up_s
        self._paused.clear()

    def _run(self) -> None:
        try:
            detector = self.detector_factory()
            self.detector = detector
            with self.stream_factory() as stream:
                self.listening = True
                read = lambda: bytes(stream.read(voice.BLOCK)[0])        # noqa: E731
                last_wake = 0.0
                while not self._stop.is_set():
                    block = read()
                    if self._paused.is_set() or time.time() - last_wake < self.cooldown:
                        continue                                          # keep draining, ignore detections
                    if self._follow_until:
                        window, self._follow_until = self._follow_until - time.time(), 0.0
                        if window > 0:
                            self._paused.set()
                            if self.on_follow_up:
                                self.on_follow_up()
                            raw = voice.record_from(read, window, self.phrase_limit)
                            if raw is None:
                                self._paused.clear()                      # nobody spoke: back to waiting for the wake word
                            else:
                                self.on_command(raw)
                            continue
                    if detector.feed(block):
                        last_wake = time.time()
                        self._paused.set()
                        self.on_wake()
                        for _ in range(SETTLE_BLOCKS):                    # let the beep die away; never record it
                            read()
                        raw = voice.record_from(read, self.command_timeout, self.phrase_limit)
                        self.on_command(raw)
        except Exception as e:                                            # no mic, permission, model problem...
            self.on_error(f"The wake word stopped: {e.__class__.__name__}. {voice.MIC_HELP}")
        finally:
            self.listening = False
