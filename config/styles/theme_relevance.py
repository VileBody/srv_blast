"""Track-characteristic THEMES and the footage buckets that fit each one.

The ranker's principle (restored 2026-07-02): a track is first CLASSIFIED into
emotional/topical THEMES (heartbreak, aggression, hustle, party, serene…) — a
small, grounded task the LLM does well with real instructions — and each matched
theme expands to its relevant visual BUCKETS. This is far better than asking the
LLM to blind-rank 48 flat vibes.

THEME_BUCKETS is a MANY-TO-MANY relevance map: one visual bucket can fit several
track types (a "lonely dark" visual suits heartbreak, depression AND betrayal).
It was reconstructed from the pre-consolidation theme→group grouping (before the
buckets were deduped for picking), with clones mapped onto their surviving
canonical bucket. So picking stays flat, while ranking keeps the rich
track→visual associations.

THEME_DESCRIPTIONS_RU are the ranker's INSTRUCTIONS — what kind of track each
theme is for. Refine freely; they only affect the shortlist offered to the user,
never the picking itself.
"""
from __future__ import annotations

from typing import Dict, List

# theme (track characteristic) -> relevant canonical bucket_ids, in priority order
THEME_BUCKETS: Dict[str, List[str]] = {
    "romance_major": ["romance_major:nature_sunset", "romance_major:couple_moments", "romance_major:warm_vibes"],
    "romance_minor": ["jealousy_minor:eerie_nature", "romance_minor:intimacy_fading"],
    "epic_love_major": ["romance_major:nature_sunset", "epic_love_major:dynamic_couple"],
    "epic_love_minor": ["epic_love_minor:stormy_elements", "epic_love_minor:dramatic_landscape", "epic_love_minor:tragic_couple"],
    "heartbreak_minor": ["heartbreak_minor:girl_portrait_sad", "heartbreak_minor:winter_isolation", "jealousy_minor:eerie_nature", "loneliness_isolation_minor:urban_solitude", "heartbreak_minor:silhouette_vibe"],
    "betrayal_minor": ["betrayal_minor:girl_urban_night", "loneliness_isolation_minor:urban_solitude", "betrayal_minor:dark_elements"],
    "jealousy_minor": ["betrayal_minor:girl_urban_night", "jealousy_minor:eerie_nature", "cyber_alienation_minor:digital_glitch"],
    "depression_minor": ["depression_minor:empty_spaces", "depression_minor:mental_fog", "motivation_minor:night_grind"],
    "self_destruction_minor": ["self_destruction_minor:nightlife_decay", "cyber_alienation_minor:digital_glitch", "loneliness_isolation_minor:urban_solitude"],
    "aggression_minor": ["aggression_minor:chaos_elements", "aggression_minor:urban_grit", "aggression_minor:night_intensity"],
    "motivation_major": ["motivation_major:urban_triumph", "motivation_major:action_movement", "romance_major:nature_sunset"],
    "motivation_minor": ["motivation_minor:night_grind", "motivation_minor:tough_environment", "loneliness_isolation_minor:urban_solitude"],
    "hustle_minor": ["hustle_minor:urban_wealth", "hustle_minor:luxury_lifestyle"],
    "sex_major": ["sex_major:soft_intimacy", "sex_major:warm_aesthetics"],
    "sex_minor": ["sex_minor:neon_passion", "sex_minor:intimate_details"],
    "nostalgia_city_minor": ["nostalgia_city_minor:retro_city", "nostalgia_city_minor:lofi_textures"],
    "adrenaline_flex_major": ["adrenaline_flex_major:car_action", "adrenaline_flex_major:night_streets", "youth_rebellion_major:street_culture"],
    "escapism_dreams_minor": ["escapism_dreams_minor:cosmic_journey", "escapism_dreams_minor:surreal_magic", "jealousy_minor:eerie_nature"],
    "loneliness_isolation_minor": ["jealousy_minor:eerie_nature", "loneliness_isolation_minor:urban_solitude"],
    "youth_rebellion_major": ["youth_rebellion_major:street_culture", "youth_rebellion_major:friend_hangouts", "romance_major:nature_sunset"],
    "mysticism_fate_minor": ["mysticism_fate_minor:gothic_architecture", "jealousy_minor:eerie_nature"],
    "cyber_alienation_minor": ["cyber_alienation_minor:digital_glitch", "cyber_alienation_minor:cyberpunk_city", "cyber_alienation_minor:surveillance_isolation"],
    "serene_landscape_major": ["serene_landscape_major:open_landscapes", "serene_landscape_major:water_coast", "serene_landscape_major:bright_sky"],
    "nightlife_electro_minor": ["nightlife_electro_minor:nightlife_party"],
    "urban_blocks_minor": ["urban_blocks_minor:residential_blocks"],
}


