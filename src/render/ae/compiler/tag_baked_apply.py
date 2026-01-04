from __future__ import annotations

import os
import logging
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from src.config.styles.paths import get_style_paths

log = logging.getLogger(__name__)
_DEBUG = os.getenv("TAG_APPLY_DEBUG", "").strip() in {"1", "true", "TRUE", "yes", "YES"}
_STATS = os.getenv("TAG_APPLY_STATS", "").strip() in {"1", "true", "TRUE", "yes", "YES"}
_STRICT = os.getenv("TAG_APPLY_STRICT", "").strip() in {"1", "true", "TRUE", "yes", "YES"}


def _safe_read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_relpath_to_segments(rel_path: str) -> List[Union[str, int]]:
    out: List[Union[str, int]] = []
    for seg in (rel_path or "").strip("/").split("/"):
        if not seg:
            continue
        if "#" in seg:
            name, idx = seg.split("#", 1)
            out.append(name)
            try:
                out.append(int(idx))
            except Exception:
                pass
        else:
            out.append(seg)
    return out


def _leaf_key(meta: Dict[str, Any], segs: List[Union[str, int]]) -> Optional[str]:
    """
    Prefer canonical matchName from canonical_debug.
    Strict mode: if matchName missing -> None (skip).
    Non-strict: fallback to last string segment from relPath.
    """

    m = meta.get("matchName") or ""
    if isinstance(m, str) and m.strip():
        return m.strip()
    if _STRICT:
        return None
    for s in reversed(segs):
        if isinstance(s, str) and s.strip():
            return s.strip()
    return None


@dataclass
class ApplyStats:
    # effects
    fx_objects: int = 0
    fx_params_total: int = 0
    fx_params_applied: int = 0
    fx_params_missing: int = 0
    fx_params_skipped: int = 0

    # transform
    tr_layers: int = 0
    tr_assignments: int = 0
    tr_unknown: int = 0

    # textDoc
    td_layers: int = 0
    td_fields_total: int = 0
    td_fields_applied: int = 0
    td_fields_skipped: int = 0  # keyframed/expr/unknown fields ignored

    # textAnim
    ta_animators: int = 0
    ta_props: int = 0
    ta_sel_props: int = 0
    ta_sel_adv: int = 0
    ta_unknown: int = 0

    # layers processed
    layers_total: int = 0
    layers_with_baked: int = 0


class CanonIndex:
    def __init__(self) -> None:
        self.by_object: Dict[str, dict] = {}
        self.slug_meta: Dict[Tuple[str, str], dict] = {}

    def add_object(self, canonical_name: str, obj_json: dict) -> None:
        if not canonical_name:
            return
        self.by_object[canonical_name] = obj_json

        tpl = (obj_json.get("template") or {}).get("params") or []
        for p in tpl:
            if not isinstance(p, dict):
                continue
            slug = p.get("slug")
            rel = p.get("relPath") or ""
            trip = p.get("sampleTriplet") or {}
            m = trip.get("matchName") or ""
            if slug:
                self.slug_meta[(canonical_name, slug)] = {"relPath": rel, "matchName": m}

        kf = (obj_json.get("keyframes") or {}).get("params") or []
        for p in kf:
            if not isinstance(p, dict):
                continue
            slug = p.get("slug")
            rel = p.get("relPath") or ""
            trip = p.get("sampleTriplet") or {}
            m = trip.get("matchName") or ""
            if slug:
                self.slug_meta[(canonical_name, slug)] = {"relPath": rel, "matchName": m}


def _load_canon_index(style_id: Optional[str], domain: str, role: str) -> CanonIndex:
    paths = get_style_paths(style_id)
    base = paths["canonical_debug_dir"]
    idx = CanonIndex()
    if not isinstance(base, Path):
        return idx
    canon_dir = base / role / domain / "canonical_representation"
    if not canon_dir.is_dir():
        return idx
    for p in sorted(canon_dir.glob("*.json")):
        obj = _safe_read_json(p)
        cn = obj.get("canonicalName") or ""
        if cn:
            idx.add_object(cn, obj)
    return idx


