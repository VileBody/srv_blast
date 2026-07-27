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
DRIVE = _t("night drive", "driving", "drive", "highway", "road trip", "wet road", "moving car", "traffic")
OUTDOOR = _t("outdoor", "outdoors", "landscape", "forest", "mountain", "field", "street", "road", "cityscape", "coast", "beach")
SOLITUDE = _t("alone", "solo", "solitude", "lonely", "lonely figure", "single person", "sitting alone")

CAR_INTERIOR = _t("car interior", "vehicle interior", "interior", "inside car", "dashboard", "steering wheel", "driver seat", "passenger seat", "cabin", "car window")
RED_ANY = _t("red", "red light", "red lights", "red lighting", "red glow", "red background", "crimson", "scarlet")
ANIMALS = _t(
    "animal", "animals", "wildlife", "wild animal",
    "dog", "dogs", "cat", "cats", "horse", "horses",
    "deer", "deer silhouette", "stag", "doe", "fawn", "elk", "moose",
    "reindeer", "antler", "antlers", "wolf", "wolves", "bird", "birds",
    "sheep", "cow", "cows",
)
# reusable exclude groups (facet-breakers)
WATER_ANY = _t("water", "ocean", "sea", "coast", "coastal", "beach", "lake", "river", "waves", "waterfront", "beach sunset", "shore")
KNIGHT = _t("knight", "knights", "wizard", "mage", "medieval", "armor", "warrior", "castle", "fantasy", "sword")
BOATS = _t("boat", "boats", "ship", "ships", "sailboat", "yacht", "fishing boat")
DIGITAL_STYLE = _t("neon", "neon lights", "glow", "glowing", "led", "red lights", "blue lighting", "purple lighting", "digital art", "abstract", "glitch", "distortion")
ROMANCE_ANY = _t("romance", "romantic", "romantic moment", "kiss", "hug", "embrace", "intimacy", "intimate", "intimate moment", "love", "couple", "date", "tender")
PEOPLE_ANY = _t("man", "woman", "guy", "girl", "person", "people", "couple", "crowd", "portrait", "face", "man on sidewalk")
BUILT_ENVIRONMENT = CITY + _t(
    "urban setting", "urban area", "urban environment", "suburban", "suburb",
    "residential", "residential area", "village", "town", "architecture",
    "building", "buildings", "house", "houses", "home", "homes",
    "country house", "rural house", "farmhouse", "cottage", "cabin", "hut",
    "barn", "shed", "road", "roads", "street", "highway", "driveway",
    "bridge", "railway", "railroad", "power lines", "utility pole",
)


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
    exclude_people: Tuple[str, ...] = ()
    require_detected_subjects: Tuple[str, ...] = ()
    exclude_detected_subjects: Tuple[str, ...] = ()
    exclude_clip_ids: Tuple[str, ...] = ()

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
    clip_id = Path(str(
        asset.get("clip_id") or asset.get("video_key") or
        asset.get("file_name") or asset.get("video_path") or ""
    )).stem
    framing_value = asset.get("meta_framing") or asset.get("framing")
    framing = framing_value if isinstance(framing_value, Mapping) else {}
    detected_subject = _n(framing.get("subject_class"))
    quality = framing.get("quality")

    if isinstance(quality, Mapping) and quality.get("reject"):
        return False, "quality"
    if clip_id and clip_id in bucket.exclude_clip_ids:
        return False, "clip_exclude"
    if bucket.require_detected_subjects and detected_subject not in bucket.require_detected_subjects:
        return False, "detected_subject"
    if detected_subject and detected_subject in bucket.exclude_detected_subjects:
        return False, "detected_subject"
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
    if bucket.exclude_people and people in bucket.exclude_people:
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
def _b(
    bucket_id, label, lead, facets, require, colors=(), people="any", exclude=(),
    exclude_people=(), require_detected_subjects=(), exclude_detected_subjects=(),
    exclude_clip_ids=(),
):
    return PhotoBucket(
        bucket_id=bucket_id, label=label, lead=lead, facets=facets,
        require_groups=tuple(require), exclude_terms=_t(*exclude),
        colors=tuple(colors), people=people, exclude_people=_t(*exclude_people),
        require_detected_subjects=_t(*require_detected_subjects),
        exclude_detected_subjects=_t(*exclude_detected_subjects),
        exclude_clip_ids=tuple(str(x) for x in exclude_clip_ids),
    )


