from __future__ import annotations

import pytest

from config.styles.theme_relevance import (
    THEME_BUCKETS,
    THEME_DESCRIPTIONS_RU,
    candidate_themes,
)
from mlcore.footage_bucket_catalog import get_bucket_catalog
from mlcore.footage_bucket_ranker import (
    build_theme_prompt,
    catalog_fingerprint,
    lexicon_theme_rank,
    parse_theme_ranking,
    rank_buckets,
    ranker_cache_key,
)

_CAT = get_bucket_catalog()
_IDS = {b.bucket_id for b in _CAT}


def _theme_of(bid: str) -> str:
    return bid.split(":", 1)[0]


# --------------------------------------------------------------------------- #
# relevance map integrity (the reconstructed track->visual associations)
# --------------------------------------------------------------------------- #
def test_relevance_map_valid_and_complete() -> None:
    for t, bids in THEME_BUCKETS.items():
        for bid in bids:
            assert bid in _IDS, f"{t} -> {bid} not in live catalog"
        assert THEME_DESCRIPTIONS_RU.get(t), f"no description for theme {t}"
    reachable = {x for v in THEME_BUCKETS.values() for x in v}
    assert not (_IDS - reachable), f"catalog buckets unreachable from any theme: {_IDS - reachable}"


def test_candidate_themes_mood_filter() -> None:
    assert all(t.endswith("_minor") for t in candidate_themes("minor"))
    assert all(t.endswith("_major") for t in candidate_themes("major"))
    assert set(candidate_themes("")) == set(THEME_BUCKETS)  # unknown -> all


# --------------------------------------------------------------------------- #
# theme classification prompt / parse / heuristic
# --------------------------------------------------------------------------- #
def test_build_theme_prompt_carries_instructions_and_descriptions() -> None:
    p = build_theme_prompt("какой-то текст", ["heartbreak_minor", "hustle_minor"])
    assert "арт-директор" in p["system"]                     # real instructions
    assert "разбитое сердце" in p["user"]                    # heartbreak description
    assert "heartbreak_minor" in p["user"] and "hustle_minor" in p["user"]


def test_parse_theme_ranking_completes_and_validates() -> None:
    themes = ["heartbreak_minor", "hustle_minor", "aggression_minor"]
    out = parse_theme_ranking('["hustle_minor", "bogus_theme", "aggression_minor"]', themes)
    assert out[0] == "hustle_minor" and out[1] == "aggression_minor"
    assert set(out) == set(themes) and len(out) == len(themes)
    # fenced json + regex fallback
    assert parse_theme_ranking('```json\n["aggression_minor"]\n```', themes)[0] == "aggression_minor"
    assert parse_theme_ranking("сначала hustle_minor потом heartbreak_minor", themes)[0] == "hustle_minor"


def test_lexicon_theme_rank_is_ru_aware_and_deterministic() -> None:
    themes = ["heartbreak_minor", "hustle_minor", "serene_landscape_major"]
    # lyrics -> tags (lexicon) -> theme scored by overlap with its buckets' tags
    assert lexicon_theme_rank("деньги тачки золото украшения", themes, _CAT)[0] == "hustle_minor"
    assert lexicon_theme_rank("море пляж закат солнце", themes, _CAT)[0] == "serene_landscape_major"


# --------------------------------------------------------------------------- #
# rank_buckets (theme-first end to end)
# --------------------------------------------------------------------------- #
def test_rank_buckets_classifies_then_expands() -> None:
    out = rank_buckets(lyrics="про деньги и успех", mood="minor", catalog=_CAT,
                       llm_call=lambda s, u: '["hustle_minor"]')
    assert out[0] in THEME_BUCKETS["hustle_minor"]           # hustle's visuals first
    assert set(out) <= _IDS and len(out) == len(set(out))    # complete, deduped


def test_rank_buckets_mood_hard_filters() -> None:
    # minor track; model tries to pick a MAJOR theme -> dropped, no major buckets shown
    out = rank_buckets(lyrics="x", mood="minor", catalog=_CAT,
                       llm_call=lambda s, u: '["serene_landscape_major"]')
    assert out and all(_theme_of(b).endswith("_minor") for b in out)