# What kind of track each theme is for — the ranker's instructions (RU, draft).
THEME_DESCRIPTIONS_RU: Dict[str, str] = {
    "romance_major": "тёплая счастливая любовь, нежность, признания, лёгкие отношения",
    "romance_minor": "грустная меланхоличная любовь, тоска в отношениях, надвигающееся расставание",
    "epic_love_major": "большая светлая любовь, страсть, эпичные всепоглощающие чувства",
    "epic_love_minor": "драматичная любовь, буря чувств, трагичная страсть, надрыв",
    "heartbreak_minor": "расставание, разбитое сердце, боль потери, тоска по бывшему",
    "betrayal_minor": "предательство, обман, измена, злость и обида на близкого",
    "jealousy_minor": "ревность, подозрения, навязчивые мысли, тревога, собственничество",
    "depression_minor": "депрессия, апатия, пустота, безысходность, ничего не хочется",
    "self_destruction_minor": "саморазрушение, срыв, зависимости, тёмная ночная жизнь, край",
    "aggression_minor": "агрессия, злость, конфликт, дисс, ярость, вызов",
    "motivation_major": "мотивация, амбиции, успех, подъём, драйв к цели (светлый настрой)",
    "motivation_minor": "мотивация через борьбу, ночной грайнд, преодоление, путь наверх (тёмный)",
    "hustle_minor": "деньги, флекс, роскошь, статус, уличный успех, богатство",
    "sex_major": "чувственность, тепло, близость, лёгкая страсть (светлая)",
    "sex_minor": "чувственность, тёмная страсть, интим, ночь, вожделение (тёмная)",
    "nostalgia_city_minor": "ностальгия, воспоминания, ретро-город, тоска по прошлому",
    "adrenaline_flex_major": "адреналин, скорость, тачки, гонки, ночной драйв, риск",
    "escapism_dreams_minor": "эскапизм, сны, космос, уход от реальности, сюрреализм",
    "loneliness_isolation_minor": "одиночество, изоляция, отчуждённость, никого рядом",
    "youth_rebellion_major": "молодёжный бунт, свобода, тусовки с друзьями, улица (светлый)",
    "mysticism_fate_minor": "мистика, судьба, готика, потустороннее, рок",
    "cyber_alienation_minor": "киберотчуждение, цифровой мир, неон, технологии, глитч, антиутопия",
    "serene_landscape_major": "светлый спокойный трек, природа, пейзажи, умиротворение, тревел, чил",
    "nightlife_electro_minor": "тусовка, электроника, клуб, рейв, ночная жизнь, танцы",
    "urban_blocks_minor": "мрачный урбан, спальные районы, панельки, русская хандра, окраины",
}


def all_theme_ids() -> List[str]:
    return list(THEME_BUCKETS.keys())


def theme_mood(theme: str) -> str:
    t = str(theme or "")
    if t.endswith("_major"):
        return "major"
    if t.endswith("_minor"):
        return "minor"
    return ""


def candidate_themes(mood: str = "") -> List[str]:
    """Themes to offer for classification, hard-filtered by mood when known."""
    m = " ".join(str(mood or "").strip().lower().split())
    if m not in {"major", "minor"}:
        return all_theme_ids()
    kept = [t for t in THEME_BUCKETS if theme_mood(t) == m]
    return kept or all_theme_ids()


def buckets_for_themes(theme_ids: List[str], *, valid_ids: set, mood: str = "") -> List[str]:
    """Expand ranked theme_ids -> ordered, deduped bucket_ids that exist in the
    live catalog (`valid_ids`), optionally mood-filtered. Buckets from earlier
    (better-fitting) themes come first."""
    m = " ".join(str(mood or "").strip().lower().split())
    out: List[str] = []
    for t in theme_ids:
        for bid in THEME_BUCKETS.get(t, []):
            if bid not in valid_ids or bid in out:
                continue
            if m in {"major", "minor"} and theme_mood(bid.split(":", 1)[0]) != m:
                continue
            out.append(bid)
    return out
