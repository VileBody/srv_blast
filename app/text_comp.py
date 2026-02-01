from __future__ import annotations

from typing import Any, Dict, List

from app.orchestrator import ProjectOrchestrator


def _normalize_layer_dict(l: Dict[str, Any], *, text_comp_name: str, mine_comp_name: str) -> None:
    td = l.get("text_data") or {}
    meta = td.get("layer_meta") or {}
    td["layer_meta"] = meta
    l["text_data"] = td

    meta.setdefault("comp_name_target", text_comp_name)

    # legacy mine-inner routing
    if meta.get("comp_id_target") == 88:
        meta["comp_name_target"] = mine_comp_name

    # new by-name video -> precomp
    if l.get("type") == "video":
        comp_name = l.get("comp_name")
        if isinstance(comp_name, str) and comp_name.strip():
            l["type"] = "precomp"
            td["precomp_source"] = {"comp_name": comp_name}
            meta["comp_name_target"] = text_comp_name

    # legacy by-id video -> precomp (both Mine layers)
    if l.get("type") == "video" and int(l.get("comp_id") or 0) == 88:
        l["type"] = "precomp"
        l["comp_name"] = mine_comp_name
        td["precomp_source"] = {"comp_name": mine_comp_name}
        meta["comp_name_target"] = text_comp_name


def build_text_layers(*, full_edit_config: Dict[str, Any], text_comp_name: str, mine_comp_name: str) -> List[Dict[str, Any]]:
    orch = ProjectOrchestrator(full_edit_config)
    orch.build()

    layers: List[Dict[str, Any]] = list(orch.final_stack)

    for l in layers:
        # precomp node: нормализуем ВНУТРЕННИЕ слои тоже (на будущее, и чтобы было железобетонно)
        if isinstance(l, dict) and l.get("type") == "precomp" and isinstance(l.get("comp"), dict):
            inner = l.get("layers")
            if isinstance(inner, list):
                for it in inner:
                    if isinstance(it, dict):
                        _normalize_layer_dict(it, text_comp_name=text_comp_name, mine_comp_name=mine_comp_name)
            continue

        if isinstance(l, dict):
            _normalize_layer_dict(l, text_comp_name=text_comp_name, mine_comp_name=mine_comp_name)

    return layers
