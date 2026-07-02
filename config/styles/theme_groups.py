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
    "romance_minor": ["intimacy_fading"],
    "epic_love_major": ["dynamic_couple"],
    "epic_love_minor": ["stormy_elements", "dramatic_landscape", "tragic_couple"],
    "heartbreak_minor": ["girl_portrait_sad", "winter_isolation", "silhouette_vibe"],
    "betrayal_minor": ["girl_urban_night", "dark_elements"],
    "jealousy_minor": ["eerie_nature"],
    "depression_minor": ["empty_spaces", "mental_fog"],
    "self_destruction_minor": ["nightlife_decay"],
    "aggression_minor": ["chaos_elements", "urban_grit", "night_intensity"],
    "motivation_major": ["urban_triumph", "action_movement"],
    "motivation_minor": ["night_grind", "tough_environment"],
    "hustle_minor": ["urban_wealth", "luxury_lifestyle"],
    "sex_major": ["soft_intimacy", "warm_aesthetics"],
    "sex_minor": ["neon_passion", "intimate_details"],
    "nostalgia_city_minor": ["retro_city", "lofi_textures"],
    "adrenaline_flex_major": ["car_action", "night_streets"],
    "escapism_dreams_minor": ["cosmic_journey", "surreal_magic"],
    "loneliness_isolation_minor": ["urban_solitude"],
    "youth_rebellion_major": ["street_culture", "friend_hangouts"],
    "mysticism_fate_minor": ["gothic_architecture"],
    "cyber_alienation_minor": ["digital_glitch", "cyberpunk_city", "surveillance_isolation"],
    "serene_landscape_major": ["open_landscapes", "water_coast", "bright_sky"],
    "nightlife_electro_minor": ["nightlife_party"],
    "urban_blocks_minor": ["residential_blocks"],
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


# Human-readable RU labels for the theme keys, shown as buttons in the bot when
# the user picks a theme explicitly. Keep in sync with THEME_GROUPS keys
# (CI gate: tests/test_footage_taxonomy_gates covers theme<->prompt; the bot
# parity test covers labels coverage).
THEME_LABELS_RU: Dict[str, str] = {
    "romance_major": "Романтика (тёплая)",
    "romance_minor": "Романтика (грустная)",
    "epic_love_major": "Большая любовь (светлая)",
    "epic_love_minor": "Большая любовь (драма)",
    "heartbreak_minor": "Разбитое сердце",
    "betrayal_minor": "Предательство",
    "jealousy_minor": "Ревность",
    "depression_minor": "Депрессия",
    "self_destruction_minor": "Саморазрушение",
    "aggression_minor": "Агрессия",
    "motivation_major": "Мотивация (светлая)",
    "motivation_minor": "Мотивация (тёмная)",
    "hustle_minor": "Хастл / деньги",
    "sex_major": "Чувственность (тёплая)",
    "sex_minor": "Чувственность (тёмная)",
    "nostalgia_city_minor": "Ностальгия / город",
    "adrenaline_flex_major": "Адреналин / тачки",
    "escapism_dreams_minor": "Эскапизм / сны",
    "loneliness_isolation_minor": "Одиночество",
    "youth_rebellion_major": "Молодёжный бунт",
    "mysticism_fate_minor": "Мистика / судьба",
    "cyber_alienation_minor": "Киберотчуждение",
    "serene_landscape_major": "Пейзаж / природа",
    "nightlife_electro_minor": "Тусовка / электро",
    "urban_blocks_minor": "Панельки / мрачный урбан",
}