PHOTO_BUCKETS: List[PhotoBucket] = [
    # ---- NATURE / LANDSCAPE (people=none) ----
    # iter2: exclude water entirely — golden-hour LAND nature only.
    _b("photo:nature_golden_warm", "Природа / золотой час", "тёплый природный пейзаж на закате (без воды)",
       {"subject": "land nature", "time": "golden", "people": "none", "color": "warm", "energy": "serene"},
       [FOREST + _t("hills", "green hills", "landscape"), GOLDEN],
       colors=("warm", "light"), people="none",
       exclude=SILHOUETTE + DECAY + BUILT_ENVIRONMENT + NIGHT + WATER_ANY + CAR + NEON + SOLITUDE + _t("field", "meadow", "flowers", "wildflowers", "flower field", "dark atmosphere", "dark forest", "american flag", "flag", "tropical", "palm trees", "train", "train window"),
       exclude_detected_subjects=("person",),
       exclude_clip_ids=("1151373460999954766", "1111896595505880713", "965037026415009952", "381750505936018149")),
    _b("photo:forest_fog_dark", '\u0422\u0451\u043c\u043d\u044b\u0439 \u043b\u0435\u0441 / \u0442\u0443\u043c\u0430\u043d', '\u0441\u0442\u0440\u043e\u0433\u043e \u0442\u0443\u043c\u0430\u043d\u043d\u044b\u0439 \u0442\u0451\u043c\u043d\u044b\u0439 \u043b\u0435\u0441 \u0431\u0435\u0437 \u043f\u043e\u0441\u0442\u0440\u043e\u0435\u043a \u0438 \u0434\u043e\u0440\u043e\u0433',
       {"subject": "forest", "weather": "fog", "people": "none", "color": "dark"},
       [FOREST, FOG], colors=("dark", "cold", "neutral"), people="none",
       exclude=CITY + INTERIOR + CAR + _t(
           "road", "roads", "highway", "house", "houses", "home", "homes",
           "cottage", "cabin", "hut", "building", "buildings",
       ),
       exclude_detected_subjects=("person",),
       exclude_clip_ids=("720716746681049424",)),
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
    _b("photo:warm_field_flowers", "Поле / золотой час", "тёплое поле или луг в золотой час",
       {"subject": "field", "time": "golden", "people": "none", "color": "warm"},
       [FIELD, GOLDEN], colors=("warm", "light", "neutral"), people="none",
       exclude=BUILT_ENVIRONMENT + NIGHT + DECAY + SILHOUETTE + PEOPLE_ANY + KNIGHT + CAR + WATER_ANY + MOUNTAIN + SNOW + _t("tractor", "truck", "motorcycle", "parking", "dark atmosphere", "waterfall"),
       exclude_detected_subjects=("person",),
       exclude_clip_ids=("955185402232149163",)),
    _b("photo:ocean_golden_warm", "Океан / золотой час", "океан, берег и волны на закате",
       {"subject": "ocean", "time": "golden", "people": "none", "color": "warm"},
       [COAST, GOLDEN], colors=("warm", "light", "neutral"), people="none",
       exclude=BUILT_ENVIRONMENT + CAR + PEOPLE_ANY + BOATS + SILHOUETTE),

    # ---- URBAN (people=none) ----
    # (removed urban_night_empty — too varied to control.)
    # Rain and neon are separate themes: rain rejects cyberpunk traces while the
    # neon bucket may contain wet reflective streets.
    _b("photo:urban_rain_night", "Дождливый ночной город", "мокрый ночной город, дождь",
       {"subject": "city", "time": "night", "weather": "rain", "people": "none", "color": "dark"},
       [CITY, RAIN], colors=("dark", "cold"), people="none",
       exclude=DIGITAL_STYLE + CAR + DECAY + FOREST + INTERIOR + _t("train", "rail", "railway", "railroad", "train tracks")),
    _b("photo:urban_decay_dark", "Заброшка / распад", "заброшенные здания, руины",
       {"subject": "decay", "people": "none", "color": "dark"},
       [DECAY], colors=("dark", "cold", "neutral"), people="none",
       exclude=NEON + SILHOUETTE + PEOPLE_ANY + CAR + _t("alien", "strange figure", "artificial flower", "indoor pool", "water slide", "forest", "ocean", "mountain", "beach")),
    _b("photo:neon_night_city", "Неон / киберпанк-ночь", "неоновый ночной город",
       {"visual_style": "neon", "setting": "night city", "people": "none", "color": "dark"},
       [_t("neon", "neon lights", "neon glow", "neon city", "cyberpunk"), CITY, NIGHT], colors=("dark", "cold"), people="none",
       exclude=PORTRAIT + SILHOUETTE + COUPLE + DECAY + CAR),
    _b("photo:urban_night_skyline", "Ночной город / skyline", "ночная городская панорама без дождя и неона",
       {"subject": "city skyline", "time": "night", "people": "none", "color": "dark"},
       [_t("cityscape", "skyline", "urban skyline", "skyscraper", "skyscrapers", "high-rise buildings", "city lights", "night city"), NIGHT + DARKMOOD],
       colors=("dark", "cold"), people="none",
       exclude=RAIN + DIGITAL_STYLE + CAR + DECAY + INTERIOR + FOREST + _t("street", "city street", "wet road")),
    _b("photo:dark_empty_interior", "Тёмный пустой интерьер", "пустая тёмная комната или коридор",
       {"subject": "empty interior", "setting": "interior", "people": "none", "color": "dark"},
       [INTERIOR, DARKMOOD], colors=("dark", "cold", "neutral"), people="none",
       exclude=DECAY + CAR + SILHOUETTE + PEOPLE_ANY),

    # ---- DIGITAL ----
    _b("photo:digital_silhouette_cold", "Digital-силуэт / холодный", "неоновый цифровой силуэт человека",
       {"subject": "silhouette", "visual_style": "digital/neon", "color": "cold"},
       [SILHOUETTE, _t(
           "neon", "neon lights", "neon glow", "glow", "glowing", "led",
           "blue lighting", "purple lighting", "digital", "digital art",
           "abstract", "glitch", "distortion",
       )], colors=("dark", "cold"),
       exclude=PORTRAIT + COUPLE + ROMANCE_ANY + RED_ANY + INTERIOR + COAST + CAR + PERFORMANCE + _t("dance", "dancing", "dance floor", "night club", "field", "water", "stormy weather")),
    _b("photo:digital_glitch", "Digital / glitch", "глитч, цифровое искажение",
       {"visual_style": "glitch", "color": "dark"},
       [GLITCH], colors=("dark", "cold"), exclude=()),
    # iter4: digital_glitch and digital_silhouette_cold read as the same vibe on
    # screen (dark digital artifact / neon-lit figure), so they are merged into one
    # selectable group. Subject = a silhouette OR a glitch artifact, style = digital
    # /neon/glitch. A glitched close-up stays on theme, so PORTRAIT is not excluded
    # here (unlike the silhouette-only parent).
    _b("photo:digital_dark", "Digital / глитч и силуэты", "цифровой тёмный вайб: глитч, неон, силуэты",
       {"subject": "silhouette/glitch", "visual_style": "digital/neon", "color": "dark/cold"},
       [SILHOUETTE + GLITCH + _t("digital art", "abstract"),
        _t("neon", "neon lights", "neon glow", "glow", "glowing", "led",
           "blue lighting", "purple lighting", "digital", "digital art",
           "abstract", "glitch", "distortion", "screen glow", "datamosh")],
       colors=("dark", "cold"),
       exclude=COUPLE + ROMANCE_ANY + RED_ANY + INTERIOR + COAST + CAR + PERFORMANCE
               + _t("dance", "dancing", "dance floor", "night club", "field", "water", "stormy weather")),

    # ---- LONE FIGURE (none-tagged silhouettes) ----
    # (removed girl_silhouette_mood — uncontrollable.)
    # iter2: no warm.
    _b("photo:lone_figure_scene", "Одинокий силуэт в кадре", "одинокая фигура/силуэт в пейзаже",
       {"subject": "lone figure", "energy": "moody", "people": "none", "color": "dark/cold"},
       [SILHOUETTE + _t("lonely figure"), OUTDOOR], colors=("dark", "cold", "neutral"), people="none",
       exclude=DECAY + DIGITAL_STYLE + COUPLE + PORTRAIT + INTERIOR + KNIGHT + ACTION + ANIMALS + _t("cape", "flying", "night club", "dance floor"),
       require_detected_subjects=("person",)),

    # ---- SOLO PERSON ----
    # iter2: drop all intimacy + indoor setting.
    _b("photo:solitary_person_dark", "Человек / одиночество", "один человек в тёмном настроенческом кадре",
       {"subject": "person", "setting": "nature", "energy": "solitude", "color": "dark"},
       [SOLITUDE, FOREST + MOUNTAIN + FIELD + _t("nature", "landscape", "outdoor")],
       colors=("dark", "cold"), people="present",
       # girls have their own moody/silhouette/indoor contracts — without this the
       # bucket filled up with them (18 of 24 stills) instead of reading as
       # "a person alone in nature".
       exclude_people=("girls",),
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
       colors=("light", "warm", "neutral"), people="girls",
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
       [OUTDOOR, GOLDEN], colors=("warm", "light", "neutral"), people="girls",
       exclude=CAR + INTERIOR + ROMANCE_ANY + _t("horse", "cowgirl", "weapon", "gun")),
    _b("photo:girl_silhouette_dark", "Девушка / тёмный силуэт", "одинокий женский силуэт в тёмном кадре",
       {"subject": "girl silhouette", "energy": "moody", "people": "girls", "color": "dark"},
       [SILHOUETTE, NIGHT + DARKMOOD], colors=("dark", "cold"), people="girls",
       exclude=ROMANCE_ANY + CAR + INTERIOR + COAST + DIGITAL_STYLE + ANIMALS),

    _b("photo:girl_moody_dark", "Девушка / тёмное настроение", "одинокая девушка или тёмный портрет без digital-стилизации",
       {"subject": "girl", "energy": "moody", "people": "girls", "color": "dark"},
       [PORTRAIT + _t("girl", "woman", "long hair"), DARKMOOD],
       colors=("dark", "cold"), people="girls",
       exclude=SILHOUETTE + ROMANCE_ANY + CAR + FASHION + DIGITAL_STYLE + ANIMALS),

    # ---- COUPLE ----
    _b("photo:couple_light_warm", "Пара / золотой час", "пара в мягком золотом дневном свете",
       {"subject": "couple", "energy": "romantic", "time": "golden", "color": "warm"},
       [GOLDEN + _t("soft light", "daylight")],
       colors=("light", "warm", "neutral"), people="couple",
       exclude=CAR + INTERIOR + NIGHT + DARKMOOD + _t("rain", "wet road", "fire", "castle", "black and white")),
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
    _b("photo:car_night", "Машины / ночная езда", "авто только снаружи, ночная езда и городские огни",
       {"subject": "car", "time": "night", "action": "drive", "people": "none"},
       [CAR, NIGHT + _t("city lights", "neon lights")],
       colors=("dark", "cold"), people="none",
       exclude=CAR_INTERIOR + _t("race", "drift", "burnout")),
    _b("photo:road_night_empty", "Пустая ночная дорога", "пустая тёмная дорога без города и машин",
       {"subject": "road", "time": "night", "people": "none", "color": "dark"},
       [_t("road", "roads", "highway", "wet road", "empty road", "dark road"), NIGHT + DARKMOOD],
       colors=("dark", "cold"), people="none",
       exclude=CITY + CAR + DECAY + INTERIOR,
       exclude_clip_ids=("1067634655465292718",)),
    # iter2: strictly dark/cold — warm split off (dark and warm must not mix).
    _b("photo:car_race", "Дрифт / гонки", "дрифт, гонки, скорость (тёмный/холодный)",
       {"subject": "car", "action": "race", "energy": "energetic", "color": "dark/cold"},
       [CAR, _t("drift", "racing", "race", "burnout", "speed", "night drift")],
       colors=("dark", "cold"), exclude=()),

    # ---- iter3: SETTING-SPLIT buckets for people categories that had no home ----
    # The pool audit showed 1671 stills matching no active contract, concentrated in
    # people x color x SETTING cells with no bucket at all (guys had none whatsoever;
    # girls only had portrait/silhouette/golden variants). Rather than widening the
    # existing contracts (different settings read very differently), each cell gets
    # its own narrow contract — many small pure groups beat a few broad ones.
    _b("photo:girl_indoor_dark", "Девушка / тёмный интерьер", "девушка в тёмном помещении (не портрет-крупняк)",
       {"subject": "girl", "setting": "indoor", "people": "girls", "color": "dark"},
       [INTERIOR],
       colors=("dark", "cold"), people="girls",
       exclude=COUPLE + CROWD + CAR + CAR_INTERIOR + DRIVE + CITY + DECAY + ROMANCE_ANY + KNIGHT
               + _t("beach", "ocean", "forest", "mountain")),
    _b("photo:girl_car_night", "Девушка / машина ночью", "девушка в машине / у машины ночью",
       {"subject": "girl", "setting": "car", "time": "night", "people": "girls", "color": "dark"},
       [CAR + CAR_INTERIOR + DRIVE],
       colors=("dark", "cold"), people="girls",
       exclude=COUPLE + CROWD + DECAY + ROMANCE_ANY + _t("race", "drift", "burnout")),
    _b("photo:girl_city_night", "Девушка / ночной город", "девушка на фоне ночного города",
       {"subject": "girl", "setting": "night city", "time": "night", "people": "girls", "color": "dark"},
       [CITY],
       colors=("dark", "cold"), people="girls",
       exclude=COUPLE + CROWD + CAR + DECAY + INTERIOR + ROMANCE_ANY
               + _t("forest", "mountain", "beach", "ocean")),
    _b("photo:girl_indoor_warm", "Девушка / тёплый интерьер", "девушка в тёплом домашнем свете",
       {"subject": "girl", "setting": "indoor", "people": "girls", "color": "warm"},
       [INTERIOR],
       colors=("warm", "light"), people="girls",
       exclude=COUPLE + CROWD + CAR + CAR_INTERIOR + DRIVE + CITY + DECAY + ROMANCE_ANY + KNIGHT + PORTRAIT
               + _t("beach", "ocean", "forest", "mountain")),
    _b("photo:guy_car_night", "Парень / машина ночью", "парень в машине / у машины ночью",
       {"subject": "guy", "setting": "car", "time": "night", "people": "guys", "color": "dark"},
       [CAR + CAR_INTERIOR + DRIVE],
       colors=("dark", "cold"), people="guys",
       exclude=COUPLE + CROWD + DECAY + ROMANCE_ANY + _t("race", "drift", "burnout")),
    _b("photo:guy_city_night", "Парень / ночной город", "парень на ночной улице",
       {"subject": "guy", "setting": "night city", "time": "night", "people": "guys", "color": "dark"},
       [CITY],
       colors=("dark", "cold"), people="guys",
       exclude=COUPLE + CROWD + CAR + DECAY + INTERIOR + ROMANCE_ANY
               + _t("forest", "mountain", "beach", "ocean")),
    _b("photo:crowd_club_dark", "Толпа / клуб", "толпа на концерте или в клубе",
       {"subject": "crowd", "setting": "club/stage", "energy": "energetic", "color": "dark"},
       [CROWD + PERFORMANCE], colors=("dark", "cold"), people="crowd",
       exclude=DECAY + CAR + KNIGHT + _t("private jet", "street scene")),
    _b("photo:forest_warm_day", "Лес / тёплый свет", "тёплый светлый лес без тумана и построек",
       {"subject": "forest", "time": "day/golden", "people": "none", "color": "warm"},
       [FOREST], colors=("warm", "light"), people="none",
       exclude=CITY + DECAY + CAR + INTERIOR + SILHOUETTE + PEOPLE_ANY + KNIGHT + FOG + FIELD
               + _t("ocean", "sea", "beach", "coast", "snow", "winter"),
       exclude_detected_subjects=("person",)),
]