def _is_missing_payload(v: Any) -> bool:
    return v == {"__MISSING__": True} or (isinstance(v, dict) and v.get("__MISSING__") is True)


def _used_map_from_used_file(doc: dict) -> Dict[str, Any]:
    used = doc.get("used")
    return used if isinstance(used, dict) else {}


def _iter_kf_tree(tree: Any) -> List[Tuple[str, Any]]:
    out: List[Tuple[str, Any]] = []

    def rec(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if "__slug__" in node and "payload" in node:
            slug = node.get("__slug__")
            payload = node.get("payload")
            if isinstance(slug, str):
                out.append((slug, payload))
        for v in node.values():
            if isinstance(v, dict):
                rec(v)

    rec(tree)
    return out


def _merge_template_and_keyframes(tpl_map: Dict[str, Any], kf_tree: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for slug, payload in (tpl_map or {}).items():
        if _is_missing_payload(payload):
            continue
        merged[slug] = payload
    if isinstance(kf_tree, dict):
        for slugt, payload in _iter_kf_tree(kf_tree):
            if _is_missing_payload(payload):
                continue
            merged[slugt] = payload
    return merged


def _build_effects_from_baked(
    baked: Dict[str, Any],
    style_id: Optional[str],
    role: str,
    stats: Optional[ApplyStats] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    dom = baked.get("effects") or {}
    tpl_doc = dom.get("template") or {}
    kf_doc = dom.get("keyframes") or {}
    tpl_used = _used_map_from_used_file(tpl_doc)
    canon = _load_canon_index(style_id, "effects", role)
    kf_trees = kf_doc.get("used") if isinstance(kf_doc, dict) else {}

    for obj_name, slug_map in tpl_used.items():
        if stats:
            stats.fx_objects += 1
        if not isinstance(slug_map, dict):
            continue
        obj_canon = canon.by_object.get(obj_name) or {}
        match_name = obj_canon.get("groupKey") or obj_canon.get("matchName") or ""
        if not match_name:
            match_name = obj_canon.get("canonicalName") or obj_name

        tree = (kf_trees or {}).get(obj_name)
        merged = _merge_template_and_keyframes(slug_map, tree)

        params_list: List[Dict[str, Any]] = []
        applied = 0
        skipped = 0
        missing = 0
        total = 0
        for slug, payload in merged.items():
            total += 1
            meta = canon.slug_meta.get((obj_name, slug)) or {}
            rel_path = meta.get("relPath") or ""
            if not rel_path and _STRICT:
                skipped += 1
                missing += 1
                continue

            segs = _parse_relpath_to_segments(rel_path) if rel_path else []
            if rel_path:
                path = segs
            else:
                # non-strict fallback
                lk = _leaf_key(meta, segs) or str(slug)
                path = [lk]

            if not path:
                skipped += 1
                continue
            if not rel_path:
                missing += 1
            params_list.append({"path": path, "value": payload})
            applied += 1

        if _DEBUG:
            log.debug(
                "[tag_apply][effects] fx=%s matchName=%s params=%d (applied=%d skipped=%d)",
                obj_name,
                match_name,
                len(params_list),
                applied,
                skipped,
            )
        if stats:
            stats.fx_params_total += total
            stats.fx_params_applied += applied
            stats.fx_params_skipped += skipped
            stats.fx_params_missing += missing
        out.append({"matchName": match_name, "params": params_list})

    return out


def _apply_transform_from_baked(
    layer: Dict[str, Any],
    baked: Dict[str, Any],
    style_id: Optional[str],
    role: str,
    stats: Optional[ApplyStats] = None,
) -> None:
    dom = baked.get("transform") or {}
    tpl_doc = dom.get("template") or {}
    kf_doc = dom.get("keyframes") or {}
    tpl_used = _used_map_from_used_file(tpl_doc)
    kf_trees = (kf_doc.get("used") or {}) if isinstance(kf_doc, dict) else {}
    canon = _load_canon_index(style_id, "transform", role)
    if not tpl_used:
        return
    if stats:
        stats.tr_layers += 1
    obj_name = next(iter(tpl_used.keys()))
    tree = (kf_trees or {}).get(obj_name)
    merged = _merge_template_and_keyframes((tpl_used.get(obj_name) or {}), tree)
    tr: Dict[str, Any] = layer.get("transform") if isinstance(layer.get("transform"), dict) else {}
    applied = 0
    unknown = 0
    for slug, payload in merged.items():
        meta = canon.slug_meta.get((obj_name, slug)) or {}
        m_raw = (meta.get("matchName") or "")
        if not m_raw and _STRICT:
            unknown += 1
            continue
        m = str(m_raw).lower()
        if "position" in m:
            tr["position"] = payload
            applied += 1
        elif "scale" in m:
            tr["scale"] = payload
            applied += 1
        elif "rotation" in m:
            tr["rotation"] = payload
            applied += 1
        elif "opacity" in m:
            tr["opacity"] = payload
            applied += 1
        else:
            unknown += 1
    if tr:
        layer["transform"] = tr
    if _DEBUG:
        log.debug("[tag_apply][transform] layer=%s applied=%d", layer.get("name"), applied)
    if stats:
        stats.tr_assignments += applied
        stats.tr_unknown += unknown


def _apply_textdoc_from_baked(layer: Dict[str, Any], baked: Dict[str, Any], stats: Optional[ApplyStats] = None) -> None:
    dom = baked.get("textDoc") or {}
    tpl_doc = dom.get("template") or {}
    tpl_used = _used_map_from_used_file(tpl_doc)
    if not tpl_used:
        return
    if stats:
        stats.td_layers += 1
    obj_name = next(iter(tpl_used.keys()))
    fields = tpl_used.get(obj_name) or {}
    if not isinstance(fields, dict):
        return
    td = layer.get("textDocument") if isinstance(layer.get("textDocument"), dict) else {"text": ""}
    real_text = td.get("text")
    prefix = "ADBE_Text_Document.value."
    applied = 0
    skipped = 0
    total = 0
    # Collect indexed colors: fillColor[0..2], strokeColor[0..2]
    fill_parts: Dict[int, float] = {}
    stroke_parts: Dict[int, float] = {}

    def parse_indexed_color(key: str) -> Optional[Tuple[str, int]]:
        # e.g. "fillColor[2]" -> ("fillColor", 2)
        if not key.endswith("]") or "[" not in key:
            return None
        base, idx_s = key[:-1].split("[", 1)
        base = base.strip()
        try:
            idx = int(idx_s)
        except Exception:
            return None
        if base not in {"fillColor", "strokeColor"}:
            return None
        if idx < 0 or idx > 3:
            return None
        return base, idx

    for slug, payload in fields.items():
        total += 1
        if not isinstance(slug, str) or not slug.startswith(prefix):
            skipped += 1
            continue
        key = slug[len(prefix) :]

        # Hard rule: __MISSING__ NEVER propagates
        if _is_missing_payload(payload):
            skipped += 1
            continue

        # Handle fillColor[0]/strokeColor[0] style keys
        idx_info = parse_indexed_color(key)
        if idx_info is not None:
            base, idx = idx_info
            if isinstance(payload, (int, float)):
                if base == "fillColor":
                    fill_parts[idx] = float(payload)
                else:
                    stroke_parts[idx] = float(payload)
                applied += 1
            else:
                skipped += 1
            continue

        # strict: only whitelist fields we know how to apply
        # (keyframed textDocument fields are ignored here intentionally)
        # text itself already comes from layer.textDocument.text
        if key in {
            "font",
            "fontSize",
            "tracking",
            "leading",
            "justification",
            "applyFill",
            "fillColor",
            "applyStroke",
            "strokeColor",
            "strokeWidth",
        }:
            if isinstance(payload, dict) and ("keys" in payload or "expression" in payload):
                skipped += 1
                continue

            # Normalize justification if it comes as numeric string (canonical_debug sometimes does)
            if key == "justification" and isinstance(payload, str) and payload.strip().isdigit():
                payload = int(payload.strip())

            td[key] = payload
            applied += 1
        else:
            skipped += 1

    # Materialize collected colors
    def materialize_color(existing: Any, parts: Dict[int, float]) -> Optional[List[float]]:
        if not parts:
            return None
        base: List[float]
        if isinstance(existing, list) and len(existing) >= 3:
            base = [float(existing[0]), float(existing[1]), float(existing[2])]
        else:
            base = [1.0, 1.0, 1.0]
        for i, v in parts.items():
            if 0 <= i <= 2:
                base[i] = float(v)
        return base

    fill = materialize_color(td.get("fillColor"), fill_parts)
    if fill is not None:
        td["fillColor"] = fill
    stroke = materialize_color(td.get("strokeColor"), stroke_parts)
    if stroke is not None:
        td["strokeColor"] = stroke
    if real_text is not None:
        td["text"] = real_text
    layer["textDocument"] = td
    if _DEBUG:
        log.debug("[tag_apply][textDoc] layer=%s applied=%d", layer.get("name"), applied)
    if stats:
        stats.td_fields_total += total
        stats.td_fields_applied += applied
        stats.td_fields_skipped += skipped


def _route_textanim_param(segs: List[Union[str, int]]) -> Tuple[str, Optional[int], Optional[str]]:
    if not segs:
        return ("unknown", None, None)
    if segs[0] == "ADBE Text Animator Properties":
        return ("animator_prop", None, None)
    if segs[0] == "ADBE Text Selectors":
        selector_match = segs[1] if (len(segs) >= 2 and isinstance(segs[1], str)) else None
        selector_idx = None
        for s in segs:
            if isinstance(s, int):
                selector_idx = s
                break
        if "ADBE Text Selector Advanced" in segs or "ADBE Text Range Advanced" in segs:
            return ("selector_adv", selector_idx, selector_match)
        return ("selector_prop", selector_idx, selector_match)
    return ("unknown", None, None)


def _build_text_animators_from_baked(
    baked: Dict[str, Any],
    style_id: Optional[str],
    role: str,
    stats: Optional[ApplyStats] = None,
) -> List[Dict[str, Any]]:
    dom = baked.get("textAnim") or {}
    tpl_doc = dom.get("template") or {}
    kf_doc = dom.get("keyframes") or {}
    tpl_used = _used_map_from_used_file(tpl_doc)
    kf_trees = (kf_doc.get("used") or {}) if isinstance(kf_doc, dict) else {}
    canon = _load_canon_index(style_id, "textAnim", role)
    animators_out: List[Dict[str, Any]] = []
    for obj_name, slug_map in tpl_used.items():
        if not isinstance(slug_map, dict):
            continue
        if stats:
            stats.ta_animators += 1
        tree = (kf_trees or {}).get(obj_name)
        merged = _merge_template_and_keyframes(slug_map, tree)
        animator = {"name": obj_name, "properties": {}, "selectors": []}
        sels: Dict[int, Dict[str, Any]] = {}
        applied_props = applied_sel_props = applied_sel_adv = unknown = 0
        for slug, payload in merged.items():
            meta = canon.slug_meta.get((obj_name, slug)) or {}
            rel = meta.get("relPath") or ""
            segs = _parse_relpath_to_segments(rel)
            leaf_match = _leaf_key(meta, segs)
            if leaf_match is None:
                # strict: skip; non-strict: _leaf_key already tried relPath fallback
                unknown += 1
                continue
            kind, sel_idx, sel_match = _route_textanim_param(segs)
            if kind == "animator_prop":
                if leaf_match:
                    animator["properties"][leaf_match] = payload
                    applied_props += 1
                else:
                    unknown += 1
            elif kind in {"selector_prop", "selector_adv"}:
                if sel_idx is None:
                    sel_idx = 1
                sel = sels.setdefault(
                    sel_idx,
                    {
                        "matchName": sel_match or "ADBE Text Selector",
                        "name": (sel_match or "ADBE Text Selector") + "#" + str(sel_idx),
                        "properties": {},
                        "advanced": {},
                    },
                )
                if sel_match:
                    sel["matchName"] = sel_match
                if not leaf_match:
                    unknown += 1
                    continue
                if kind == "selector_adv":
                    sel["advanced"][leaf_match] = payload
                    applied_sel_adv += 1
                else:
                    sel["properties"][leaf_match] = payload
                    applied_sel_props += 1
            else:
                # strict: don't silently stuff into animator.properties
                # non-strict: we can still try to keep it
                if not _STRICT:
                    animator["properties"][leaf_match] = payload
                unknown += 1
        if sels:
            animator["selectors"] = [sels[i] for i in sorted(sels.keys())]
        animators_out.append(animator)
        if _DEBUG:
            log.debug(
                "[tag_apply][textAnim] animator=%s props=%d sel_props=%d sel_adv=%d unknown=%d",
                obj_name,
                applied_props,
                applied_sel_props,
                applied_sel_adv,
                unknown,
            )
        if stats:
            stats.ta_props += applied_props
            stats.ta_sel_props += applied_sel_props
            stats.ta_sel_adv += applied_sel_adv
            stats.ta_unknown += unknown
    return animators_out


def apply_tag_baked_to_layers(items: List[Dict[str, Any]], *, style_id: Optional[str]) -> int:
    stats = ApplyStats()
    changed = 0
    for it in items:
        if (it.get("type") or "").lower() != "comp":
            continue
        layers = it.get("layers") or []
        if not isinstance(layers, list):
            continue
        for layer in layers:
            stats.layers_total += 1
            if not isinstance(layer, dict):
                continue
            baked = layer.get("tagBaked")
            if not isinstance(baked, dict):
                continue
            stats.layers_with_baked += 1
            ltype = (layer.get("type") or "").lower()
            role = "text_layers" if ltype == "text" else ("adj_text" if ltype == "adjustment" else "")
            if not role:
                continue
            before_fx = len(layer.get("effects") or []) if isinstance(layer.get("effects"), list) else 0
            before_an = len(layer.get("textAnimators") or []) if isinstance(layer.get("textAnimators"), list) else 0
            fx = _build_effects_from_baked(baked, style_id, role, stats=stats)
            if fx:
                layer["effects"] = fx
            _apply_transform_from_baked(layer, baked, style_id, role, stats=stats)
            if ltype == "text":
                _apply_textdoc_from_baked(layer, baked, stats=stats)
                anims = _build_text_animators_from_baked(baked, style_id, "text_layers", stats=stats)
                if anims:
                    layer["textAnimators"] = anims
            after_fx = len(layer.get("effects") or []) if isinstance(layer.get("effects"), list) else 0
            after_an = len(layer.get("textAnimators") or []) if isinstance(layer.get("textAnimators"), list) else 0
            log.info(
                "[tag_apply] layer=%s type=%s effects=%d->%d animators=%d->%d",
                layer.get("name") or layer.get("textDocument", {}).get("text", "<unnamed>"),
                ltype,
                before_fx,
                after_fx,
                before_an,
                after_an,
            )
            # Do not leak raw baked blobs further.
            layer.pop("tagBaked", None)
            changed += 1

    if _STATS:
        log.info(
            "[tag_apply][stats] layers: total=%d with_tagBaked=%d changed=%d",
            stats.layers_total,
            stats.layers_with_baked,
            changed,
        )
        log.info(
            "[tag_apply][stats] effects: objects=%d params_total=%d applied=%d skipped=%d missing_map=%d",
            stats.fx_objects,
            stats.fx_params_total,
            stats.fx_params_applied,
            stats.fx_params_skipped,
            stats.fx_params_missing,
        )
        log.info(
            "[tag_apply][stats] transform: layers=%d assignments=%d unknown=%d",
            stats.tr_layers,
            stats.tr_assignments,
            stats.tr_unknown,
        )
        log.info(
            "[tag_apply][stats] textDoc: layers=%d fields_total=%d applied=%d skipped=%d",
            stats.td_layers,
            stats.td_fields_total,
            stats.td_fields_applied,
            stats.td_fields_skipped,
        )
        log.info(
            "[tag_apply][stats] textAnim: animators=%d props=%d sel_props=%d sel_adv=%d unknown=%d",
            stats.ta_animators,
            stats.ta_props,
            stats.ta_sel_props,
            stats.ta_sel_adv,
            stats.ta_unknown,
        )
    return changed
