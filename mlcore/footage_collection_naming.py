"""Stable, collision-free, ASCII-safe names for collection-plane clips.

Two properties of the real uploads forced this module into existence, and both
were found the hard way against a 939-file / 12-folder films batch:

1. **Basenames repeat across folders.** Every collection was delivered as
   ``clip_001.mp4 … clip_0NN.mp4``, and the inventory builder keys its asset map
   on the basename alone — so 939 files collapsed to 120 and eleven of twelve
   collections silently emptied. Identity therefore has to carry the folder, not
   just the file.

2. **Folder names are Cyrillic** ("бойцовский клуб"). That is fine in an S3 key,
   but AE fails on non-ASCII local paths, and the media sanitizer only strips
   Windows-forbidden characters — it passes Cyrillic straight through. So the
   name the render node writes to disk has to be ASCII by construction.

The qualified name is an OPAQUE identity. Nothing parses it back: the real object
to fetch travels separately as ``media_file_name``, and the folder a clip belongs
to travels as ``genre``/``tag``. That keeps this file free to change shape.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Tuple

# Separator between the folder part and the basename. Underscores survive
# `_sanitize_media_file_name` untouched (it only rewrites Windows-forbidden
# characters), so a qualified name reaches the node intact.
SEP = "__"

# Cyrillic -> Latin. Only so an operator reading a log or a media folder can tell
# which film a clip came from; correctness rests on the hash below, never on this.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

_KEEP_RE = re.compile(r"[^a-z0-9]+")
_TRIM_RE = re.compile(r"^-+|-+$")

_HASH_LEN = 6


def _translit(text: str) -> str:
    out = []
    for ch in str(text or "").lower():
        out.append(_TRANSLIT.get(ch, ch))
    return "".join(out)


def ascii_slug(text: Any, *, max_len: int = 40) -> str:
    """Lowercase ASCII slug of `text`. May be empty for fully non-Latin input."""
    s = _KEEP_RE.sub("-", _translit(text))
    s = _TRIM_RE.sub("", s)
    return s[:max_len].rstrip("-")


def folder_token(genre: Any, tag: Any) -> str:
    """Readable-but-unique token for one collection folder.

    The hash is unconditional, not a fallback for unslugifiable input. Two folders
    can differ in ways a slug erases — case (S3 keys are case-sensitive),
    punctuation, or a script the translit table does not cover — and collapsing
    them would merge two collections into one. The hash is taken over the EXACT
    original pair, so distinct folders always yield distinct tokens.
    """
    exact = f"{str(genre or '')}/{str(tag or '')}"
    digest = hashlib.sha1(exact.encode("utf-8")).hexdigest()[:_HASH_LEN]
    slug = ascii_slug(tag)
    return f"{slug}-{digest}" if slug else digest


def qualified_file_name(genre: Any, tag: Any, file_name: Any) -> str:
    """Picker identity for one collection clip: ``<genre>__<folder>__<base>``.

    Opaque by design — see the module docstring. The extension is preserved
    because both AE and the media fetcher key behaviour off it.
    """
    name = str(file_name or "").strip()
    if not name:
        raise RuntimeError("qualified_file_name requires a non-empty file_name")
    stem, dot, ext = name.rpartition(".")
    base = ascii_slug(stem if dot else name, max_len=60) or "clip"
    kind = ascii_slug(genre) or "collection"
    qualified = f"{kind}{SEP}{folder_token(genre, tag)}{SEP}{base}"
    return f"{qualified}.{ext.lower()}" if dot else qualified


def collection_clip_id(genre: Any, tag: Any, file_name: Any) -> str:
    """Registry primary key for a collection clip.

    The shared extractor wants 8+ consecutive digits in the name, which the
    delivered files (``clip_003.mp4``) do not have — every row was dropped and the
    Postgres pool registry came back empty. A collection clip is identified by
    WHERE it lives plus its own name, so that is what the id is built from.
    """
    stem = str(file_name or "").strip()
    stem = stem.rpartition(".")[0] or stem
    exact = f"{str(genre or '')}/{str(tag or '')}/{stem}"
    digest = hashlib.sha1(exact.encode("utf-8")).hexdigest()[:16]
    slug = ascii_slug(stem, max_len=24)
    return f"{slug}-{digest}" if slug else digest


def is_enabled_for(media_type: Any) -> bool:
    return str(media_type or "").strip().lower() == "collection"


def split_env_override() -> Tuple[bool, str]:
    """Escape hatch: FOOTAGE_COLLECTION_QUALIFY=0 restores raw basenames.

    Kept because qualification changes every collection clip's identity, and an
    identity change invalidates the cooldown ledger and any stored selection. If
    that ever needs undoing in prod it must not require a deploy.
    """
    raw = str(os.environ.get("FOOTAGE_COLLECTION_QUALIFY") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off"), raw
