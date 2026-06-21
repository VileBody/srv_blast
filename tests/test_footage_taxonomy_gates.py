"""CI gates that catch footage taxonomy/config drift before prod.

These are intentionally strict: adding an artist or theme in one file but not
the others, or an alias pointing at a non-existent taxonomy tag, fails CI
instead of silently degrading the picker.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _footage_v2_src() -> str:
    return (_ROOT / "footage_v2.py").read_text(encoding="utf-8")


def _themes_logic_text() -> str:
    src = _footage_v2_src()
    i = src.find("THEMES LOGIC")
    assert i >= 0, "THEMES LOGIC section missing from footage_v2.py"
    return src[i:]


def _defined_themes() -> set:
    return set(re.findall(r'"([a-z0-9_]+_(?:minor|major))":\s*\{', _themes_logic_text()))


def _taxonomy_tags() -> set:
    out = set()
    for t in re.findall(r'"([^"]+)"', _themes_logic_text()):
        tn = " ".join(t.strip().lower().split())
        if tn and not tn.startswith("_") and tn not in {"color", "exclude", "tags_groups"}:
            out.add(tn)
    return out


def _artist_presets() -> dict:
    return json.loads((_ROOT / "config" / "styles" / "artist_presets.json").read_text(encoding="utf-8"))


def _aliases() -> dict:
    return json.loads((_ROOT / "data" / "tag_aliases.json").read_text(encoding="utf-8")).get("aliases", {})


def test_every_preset_artist_is_in_the_stage2b_prompt() -> None:
    src = _footage_v2_src()
    missing = []
    for g in _artist_presets()["genres"]:
        for a in g["artists"]:
            if f'"{a["key"]}"' not in src:
                missing.append(a["key"])
    assert not missing, f"artists in artist_presets.json but not in footage_v2.py prompt: {missing}"


def test_every_referenced_theme_is_defined_in_themes_logic() -> None:
    defined = _defined_themes()
    referenced = set()
    for g in _artist_presets()["genres"]:
        for a in g["artists"]:
            referenced |= set(a.get("themes") or [])
    missing = sorted(referenced - defined)
    assert not missing, f"themes referenced by artists but missing from THEMES LOGIC: {missing}"


def test_alias_values_exist_in_taxonomy() -> None:
    tax = _taxonomy_tags()
    bad = {k: v for k, v in _aliases().items() if v not in tax}
    assert not bad, f"tag_aliases.json values not present in taxonomy (useless/buggy): {bad}"


def test_alias_keys_are_not_already_taxonomy_tags() -> None:
    # Aliasing a canonical tag remaps clips off it and can BREAK matches.
    tax = _taxonomy_tags()
    clashes = [k for k in _aliases() if " ".join(k.strip().lower().split()) in tax]
    assert not clashes, f"tag_aliases.json keys that are already taxonomy tags: {clashes}"


def test_global_ban_tags_parse_non_empty() -> None:
    from mlcore.footage_picker import _load_global_ban_tags

    tags = _load_global_ban_tags()
    assert tags, "global ban tags parsed empty — check 3rd_footage_selection_prompt/prompt.md"
