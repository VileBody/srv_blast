"""Standalone PHOTO bucket catalog — separate plane from the video visual catalog.

Photos barely overlap footage, so mixing them in one catalog only causes drift.
This module owns the photo vibes end to end: a bucket is a FACET CONTRACT, the
same way the video buckets' theme_tags were authored — not a flat bag of tags.

Facets: subject, setting, action, visual_style, time, people, energy, color.
A bucket declares the values that DEFINE it (require, AND across facet groups, OR
inside a group) and the values that BREAK it (exclude). `people` and `color` are
first-class facets read off the Qwen tags (meta_people_type / meta_color_tone);
the rest are matched against the theme tags. Substring match, so "night" catches
"night city" — keep exclude terms specific enough not to clip a required tag.

The goal is one theme leading per bucket: strictly a forest, not a forest with a
building; a light warm couple, not two robed figures by a fire. When a tag is
ambiguous we exclude it — thin-but-pure beats wide-but-mixed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


def _n(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().replace("_", " ").split())


def _t(*vals: str) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(_n(v) for v in vals if _n(v)))


def _matches(tags: Sequence[str], term: str) -> bool:
    needle = _n(term)
    return any(needle == tag or needle in tag for tag in tags)


# ---------------------------------------------------------------------------- #
# Facet vocabulary (canonical tag values per facet, from the 2026-07-17 snapshot)
# ---------------------------------------------------------------------------- #
# subject / scene
PERSON = _t("alone", "solo", "lonely figure", "single person", "person", "man", "woman", "guy", "girl", "portrait")
COUPLE = _t("couple", "couple walking", "couple hug", "couple holding hands", "couple dancing", "two people")
CROWD = _t("crowd", "audience", "urban crowd", "raised hands")
CAR = _t("car", "cars", "sports car", "luxury car", "vehicle", "car interior", "car window")
CITY = _t("city", "cityscape", "urban", "urban landscape", "urban skyline", "skyline", "skyscraper", "skyscrapers", "high-rise buildings", "street", "city street", "apartment building", "downtown")
FOREST = _t("forest", "trees", "woods", "foggy forest", "dark forest", "dark trees", "misty forest", "bare trees")
MOUNTAIN = _t("mountain", "mountains", "cliff", "cliffs", "mountain view", "mountain road")
COAST = _t("ocean", "sea", "coast", "coastal", "beach", "shore", "waves", "rocky shore")
CALMWATER = _t("lake", "river", "reflection", "calm water", "pond", "waterfall", "waterfront")
SKY = _t("night sky", "stars", "starry sky", "moon", "milky way", "moonlight")
CLOUDS = _t("clouds", "cloudy sky", "dramatic sky", "clear sky", "blue sky", "overcast sky")
FIELD = _t("field", "meadow", "grass", "green field", "open field", "flowers", "wildflowers", "flower field", "green landscape", "hills", "green hills")
SILHOUETTE = _t("silhouette", "human silhouette", "glowing silhouette", "neon silhouette", "hooded figure", "silhouettes")
INTERIOR = _t("interior", "indoor", "room", "bedroom", "hallway", "empty room", "dark interior", "dark room", "indoor setting")
DECAY = _t("abandoned", "abandoned building", "ruins", "derelict", "destruction", "dilapidated", "rubble", "wreckage")
PORTRAIT = _t("portrait", "face", "close up", "close-up", "headshot", "selfie")
GLITCH = _t("glitch", "distortion", "datamosh", "noise", "distorted", "digital art", "digital distortion")

# visual_style
NEON = _t("neon", "neon lights", "neon glow", "glow", "glowing", "led", "red lights", "blue lighting", "purple lighting", "city lights", "neon city")
FASHION = _t("fashion", "streetwear", "jewelry", "model", "outfit", "stylish", "style")

# time
NIGHT = _t("night", "nighttime", "night city", "midnight", "night sky")
GOLDEN = _t("golden hour", "sunset", "sunrise", "sunlight", "evening", "beach sunset", "warm lighting", "soft light")
DAY = _t("daytime", "daylight", "sunny day", "clear sky", "blue sky")

# weather
RAIN = _t("rain", "rainy", "rainy night", "wet road", "raindrops", "wet street", "puddle", "stormy weather", "storm")
FOG = _t("fog", "mist", "foggy", "misty", "haze", "misty atmosphere", "foggy forest")
SNOW = _t("snow", "winter", "ice", "frost", "blizzard", "winter landscape", "snowy night", "snowfall", "snowy road")

# energy / atmosphere
DARKMOOD = _t("dark atmosphere", "dim lighting", "moody", "dramatic lighting", "low light", "shadows", "dark landscape", "dark sky")
ACTION = _t("skate", "skateboard", "running", "dance", "dancing", "sport", "action", "movement", "jump", "cycling", "nightlife", "motion blur")


@dataclass(frozen=True)
class PhotoBucket:
    bucket_id: str
    label: str                      # RU display label
    lead: str                       # one-line leading semantic unit
    facets: Mapping[str, str]       # documentation: facet -> value (for review)
    require_groups: Tuple[Tuple[str, ...], ...]   # AND of groups; each group = OR
    exclude_terms: Tuple[str, ...] = ()
    colors: Tuple[str, ...] = ()    # allowed meta_color_tone (neutral folds to light)
    people: str = "any"            # any | none | present | girls | guys | couple | crowd

    # picker/preview duck-compat
    @property
    def theme(self) -> str:
        return "photo"

    @property
    def tags_group(self) -> str:
        return self.bucket_id.split(":", 1)[-1]

    @property
    def mood(self) -> str:
        return ""

    @property
    def priority_tags(self) -> List[str]:
        return list(dict.fromkeys(x for g in self.require_groups for x in g))

    @property
    def exclude_tags(self) -> List[str]:
        return list(self.exclude_terms)

    @property
    def color(self) -> List[str]:
        return list(self.colors)

    @property
    def exclude(self) -> List[str]:
        return []


TEXT_TERMS = _t("text", "typography", "title", "subtitle", "caption", "watermark", "logo", "phone screen")


def evaluate(bucket: PhotoBucket, asset: Mapping[str, Any]) -> Tuple[bool, str]:
    """Facet gate for one still. Returns (eligible, reject_stage)."""
    tags = tuple(_n(x) for x in (asset.get("meta_theme_tags") or asset.get("theme_tags") or []) if _n(x))
    color = _n(asset.get("meta_color_tone") or asset.get("color_tone"))
    people = _n(asset.get("meta_people_type") or asset.get("people_type"))

    if any(_matches(tags, x) for x in TEXT_TERMS):
        return False, "visible_text"
    if bucket.colors:
        c = "light" if color == "neutral" else color
        if c not in bucket.colors:
            return False, "color"
    want = bucket.people
    if want == "none" and people not in ("none", "no people", ""):
        return False, "people"
    if want == "present" and people in ("none", "no people", ""):
        return False, "people"
    if want in ("girls", "guys", "couple", "crowd") and people != want:
        return False, "people"
    for term in bucket.exclude_terms:
        if _matches(tags, term):
            return False, "facet_exclude"
    for group in bucket.require_groups:
        if not any(_matches(tags, x) for x in group):
            return False, "missing_facet"
    return True, "eligible"


# ---------------------------------------------------------------------------- #
# The catalog. Ordered by family. Each bucket authored facet-coherently.
# ---------------------------------------------------------------------------- #
def _b(bucket_id, label, lead, facets, require, colors=(), people="any", exclude=()):
    return PhotoBucket(
        bucket_id=bucket_id, label=label, lead=lead, facets=facets,
        require_groups=tuple(require), exclude_terms=_t(*exclude),
        colors=tuple(colors), people=people,
    )


PHOTO_BUCKETS: List[PhotoBucket] = [
    # ---- NATURE / LANDSCAPE (people=none) ----
    _b("photo:nature_golden_warm", "Природа / золотой час", "тёплый природный пейзаж на закате",
       {"subject": "nature", "time": "golden", "people": "none", "color": "warm", "energy": "serene"},
       [COAST + FIELD + FOREST + MOUNTAIN + _t("landscape", "nature"), GOLDEN],
       colors=("warm", "light"), people="none",
       exclude=SILHOUETTE + DECAY + CITY + NIGHT + _t("dark atmosphere", "dark forest")),
    _b("photo:forest_fog_dark", "Тёмный лес / туман", "строго туманный тёмный лес",
       {"subject": "forest", "weather": "fog", "people": "none", "color": "dark"},
       [FOREST, FOG], colors=("dark", "cold"), people="none",
       exclude=DECAY + MOUNTAIN + CITY + _t("architecture", "bedroom", "dark interior")),
    _b("photo:rain_nature_dark", "Дождливая природа", "дождь/непогода в природном пейзаже",
       {"subject": "nature", "weather": "rain", "people": "none", "color": "dark"},
       [FOREST + MOUNTAIN + COAST + FIELD + _t("nature", "landscape"), RAIN],
       colors=("dark", "cold"), people="none",
       exclude=CITY + DECAY + _t("architecture")),
    _b("photo:mountain_dark", "Тёмные горы", "суровые тёмные горы",
       {"subject": "mountain", "people": "none", "color": "dark"},
       [MOUNTAIN], colors=("dark", "cold"), people="none",
       exclude=DECAY + COAST + _t("castle", "architecture", "red lights", "glowing")),
    _b("photo:mountain_light", "Светлые горы", "светлые горы днём",
       {"subject": "mountain", "people": "none", "color": "light"},
       [MOUNTAIN], colors=("light", "warm"), people="none",
       exclude=DECAY + _t("castle", "architecture", "ocean", "sea", "coast")),
    _b("photo:ocean_storm_dark", "Тёмный океан / шторм", "штормовой тёмный океан",
       {"subject": "ocean", "weather": "storm", "people": "none", "color": "dark"},
       [COAST, RAIN + FOG + NIGHT + _t("dark clouds", "rough waves", "dark sky")],
       colors=("dark", "cold"), people="none",
       exclude=MOUNTAIN + CITY + _t("architecture", "wet road", "night drive")),
    _b("photo:winter_dark", "Тёмная зима", "тёмная зимняя природа",
       {"subject": "nature", "weather": "snow", "people": "none", "color": "dark"},
       [SNOW], colors=("dark", "cold"), people="none",
       exclude=CITY + _t("ocean", "coast", "red lights", "glowing")),
    _b("photo:night_sky_stars", "Ночное небо / звёзды", "звёздное ночное небо",
       {"subject": "sky", "time": "night", "people": "none", "color": "dark"},
       [SKY], colors=("dark", "cold"), people="none",
       exclude=INTERIOR + DECAY + CAR + _t("castle", "architecture", "road")),
    _b("photo:calm_water", "Спокойная вода / отражения", "зеркальная вода, озеро, отражение",
       {"subject": "water", "energy": "calm", "people": "none"},
       [CALMWATER], people="none",
       exclude=CITY + DECAY + NEON + _t("storm", "night city", "wet road")),
    _b("photo:warm_field_flowers", "Поле / цветы", "тёплое поле, луг, цветы",
       {"subject": "field", "people": "none", "color": "warm"},
       [_t("flowers", "wildflowers", "flower field", "meadow", "green field", "grass", "field")],
       colors=("warm", "light", "neutral"), people="none",
       exclude=CITY + NIGHT + DECAY + _t("dark atmosphere")),

    # ---- URBAN (people=none) ----
    _b("photo:urban_night_empty", "Пустой ночной город", "пустой ночной город, скайлайн",
       {"subject": "city", "time": "night", "people": "none", "color": "dark"},
       [CITY, NIGHT], colors=("dark", "cold"), people="none",
       exclude=DECAY + COAST + SILHOUETTE + _t("palm trees", "trees", "airplane", "neon")),
    _b("photo:urban_rain_night", "Дождливый ночной город", "мокрый ночной город, дождь",
       {"subject": "city", "time": "night", "weather": "rain", "people": "none", "color": "dark"},
       [CITY, RAIN], colors=("dark",), people="none",
       exclude=_t("silhouette", "trees", "palm trees", "forest")),
    _b("photo:urban_decay_dark", "Заброшка / распад", "заброшенные здания, руины",
       {"subject": "decay", "people": "none", "color": "dark"},
       [DECAY], colors=("dark", "cold", "neutral"), people="none",
       exclude=NEON + _t("forest", "ocean", "mountain", "beach")),
    _b("photo:neon_night_city", "Неон / киберпанк-ночь", "неоновый ночной город",
       {"visual_style": "neon", "setting": "night city", "people": "none", "color": "dark"},
       [NEON, CITY + NIGHT], colors=("dark", "cold"), people="none",
       exclude=PORTRAIT + SILHOUETTE + COUPLE + DECAY),

    # ---- DIGITAL ----
    _b("photo:digital_silhouette_cold", "Digital-силуэт / холодный", "неоновый цифровой силуэт человека",
       {"subject": "silhouette", "visual_style": "digital/neon", "color": "cold"},
       [SILHOUETTE, NEON + _t("digital", "abstract")], colors=("dark", "cold"),
       exclude=PORTRAIT + COUPLE + INTERIOR + COAST + CAR + _t("dance floor", "night club", "field", "water", "stormy weather")),
    _b("photo:digital_glitch", "Digital / glitch", "глитч, цифровое искажение",
       {"visual_style": "glitch", "color": "dark"},
       [GLITCH], colors=("dark", "cold"), exclude=()),

    # ---- LONE FIGURE / PEOPLE (none-tagged silhouettes) ----
    _b("photo:lone_figure_scene", "Одинокий силуэт в кадре", "одинокая фигура/силуэт в пейзаже",
       {"subject": "lone figure", "energy": "moody", "people": "none"},
       [SILHOUETTE + _t("lonely figure", "lonely", "alone")], people="none",
       exclude=DECAY + NEON + COUPLE + _t("night club", "dance floor")),

    # ---- SOLO PERSON ----
    _b("photo:solitary_person_dark", "Человек / одиночество", "один человек в тёмном настроенческом кадре",
       {"subject": "person", "energy": "solitude", "time": "night", "color": "dark"},
       [_t("alone", "solitude", "lonely", "single person", "man", "woman", "guy", "girl", "solo", "sitting alone")],
       colors=("dark", "cold"), people="present",
       exclude=COUPLE + COAST + CAR + _t("romance", "romantic", "intimate", "intimacy", "kiss", "hug", "jewelry", "night drive")),
    _b("photo:guy_solo_mood", "Парень / настроение", "одинокий парень, настроенческий кадр",
       {"subject": "guy", "energy": "moody", "people": "guys"},
       [PERSON + PORTRAIT + SILHOUETTE + _t("man", "guy", "male", "solo", "streetwear")],
       people="guys", exclude=COUPLE + CROWD),

    # ---- GIRL ----
    _b("photo:girl_portrait_light", "Портрет девушки / светлый", "портрет девушки в тёплом свете",
       {"subject": "girl", "visual_style": "portrait", "setting": "indoor", "color": "warm"},
       [PORTRAIT + _t("girl", "woman", "long hair"), INTERIOR],
       colors=("light", "warm"), people="girls",
       exclude=SILHOUETTE + _t("dark atmosphere", "dark interior", "dim lighting", "sport", "skate", "couple")),
    _b("photo:girl_portrait_dark", "Портрет девушки / тёмный", "тёмный портрет девушки, лицо видно",
       {"subject": "girl", "visual_style": "portrait", "setting": "indoor", "color": "dark"},
       [PORTRAIT + _t("girl", "woman", "long hair"), INTERIOR],
       colors=("dark", "cold"), people="girls",
       exclude=SILHOUETTE + COUPLE + _t("intimate", "intimacy", "tender", "sport", "skate")),
    _b("photo:girl_silhouette_mood", "Девушка / силуэт", "силуэт/бэклит девушки, настроение",
       {"subject": "girl", "visual_style": "silhouette", "color": "dark"},
       [_t("girl", "woman", "long hair", "solo"), SILHOUETTE + _t("backlit", "dim lighting", "low light")],
       colors=("dark", "cold"), people="girls",
       exclude=COUPLE + CROWD),
    _b("photo:girl_golden_outdoor", "Девушка / золотой час", "девушка на улице в тёплом свете",
       {"subject": "girl", "setting": "outdoor", "time": "golden", "color": "warm"},
       [_t("girl", "woman", "long hair", "portrait"), GOLDEN + _t("outdoor")],
       colors=("warm", "light"), people="girls",
       exclude=_t("indoor", "indoor setting", "bedroom", "couple")),

    # ---- COUPLE ----
    _b("photo:couple_light_warm", "Пара / светлая", "светлая влюблённая пара, тёплый свет",
       {"subject": "couple", "energy": "romantic", "time": "day/golden", "color": "warm"},
       [COUPLE + _t("romance", "romantic", "intimacy", "love")],
       colors=("light", "warm"), people="couple",
       exclude=_t("night", "silhouette", "dark forest", "dark sky", "dark atmosphere", "fire", "castle",
                  "black and white", "glowing", "dim lighting", "rain", "wet road", "city",
                  "alone", "solitude", "lonely")),
    _b("photo:couple_moody_dark", "Пара / тёмная", "пара в тёмном/ночном настроении",
       {"subject": "couple", "energy": "moody", "time": "night", "color": "dark"},
       [COUPLE + _t("romance", "romantic", "intimate", "intimacy")],
       colors=("dark", "cold", "neutral"), people="couple", exclude=()),
    _b("photo:coastal_couple_warm", "Романтика у воды", "пара у воды на закате",
       {"subject": "couple", "setting": "coast", "time": "golden", "color": "warm"},
       [COAST, COUPLE + _t("romance", "romantic", "walking", "running")],
       colors=("light", "warm"), people="couple",
       exclude=_t("dark atmosphere", "wet road", "night")),

    # ---- PEOPLE IN SCENE ----
    _b("photo:street_people_night", "Люди на ночной улице", "люди в ночном городе, стрит",
       {"subject": "people", "setting": "night street", "time": "night", "color": "dark"},
       [CITY + _t("street scene", "sidewalk", "city life"), NIGHT + DARKMOOD],
       colors=("dark", "cold", "neutral"), people="present",
       exclude=CAR + NEON + DECAY + COUPLE),
    _b("photo:performance_crowd", "Сцена / толпа", "концерт, клуб, толпа",
       {"subject": "crowd", "setting": "stage/club", "energy": "energetic", "color": "dark"},
       [_t("concert", "performance", "stage", "crowd", "audience", "club", "night club", "party", "rave", "dance floor")],
       colors=("dark", "cold", "neutral"), people="present",
       exclude=DECAY + CAR + PORTRAIT + _t("private jet", "jewelry", "street scene", "city life")),
    _b("photo:active_life_night", "Активная жизнь", "движение: скейт/танец/спорт ночью",
       {"subject": "person", "action": "sport/dance", "energy": "energetic", "color": "dark"},
       [_t("skate", "skateboard", "running", "dance", "dancing", "sport", "action", "jump", "cycling", "nightlife")],
       colors=("dark", "cold"), people="present",
       exclude=CAR + DECAY + _t("crowd", "audience")),
    _b("photo:fashion_street", "Фэшн / стритвир", "стиль, украшения, аутфит",
       {"subject": "person", "visual_style": "fashion", "people": "present"},
       [FASHION], people="present", exclude=CROWD),

    # ---- VEHICLES ----
    _b("photo:car_night", "Машины / ночная езда", "авто, ночная езда, интерьер авто",
       {"subject": "car", "time": "night", "action": "drive"},
       [CAR + _t("night drive", "driving", "highway", "road trip"), NIGHT + NEON + _t("wet road", "city lights")],
       exclude=_t("race", "drift", "burnout")),
    _b("photo:car_race", "Дрифт / гонки", "дрифт, гонки, скорость",
       {"subject": "car", "action": "race", "energy": "energetic"},
       [CAR, _t("drift", "racing", "race", "burnout", "speed", "night drift")], exclude=()),
]


def load_photo_catalog() -> List[PhotoBucket]:
    ids = [b.bucket_id for b in PHOTO_BUCKETS]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate photo bucket_id")
    return list(PHOTO_BUCKETS)


CATALOG_VERSION = "photo-facet-v1-2026-07-17"
