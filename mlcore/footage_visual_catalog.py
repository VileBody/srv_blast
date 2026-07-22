"""Final semantic footage catalog and hard-gate evaluator.

The track taxonomy and visual ontology are intentionally separate.  Track themes
rank ``visual:*`` ids; this module decides whether an asset is eligible for a
visual contract.  Ranking/line boosts only run after these gates.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "footage_semantic_catalog_final_resolved_v2.json"
CATALOG_VERSION = "semantic-v2.1-2026-07-13"


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _terms(*values: str) -> Tuple[str, ...]:
    return tuple(_norm(v) for v in values if _norm(v))


NONE = _terms("none", "no people")
GIRLS = _terms("girl", "girls", "woman", "women", "female")

TEXT_TERMS = _terms(
    "text", "typography", "title", "subtitle", "caption", "quote", "words",
    "lettering", "watermark", "logo", "sign with text", "phone screen",
)

# User-reviewed rejects.  This is a safety net, not a substitute for contracts.
QUALITY_REJECT_IDS = frozenset({
    "867717053190572283", "1090152653543727548", "763923155585536644",
    "902408844090944543", "1129488781561648617", "973270169503988994",
    "664703226297754037", "934004410228900122", "746612444518032222",
    "643100021830824934", "1027594839985017993", "1003106517033404894",
    "1074178948636471341", "1065312486849969930",
    "728457308528976739",
})


@dataclass(frozen=True)
class VisualContract:
    bucket_id: str
    label: str
    potential: int
    sources: Tuple[str, ...]
    # Every group must match at least one tag.  A term matches an exact tag or a
    # phrase contained in that tag ("mountain" matches "mountain road").
    require_groups: Tuple[Tuple[str, ...], ...] = ()
    exclude_terms: Tuple[str, ...] = ()
    # Semantic invariants checked before manually reviewed sources.
    hard_require_groups: Tuple[Tuple[str, ...], ...] = ()
    hard_exclude_terms: Tuple[str, ...] = ()
    colors: Tuple[str, ...] = ()
    people: str = "any"  # any | none | girls | present
    fallback_terms: Tuple[str, ...] = ()

    @property
    def theme(self) -> str:
        return "visual"

    @property
    def tags_group(self) -> str:
        return self.bucket_id.split(":", 1)[-1]

    @property
    def mood(self) -> str:
        return ""

    @property
    def priority_tags(self) -> List[str]:
        return list(dict.fromkeys(x for group in self.require_groups for x in group))

    @property
    def exclude_tags(self) -> List[str]:
        return list(self.exclude_terms)

    @property
    def color(self) -> List[str]:
        return list(self.colors)

    @property
    def exclude(self) -> List[str]:
        return []


VEHICLE = _terms("car", "cars", "vehicle", "sports car", "luxury car", "race car")
MOTION = _terms("drift", "racing", "race", "burnout", "speed", "driving", "car motion", "city motion")
NATURE = _terms("nature", "forest", "tree", "field", "ocean", "sea", "coast", "beach", "mountain", "cliff", "snow")
URBAN = _terms("urban", "city", "cityscape", "street", "building", "skyscraper", "apartment", "house")
BUILT = URBAN + _terms("interior", "room", "window", "road", "bridge", "architecture")
DIGITAL = _terms("digital", "animation", "3d", "cgi", "abstract", "visual effect", "graphic", "glitch")
SILHOUETTE = _terms("silhouette", "human silhouette", "glowing silhouette", "neon silhouette")
WEATHER = _terms("rain", "snow", "fog", "mist", "storm")
COAST = _terms("ocean", "sea", "coast", "coastal", "beach", "shore", "waves")
WATER = COAST + _terms("water", "lake", "river", "pool", "waterfall")
MOUNTAIN = _terms("mountain", "mountains", "cliff", "mountain view", "mountain road")
INDOOR = _terms(
    "interior", "indoor", "indoor setting", "room", "bedroom", "hallway",
    "studio", "home", "apartment", "dim room", "dark interior",
)
VEHICLE_CONTEXT = VEHICLE + MOTION + _terms(
    "automobile", "motorcycle", "train", "tram", "bus", "truck", "highway",
    "traffic", "night drive", "night driving", "car interior", "car wheel",
    "garage", "headlights", "road trip",
)
OUTDOOR_URBAN = _terms(
    "urban", "city", "cityscape", "street", "building", "skyscraper",
    "downtown", "architecture",
)

# Contract vocabulary is deliberately conservative.  It can grow after logged
# false negatives; semantic conflicts must never be relaxed as a fallback.
RULES: Mapping[str, Dict[str, Any]] = {
    "visual:vehicle_drift_racing": dict(require_groups=(VEHICLE, _terms("drift", "racing", "race", "burnout")), exclude_terms=DIGITAL + _terms("wallpaper", "overlay"), people="none"),
    "visual:vehicle_motion_aesthetic": dict(require_groups=(VEHICLE, MOTION + _terms("night drive", "car convoy", "highway")), exclude_terms=DIGITAL + _terms("overlay", "snow", "interior", "apartment", "room"), people="none"),
    "visual:forest_fog_dark": dict(require_groups=(_terms("forest", "woods", "trees"), _terms("fog", "mist", "haze")), exclude_terms=URBAN + VEHICLE + _terms("fire", "horror", "knight", "snow", "winter"), colors=("dark", "cold"), people="none"),
    "visual:digital_human_silhouette_cold": dict(require_groups=(SILHOUETTE,), colors=("dark", "cold"), exclude_terms=_terms("plain portrait", "close-up", "close up")),
    "visual:digital_human_silhouette_warm": dict(require_groups=(SILHOUETTE,), colors=("warm", "light"), exclude_terms=_terms("plain portrait", "close-up", "close up")),
    "visual:digital_glitch_dark_cold": dict(require_groups=(_terms("glitch", "digital distortion", "datamosh", "noise", "distorted"),), colors=("dark", "cold"), exclude_terms=VEHICLE + URBAN, fallback_terms=SILHOUETTE),
    "visual:urban_solitude_dark": dict(require_groups=(URBAN,), exclude_terms=VEHICLE + DIGITAL + _terms("interior", "room", "party", "crowd", "rain", "snow", "storm"), colors=("dark", "cold"), people="none"),
    "visual:urban_weather_dark": dict(require_groups=(_terms("urban", "city", "street", "building", "cityscape"), WEATHER), exclude_terms=VEHICLE + _terms("automobile", "motorcycle", "train", "tram", "bus", "truck", "night drive", "night driving", "traffic jam", "traffic flow", "city traffic", "night traffic", "headlights", "camper", "drift", "windshield") + DIGITAL + _terms("interior", "indoor", "room", "bedroom", "hallway", "corridor", "window scene", "garage", "window view", "bedroom window", "destruction", "disaster", "tornado", "fire", "burning", "explosion", "gas station", "abandoned"), colors=("dark",), people="none"),
    "visual:couple_intimacy_light_warm": dict(require_groups=(_terms("couple", "romance", "romantic", "kiss", "hug", "intimacy", "love"),), exclude_terms=_terms("crowd", "performance", "sport"), hard_exclude_terms=WATER, colors=("light", "warm"), people="present"),
    "visual:solitary_person_dark_cold": dict(require_groups=(_terms("alone", "solitude", "lonely", "single person", "portrait", "man", "woman", "guy", "girl"),), exclude_terms=_terms("couple", "crowd", "group", "party", "performance"), colors=("dark", "cold"), people="present"),
    "visual:performance_crowd_dark": dict(require_groups=(_terms("concert", "performance", "stage", "crowd", "audience", "club", "party", "rave"),), colors=("dark",), people="present"),
    "visual:girls_portrait_light_warm": dict(require_groups=(_terms("portrait", "face", "close up", "close-up", "girl", "woman", "long hair", "casual pose"),), exclude_terms=_terms("sport", "skate", "running", "crowd", "couple"), hard_require_groups=(INDOOR,), hard_exclude_terms=VEHICLE_CONTEXT + OUTDOOR_URBAN, colors=("light", "warm"), people="girls"),
    "visual:girls_portrait_dark_cold": dict(require_groups=(_terms("portrait", "face", "close up", "close-up", "girl", "woman", "long hair", "low light"),), exclude_terms=_terms("sport", "skate", "running", "crowd", "couple"), hard_require_groups=(INDOOR,), hard_exclude_terms=VEHICLE_CONTEXT + OUTDOOR_URBAN, colors=("dark", "cold"), people="girls"),
    "visual:active_life_dark_cold": dict(require_groups=(_terms("skate", "skateboard", "running", "dance", "dancing", "sport", "action", "movement", "jump", "cycling", "nightlife"),), exclude_terms=_terms("couple", "romance", "beach romance", "portrait"), colors=("dark", "cold"), people="present"),
    "visual:warm_coastal_romance_light": dict(require_groups=(COAST, _terms("romance", "couple", "love", "girl", "woman", "man", "guy", "running", "walking")), exclude_terms=_terms("storm", "dark ocean", "sport", "skate"), colors=("light", "warm"), people="present"),
    "visual:ocean_storm_dark_cold": dict(require_groups=(COAST, _terms("storm", "rain", "fog", "mist", "night", "dark clouds", "rough waves")), exclude_terms=URBAN + VEHICLE + _terms("interior", "building", "house"), colors=("dark", "cold"), people="none"),
    "visual:nature_sunset_light_warm": dict(require_groups=(NATURE, _terms("sunset", "golden hour", "sunlight", "sunrise")), exclude_terms=BUILT + VEHICLE + MOUNTAIN, colors=("light", "warm"), people="none"),
    "visual:rain_nature_dark_cold": dict(require_groups=(NATURE, _terms("rain", "storm", "wet", "raindrops")), exclude_terms=URBAN + VEHICLE + _terms("interior"), colors=("dark", "cold"), people="none"),
    "visual:mountain_dark_cold": dict(require_groups=(MOUNTAIN,), exclude_terms=URBAN + VEHICLE + _terms("train", "interior"), colors=("dark", "cold"), people="none"),
    "visual:mountain_light_warm": dict(require_groups=(MOUNTAIN,), exclude_terms=URBAN + VEHICLE + _terms("train", "interior"), colors=("light", "warm"), people="none"),
    "visual:winter_nature_dark_cold": dict(require_groups=(NATURE, _terms("snow", "winter", "ice", "frost", "blizzard")), exclude_terms=URBAN + VEHICLE + _terms("interior"), colors=("dark", "cold"), people="none"),
    "visual:dark_interior_atmosphere": dict(require_groups=(_terms("interior", "room", "hallway", "bedroom", "empty room", "dark interior"),), exclude_terms=URBAN + VEHICLE + _terms("red", "orange", "yellow", "gold", "fire", "warm light"), colors=("dark", "cold"), people="none"),
    "visual:night_sky_space_dark_cold": dict(require_groups=(_terms("night sky", "stars", "starry sky", "moon", "milky way"),), exclude_terms=DIGITAL + URBAN + _terms("space animation", "planet animation"), colors=("dark", "cold"), people="none"),
}

# The photo pool is much denser than footage for a few broad visual contracts.
# Keep the canonical footage rules untouched and require extra semantic anchors
# only when the picker is drawing stills. These were calibrated against the
# 2026-07-16 photo snapshot: broad pools 411/282/198 -> about 96/127/104.
PHOTO_REQUIRE_GROUPS: Mapping[str, Tuple[Tuple[str, ...], ...]] = {
    "visual:digital_human_silhouette_cold": (
        _terms("digital", "glowing", "neon", "abstract", "blue lighting"),
    ),
    # Same digital anchor as the cold sibling, minus the cold-only "blue lighting".
    # Calibrated on the 2026-07-17 photo snapshot: without it the warm silhouette
    # bucket was 118 stills of which 96 were beach/sunset silhouettes (sunset ×72,
    # golden hour ×69, ocean ×30) — a sunset dumping ground, not digital
    # silhouettes. The anchor drops it to the ~14 genuine digital-warm silhouettes;
    # thin-but-honest (grow or drop is a base decision, not a contract one).
    "visual:digital_human_silhouette_warm": (
        _terms("digital", "glowing", "neon", "led", "abstract", "light trail"),
    ),
    "visual:urban_solitude_dark": (
        _terms("night city", "nighttime"),
    ),
    "visual:solitary_person_dark_cold": (
        _terms("alone", "solitude", "lonely figure", "solo"),
        _terms("indoor", "room", "interior", "urban", "city", "night"),
    ),
}

# Themes that must stay APART in the photo pool.
#
# A still carries no motion, so a tag set that reads unambiguously on footage goes
# flat on a photo: "nightlife" on a moving clip is people out at night, on a still
# it is just as often an empty decaying facade; "romance" on a clip is two people
# interacting, on a still it can be one person in warm light. The footage rules
# stay untouched — these exclusions apply only when the picker draws stills, and
# like the photo anchors they are semantic gates a manual source review cannot
# override.
DECAY = _terms(
    "decay", "urban decay", "abandoned", "abandoned building", "derelict",
    "dilapidated", "ruins", "rubble", "demolition", "wreckage", "rust",
)
ROMANCE = _terms("romance", "romantic", "kiss", "hug", "embrace", "intimacy", "love", "date")
SOLITUDE = _terms("alone", "solitude", "lonely", "lonely figure", "solo", "loneliness")
PORTRAIT = _terms("portrait", "face", "close up", "close-up", "headshot", "selfie", "plain portrait")

# Per-bucket exclusions calibrated on the 2026-07-17 photo snapshot (2341 stills).
# For each bucket we picked ONE leading semantic unit (comment) and strip the tags
# that pull a neighbouring theme in — the goal is one theme leading, not a broad
# "close enough" pool. Each list was tuned to shed roughly a quarter to a third of
# the bucket (total ~36%); when a tag was ambiguous we cut it (thin-but-pure beats
# wide-but-mixed). Substring match (_matches), photo-only, unreviewable-override.
PHOTO_EXCLUDE_TERMS: Mapping[str, Tuple[str, ...]] = {
    # warm sunset NATURE landscape (no people): drop human subjects + dark markers
    "visual:nature_sunset_light_warm": _terms(
        "silhouette", "lonely figure", "lonely", "alone", "dark forest",
        "dark atmosphere", "dark sky",
    ),
    # a lone PERSON in a dark/cold moody setting: drop coastal / vehicle / romance / fashion
    "visual:solitary_person_dark_cold": ROMANCE + _terms(
        "couple", "beach", "ocean", "sea", "coast", "water", "waves",
        "car interior", "night drive", "night drift", "jewelry",
    ),
    # dark empty NORMAL interior (no people): drop decay/industrial + outdoor + digital.
    # (keep silhouette/dim/shadows — that IS the dark-interior look.)
    "visual:dark_interior_atmosphere": _terms(
        "abandoned", "ruins", "derelict", "destruction", "rubble", "tunnel",
        "control room", "monitor wall", "office", "trees", "forest", "ocean",
        "mountain", "dark landscape", "night sky", "stars", "wet road",
        "abstract", "digital art", "distortion", "glitch",
    ),
    # cold digital/neon human SILHOUETTE: drop generic portrait / romance / performance /
    # nature / indoor-domestic / vehicle
    "visual:digital_human_silhouette_cold": PORTRAIT + _terms(
        "couple", "intimate", "romance", "night club", "dance floor", "dance",
        "night beach", "field", "empty road", "bedroom", "dark room",
        "car interior", "water", "stormy weather",
    ),
    # warm digital SILHOUETTE: drop generic portrait + the "two mages" / romance / fire
    "visual:digital_human_silhouette_warm": PORTRAIT + _terms(
        "couple", "romance", "romantic", "intimacy", "fire",
    ),
    # empty NIGHT CITY (no people): drop decay / coastal-nature / people
    "visual:urban_solitude_dark": _terms(
        "abandoned", "ruins", "silhouette", "lonely figure", "alone", "beach",
        "palm trees", "trees", "waterfront", "airplane",
    ),
    # rainy/weathery NIGHT CITY (no people): drop nature
    "visual:urban_weather_dark": _terms("silhouette", "trees", "palm trees", "forest"),
    # strictly foggy dark FOREST: drop buildings/decay + other landforms + interior mislabels
    # (keep forest roads + tree silhouettes — that is the vibe)
    "visual:forest_fog_dark": _terms(
        "abandoned", "old architecture", "architecture", "mountain", "bedroom",
        "dark interior", "ruins",
    ),
    # rainy dark NATURAL landscape: drop urban + buildings/decay
    "visual:rain_nature_dark_cold": _terms(
        "urban", "city", "street", "building", "abandoned", "architecture",
        "silhouette", "lonely figure",
    ),
    # dark cold MOUNTAINS: drop buildings/decay + sea (snow/forest on a mountain is fine)
    "visual:mountain_dark_cold": _terms(
        "castle", "architecture", "ruins", "abandoned", "ocean", "sea",
        "red lights", "glowing",
    ),
    # light warm MOUNTAINS: drop buildings + open sea (a mountain lake is fine)
    "visual:mountain_light_warm": _terms("castle", "architecture", "ocean", "sea", "coast"),
    # dark stormy OCEAN: drop other landforms / roads / vehicle
    "visual:ocean_storm_dark_cold": _terms("mountain", "architecture", "wet road", "night drive"),
    # dark WINTER nature: drop non-winter sea + urban light glow
    "visual:winter_nature_dark_cold": _terms("ocean", "coast", "red lights", "glowing"),
    # NIGHT SKY / stars (sky-dominant): drop foreground interior / buildings / road
    "visual:night_sky_space_dark_cold": _terms(
        "dark interior", "interior", "castle", "ruins", "architecture",
        "abandoned", "night drive", "road",
    ),
    # car in motion at night: drop aircraft
    "visual:vehicle_motion_aesthetic": _terms("airplane", "aircraft"),
    # warm COUPLE by the water: drop dark/night markers
    "visual:warm_coastal_romance_light": _terms("dark atmosphere", "wet road", "night"),
    # LIGHT warm loving COUPLE (daytime/golden): drop solitude + dark/night/fantasy/urban
    "visual:couple_intimacy_light_warm": SOLITUDE + _terms(
        "night", "silhouette", "dark forest", "dark sky", "dark atmosphere",
        "fire", "castle", "black and white", "glowing", "dim lighting", "rain",
        "wet road", "city",
    ),
    # CROWD / stage / club performance: drop decay + solo-portrait / vehicle / luxury / plain street
    "visual:performance_crowd_dark": DECAY + _terms(
        "private jet", "car interior", "portrait", "close-up", "close up",
        "jewelry", "street scene", "city life",
    ),
    # a PERSON in motion / nightlife: drop decay + vehicles + pure crowd/audience (crowd bucket)
    "visual:active_life_dark_cold": DECAY + _terms(
        "sports car", "car", "cars", "night drift", "vehicle", "crowd", "audience",
    ),
    # girl PORTRAIT (light warm): drop silhouette (not a face) + dark leak
    "visual:girls_portrait_light_warm": _terms(
        "silhouette", "dark atmosphere", "dark interior", "dim lighting",
    ),
    # girl PORTRAIT (dark cold): drop silhouette (hides the face) + romance/couple
    "visual:girls_portrait_dark_cold": _terms(
        "silhouette", "intimate", "intimacy", "tender", "couple",
    ),
}


def load_visual_catalog(path: Path = CATALOG_PATH) -> List[VisualContract]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: List[VisualContract] = []
    for row in raw.get("buckets") or []:
        bid = str(row.get("bucket_id") or "").strip()
        rule = dict(RULES.get(bid) or {})
        if not rule:
            raise RuntimeError(f"visual contract has no rules: {bid}")
        out.append(VisualContract(
            bucket_id=bid, label=str(row.get("label_ru") or bid),
            potential=int(row.get("potential") or 0),
            sources=tuple(str(x) for x in (row.get("sources") or [])), **rule,
        ))
    if set(RULES) != {x.bucket_id for x in out}:
        raise RuntimeError("visual contract rules and catalog ids differ")
    return out


def load_theme_buckets(path: Path = CATALOG_PATH) -> Dict[str, List[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): [str(x) for x in v] for k, v in (raw.get("theme_buckets") or {}).items()}


def _matches(tags: Sequence[str], term: str) -> bool:
    needle = _norm(term)
    return any(needle == tag or needle in tag for tag in tags)


def evaluate_asset(
    contract: VisualContract,
    asset: Mapping[str, Any],
    *,
    media_type: str = "video",
) -> Tuple[bool, str, Dict[str, Any]]:
    """Return eligibility, rejection stage and compact diagnostics."""
    file_name = str(asset.get("file_name") or asset.get("video_key") or "")
    clip_id = Path(file_name).stem
    tags = tuple(_norm(x) for x in (asset.get("meta_theme_tags") or asset.get("theme_tags") or []) if _norm(x))
    people = _norm(asset.get("meta_people_type") or asset.get("people_type"))
    color = _norm(asset.get("meta_color_tone") or asset.get("color_tone"))
    diag = {"bucket_id": contract.bucket_id, "clip_id": clip_id, "color": color, "people": people}
    if clip_id in QUALITY_REJECT_IDS:
        return False, "quality_override", diag
    if any(_matches(tags, x) for x in TEXT_TERMS):
        return False, "visible_text", diag
    if contract.colors:
        normalized_color = "light" if color == "neutral" else color
        if normalized_color not in contract.colors:
            return False, "color", diag
    if any(_matches(tags, x) for x in contract.hard_exclude_terms):
        return False, "hard_semantic_exclude", diag
    hard_matched = []
    for group in contract.hard_require_groups:
        hits = [x for x in group if _matches(tags, x)]
        if not hits:
            return False, "hard_missing_anchor", {**diag, "matched_groups": hard_matched}
        hard_matched.append(hits[:3])
    if _norm(media_type) == "photo":
        for term in PHOTO_EXCLUDE_TERMS.get(contract.bucket_id, ()):
            if _matches(tags, term):
                return False, "photo_semantic_exclude", {**diag, "matched_term": term}
        photo_matched = []
        for group in PHOTO_REQUIRE_GROUPS.get(contract.bucket_id, ()):
            hits = [x for x in group if _matches(tags, x)]
            if not hits:
                return False, "photo_missing_anchor", {
                    **diag,
                    "matched_groups": hard_matched + photo_matched,
                }
            photo_matched.append(hits[:3])
        hard_matched.extend(photo_matched)
    if clip_id in contract.sources:
        # Review can override noisy tags/people, never global or hard semantic gates.
        return True, "reviewed_source", {**diag, "matched_groups": hard_matched + [["manual_review"]]}
    if contract.people == "none" and people not in NONE:
        return False, "people", diag
    if contract.people == "present" and people in NONE + ("",):
        return False, "people", diag
    if contract.people == "girls" and people not in GIRLS:
        return False, "people", diag
    if any(_matches(tags, x) for x in contract.exclude_terms):
        return False, "semantic_exclude", diag
    matched = []
    for group in contract.require_groups:
        hits = [x for x in group if _matches(tags, x)]
        if not hits:
            return False, "missing_anchor", {**diag, "matched_groups": matched}
        matched.append(hits[:3])
    return True, "eligible", {**diag, "matched_groups": matched}


def eligible_assets(contract: VisualContract, assets: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for asset in assets:
        ok, _, diag = evaluate_asset(contract, asset)
        if ok:
            row = dict(asset)
            row["_visual_contract"] = diag
            out.append(row)
    return out
