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
CATALOG_VERSION = "semantic-v2-2026-07-13"


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
MOUNTAIN = _terms("mountain", "mountains", "cliff", "mountain view", "mountain road")

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
    "visual:couple_intimacy_light_warm": dict(require_groups=(_terms("couple", "romance", "romantic", "kiss", "hug", "intimacy", "love"),), exclude_terms=_terms("crowd", "performance", "sport"), colors=("light", "warm"), people="present"),
    "visual:solitary_person_dark_cold": dict(require_groups=(_terms("alone", "solitude", "lonely", "single person", "portrait", "man", "woman", "guy", "girl"),), exclude_terms=_terms("couple", "crowd", "group", "party", "performance"), colors=("dark", "cold"), people="present"),
    "visual:performance_crowd_dark": dict(require_groups=(_terms("concert", "performance", "stage", "crowd", "audience", "club", "party", "rave"),), colors=("dark",), people="present"),
    "visual:girls_portrait_light_warm": dict(require_groups=(_terms("portrait", "face", "close up", "close-up", "girl", "woman", "long hair", "casual pose"),), exclude_terms=_terms("sport", "skate", "running", "crowd", "couple"), colors=("light", "warm"), people="girls"),
    "visual:girls_portrait_dark_cold": dict(require_groups=(_terms("portrait", "face", "close up", "close-up", "girl", "woman", "long hair", "low light"),), exclude_terms=_terms("sport", "skate", "running", "crowd", "couple"), colors=("dark", "cold"), people="girls"),
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


def evaluate_asset(contract: VisualContract, asset: Mapping[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
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
    if clip_id in contract.sources:
        # Review can override noisy tags/people, never the global palette gate.
        return True, "reviewed_source", {**diag, "matched_groups": [["manual_review"]]}
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