# Track-theme -> photo vibe expansion. This mirrors the footage visual catalog's
# theme-first ranker, but only references active photo:* contracts.
PHOTO_THEME_BUCKETS: Mapping[str, Tuple[str, ...]] = {
    "romance_major": ("photo:couple_light_warm", "photo:girl_golden_outdoor", "photo:ocean_golden_warm"),
    "romance_minor": ("photo:girl_silhouette_dark", "photo:girl_moody_dark", "photo:dark_empty_interior"),
    "epic_love_major": ("photo:ocean_golden_warm", "photo:couple_light_warm", "photo:warm_field_flowers"),
    "epic_love_minor": ("photo:girl_silhouette_dark", "photo:urban_night_skyline"),
    "heartbreak_minor": ("photo:girl_silhouette_dark", "photo:girl_moody_dark", "photo:digital_dark"),
    "betrayal_minor": ("photo:urban_decay_dark", "photo:digital_dark", "photo:dark_empty_interior"),
    "jealousy_minor": ("photo:digital_dark", "photo:neon_night_city", "photo:urban_night_skyline"),
    "depression_minor": ("photo:girl_silhouette_dark", "photo:dark_empty_interior", "photo:urban_decay_dark"),
    "self_destruction_minor": ("photo:digital_dark", "photo:urban_decay_dark", "photo:car_night"),
    "aggression_minor": ("photo:car_night", "photo:urban_decay_dark", "photo:urban_night_skyline"),
    "motivation_major": ("photo:car_night", "photo:warm_field_flowers", "photo:girl_golden_outdoor"),
    "motivation_minor": ("photo:car_night", "photo:urban_rain_night"),
    "hustle_minor": ("photo:car_night", "photo:neon_night_city", "photo:urban_night_skyline"),
    "sex_major": ("photo:couple_light_warm", "photo:girl_portrait_light", "photo:girl_golden_outdoor"),
    "sex_minor": ("photo:girl_moody_dark", "photo:girl_silhouette_dark", "photo:digital_dark"),
    "nostalgia_city_minor": ("photo:urban_rain_night", "photo:neon_night_city", "photo:urban_night_skyline"),
    "adrenaline_flex_major": ("photo:car_night", "photo:neon_night_city"),
    "escapism_dreams_minor": ("photo:digital_dark", "photo:urban_night_skyline"),
    "loneliness_isolation_minor": ("photo:girl_silhouette_dark", "photo:girl_moody_dark", "photo:dark_empty_interior"),
    "youth_rebellion_major": ("photo:car_night", "photo:neon_night_city", "photo:girl_golden_outdoor"),
    "mysticism_fate_minor": ("photo:digital_dark", "photo:girl_silhouette_dark"),
    "cyber_alienation_minor": ("photo:digital_dark", "photo:neon_night_city", "photo:urban_night_skyline"),
    "serene_landscape_major": ("photo:ocean_golden_warm", "photo:warm_field_flowers"),
    "nightlife_electro_minor": ("photo:neon_night_city", "photo:digital_dark", "photo:urban_night_skyline"),
    "urban_blocks_minor": ("photo:urban_rain_night", "photo:urban_decay_dark", "photo:urban_night_skyline"),
    # iter3: setting-split themes (girls/guys/crowd cells + forest/fog variants).
    "night_ride_minor": ("photo:car_night", "photo:girl_car_night", "photo:guy_car_night"),
    "street_youth_minor": ("photo:guy_city_night", "photo:girl_city_night", "photo:street_people_night"),
    "home_intimacy_minor": ("photo:girl_indoor_dark", "photo:girl_indoor_warm", "photo:dark_empty_interior"),
    "party_energy_major": ("photo:crowd_club_dark", "photo:neon_night_city", "photo:girl_city_night"),
    "forest_calm_minor": ("photo:forest_fog_dark", "photo:forest_warm_day", "photo:solitary_person_dark"),
    "digital_dream_minor": ("photo:digital_dark", "photo:neon_night_city"),
}


