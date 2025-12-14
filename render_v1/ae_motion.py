from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, List, Optional, Tuple

# -----------------------------
# matchNamePath parsing w/ indexing: "ADBE Text Animator[2]"
# -----------------------------

_SEG_RE = re.compile(r"^(?P<name>.+?)(?:\[(?P<idx>\d+)\])?$")


def parse_segment(seg: str) -> Tuple[str, Optional[int]]:
    """Return (matchName, index_1based).

    Examples:
        "ADBE Text Animator"    -> ("ADBE Text Animator", None)
        "ADBE Text Animator[2]" -> ("ADBE Text Animator", 2)
    """
    seg = (seg or "").strip()
    m = _SEG_RE.match(seg)
    if not m:
        return seg, None
    name = (m.group("name") or "").strip()
    idx = m.group("idx")
    return name, (int(idx) if idx is not None else None)


def _is_value_data_dict(v: Any) -> bool:
    """Heuristic: dict is a value payload (keys/expression/value/procedural), not a nested AE group."""
    return isinstance(v, dict) and (
        "keys" in v
        or "expression" in v
        or "value" in v
        or "procedural" in v
    )


def normalize_property_tree(node: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize preset trees:

    If node.properties contains nested group dicts, convert them into children nodes.
    This keeps the JSX engine simpler: it applies a uniform matchName tree.
    """
    if not isinstance(node, dict):
        return node

    children = list(node.get("children") or [])
    props = dict(node.get("properties") or {})

    new_props: Dict[str, Any] = {}
    for k, v in props.items():
        if isinstance(v, dict) and not _is_value_data_dict(v):
            children.append({"matchName": k, "properties": v})
        else:
            new_props[k] = v

    node["properties"] = new_props if new_props else None
    node["children"] = children if children else None

    if node.get("children"):
        node["children"] = [normalize_property_tree(c) for c in node["children"]]

    if node.get("properties") is None:
        node.pop("properties", None)
    if node.get("children") is None:
        node.pop("children", None)

    return node


def _tree_find_or_create_child(
    node: Dict[str, Any],
    match_name: str,
    index_1based: int = 1,
) -> Dict[str, Any]:
    """Return the N-th (1-based) child with matchName, creating siblings if needed."""
    if index_1based < 1:
        index_1based = 1

    children = node.setdefault("children", [])
    hits = [ch for ch in children if isinstance(ch, dict) and ch.get("matchName") == match_name]

    while len(hits) < index_1based:
        new_ch = {"matchName": match_name}
        children.append(new_ch)
        hits.append(new_ch)

    return hits[index_1based - 1]


def tree_set_value(root: Dict[str, Any], match_name_path: str, value: Any) -> None:
    """Set a property value by matchNamePath (supports [index] segments)."""
    segs = [s for s in (match_name_path or "").split("/") if s]
    if not segs:
        return

    node = root

    root_name, _root_idx = parse_segment(segs[0])
    if node.get("matchName") == root_name:
        segs = segs[1:]
    if not segs:
        return

    for seg in segs[:-1]:
        name, idx = parse_segment(seg)
        node = _tree_find_or_create_child(node, name, idx or 1)

    leaf_name, _leaf_idx = parse_segment(segs[-1])
    props = node.setdefault("properties", {})
    props[leaf_name] = value


def expand_procedural(value_data: Any, *, layer_in: float, layer_out: float, fps: float) -> Any:
    """If value_data contains {'procedural': {...}}, bake to concrete keyframes."""
    if not isinstance(value_data, dict) or "procedural" not in value_data:
        return value_data

    spec = value_data.get("procedural") or {}
    kind = spec.get("kind")

    start = float(spec.get("startTime", layer_in))
    end = float(spec.get("endTime", layer_out))
    if end < start:
        start, end = end, start

    if kind == "ramp":
        v0 = spec.get("from", 0)
        v1 = spec.get("to", 100)
        tpl = spec.get("templateRef")
        return {
            "keys": [
                {"time": start, "value": v0, **({"templateRef": tpl} if tpl else {})},
                {"time": end, "value": v1, **({"templateRef": tpl} if tpl else {})},
            ]
        }

    if kind == "normalized_curve":
        pts = spec.get("points") or []
        tpl = spec.get("templateRef")
        dur = max(1e-6, end - start)
        keys = []
        for p in pts:
            t_norm = float(p.get("t", 0))
            v = p.get("value")
            t_abs = start + t_norm * dur
            k = {"time": t_abs, "value": v}
            if tpl:
                k["templateRef"] = tpl
            keys.append(k)
        return {"keys": keys}

    if kind == "oscillate":
        wave = spec.get("wave", "sine")
        freq = float(spec.get("frequencyHz", 2.0))
        amp = float(spec.get("amplitude", 10.0))
        off = float(spec.get("offset", 0.0))
        phase = float(spec.get("phase", 0.0))
        sample_rate = float(spec.get("sampleRate", fps))
        tpl = spec.get("templateRef")

        step = 1.0 / max(1.0, sample_rate)
        t = start
        keys = []
        while t <= end + 1e-6:
            x = 2.0 * math.pi * freq * (t - start) + phase
            if wave == "triangle":
                tri = 2.0 * abs(2.0 * ((x / (2.0 * math.pi)) % 1.0) - 1.0) - 1.0
                v = off + amp * tri
            else:
                v = off + amp * math.sin(x)

            k = {"time": t, "value": v}
            if tpl:
                k["templateRef"] = tpl
            keys.append(k)
            t += step

        return {"keys": keys}

    # Unknown kind: keep as-is (debug-friendly)
    return value_data


def resolve_preset_tree(
    preset: Dict[str, Any],
    overrides: Dict[str, Any],
    *,
    layer_in: float,
    layer_out: float,
    fps: float,
) -> Optional[Dict[str, Any]]:
    """Resolve a motion preset to a concrete property tree by applying overrides."""
    tree = preset.get("propertyTree")
    if not tree:
        return None

    tree = normalize_property_tree(copy.deepcopy(tree))

    exposed = preset.get("exposedParams") or []
    mapping: List[Tuple[str, str]] = []

    if isinstance(exposed, list):
        for rec in exposed:
            if not isinstance(rec, dict):
                continue
            k = rec.get("key")
            p = rec.get("matchNamePath")
            if k and p:
                mapping.append((str(k), str(p)))
    elif isinstance(exposed, dict):
        for k, p in exposed.items():
            mapping.append((str(k), str(p)))

    for k, path in mapping:
        if k not in overrides:
            continue
        v = expand_procedural(overrides[k], layer_in=layer_in, layer_out=layer_out, fps=fps)
        tree_set_value(tree, path, v)

    return tree