# RU labels for tags_group (subtheme) keys — the user-facing name of a footage
# bucket's VISUAL. Draft; refine freely. Fallback = prettified key.
SUBTHEME_LABELS_RU: Dict[str, str] = {
    "action_movement": "Экшн / движение",
    "blurry_reality": "Размытая реальность",
    "bright_starts": "Светлое начало",
    "car_action": "Тачки / дрифт",
    "chaos_elements": "Хаос / огонь",
    "cinematic_nature": "Кино-природа",
    "cosmic_journey": "Космос",
    "couple_moments": "Пара / моменты",
    "cyberpunk_city": "Киберпанк-город",
    "dark_dreamscape": "Тёмный сон",
    "dark_elements": "Тёмные элементы",
    "digital_glitch": "Диджитал-глитч",
    "dramatic_landscape": "Драматичный пейзаж",
    "dynamic_couple": "Пара в движении",
    "eerie_nature": "Жуткая природа",
    "empty_spaces": "Пустые пространства",
    "foggy_desolation": "Туманная пустошь",
    "friend_hangouts": "Тусовка с друзьями",
    "girl_portrait_sad": "Грустный портрет девушки",
    "girl_unease": "Тревога / девушка",
    "girl_urban_night": "Девушка / ночной город",
    "glitchy_mind": "Глитч-сознание",
    "gothic_architecture": "Готическая архитектура",
    "intimacy_fading": "Угасающая близость",
    "intimate_details": "Интимные детали",
    "lofi_textures": "Lo-fi текстуры",
    "lonely_nature": "Одинокая природа",
    "lonely_paths": "Одинокие тропы",
    "luxury_lifestyle": "Лакшери-лайфстайл",
    "mental_fog": "Туман в голове",
    "messy_aftermath": "После хаоса",
    "nature_sunset": "Природа / закат",
    "neon_passion": "Неоновая страсть",
    "night_grind": "Ночной грайнд",
    "night_intensity": "Ночной драйв",
    "night_streets": "Ночные улицы",
    "nightlife_decay": "Ночная жизнь / упадок",
    "retro_city": "Ретро-город",
    "silhouette_vibe": "Силуэты",
    "soft_intimacy": "Нежная близость",
    "solitary_focus": "Одиночество / фокус",
    "stormy_elements": "Буря / стихия",
    "street_action": "Уличный экшн",
    "street_culture": "Уличная культура",
    "sunset_vibes": "Закатный вайб",
    "surreal_magic": "Сюрреал / магия",
    "surveillance_isolation": "Слежка / изоляция",
    "tough_environment": "Суровая среда",
    "tragic_couple": "Трагичная пара",
    "urban_grit": "Городская жесть",
    "urban_isolation": "Городская изоляция",
    "urban_solitude": "Городское одиночество",
    "urban_triumph": "Городской триумф",
    "urban_wealth": "Городское богатство",
    "vintage_tech": "Винтажная техника",
    "warm_aesthetics": "Тёплая эстетика",
    "warm_vibes": "Тёплый вайб",
    "winter_isolation": "Зимняя изоляция",
    "open_landscapes": "Открытые пейзажи",
    "water_coast": "Вода / берег",
    "bright_sky": "Яркое небо",
    "nightlife_party": "Тусовка / клуб",
    "residential_blocks": "Панельки / кварталы",
}


def get_subtheme_label(group: str) -> str:
    """RU label for a tags_group (subtheme) key; prettified fallback if unmapped."""
    g = str(group or "").strip()
    if g in SUBTHEME_LABELS_RU:
        return SUBTHEME_LABELS_RU[g]
    return g.replace("_", " ").strip().capitalize()


def get_theme_label(theme: str) -> str:
    """RU label for a theme key; falls back to a prettified key if unmapped."""
    t = str(theme or "").strip()
    if t in THEME_LABELS_RU:
        return THEME_LABELS_RU[t]
    return t.replace("_minor", "").replace("_major", "").replace("_", " ").strip().capitalize()


def get_artist_theme_choices(artist_id: str) -> List[Tuple[str, str, bool]]:
    """Ordered [(theme_key, label, is_primary)] for the bot's theme picker.

    Themes come from the artist profile order (deduped). The first theme is the
    primary one — the one shown in the artist's example video — so the bot marks
    it "как в примере".
    """
    out: List[Tuple[str, str, bool]] = []
    seen: set[str] = set()
    for theme in get_artist_themes(artist_id):
        t = str(theme or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append((t, get_theme_label(t), not out))
    return out
