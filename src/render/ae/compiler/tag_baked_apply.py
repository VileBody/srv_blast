from __future__ import annotations

import os
import logging
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from src.config.styles.paths import get_style_paths

log = logging.getLogger(__name__)
_DEBUG = os.getenv("TAG_APPLY_DEBUG", "").strip() in {"1", "true", "TRUE", "yes", "YES"}


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


def _build_effects_from_baked(baked: Dict[str, Any], style_id: Optional[str], role: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    dom = baked.get("effects") or {}
    tpl_doc = dom.get("template") or {}
    kf_doc = dom.get("keyframes") or {}
    tpl_used = _used_map_from_used_file(tpl_doc)
    canon = _load_canon_index(style_id, "effects", role)
    kf_trees = kf_doc.get("used") if isinstance(kf_doc, dict) else {}

    for obj_name, slug_map in tpl_used.items():
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
        for slug, payload in merged.items():
            meta = canon.slug_meta.get((obj_name, slug)) or {}
            rel_path = meta.get("relPath") or ""
            if rel_path:
                path = _parse_relpath_to_segments(rel_path)
            else:
                m = meta.get("matchName") or slug
                path = [m]
            if not path:
                skipped += 1
                continue
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
        out.append({"matchName": match_name, "params": params_list})

    return out


def _apply_transform_from_baked(layer: Dict[str, Any], baked: Dict[str, Any], style_id: Optional[str], role: str) -> None:
    dom = baked.get("transform") or {}
    tpl_doc = dom.get("template") or {}
    kf_doc = dom.get("keyframes") or {}
    tpl_used = _used_map_from_used_file(tpl_doc)
    kf_trees = (kf_doc.get("used") or {}) if isinstance(kf_doc, dict) else {}
    canon = _load_canon_index(style_id, "transform", role)
    if not tpl_used:
        return
    obj_name = next(iter(tpl_used.keys()))
    tree = (kf_trees or {}).get(obj_name)
    merged = _merge_template_and_keyframes((tpl_used.get(obj_name) or {}), tree)
    tr: Dict[str, Any] = layer.get("transform") if isinstance(layer.get("transform"), dict) else {}
    applied = 0
    for slug, payload in merged.items():
        meta = canon.slug_meta.get((obj_name, slug)) or {}
        m = (meta.get("matchName") or "").lower()
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
    if tr:
        layer["transform"] = tr
    if _DEBUG:
        log.debug("[tag_apply][transform] layer=%s applied=%d", layer.get("name"), applied)


def _apply_textdoc_from_baked(layer: Dict[str, Any], baked: Dict[str, Any]) -> None:
    dom = baked.get("textDoc") or {}
    tpl_doc = dom.get("template") or {}
    tpl_used = _used_map_from_used_file(tpl_doc)
    if not tpl_used:
        return
    obj_name = next(iter(tpl_used.keys()))
    fields = tpl_used.get(obj_name) or {}
    if not isinstance(fields, dict):
        return
    td = layer.get("textDocument") if isinstance(layer.get("textDocument"), dict) else {"text": ""}
    real_text = td.get("text")
    prefix = "ADBE_Text_Document.value."
    applied = 0
    for slug, payload in fields.items():
        if not isinstance(slug, str) or not slug.startswith(prefix):
            continue
        key = slug[len(prefix) :]
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
                continue
            td[key] = payload
            applied += 1
    if real_text is not None:
        td["text"] = real_text
    layer["textDocument"] = td
    if _DEBUG:
        log.debug("[tag_apply][textDoc] layer=%s applied=%d", layer.get("name"), applied)


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


def _build_text_animators_from_baked(baked: Dict[str, Any], style_id: Optional[str], role: str) -> List[Dict[str, Any]]:
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
        tree = (kf_trees or {}).get(obj_name)
        merged = _merge_template_and_keyframes(slug_map, tree)
        animator = {"name": obj_name, "properties": {}, "selectors": []}
        sels: Dict[int, Dict[str, Any]] = {}
        applied_props = applied_sel_props = applied_sel_adv = unknown = 0
        for slug, payload in merged.items():
            meta = canon.slug_meta.get((obj_name, slug)) or {}
            rel = meta.get("relPath") or ""
            leaf_match = meta.get("matchName") or ""
            segs = _parse_relpath_to_segments(rel)
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
                        "name": "sel#" + str(sel_idx),
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
                if leaf_match:
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
    return animators_out


def apply_tag_baked_to_layers(items: List[Dict[str, Any]], *, style_id: Optional[str]) -> int:
    changed = 0
    for it in items:
        if (it.get("type") or "").lower() != "comp":
            continue
        layers = it.get("layers") or []
        if not isinstance(layers, list):
            continue
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            baked = layer.get("tagBaked")
            if not isinstance(baked, dict):
                continue
            ltype = (layer.get("type") or "").lower()
            role = "text_layers" if ltype == "text" else ("adj_text" if ltype == "adjustment" else "")
            if not role:
                continue
            before_fx = len(layer.get("effects") or []) if isinstance(layer.get("effects"), list) else 0
            before_an = len(layer.get("textAnimators") or []) if isinstance(layer.get("textAnimators"), list) else 0
            fx = _build_effects_from_baked(baked, style_id, role)
            if fx:
                layer["effects"] = fx
            _apply_transform_from_baked(layer, baked, style_id, role)
            if ltype == "text":
                _apply_textdoc_from_baked(layer, baked)
                anims = _build_text_animators_from_baked(baked, style_id, "text_layers")
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
            changed += 1
    return changed