def load_photo_theme_buckets() -> Dict[str, List[str]]:
    active = {b.bucket_id for b in load_photo_catalog()}
    return {
        theme: [bucket_id for bucket_id in bucket_ids if bucket_id in active]
        for theme, bucket_ids in PHOTO_THEME_BUCKETS.items()
    }

# Calibrated against data/photo_tags_snapshot_framed_review.json after quality
# and framing. Every selectable contract has at least 30 pickable stills.
ACTIVE_PHOTO_BUCKET_IDS = frozenset({
    "photo:girl_moody_dark",
    "photo:warm_field_flowers",
    "photo:ocean_golden_warm",
    "photo:urban_rain_night",
    "photo:urban_decay_dark",
    "photo:neon_night_city",
    "photo:urban_night_skyline",
    "photo:dark_empty_interior",
    "photo:digital_dark",
    "photo:girl_portrait_light",
    "photo:girl_golden_outdoor",
    "photo:girl_silhouette_dark",
    "photo:couple_light_warm",
    "photo:car_night",
    # iter3: setting-split cells that previously had no contract at all.
    "photo:girl_indoor_dark",
    "photo:girl_car_night",
    "photo:girl_city_night",
    "photo:guy_car_night",
    "photo:guy_city_night",
    "photo:crowd_club_dark",
    # iter3b: distinct narrow themes re-activated after the setting-split — each
    # adds photos no other active contract claims (verified: max overlap <= 5).
    "photo:forest_fog_dark",
})
RETIRED_THIN_BUCKET_IDS = frozenset(
    b.bucket_id for b in PHOTO_BUCKETS if b.bucket_id not in ACTIVE_PHOTO_BUCKET_IDS
)


def load_photo_catalog() -> List[PhotoBucket]:
    all_ids = {b.bucket_id for b in PHOTO_BUCKETS}
    unknown = ACTIVE_PHOTO_BUCKET_IDS - all_ids
    if unknown:
        raise RuntimeError(f"unknown active photo bucket ids: {sorted(unknown)}")
    ids = [b.bucket_id for b in PHOTO_BUCKETS if b.bucket_id in ACTIVE_PHOTO_BUCKET_IDS]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate photo bucket_id")
    return [b for b in PHOTO_BUCKETS if b.bucket_id in ACTIVE_PHOTO_BUCKET_IDS]


CATALOG_VERSION = "photo-facet-v4-min30-2026-07-27"
