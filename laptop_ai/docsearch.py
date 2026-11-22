"""Search INSIDE your documents (not just file names).

Reads only plain-text style files (and Word .docx) in your personal folders, locally. Nothing is
uploaded and nothing is indexed on disk. Limits keep it quick: a few thousand files, a few seconds.
"""
from __future__ import annotations

import os
import re
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional

TEXT_EXT = {".txt", ".md", ".csv", ".log", ".json", ".py", ".js", ".ts", ".html", ".htm", ".xml", ".yml",
            ".yaml", ".ini", ".cfg", ".php", ".css", ".sql", ".tex", ".rtf"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "appdata", "$recycle.bin", "vendor"}
MAX_FILES = 4000
MAX_BYTES = 1_500_000
MAX_SECONDS = 10.0
STOP = {"the", "a", "an", "of", "to", "and", "in", "on", "for", "my", "about", "is", "was", "what", "did", "i", "me"}
_TAGS = re.compile(r"<[^>]+>")


def words(query: str) -> list[str]:
    found = [w for w in re.findall(r"[\w'-]+", query.lower()) if len(w) > 1 and w not in STOP]
    return list(dict.fromkeys(found))[:8]


def read_text(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        if path.suffix.lower() == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            return _TAGS.sub(" ", xml.replace("</w:p>", "\n"))
        if path.suffix.lower() in TEXT_EXT:
            return path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    return None


def snippet(text: str, word: str, width: int = 90) -> str:
    i = text.lower().find(word)
    if i < 0:
        return " ".join(text[:2 * width].split())
    a, b = max(0, i - width), min(len(text), i + len(word) + width)
    return ("..." if a else "") + " ".join(text[a:b].split()) + ("..." if b < len(text) else "")


def search(query: str, roots: list[Path], limit: int = 8, clock: Callable[[], float] = time.monotonic) -> dict:
    terms = words(query)
    if not terms:
        return {"error": "Give me at least one word to look for."}
    start, scanned, hits, cut = clock(), 0, [], False
    for root in roots:
        for dirpath, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
            for n in names:
                if scanned >= MAX_FILES or clock() - start > MAX_SECONDS:
                    cut = True
                    break
                p = Path(dirpath) / n
                if p.suffix.lower() not in TEXT_EXT and p.suffix.lower() != ".docx":
                    continue
                scanned += 1
                text = read_text(p)
                if text is None:
                    continue
                low, name = text.lower(), p.name.lower()
                counts = {t: low.count(t) for t in terms}
                present = [t for t in terms if counts[t] or t in name]
                if not present:
                    continue
                score = sum(min(counts[t], 10) for t in terms) + 8 * sum(t in name for t in terms)
                score += 6 * (len(present) == len(terms))
                first = next((t for t in terms if counts[t]), present[0])
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    mtime = 0
                hits.append((score, mtime, p, snippet(text, first)))
            if cut:
                break
        if cut:
            break
    hits.sort(key=lambda h: (-h[0], -h[1]))
    return {"searched": scanned, "found": len(hits), "cut_short": cut,
            "showing": [{"file": h[2].name, "folder": str(h[2].parent), "match": h[3]} for h in hits[:limit]]}
