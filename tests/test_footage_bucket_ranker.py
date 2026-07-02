from __future__ import annotations

from mlcore.footage_bucket_catalog import Bucket
from mlcore.footage_bucket_ranker import (
    build_ranker_prompt,
    catalog_fingerprint,
    filter_by_mood,
    heuristic_rank,
    parse_ranking_response,
    rank_buckets,
    ranker_cache_key,
)


def _b(bid, theme, group, mood, tags, label):
    return Bucket(bucket_id=bid, theme=theme, tags_group=group, mood=mood,
                  priority_tags=tags, subtheme_label=label)


_CAT = [
    _b("heartbreak_minor:winter_isolation", "heartbreak_minor", "winter_isolation", "minor",
       ["snow", "winter", "cold", "snowy road"], "Зимняя изоляция"),
    _b("aggression_minor:chaos_elements", "aggression_minor", "chaos_elements", "minor",
       ["fire", "explosion", "smoke", "chaos"], "Хаос / огонь"),
    _b("romance_major:nature_sunset", "romance_major", "nature_sunset", "major",
       ["sunset", "beach", "ocean", "golden hour"], "Природа / закат"),
]


def test_filter_by_mood_keeps_matching_else_all() -> None:
    assert {b.bucket_id for b in filter_by_mood(_CAT, "minor")} == {
        "heartbreak_minor:winter_isolation", "aggression_minor:chaos_elements"}
    assert len(filter_by_mood(_CAT, "")) == 3  # unknown mood -> all
    # filter that would empty falls back to all
    only_major = [_CAT[2]]
    assert filter_by_mood(only_major, "minor") == only_major


def test_parse_ranking_completes_and_validates() -> None:
    ids = [b.bucket_id for b in _CAT]
    # model returns 2 of 3 (and a bogus id) -> keep valid order, append missing
    raw = '["romance_major:nature_sunset", "bogus:id", "aggression_minor:chaos_elements"]'
    out = parse_ranking_response(raw, ids)
    assert out[0] == "romance_major:nature_sunset"
    assert out[1] == "aggression_minor:chaos_elements"
    assert set(out) == set(ids) and len(out) == len(ids)  # complete, deduped


def test_parse_ranking_tolerates_fences_and_regex_fallback() -> None:
    ids = [b.bucket_id for b in _CAT]
    fenced = '```json\n["heartbreak_minor:winter_isolation"]\n```'
    assert parse_ranking_response(fenced, ids)[0] == "heartbreak_minor:winter_isolation"
    # non-JSON: ids pulled by regex in order
    loose = "first heartbreak_minor:winter_isolation then romance_major:nature_sunset"
    out = parse_ranking_response(loose, ids)
    assert out[0] == "heartbreak_minor:winter_isolation"
    assert set(out) == set(ids)


def test_heuristic_rank_prefers_lyric_overlap() -> None:
    out = heuristic_rank("snow falling on a cold winter road", _CAT)
    assert out[0] == "heartbreak_minor:winter_isolation"


def test_rank_buckets_uses_llm_then_falls_back() -> None:
    ids = [b.bucket_id for b in _CAT]
    # LLM path
    called = {}
    def fake_llm(system, user):
        called["yes"] = True
        return '["aggression_minor:chaos_elements"]'
    out = rank_buckets(lyrics="burn it all down", mood="minor", catalog=_CAT, llm_call=fake_llm)
    assert called.get("yes") and out[0] == "aggression_minor:chaos_elements"
    # mood filter excluded the major bucket
    assert "romance_major:nature_sunset" not in out

    # LLM raises -> heuristic fallback
    def boom(system, user):
        raise RuntimeError("llm down")
    out2 = rank_buckets(lyrics="snow winter cold", mood="", catalog=_CAT, llm_call=boom)
    assert out2[0] == "heartbreak_minor:winter_isolation"


def test_rank_buckets_raise_on_llm_error_reraises() -> None:
    import pytest

    def boom(system, user):
        raise RuntimeError("llm down")

    # default: swallow + heuristic
    out = rank_buckets(lyrics="snow winter", mood="", catalog=_CAT, llm_call=boom)
    assert out[0] == "heartbreak_minor:winter_isolation"
    # strict: re-raise so the caller does NOT cache a heuristic
    with pytest.raises(RuntimeError):
        rank_buckets(
            lyrics="snow winter", mood="", catalog=_CAT, llm_call=boom,
            raise_on_llm_error=True,
        )


def test_empty_lyrics_returns_catalog_order() -> None:
    out = rank_buckets(lyrics="   ", mood="", catalog=_CAT, llm_call=None)
    assert out == [b.bucket_id for b in _CAT]


def test_build_prompt_lists_every_bucket() -> None:
    p = build_ranker_prompt("some lyrics", _CAT)
    for b in _CAT:
        assert b.bucket_id in p["user"]
    assert "JSON array" in p["system"]


# ---- ranker cache key -----------------------------------------------------

def test_cache_key_same_inputs_same_key() -> None:
    k1 = ranker_cache_key(lyrics="Snow winter cold", mood="minor", catalog=_CAT, model="m1")
    k2 = ranker_cache_key(lyrics="  snow   WINTER cold ", mood="Minor", catalog=_CAT, model="m1")
    assert k1 == k2  # normalized lyrics/mood → identical key


def test_cache_key_changes_on_any_input() -> None:
    base = ranker_cache_key(lyrics="a b c", mood="minor", catalog=_CAT, model="m1")
    assert base != ranker_cache_key(lyrics="a b d", mood="minor", catalog=_CAT, model="m1")
    assert base != ranker_cache_key(lyrics="a b c", mood="major", catalog=_CAT, model="m1")
    assert base != ranker_cache_key(lyrics="a b c", mood="minor", catalog=_CAT, model="m2")
    # catalog change (drop a bucket) → different fingerprint → different key
    smaller = _CAT[:-1]
    assert base != ranker_cache_key(lyrics="a b c", mood="minor", catalog=smaller, model="m1")


def test_catalog_fingerprint_is_order_independent() -> None:
    assert catalog_fingerprint(_CAT) == catalog_fingerprint(list(reversed(_CAT)))
    assert catalog_fingerprint(_CAT) != catalog_fingerprint(_CAT[:-1])


def test_cache_key_prefixed_and_versioned() -> None:
    k = ranker_cache_key(lyrics="x", mood="", catalog=_CAT, model="gemini-2.0-flash")
    assert k.startswith("ranker:v1:gemini-2.0-flash:")
