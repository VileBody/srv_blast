from __future__ import annotations

from config.styles.theme_groups import (
    THEME_GROUPS,
    THEME_LABELS_RU,
    get_artist_theme_choices,
    get_theme_label,
)


def test_every_theme_group_key_has_a_label() -> None:
    missing = [t for t in THEME_GROUPS if t not in THEME_LABELS_RU]
    assert not missing, f"themes without a RU label: {missing}"


def test_get_theme_label_fallback() -> None:
    assert get_theme_label("aggression_minor") == "Агрессия"
    # unmapped key -> prettified fallback, never crashes
    assert get_theme_label("brand_new_theme_minor") == "Brand new theme"


def test_artist_theme_choices_first_is_primary_and_deduped() -> None:
    choices = get_artist_theme_choices("rock_grunge")
    assert choices, "rock_grunge should expose themes"
    keys = [c[0] for c in choices]
    assert len(keys) == len(set(keys)), "themes must be deduped"
    primaries = [c for c in choices if c[2]]
    assert len(primaries) == 1 and primaries[0] is choices[0], "exactly the first is primary"
    # labels are non-empty strings
    assert all(isinstance(c[1], str) and c[1] for c in choices)


def test_unknown_artist_yields_no_choices() -> None:
    assert get_artist_theme_choices("not_a_real_artist") == []
