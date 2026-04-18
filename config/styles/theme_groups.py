"""Ordered (theme, tags_group) mapping mirroring footage_v2.py THEMES LOGIC.

This module is the single machine-readable source of truth for theme -> groups
rotation used by the Stage 2B cursor system. Kept in sync manually with
footage_v2.py SYSTEM_PART. If you add/remove/reorder groups there, mirror here.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .artist_presets_loader import get_artist_themes


THEME_GROUPS: Dict[str, List[str]] = {
    "romance_major": ["nature_sunset", "couple_moments", "warm_vibes"],
    "romance_minor": ["lonely_nature", "intimacy_fading"],
    "epic_love_major": ["cinematic_nature", "dynamic_couple"],
    "epic_love_minor": ["stormy_elements", "dramatic_landscape", "tragic_couple"],
    "heartbreak_minor": [
        "girl_portrait_sad",
        "winter_isolation",
        "foggy_desolation",
        "lonely_paths",
        "silhouette_vibe",
    ],
    "betrayal_minor": ["girl_urban_night", "lonely_paths", "dark_elements"],
    "jealousy_minor": ["girl_unease", "eerie_nature", "glitchy_mind"],
    "depression_minor": ["empty_spaces", "mental_fog", "urban_isolation"],
    "self_destruction_minor": ["nightlife_decay", "blurry_reality", "messy_aftermath"],
    "aggression_minor": ["chaos_elements", "urban_grit", "night_intensity"],
    "motivation_major": ["urban_triumph", "action_movement", "bright_starts"],
    "motivation_minor": ["night_grind", "tough_environment", "solitary_focus"],
    "hustle_minor": ["urban_wealth", "luxury_lifestyle"],
    "sex_major": ["soft_intimacy", "warm_aesthetics"],
    "sex_minor": ["neon_passion", "intimate_details"],
    "nostalgia_city_minor": ["vintage_tech", "retro_city", "lofi_textures"],
    "adrenaline_flex_major": ["car_action", "night_streets", "street_action"],
    "escapism_dreams_minor": ["cosmic_journey", "surreal_magic", "dark_dreamscape"],
    "loneliness_isolation_minor": ["eerie_nature", "urban_solitude"],
    "youth_rebellion_major": ["street_culture", "friend_hangouts", "sunset_vibes"],
    "mysticism_fate_minor": ["gothic_architecture", "eerie_nature"],
    "cyber_alienation_minor": ["digital_glitch", "cyberpunk_city", "surveillance_isolation"],
}


def get_theme_groups(theme: str) -> List[str]:
    """Return ordered tags_groups for a theme. Empty list if theme unknown."""
    t = str(theme or "").strip()
    return list(THEME_GROUPS.get(t) or [])


def get_artist_rotation_slots(artist_id: str) -> List[Tuple[str, str]]:
    """Build a flat ordered rotation list [(theme, group), ...] for an artist.

    Slots are produced by walking themes in their profile order, and for each
    theme walking its groups in the order defined in THEME_GROUPS.
    """
    slots: List[Tuple[str, str]] = []
    for theme in get_artist_themes(artist_id):
        for group in get_theme_groups(theme):
            slots.append((theme, group))
    return slots


def get_rotation_slot(artist_id: str, cursor: int) -> Optional[Tuple[str, str]]:
    """Return (theme, group) for this cursor position, wrapping around.

    None if the artist has no slots at all.
    """
    slots = get_artist_rotation_slots(artist_id)
    if not slots:
        return None
    idx = int(cursor) % len(slots)
    return slots[idx]


def get_rotation_length(artist_id: str) -> int:
    return len(get_artist_rotation_slots(artist_id))
