"""Accent colour themes. The dark look stays; the accent (buttons, highlights, glow) changes. Applied when the app starts."""
from __future__ import annotations

# name: (button, button on hover, glow)
PRESETS = {
    "blue": ("#1f6bff", "#3b82ff", "#2a62d9"),
    "emerald": ("#0e9f6e", "#10b981", "#0f9f6e"),
    "violet": ("#7c5cff", "#9b82ff", "#6a4be0"),
    "gold": ("#c98a00", "#e0a100", "#b87f00"),
    "rose": ("#e11d48", "#f43f5e", "#d6314f"),
}
LABELS = {"blue": "Electric blue", "emerald": "Emerald", "violet": "Violet", "gold": "Gold", "rose": "Rose"}


def colours(name: str) -> tuple[str, str, str]:
    return PRESETS.get(str(name).lower(), PRESETS["blue"])


def label_to_name(label: str) -> str:
    for k, v in LABELS.items():
        if v == label:
            return k
    return "blue"
