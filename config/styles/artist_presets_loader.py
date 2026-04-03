from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


_PRESETS_PATH = Path(__file__).resolve().parent / "artist_presets.json"
_CACHE: Optional[Dict[str, Any]] = None


def _load() -> Dict[str, Any]:
    global _CACHE  # noqa: PLW0603
    if _CACHE is None:
        _CACHE = json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
    return _CACHE


def get_genres() -> List[Dict[str, Any]]:
    """Return list of genre dicts with keys: key, label, artists."""
    return list(_load()["genres"])


def get_artists(genre_key: str) -> List[Dict[str, Any]]:
    """Return artist list for a given genre key. Raises KeyError if not found."""
    for g in _load()["genres"]:
        if g["key"] == genre_key:
            return list(g["artists"])
    raise KeyError(f"Unknown genre key: {genre_key!r}")


def get_preset(genre_key: str, artist_key: str) -> Dict[str, Any]:
    """Return a single artist preset dict. Raises KeyError if not found."""
    for artist in get_artists(genre_key):
        if artist["key"] == artist_key:
            return dict(artist)
    raise KeyError(f"Unknown artist key: {artist_key!r} in genre {genre_key!r}")


def get_genre_labels() -> Dict[str, str]:
    """Return {genre_key: genre_label} mapping."""
    return {g["key"]: g["label"] for g in _load()["genres"]}


def find_preset_by_artist_id(artist_id: str) -> Optional[Dict[str, Any]]:
    """Find artist preset by its key (artist_id). Returns None if not found."""
    aid = str(artist_id or "").strip()
    if not aid:
        return None
    for genre in _load()["genres"]:
        for artist in genre["artists"]:
            if artist["key"] == aid:
                return {"genre_key": genre["key"], **artist}
    return None
