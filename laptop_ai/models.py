"""Download an Ollama model from inside the app, with progress (no terminal needed)."""
from __future__ import annotations

import json
from typing import Callable, Optional

import requests

CHOICES = [("llama3.2:3b", "Llama 3.2 (3B) - smarter, about 2 GB"), ("llama3.2:1b", "Llama 3.2 (1B) - smaller and faster, about 1.3 GB")]


def installed(url: str) -> list[str]:
    try:
        r = requests.get(url.rstrip("/") + "/api/tags", timeout=3)
        r.raise_for_status()
        return [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
    except (requests.RequestException, ValueError):
        return []


def has(name: str, names: list[str]) -> bool:
    base = name if ":" in name else name + ":latest"
    return any(n == name or n == base for n in names)


def pull(url: str, name: str, progress: Callable[[float, str], None] = lambda f, s: None,
         cancelled: Callable[[], bool] = lambda: False, session=None) -> tuple[bool, str]:
    """Pull `name`. progress(fraction 0..1, status words). Returns (ok, message). Ollama resumes a half-finished download."""
    post = (session or requests).post
    try:
        with post(url.rstrip("/") + "/api/pull", json={"model": name, "stream": True}, stream=True, timeout=(5, 120)) as r:
            if r.status_code != 200:
                return False, f"Ollama refused ({r.status_code}). Is the model name right?"
            for line in r.iter_lines():
                if cancelled():
                    return False, "Download stopped. Press the button again to carry on from where it got to."
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("error"):
                    return False, str(d["error"])
                total, done = d.get("total"), d.get("completed")
                frac: Optional[float] = (done / total) if total and done is not None else None
                progress(frac if frac is not None else -1.0, str(d.get("status", "")))
                if d.get("status") == "success":
                    return True, f"{name} is ready."
    except requests.ConnectionError:
        return False, "Ollama isn't running. Start it (open the Ollama app) and try again."
    except requests.RequestException as e:
        return False, f"The connection dropped ({e.__class__.__name__}). Press the button again; it resumes where it stopped."
    return False, "The download ended early. Press the button again; it resumes where it stopped."
