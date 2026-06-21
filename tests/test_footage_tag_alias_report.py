from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "footage_tag_alias_report",
    Path(__file__).resolve().parents[1] / "scripts" / "footage_tag_alias_report.py",
)
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)  # type: ignore

extract_taxonomy = _mod.extract_taxonomy
suggest_match = _mod.suggest_match
classify = _mod.classify

_TAX = {"rain", "mountain", "street lights", "city lights", "trees", "dark forest", "night city"}


def test_extract_taxonomy_picks_tag_strings() -> None:
    src = 'THEMES LOGIC\n"romance": {"color":["warm"], "_tags":["sunset","golden hour"]}'
    tax = extract_taxonomy(src)
    assert "sunset" in tax and "golden hour" in tax
    assert "color" not in tax and "_tags" not in tax


def test_suggest_match_token_overlap_beats_substring_garbage() -> None:
    # "tree" must NOT map to a no-shared-token tag; fuzzy -> trees.
    cand, score, _ = suggest_match("tree", _TAX)
    assert cand == "trees" and score >= 0.8
    # "mountain road" shares the 'mountain' token -> mountain (unambiguous).
    cand2, _, reason2 = suggest_match("mountain road", {"mountain", "night city", "trees"})
    assert cand2 == "mountain" and reason2 == "token"
    # Ambiguous multi-token tags pick SOME token-sharing candidate (reviewed by human).
    cand3, _, reason3 = suggest_match("night forest", _TAX)
    assert reason3 == "token" and cand3 in {"dark forest", "night city"}


def test_suggest_match_returns_none_when_nothing_shares() -> None:
    cand, score, _ = suggest_match("jewelry", _TAX)
    assert cand is None and score == 0.0


def test_suggest_match_fuzzy_singular_plural() -> None:
    cand, score, reason = suggest_match("rainy", _TAX)
    assert cand == "rain" and reason == "fuzzy" and score >= 0.85


def test_classify_buckets_and_visibility() -> None:
    tag_freq = {
        "rain": 100,            # in taxonomy -> visible
        "rainy": 50,            # alias key -> visible
        "mountains": 20,        # unmatched -> high (fuzzy mountain)
        "jewelry": 15,          # unmatched -> add_to_taxonomy
        "noise": 2,             # below min_freq -> ignored
    }
    aliases = {"rainy": "rain"}
    rep = classify(tag_freq, _TAX, aliases, min_freq=5)
    # visible instances = rain(100)+rainy(50)=150 of 187
    assert rep["totals"]["visibility_pct"] == round(100 * 150 / 187, 1)
    high_tags = {r["tag"] for r in rep["high_confidence_aliases"]}
    assert "mountains" in high_tags
    add_tags = {r["tag"] for r in rep["add_to_taxonomy_candidates"]}
    assert "jewelry" in add_tags
    # below-min-freq excluded everywhere
    all_listed = high_tags | add_tags | {r["tag"] for r in rep["mid_confidence_review"]}
    assert "noise" not in all_listed
