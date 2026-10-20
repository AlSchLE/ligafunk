"""Selectable recap voices and themes.

This module is deliberately free of heavy imports so the web app can use it
for form validation and for rendering the selector without loading the recap
engine (Sleeper client, caches, ...).
"""

from __future__ import annotations

DEFAULT_STYLE = "nfl_reporter"
DEFAULT_THEME = "none"

# Voice / language of the play-by-play text.
STYLE_META: dict[str, dict[str, str]] = {
    "nfl_reporter": {
        "name": "NFL Reporter",
        "emoji": "🎙️",
        "language": "en",
        "description": "Broadcast-Feeling wie ESPN: Box Score, Film Session, kein Gerede.",
    },
    "bromance": {
        "name": "Football Bromance",
        "emoji": "🍻",
        "language": "de",
        "description": "Lockerer Buddy-Trash-Talk auf Deutsch: Memes, Bench-Roasts, null Mitleid.",
    },
}

# Optional seasonal overlay. Works with every voice.
THEME_META: dict[str, dict[str, str]] = {
    "none": {
        "name": "Kein Theme",
        "emoji": "📋",
        "description": "Normaler Recap ohne saisonale Einfärbung.",
    },
    "christmas": {
        "name": "Weihnachten",
        "emoji": "🎄",
        "description": "Bescherung, Wunschzettel und Christkind — für die Feiertagswochen.",
    },
    "halloween": {
        "name": "Halloween",
        "emoji": "🎃",
        "description": "Grusel-Modus mit Geisterstunde, Horror-Lines und Süßem oder Saurem.",
    },
    "tight_end": {
        "name": "Tight End Week",
        "emoji": "🧱",
        "description": "Die Woche der Tight Ends: Blocking, Streamer und gebrochene Herzen.",
    },
}

STYLE_IDS = tuple(STYLE_META)
THEME_IDS = tuple(THEME_META)


def normalize_style(value: str | None) -> str:
    """Return a known style id, falling back to the default."""
    key = (value or "").strip().lower()
    return key if key in STYLE_META else DEFAULT_STYLE


def normalize_theme(value: str | None) -> str:
    """Return a known theme id, falling back to 'none'."""
    key = (value or "").strip().lower()
    return key if key in THEME_META else DEFAULT_THEME


def style_label(style: str | None) -> str:
    meta = STYLE_META.get(normalize_style(style))
    return f"{meta['emoji']} {meta['name']}" if meta else ""


def theme_label(theme: str | None) -> str:
    meta = THEME_META.get(normalize_theme(theme))
    return f"{meta['emoji']} {meta['name']}" if meta else ""
