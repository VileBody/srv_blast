"""Lyrics lexicon: stemming, extraction, and the gate that every mapped tag is a
real taxonomy tag (so lyric→tags always match bucket priority_tags)."""
from __future__ import annotations

import re
from pathlib import Path

from mlcore.lyrics_lexicon import extract_tags, load_lexicon, stem, tokenize

_ROOT = Path(__file__).resolve().parents[1]


def _taxonomy() -> set:
    src = (_ROOT / "footage_v2.py").read_text(encoding="utf-8")
    body = src[src.find("THEMES LOGIC"):]
    out = set()
    for t in re.findall(r'"([^"]+)"', body):
        tn = " ".join(t.strip().lower().split())
        if tn and not tn.startswith("_") and tn not in {"color", "exclude", "tags_groups"}:
            out.add(tn)
    return out


def test_stem_folds_inflections():
    assert stem("дождя") == stem("дождь") == stem("дождём")
    assert stem("cars") == stem("car")
    assert stem("НОЧНОЙ").startswith("ноч")


def test_tokenize_ru_en():
    assert tokenize("Иду один, rain 2am") == ["иду", "один", "rain", "2am"]


def test_load_and_extract():
    lex = load_lexicon()
    assert lex, "lexicon failed to load"
    tags = extract_tags("иду один под дождём по ночному городу", lex)
    assert tags["rain"] >= 1
    assert tags["lonely"] >= 1
    assert tags["night city"] >= 1


def test_extract_empty_when_no_match():
    lex = load_lexicon()
    assert extract_tags("qwerty zxcvb", lex) == extract_tags("", lex)  # both empty


def test_every_lexicon_tag_is_in_taxonomy():
    """Gate: a lexicon value pointing at a non-taxonomy tag would never match a
    bucket → wasted. Keep the lexicon honest."""
    import json
    raw = json.loads((_ROOT / "data" / "lyrics_lexicon.json").read_text(encoding="utf-8"))
    tax = _taxonomy()
    bad = {}
    for key, tags in raw["lexicon"].items():
        if key.startswith("_"):
            continue
        miss = [t for t in tags if " ".join(str(t).strip().lower().split()) not in tax]
        if miss:
            bad[key] = miss
    assert not bad, f"lexicon tags not in taxonomy: {bad}"
