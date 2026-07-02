"""Rank footage buckets by relevance to a track's lyrics.

One cheap LLM call (injected) returns the FULL ranked list of bucket_ids, so
the bot shows the top-3 and "Обновить" just pages further — no extra calls.
Pure helpers (prompt build / response parse / heuristic fallback / mood filter)
are separated from the LLM I/O for testing and determinism.

Determinism: caller uses temp=0 + caches by hash(lyrics); parse always returns a
COMPLETE, deduped list (valid ids the model omitted are appended in catalog
order) so pagination never runs out unexpectedly.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Dict, List, Optional

from mlcore.footage_bucket_catalog import Bucket, get_bucket_catalog

# Bump when the ranker prompt/parse semantics change so cached rankings for the
# same lyrics are invalidated (a cache miss re-ranks with the new prompt).
RANKER_PROMPT_VERSION = "v1"


def _norm(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


def catalog_fingerprint(catalog: List[Bucket]) -> str:
    """Short stable hash of the catalog's bucket-id set. Changing the catalog
    (add/remove/rename a bucket) changes the fingerprint → old rankings miss."""
    material = "|".join(sorted(str(b.bucket_id) for b in catalog))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def ranker_cache_key(
    *,
    lyrics: str,
    mood: str,
    catalog: List[Bucket],
    model: str,
) -> str:
    """Deterministic cache key for a ranking.

    Includes EVERY input that affects the output — normalized lyrics, mood,
    catalog fingerprint, model id, prompt version — so a hit can never serve a
    ranking computed for different inputs. Same inputs → same key.
    """
    lyr = _norm(lyrics)
    lyr_h = hashlib.sha256(lyr.encode("utf-8")).hexdigest()[:24]
    mood_norm = _norm(mood) or "any"
    fp = catalog_fingerprint(catalog)
    model_norm = str(model or "").strip() or "unknown"
    return f"ranker:{RANKER_PROMPT_VERSION}:{model_norm}:{fp}:{mood_norm}:{lyr_h}"


def filter_by_mood(buckets: List[Bucket], mood: str) -> List[Bucket]:
    """Hard mood filter (don't offer major vibes for a minor track). Empty/unknown
    mood → no filtering. If the filter would empty the list, fall back to all."""
    m = _norm(mood)
    if m not in {"major", "minor"}:
        return list(buckets)
    kept = [b for b in buckets if b.mood == m]
    return kept or list(buckets)


def build_ranker_prompt(lyrics: str, buckets: List[Bucket]) -> Dict[str, str]:
    """System + user prompt for the ranker. Returns {"system","user"}."""
    lines = []
    for b in buckets:
        sample = ", ".join(b.priority_tags[:5])
        lines.append(f"{b.bucket_id} | {b.label} | {sample}")
    catalog = "\n".join(lines)
    system = (
        "You are a music-video art director. Given a song's lyrics and a catalog of "
        "footage vibes (each: id | name | sample visual tags), rank the vibes by how "
        "well their VISUALS fit the song. Consider mood, imagery, and setting in the "
        "lyrics. Return ONLY a JSON array of bucket ids, most relevant first, "
        "including every id exactly once. No prose."
    )
    user = f"LYRICS:\n{lyrics.strip()}\n\nVIBES (id | name | tags):\n{catalog}\n\nReturn JSON array of ids."
    return {"system": system, "user": user}


def parse_ranking_response(raw: str, valid_ids: List[str]) -> List[str]:
    """Parse the model's id array; keep valid+unique, then append any missing
    valid ids in catalog order so the result is always complete."""
    valid = list(dict.fromkeys(valid_ids))
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
                xid = str(x or "").strip()
                if xid in valid_set and xid not in ordered:
                    ordered.append(xid)
    except Exception:
        # tolerant fallback: pull ids by regex in appearance order
        for xid in re.findall(r"[a-z0-9_]+:[a-z0-9_]+", s):
            if xid in valid_set and xid not in ordered:
                ordered.append(xid)
    for vid in valid:  # complete the list deterministically
        if vid not in ordered:
            ordered.append(vid)
    return ordered


def heuristic_rank(lyrics: str, buckets: List[Bucket]) -> List[str]:
    """LLM-free fallback: score by overlap of lyrics words with label+tags."""
    words = set(re.findall(r"[a-zа-я0-9]+", _norm(lyrics)))

    def score(b: Bucket) -> int:
        terms = set()
        for t in b.priority_tags:
            terms |= set(t.split())
        terms |= set(_norm(b.label).split())
        return len(words & terms)

    ranked = sorted(buckets, key=lambda b: (-score(b), b.bucket_id))
    return [b.bucket_id for b in ranked]


def gemini_rank_call(system: str, user: str) -> str:
    """I/O adapter: one cheap Gemini Flash text call. Raises on any failure so
    rank_buckets() falls back to the heuristic (never breaks the endpoint)."""
    import os

    from mlcore.hooks.f5_cognition._gemini import make_client

    model = (os.environ.get("FOOTAGE_RANKER_MODEL") or "gemini-2.0-flash").strip()
    client = make_client()
    resp = client.models.generate_content(
        model=model,
        contents=f"{system}\n\n{user}",
    )
    text = getattr(resp, "text", "") or ""
    if not text.strip():
        raise RuntimeError("empty ranker response")
    return text


def rank_buckets(
    *,
    lyrics: str,
    mood: str = "",
    catalog: Optional[List[Bucket]] = None,
    llm_call: Optional[Callable[[str, str], str]] = None,
    raise_on_llm_error: bool = False,
) -> List[str]:
    """Return the full ranked list of bucket_ids for these lyrics.

    llm_call(system, user) -> raw text. If None or it fails, falls back to the
    heuristic. Mood hard-filters the candidate set first.

    raise_on_llm_error: when True, a failing llm_call re-raises instead of
    silently returning the heuristic. Callers that CACHE the result use this so a
    degraded heuristic (produced while the LLM is down) is never stored as if it
    were the real ranking.
    """
    buckets = filter_by_mood(catalog if catalog is not None else get_bucket_catalog(), mood)
    valid_ids = [b.bucket_id for b in buckets]
    if not str(lyrics or "").strip():
        return valid_ids  # nothing to rank on → catalog order
    if llm_call is not None:
        try:
            prompt = build_ranker_prompt(lyrics, buckets)
            raw = llm_call(prompt["system"], prompt["user"])
            return parse_ranking_response(raw, valid_ids)
        except Exception:
            if raise_on_llm_error:
                raise
    return heuristic_rank(lyrics, buckets)