def test_rank_buckets_heuristic_fallback_ru() -> None:
    def boom(s, u):
        raise RuntimeError("llm down")
    out = rank_buckets(lyrics="деньги роскошь флекс статус", mood="minor", catalog=_CAT, llm_call=boom)
    assert out[0] in THEME_BUCKETS["hustle_minor"]


def test_rank_buckets_raise_on_llm_error() -> None:
    def boom(s, u):
        raise RuntimeError("llm down")
    # default swallows -> heuristic
    assert rank_buckets(lyrics="деньги", mood="minor", catalog=_CAT, llm_call=boom)
    with pytest.raises(RuntimeError):
        rank_buckets(lyrics="x", mood="", catalog=_CAT, llm_call=boom, raise_on_llm_error=True)


def test_empty_lyrics_returns_mood_filtered_catalog_order() -> None:
    out = rank_buckets(lyrics="   ", mood="minor", catalog=_CAT, llm_call=None)
    expected = [b.bucket_id for b in _CAT if _theme_of(b.bucket_id).endswith("_minor")]
    assert out == expected


# --------------------------------------------------------------------------- #
# cache key
# --------------------------------------------------------------------------- #
def test_cache_key_normalizes_and_versions() -> None:
    k1 = ranker_cache_key(lyrics="Snow  cold ", mood="Minor", catalog=_CAT, model="m1")
    k2 = ranker_cache_key(lyrics="snow cold", mood="minor", catalog=_CAT, model="m1")
    assert k1 == k2
    assert ranker_cache_key(lyrics="x", mood="", catalog=_CAT, model="gemini-2.0-flash").startswith(
        "ranker:v2-theme:gemini-2.0-flash:")


def test_cache_key_changes_on_any_input() -> None:
    base = ranker_cache_key(lyrics="a b c", mood="minor", catalog=_CAT, model="m1")
    assert base != ranker_cache_key(lyrics="a b d", mood="minor", catalog=_CAT, model="m1")
    assert base != ranker_cache_key(lyrics="a b c", mood="major", catalog=_CAT, model="m1")
    assert base != ranker_cache_key(lyrics="a b c", mood="minor", catalog=_CAT, model="m2")
    assert base != ranker_cache_key(lyrics="a b c", mood="minor", catalog=_CAT[:-1], model="m1")


def test_catalog_fingerprint_order_independent() -> None:
    assert catalog_fingerprint(_CAT) == catalog_fingerprint(list(reversed(_CAT)))
    assert catalog_fingerprint(_CAT) != catalog_fingerprint(_CAT[:-1])


# --- deterministic ranker robustness (urgent: rank-buckets endpoint must never
# 500 → bot must never fall to the legacy artist path) --------------------------

@pytest.mark.parametrize("lyrics", [None, "", "   ", "деньги тачки", pytest.param("x" * 5000, id="long"), 123, ["a"], {"k": 1}])
@pytest.mark.parametrize("mood", [None, "", "minor", "major", "ZZZ_unknown", 7])
def test_rank_buckets_deterministic_never_raises(lyrics, mood):
    out = rank_buckets(lyrics=lyrics, mood=mood, catalog=_CAT, llm_call=None)
    assert isinstance(out, list)
    # every id is a real catalog bucket
    assert all(i in _IDS for i in out)


def test_rank_buckets_empty_catalog_is_safe():
    assert rank_buckets(lyrics="anything", mood="", catalog=[], llm_call=None) == []


def test_rank_buckets_deterministic_smoke_top():
    # hustle/luxury lyrics → a hustle/adrenaline bucket near the top
    top = rank_buckets(lyrics="деньги тачки успех флекс", mood="", catalog=_CAT, llm_call=None)[:5]
    assert any(_theme_of(b) in ("hustle_minor", "adrenaline_flex_major") for b in top), top
    top2 = rank_buckets(lyrics="клуб рейв танцпол неон", mood="", catalog=_CAT, llm_call=None)[:5]
    assert any("nightlife" in _theme_of(b) or "night" in b for b in top2), top2
