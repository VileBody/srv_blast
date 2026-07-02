"""Lyrics lexicon — the shared word→tags bridge (no LLM).

One artifact powers two features:
  - deterministic bucket RANKING (footage_bucket_ranker): lyrics → tags → score
    each theme by overlap with its buckets' tags;
  - per-segment TARGETING (Wave 2): a subtitle line's words → tags → boost the
    clips in the chosen bucket that match those tags.

`data/lyrics_lexicon.json` maps a stemmed word (RU or EN) → list of taxonomy
tags. Matching is stem-based (light suffix stripping) so inflected forms
("дождя", "дождём") hit the same entry as the base word. It is intentionally
extensible: unknown words simply contribute nothing (graceful), and coverage
grows by editing the JSON — no code change.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "lyrics_lexicon.json"

# Suffix strip (longest first) — folds common RU inflections and EN plurals onto
# a shared stem without a morphology library. Applied longest-first; only when it
# leaves a stem of >= 3 chars.
_SUFFIXES = sorted(
    {
        # RU noun/adj case endings
        "ами", "ями", "ыми", "ими", "ого", "его", "ому", "ему", "ов", "ев", "ах",
        "ях", "ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие", "ем", "ём",
        "ом", "ью", "ах", "ей",
        # RU verb endings
        "ешь", "ете", "ели", "ало", "или", "ет", "ут", "ют", "ла", "ло", "ли",
        "ть", "ся",
        # EN plurals / verb forms
        "ing", "ies", "ers", "ed", "es",
        # single-char tails
        "у", "ю", "е", "а", "я", "и", "ы", "о", "ь", "й", "s",
    },
    key=len,
    reverse=True,
)


def stem(word: str) -> str:
    """Lowercase + strip one common suffix. Short words are left as-is."""
    w = str(word or "").strip().lower()
    if len(w) <= 3:
        return w
    for suf in _SUFFIXES:  # longest-first
        if len(suf) <= len(w) - 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zа-яё0-9]+", str(text or "").lower())


def load_lexicon(path: Path | str | None = None) -> Dict[str, List[str]]:
    """Load and index the lexicon as {stemmed_key: [tags]}. Multiple raw keys can
    stem to the same key — their tag lists merge (deduped)."""
    p = Path(path) if path else _DEFAULT_PATH
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    entries = raw.get("lexicon", raw) if isinstance(raw, dict) else {}
    out: Dict[str, List[str]] = {}
    for key, tags in entries.items():
        k = stem(key)
        if not k:
            continue
        bucket = out.setdefault(k, [])
        for t in tags or []:
            tv = " ".join(str(t).strip().lower().split())
            if tv and tv not in bucket:
                bucket.append(tv)
    return out


def extract_tags(lyrics: str, lexicon: Dict[str, List[str]]) -> Counter:
    """lyrics → Counter of taxonomy tags (weighted by how often the mapping word
    appears). Empty when nothing matches — callers fall back gracefully."""
    tags: Counter = Counter()
    if not lexicon:
        return tags
    for w in tokenize(lyrics):
        hit = lexicon.get(stem(w))
        if hit:
            for t in hit:
                tags[t] += 1
    return tags
