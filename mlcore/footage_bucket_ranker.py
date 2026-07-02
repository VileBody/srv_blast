"""Rank footage buckets by relevance to a track's lyrics — THEME-FIRST.

Principle (restored 2026-07-02): don't ask the LLM to blind-rank 48 flat vibes.
Instead CLASSIFY the track into emotional/topical THEMES (heartbreak, aggression,
hustle, party, serene…) with real instructions (config/styles/theme_relevance
descriptions), then EXPAND the ranked themes to their relevant visual BUCKETS via
the many-to-many THEME_BUCKETS map. Smaller, grounded LLM task; the prompt carries
actual rules; the shortlist is a complete, ordered list of bucket_ids.

One cheap LLM call (injected) returns the ranked THEMES; expansion is
deterministic. Pure helpers (prompt/parse/heuristic/mood) are separated from the
LLM I/O for testing. Determinism: temp=0 in the adapter + cache by hash(lyrics).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Dict, List, Optional

from config.styles.theme_groups import get_theme_label
from config.styles.theme_relevance import (
    THEME_DESCRIPTIONS_RU,
    buckets_for_themes,
    candidate_themes,
    theme_mood,
)
from mlcore.footage_bucket_catalog import Bucket, get_bucket_catalog

# Bump when the ranker prompt/parse/relevance semantics change so cached rankings
# for the same lyrics are invalidated (a cache miss re-ranks with the new logic).
RANKER_PROMPT_VERSION = "v2-theme"


def _norm(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


def catalog_fingerprint(catalog: List[Bucket]) -> str:
    """Short stable hash of the catalog's bucket-id set. Changing the catalog
    (add/remove/rename a bucket) changes the fingerprint → old rankings miss."""
    material = "|".join(sorted(str(b.bucket_id) for b in catalog))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def ranker_cache_key(*, lyrics: str, mood: str, catalog: List[Bucket], model: str) -> str:
    """Deterministic cache key — normalized lyrics + mood + catalog fingerprint +
    model + prompt version. Same inputs → same key; a hit is always for the exact
    same request."""
    lyr_h = hashlib.sha256(_norm(lyrics).encode("utf-8")).hexdigest()[:24]
    mood_norm = _norm(mood) or "any"
    fp = catalog_fingerprint(catalog)
    model_norm = str(model or "").strip() or "unknown"
    return f"ranker:{RANKER_PROMPT_VERSION}:{model_norm}:{fp}:{mood_norm}:{lyr_h}"


# --------------------------------------------------------------------------- #
# Theme classification (the LLM's task)
# --------------------------------------------------------------------------- #
def build_theme_prompt(lyrics: str, theme_ids: List[str]) -> Dict[str, str]:
    """System + user prompt to CLASSIFY the track into themes, most fitting first.
    The system prompt carries the actual rules (theme descriptions)."""
    lines = [
        f"{t} | {get_theme_label(t)} | {THEME_DESCRIPTIONS_RU.get(t, '')}"
        for t in theme_ids
    ]
    system = (
        "Ты арт-директор музыкальных клипов. По тексту трека определи, О ЧЁМ он "
        "и какое у него настроение, и отранжируй ТЕМЫ ниже по тому, насколько "
        "каждая подходит треку. Каждая тема = тип трека (id | название | описание). "
        "Верни ТОЛЬКО JSON-массив id тем, самая подходящая первой, каждый id ровно "
        "один раз. Без пояснений."
    )
    user = (
        f"ТЕКСТ ТРЕКА:\n{lyrics.strip()}\n\n"
        f"ТЕМЫ (id | название | описание):\n" + "\n".join(lines) +
        "\n\nВерни JSON-массив id тем по убыванию соответствия."
    )
    return {"system": system, "user": user}


def parse_theme_ranking(raw: str, valid_theme_ids: List[str]) -> List[str]:
    """Parse the model's theme-id array; keep valid+unique, then append any
    missing valid themes in input order so the result is always complete."""
    valid = list(dict.fromkeys(valid_theme_ids))
    valid_set = set(valid)
    s = str(raw or "").strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 2:
            s = parts[1]
            if s.lstrip().startswith("json"):
                s = s.lstrip()[4:]
            s = s.strip()
    ordered: List[str] = []
    try:
        arr = json.loads(s)
        if isinstance(arr, list):
            for x in arr:
                xid = _norm(x).replace(" ", "_")
                if xid in valid_set and xid not in ordered:
                    ordered.append(xid)
    except Exception:
        for xid in re.findall(r"[a-z0-9_]+", s.lower()):
            if xid in valid_set and xid not in ordered:
                ordered.append(xid)
    for vid in valid:  # complete deterministically
        if vid not in ordered:
            ordered.append(vid)
    return ordered


def heuristic_theme_rank(lyrics: str, theme_ids: List[str]) -> List[str]:
    """LLM-free fallback: score each theme by word overlap of the lyrics with its
    RU description + label. RU-aware (descriptions are Russian), unlike the old
    English-tag heuristic which scored ~0 on Russian lyrics."""
    words = set(re.findall(r"[a-zа-яё0-9]+", _norm(lyrics)))

    def score(t: str) -> int:
        terms = set(re.findall(r"[a-zа-яё0-9]+", _norm(THEME_DESCRIPTIONS_RU.get(t, ""))))
        terms |= set(re.findall(r"[a-zа-яё0-9]+", _norm(get_theme_label(t))))
        return len(words & terms)

    return sorted(theme_ids, key=lambda t: (-score(t), t))


# --------------------------------------------------------------------------- #
# LLM I/O adapter
# --------------------------------------------------------------------------- #
def gemini_rank_call(system: str, user: str) -> str:
    """One cheap Gemini Flash text call at temperature 0 (deterministic — matches
    the cache design). Raises on any failure so rank_buckets() falls back to the
    heuristic (never breaks the endpoint)."""
    import os

    from google.genai import types  # type: ignore

    from mlcore.hooks.f5_cognition._gemini import make_client

    model = (os.environ.get("FOOTAGE_RANKER_MODEL") or "gemini-2.0-flash").strip()
    client = make_client()
    resp = client.models.generate_content(
        model=model,
        contents=f"{system}\n\n{user}",
        config=types.GenerateContentConfig(temperature=0.0),
    )
    text = getattr(resp, "text", "") or ""
    if not text.strip():
        raise RuntimeError("empty ranker response")
    return text


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #
def rank_buckets(
    *,
    lyrics: str,
    mood: str = "",
    catalog: Optional[List[Bucket]] = None,
    llm_call: Optional[Callable[[str, str], str]] = None,
    raise_on_llm_error: bool = False,
) -> List[str]:
    """Full ranked list of bucket_ids for these lyrics, THEME-FIRST.

    1) classify the track into ranked themes (LLM with instructions, or heuristic);
    2) expand themes -> their relevant buckets (deterministic), mood-filtered;
    3) append any remaining live buckets so the list is always complete.

    raise_on_llm_error: when True a failing llm_call re-raises (so a caller that
    CACHES never stores a degraded heuristic as the real ranking).
    """
    cat = catalog if catalog is not None else get_bucket_catalog()
    valid_ids = {b.bucket_id for b in cat}
    catalog_order = [b.bucket_id for b in cat]

    def _mood_ok(bid: str) -> bool:
        m = _norm(mood)
        return m not in {"major", "minor"} or theme_mood(bid.split(":", 1)[0]) == m

    if not str(lyrics or "").strip():
        return [b for b in catalog_order if _mood_ok(b)]  # no lyrics → catalog order

    themes = candidate_themes(mood)
    ranked_themes: Optional[List[str]] = None
    if llm_call is not None:
        try:
            p = build_theme_prompt(lyrics, themes)
            ranked_themes = parse_theme_ranking(llm_call(p["system"], p["user"]), themes)
        except Exception:
            if raise_on_llm_error:
                raise
    if ranked_themes is None:
        ranked_themes = heuristic_theme_rank(lyrics, themes)

    ordered = buckets_for_themes(ranked_themes, valid_ids=valid_ids, mood=mood)
    for bid in catalog_order:  # complete the list with any bucket not yet covered
        if bid not in ordered and _mood_ok(bid):
            ordered.append(bid)
    return ordered
