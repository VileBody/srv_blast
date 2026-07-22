"""Standalone PHOTO bucket catalog — separate plane from the video visual catalog.

Photos barely overlap footage, so mixing them in one catalog only causes drift.
This module owns the photo vibes end to end: a bucket is a FACET CONTRACT, the
same way the video buckets' theme_tags were authored — not a flat bag of tags.

Facets: subject, setting, action, visual_style, time, people, energy, color.
A bucket declares the values that DEFINE it (require, AND across facet groups, OR
inside a group) and the values that BREAK it (exclude). `people` and `color` are
first-class facets read off the Qwen tags (meta_people_type / meta_color_tone);
the rest are matched against theme tags on whole-token phrase boundaries, so
"night" catches "night city" while "rain" never catches "train".

The goal is one theme leading per bucket: strictly a forest, not a forest with a
building; a light warm couple, not two robed figures by a fire. When a tag is
ambiguous we exclude it — thin-but-pure beats wide-but-mixed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


def _n(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().replace("_", " ").split())


def _t(*vals: str) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(_n(v) for v in vals if _n(v)))


def _tokens(value: str) -> Tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", _n(value), flags=re.IGNORECASE))


def _match_quality(tags: Sequence[str], term: str) -> int:
    """Return 2 for an exact tag, 1 for a token-boundary phrase, else 0."""
    needle = _tokens(term)
    if not needle:
        return 0
    width = len(needle)
    for tag in tags:
        haystack = _tokens(tag)
        if haystack == needle:
            return 2
        if any(haystack[i:i + width] == needle for i in range(len(haystack) - width + 1)):
            return 1
    return 0


def _matches(tags: Sequence[str], term: str) -> bool:
    return _match_quality(tags, term) > 0


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
CALMWATER = _t("lake", "river", "calm water", "pond", "still water", "water reflection")
SKY = _t("night sky", "stars", "starry sky", "moon", "milky way", "moonlight")
CLOUDS = _t("clouds", "cloudy sky", "dramatic sky", "clear sky", "blue sky", "overcast sky")
FIELD = _t("field", "meadow", "grass", "green field", "open field", "flowers", "wildflowers", "flower field", "green landscape", "hills", "green hills")
SILHOUETTE = _t("silhouette", "human silhouette", "glowing silhouette", "neon silhouette", "hooded figure", "silhouettes")
INTERIOR = _t("interior", "indoor", "room", "bedroom", "hallway", "empty room", "dark interior", "dark room", "indoor setting")
DECAY = _t("abandoned", "abandoned building", "ruins", "derelict", "destruction", "dilapidated", "rubble", "wreckage")
PORTRAIT = _t("portrait", "face", "close up", "close-up", "headshot", "selfie")
GLITCH = _t("glitch", "distortion", "datamosh", "distorted", "digital distortion")

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
ACTION = _t("skate", "skating", "skateboard", "skateboarding", "running", "sport", "action", "jump", "jumping", "cycling", "bicycle", "bmx")
PERFORMANCE = _t("concert", "performance", "stage", "live music", "club", "night club", "party", "rave", "dance floor")
DRIVE = _t("night drive", "driving", "drive", "highway", "road trip", "wet road", "car interior", "car window")
OUTDOOR = _t("outdoor", "outdoors", "landscape", "forest", "mountain", "field", "street", "road", "cityscape", "coast", "beach")
SOLITUDE = _t("alone", "solo", "solitude", "lonely", "lonely figure", "single person", "sitting alone")

# reusable exclude groups (facet-breakers)
WATER_ANY = _t("water", "ocean", "sea", "coast", "coastal", "beach", "lake", "river", "waves", "waterfront", "beach sunset", "shore")
KNIGHT = _t("knight", "knights", "wizard", "mage", "medieval", "armor", "warrior", "castle", "fantasy", "sword")
BOATS = _t("boat", "boats", "ship", "ships", "sailboat", "yacht", "fishing boat")
DIGITAL_STYLE = _t("neon", "neon lights", "glow", "glowing", "led", "red lights", "blue lighting", "purple lighting", "digital art", "abstract", "glitch", "distortion")
ROMANCE_ANY = _t("romance", "romantic", "romantic moment", "kiss", "hug", "embrace", "intimacy", "intimate", "intimate moment", "love", "couple", "date", "tender")
PEOPLE_ANY = _t("man", "woman", "guy", "girl", "person", "people", "couple", "crowd", "portrait", "face", "man on sidewalk")


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


def representative_score(bucket: PhotoBucket, asset: Mapping[str, Any]) -> float:
    """Rank clean representatives without rewarding an indiscriminate tag bag."""
    tags = tuple(_n(x) for x in (asset.get("meta_theme_tags") or asset.get("theme_tags") or []) if _n(x))
    score = 0.0
    for group in bucket.require_groups:
        # One best signal per AND facet: many synonyms from one facet cannot
        # beat a clean asset that expresses every required facet exactly.
        score += 3.0 * max((_match_quality(tags, term) for term in group), default=0)
    color = _n(asset.get("meta_color_tone") or asset.get("color_tone"))
    if bucket.colors and ("light" if color == "neutral" else color) in bucket.colors:
        score += 0.5
    expected_detail = 2 * len(bucket.require_groups) + 3
    score -= 0.08 * max(0, len(tags) - expected_detail)
    return score


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
    # iter2: exclude water entirely — golden-hour LAND nature only.
    _b("photo:nature_golden_warm", "Природа / золотой час", "тёплый природный пейзаж на закате (без воды)",
       {"subject": "land nature", "time": "golden", "people": "none", "color": "warm", "energy": "serene"},
       [FOREST + _t("hills", "green hills"), GOLDEN],
       colors=("warm", "light"), people="none",
       exclude=SILHOUETTE + DECAY + CITY + NIGHT + WATER_ANY + CAR + _t("road", "field", "meadow", "flowers", "wildflowers", "flower field", "dark atmosphere", "dark forest")),
    _b("photo:forest_fog_dark", "Тёмный лес / туман", "строго туманный тёмный лес",
       {"subject": "forest", "weather": "fog", "people": "none", "color": "dark"},
       [FOREST, FOG], colors=("dark", "cold"), people="none",
       exclude=DECAY + MOUNTAIN + CITY + SILHOUETTE + PEOPLE_ANY + CAR + SOLITUDE + _t("road", "alley", "architecture", "bedroom", "dark interior")),
    # iter2: no ocean/sea — rain over LAND nature (ocean_storm owns the sea).
    _b("photo:rain_nature_dark", "Дождливая природа", "дождь/непогода в природном пейзаже (без моря)",
       {"subject": "land nature", "weather": "rain", "people": "none", "color": "dark"},
       [FOREST + MOUNTAIN + FIELD + _t("nature", "landscape"), RAIN],
       colors=("dark", "cold"), people="none",
       exclude=CITY + DECAY + SILHOUETTE + PEOPLE_ANY + CAR + SOLITUDE + INTERIOR + _t("architecture", "ocean", "sea", "coast", "beach")),
    # iter2: dark only, no urban, no people.
    _b("photo:mountain_dark", "Тёмные горы", "суровые тёмные горы",
       {"subject": "mountain", "people": "none", "color": "dark"},
       [MOUNTAIN], colors=("dark", "cold"), people="none",
       exclude=DECAY + COAST + CITY + PEOPLE_ANY + SILHOUETTE + SOLITUDE + _t("castle", "architecture", "red lights", "glowing", "city lights")),
    # iter2: no people, no knights/fantasy.
    _b("photo:mountain_light", "Светлые горы", "светлые горы днём",
       {"subject": "mountain", "people": "none", "color": "light"},
       [MOUNTAIN], colors=("light", "warm"), people="none",
       exclude=DECAY + KNIGHT + PEOPLE_ANY + SILHOUETTE + FIELD + _t("architecture", "ocean", "sea", "coast")),
    # iter2: no boats, no people, no buildings.
    _b("photo:ocean_storm_dark", "Тёмный океан / шторм", "штормовой тёмный океан",
       {"subject": "ocean", "weather": "storm", "people": "none", "color": "dark"},
       [COAST, RAIN + FOG + NIGHT + _t("dark clouds", "rough waves", "dark sky")],
       colors=("dark", "cold"), people="none",
       exclude=MOUNTAIN + CITY + BOATS + PEOPLE_ANY + SILHOUETTE + _t("architecture", "building", "wet road", "night drive")),
    # iter2: no urban.
    _b("photo:winter_dark", "Тёмная зима", "тёмная зимняя природа",
       {"subject": "nature", "weather": "snow", "people": "none", "color": "dark"},
       [SNOW, FOREST + MOUNTAIN + FIELD + _t("nature", "landscape")], colors=("dark", "cold"), people="none",
       exclude=CITY + CAR + SILHOUETTE + PEOPLE_ANY + INTERIOR + _t("architecture", "office", "road", "ocean", "coast", "red lights", "glowing")),
    # iter2: no people/silhouettes.
    _b("photo:calm_water", "Спокойная вода / отражения", "зеркальная вода, озеро, отражение",
       {"subject": "water", "energy": "calm", "people": "none"},
       [CALMWATER, _t("calm", "serene", "tranquil", "reflection", "still water", "water reflection")], people="none",
       exclude=CITY + DECAY + NEON + SILHOUETTE + PEOPLE_ANY + INTERIOR + _t("architecture", "tunnel", "corridor", "waterfall", "storm", "night city", "wet road", "ocean", "beach")),
    # iter2: no silhouettes, no knights.
    _b("photo:warm_field_flowers", "Поле / цветы", "тёплое поле, луг, цветы",
       {"subject": "field", "people": "none", "color": "warm"},
       [_t("field", "meadow", "green field", "open field", "flower field"), _t("flowers", "wildflowers", "flower field")],
       colors=("warm", "light", "neutral"), people="none",
       exclude=CITY + NIGHT + DECAY + SILHOUETTE + KNIGHT + _t("dark atmosphere")),

    # ---- URBAN (people=none) ----
    # (removed urban_night_empty — too varied to control.)
    # iter2: strip cyberpunk traces (neon / digital art).
    _b("photo:urban_rain_night", "Дождливый ночной город", "мокрый ночной город, дождь",
       {"subject": "city", "time": "night", "weather": "rain", "people": "none", "color": "dark"},
       [CITY, RAIN, NIGHT], colors=("dark",), people="none",
       exclude=DIGITAL_STYLE + CAR + DECAY + _t("silhouette", "train", "rail", "railway", "railroad", "train tracks", "trees", "palm trees", "forest")),
    _b("photo:urban_decay_dark", "Заброшка / распад", "заброшенные здания, руины",
       {"subject": "decay", "people": "none", "color": "dark"},
       [DECAY], colors=("dark", "cold", "neutral"), people="none",
       exclude=NEON + SILHOUETTE + PEOPLE_ANY + CAR + _t("alien", "strange figure", "artificial flower", "indoor pool", "water slide", "forest", "ocean", "mountain", "beach")),
    _b("photo:neon_night_city", "Неон / киберпанк-ночь", "неоновый ночной город",
       {"visual_style": "neon", "setting": "night city", "people": "none", "color": "dark"},
       [_t("neon", "neon lights", "neon glow", "neon city", "cyberpunk"), CITY, NIGHT], colors=("dark", "cold"), people="none",
       exclude=PORTRAIT + SILHOUETTE + COUPLE + DECAY + CAR),

    # ---- DIGITAL ----
    _b("photo:digital_silhouette_cold", "Digital-силуэт / холодный", "неоновый цифровой силуэт человека",
       {"subject": "silhouette", "visual_style": "digital/neon", "color": "cold"},
       [SILHOUETTE, NEON + _t("digital", "abstract")], colors=("dark", "cold"),
       exclude=PORTRAIT + COUPLE + INTERIOR + COAST + CAR + PERFORMANCE + _t("dance", "dancing", "dance floor", "night club", "field", "water", "stormy weather")),
    _b("photo:digital_glitch", "Digital / glitch", "глитч, цифровое искажение",
       {"visual_style": "glitch", "color": "dark"},
       [GLITCH], colors=("dark", "cold"), exclude=()),

    # ---- LONE FIGURE (none-tagged silhouettes) ----
    # (removed girl_silhouette_mood — uncontrollable.)
    # iter2: no warm.
    _b("photo:lone_figure_scene", "Одинокий силуэт в кадре", "одинокая фигура/силуэт в пейзаже",
       {"subject": "lone figure", "energy": "moody", "people": "none", "color": "dark/cold"},
       [SILHOUETTE + _t("lonely figure"), OUTDOOR], colors=("dark", "cold", "neutral"), people="none",
       exclude=DECAY + DIGITAL_STYLE + COUPLE + PORTRAIT + INTERIOR + KNIGHT + ACTION + _t("cape", "flying", "night club", "dance floor")),

    # ---- SOLO PERSON ----
    # iter2: drop all intimacy + indoor setting.
    _b("photo:solitary_person_dark", "Человек / одиночество", "один человек в тёмном настроенческом кадре",
       {"subject": "person", "setting": "nature", "energy": "solitude", "color": "dark"},
       [SOLITUDE, FOREST + MOUNTAIN + FIELD + _t("nature", "landscape", "outdoor")],
       colors=("dark", "cold"), people="present",
       exclude=COUPLE + COAST + CAR + CITY + INTERIOR + PORTRAIT + SILHOUETTE + _t("romance", "romantic", "intimate", "intimacy", "intimate moment",
              "kiss", "hug", "jewelry", "night drive", "high speed", "water")),
    # iter2: no warm; no romance of any kind.
    _b("photo:guy_solo_mood", "Парень / настроение", "одинокий парень, настроенческий кадр",
       {"subject": "guy", "energy": "moody", "people": "guys", "color": "dark/cold"},
       [PORTRAIT, DARKMOOD + _t("male", "man", "guy")],
       colors=("dark", "cold", "neutral"), people="guys",
       exclude=ROMANCE_ANY + CROWD + SILHOUETTE + SOLITUDE + _t("flowers", "pink", "heart")),

    # ---- GIRL ----
    # iter2: drop ALL intimacy.
    _b("photo:girl_portrait_light", "Портрет девушки / светлый", "портрет девушки в тёплом свете",
       {"subject": "girl", "visual_style": "portrait", "setting": "indoor", "color": "warm"},
       [PORTRAIT + _t("girl", "woman", "long hair"), INTERIOR],
       colors=("light", "warm"), people="girls",
       exclude=SILHOUETTE + COAST + OUTDOOR + CAR + _t("dark atmosphere", "dark interior", "dim lighting", "sport", "skate", "couple",
              "intimate", "intimacy", "intimate moment")),
    # iter2: split — "plain" dark portrait EXCLUDES jewelry/fashion (those go to the lux bucket).
    _b("photo:girl_portrait_dark", "Портрет девушки / тёмный", "тёмный портрет девушки, лицо видно",
       {"subject": "girl", "visual_style": "portrait", "setting": "indoor", "color": "dark"},
       [PORTRAIT + _t("girl", "woman", "long hair"), INTERIOR],
       colors=("dark", "cold"), people="girls",
       exclude=SILHOUETTE + COUPLE + FASHION + _t("intimate", "intimacy", "tender", "sport", "skate")),
    # iter2 NEW: same girls but WITH jewelry/fashion — niche/"жеманные" girls.
    _b("photo:girl_portrait_dark_lux", "Портрет девушки / украшения", "тёмный портрет девушки с украшениями/фэшн",
       {"subject": "girl", "visual_style": "portrait+jewelry", "setting": "indoor", "color": "dark"},
       [PORTRAIT + _t("girl", "woman", "long hair"), INTERIOR, _t("jewelry", "accessories", "earrings", "necklace")],
       colors=("dark", "cold"), people="girls",
       exclude=SILHOUETTE + COUPLE + _t("intimate", "intimacy", "sport", "skate")),
    _b("photo:girl_golden_outdoor", "Девушка / золотой час", "девушка на улице в тёплом свете",
       {"subject": "girl", "setting": "outdoor", "time": "golden", "color": "warm"},
       [OUTDOOR, GOLDEN],
       colors=("warm", "light"), people="girls",
       exclude=SILHOUETTE + SOLITUDE + CAR + _t("horse", "cowgirl", "weapon", "gun", "indoor", "indoor setting", "bedroom", "couple")),

    # ---- COUPLE ----
    _b("photo:couple_light_warm", "Пара / светлая", "светлая влюблённая пара, тёплый свет",
       {"subject": "couple", "energy": "romantic", "time": "day/golden", "color": "warm"},
       [COUPLE + _t("romance", "romantic", "intimacy", "love")],
       colors=("light", "warm"), people="couple",
       exclude=COAST + CAR + _t("night", "silhouette", "dark forest", "dark sky", "dark atmosphere", "fire", "castle",
                  "black and white", "glowing", "dim lighting", "rain", "wet road", "city",
                  "alone", "solitude", "lonely")),
    # iter2: strictly dark color, no glowing/dim/digital (that mixed digital + normal sources).
    _b("photo:couple_moody_dark", "Пара / тёмная", "пара в тёмном/ночном настроении",
       {"subject": "couple", "setting": "city", "energy": "moody", "time": "night", "color": "dark"},
       [COUPLE, CITY, NIGHT + DARKMOOD],
       colors=("dark", "cold"), people="couple",
       exclude=DIGITAL_STYLE + CROWD + PERFORMANCE + CAR + COAST + INTERIOR + _t("train", "railway", "dance", "dancing")),
    _b("photo:coastal_couple_warm", "Романтика у воды", "пара у воды на закате",
       {"subject": "couple", "setting": "coast", "time": "golden", "color": "warm"},
       [COAST, COUPLE + _t("romance", "romantic", "walking", "running")],
       colors=("light", "warm"), people="couple",
       exclude=_t("dark atmosphere", "wet road", "night")),

    # ---- PEOPLE IN SCENE ----
    # (removed fashion_street — uncontrollable, always mixed.)
    # iter2: no warm (dark/cold only).
    _b("photo:street_people_night", "Люди на ночной улице", "люди в ночном городе, стрит",
       {"subject": "people", "setting": "night street", "time": "night", "color": "dark"},
       [_t("street", "city street", "street scene", "sidewalk"), NIGHT, CROWD + _t("people", "group", "street scene", "city life")],
       colors=("dark", "cold"), people="present",
       exclude=CAR + NEON + DECAY + COUPLE + INTERIOR + PORTRAIT + SOLITUDE),
    # iter2: no warm.
    _b("photo:performance_crowd", "Сцена / толпа", "концерт, клуб, толпа",
       {"subject": "crowd", "setting": "stage/club", "energy": "energetic", "color": "dark"},
       [CROWD, PERFORMANCE],
       colors=("dark", "cold"), people="present",
       exclude=DECAY + CAR + PORTRAIT + _t("private jet", "jewelry", "street scene", "city life")),
    _b("photo:active_life_night", "Активная жизнь", "движение: скейт/танец/спорт ночью",
       {"subject": "person", "action": "sport/dance", "energy": "energetic", "color": "dark"},
       [ACTION],
       colors=("dark", "cold"), people="present",
       exclude=CAR + DECAY + CROWD + COUPLE + PERFORMANCE + _t("dance", "dancing", "nightlife")),

    # ---- VEHICLES ----
    # iter2: no people — clean car shots only (a "left" driver shot slipped in).
    _b("photo:car_night", "Машины / ночная езда", "авто, ночная езда, интерьер авто",
       {"subject": "car", "time": "night", "action": "drive", "people": "none"},
       [CAR, DRIVE, NIGHT + _t("city lights", "neon lights")],
       people="none", exclude=_t("race", "drift", "burnout")),
    # iter2: strictly dark/cold — warm split off (dark and warm must not mix).
    _b("photo:car_race", "Дрифт / гонки", "дрифт, гонки, скорость (тёмный/холодный)",
       {"subject": "car", "action": "race", "energy": "energetic", "color": "dark/cold"},
       [CAR, _t("drift", "racing", "race", "burnout", "speed", "night drift")],
       colors=("dark", "cold"), exclude=()),
]

# Pools below ten stills cannot sustain a full lyric video without obvious
# repetition. Keep their contracts in source for future re-tagging, but keep
# them out of previews and selection until the source base grows.
RETIRED_THIN_BUCKET_IDS = frozenset({
    "photo:rain_nature_dark",
    "photo:mountain_dark",
    "photo:mountain_light",
    "photo:ocean_storm_dark",
    "photo:winter_dark",
    "photo:calm_water",
    "photo:guy_solo_mood",
    "photo:girl_portrait_dark",
    "photo:girl_portrait_dark_lux",
    "photo:street_people_night",
    "photo:active_life_night",
    "photo:car_race",
})


def load_photo_catalog() -> List[PhotoBucket]:
    ids = [b.bucket_id for b in PHOTO_BUCKETS if b.bucket_id not in RETIRED_THIN_BUCKET_IDS]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate photo bucket_id")
    return [b for b in PHOTO_BUCKETS if b.bucket_id not in RETIRED_THIN_BUCKET_IDS]


CATALOG_VERSION = "photo-facet-v2-2026-07-22"
