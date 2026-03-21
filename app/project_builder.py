# app/project_builder.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Tuple

from jinja2 import Environment, FileSystemLoader

from app.project_config import AE_PROJECT
from app.footage_comp import build_footage_layers, resolve_text_duration_sec
from app.text_comp import build_text_layers

LOGGER = logging.getLogger("app.project_builder")


def _apply_comp_duration_overrides(
    *,
    comps: list[Dict[str, Any]],
    main_comp_name: str,
    text_comp_name: str,
    mine_comp_name: str = "",
    comp_dur: float,
) -> list[Dict[str, Any]]:
    comp_dur = float(comp_dur)
    if comp_dur <= 0:
        return comps

    out: list[Dict[str, Any]] = []
    for c in comps:
        if not isinstance(c, dict):
            continue
        cc = dict(c)
        name = str(cc.get("name") or "")

        if name == text_comp_name:
            cc["dur"] = comp_dur
            cc["workAreaDuration"] = comp_dur
            cc.setdefault("workAreaStart", 0.0)
            cc.setdefault("displayStartTime", 0.0)

        if name == main_comp_name:
            # Keep main comp timing strictly aligned with the actual built text/footage duration.
            cc["dur"] = comp_dur
            cc["workAreaDuration"] = comp_dur
            cc.setdefault("workAreaStart", 0.0)
            cc.setdefault("displayStartTime", 0.0)

        if mine_comp_name and name == mine_comp_name:
            # Mine comp must be at least as long as the main comp so TYPE_4 layers
            # placed at absolute time t (e.g. 13s) fit inside the comp timeline.
            cc["dur"] = comp_dur
            cc["workAreaDuration"] = comp_dur
            cc.setdefault("workAreaStart", 0.0)
            cc.setdefault("displayStartTime", 0.0)

        out.append(cc)

    return out


def _tojson_filter(v: Any) -> str:
    """
    Stable JSON for embedding into JSX.
    - keep utf-8 (ensure_ascii=False)
    - compact (separators) to reduce JSX size
    """
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def build_full_project(
    *,
    repo_root: Path,
    full_edit_config_path: Path,
    footage_config_path: Path,
    out_dir: Path,
) -> Tuple[Path, Path]:
    repo_root = repo_root.resolve()
    full_edit_config_path = full_edit_config_path.resolve()
    footage_config_path = footage_config_path.resolve()
    out_dir = out_dir.resolve()

    if not full_edit_config_path.exists():
        raise FileNotFoundError(str(full_edit_config_path))
    if not footage_config_path.exists():
        raise FileNotFoundError(str(footage_config_path))

    full_edit_config = json.loads(full_edit_config_path.read_text(encoding="utf-8"))
    footage_cfg = json.loads(footage_config_path.read_text(encoding="utf-8"))

    main_comp = dict(AE_PROJECT["main_comp"])
    text_comp = dict(AE_PROJECT["text_comp"])
    mine_comp = dict(AE_PROJECT["mine_comp"])

    main_name = str(main_comp["name"])
    text_name = str(text_comp["name"])
    mine_name = str(mine_comp["name"])

    # ----------------------------------------------------------
    # Resolve factual composition duration (explicit + logged fallbacks).
    # ----------------------------------------------------------
    comp_meta = full_edit_config.get("composition") if isinstance(full_edit_config, dict) else None
    composition_dur = None
    if isinstance(comp_meta, dict):
        d = comp_meta.get("dur")
        if d is not None:
            try:
                composition_dur = float(d)
            except Exception:
                composition_dur = None
                LOGGER.warning("composition.dur is present but invalid: %r", d)

    layers_cfg = list(footage_cfg.get("layers") or [])
    comp_dur = resolve_text_duration_sec(
        composition_dur=composition_dur,
        footage_cfg=footage_cfg,
        layers_cfg=layers_cfg,
    )

    comps_list = [main_comp, text_comp, mine_comp]
    comps_list = _apply_comp_duration_overrides(
        comps=comps_list,
        main_comp_name=main_name,
        text_comp_name=text_name,
        mine_comp_name=mine_name,
        comp_dur=float(comp_dur),
    )

    main_comp = next((c for c in comps_list if c.get("name") == main_name), main_comp)
    text_comp = next((c for c in comps_list if c.get("name") == text_name), text_comp)
    mine_comp = next((c for c in comps_list if c.get("name") == mine_name), mine_comp)

    # 1) Footage layers
    footage_layers = build_footage_layers(
        repo_root=repo_root,
        footage_cfg=footage_cfg,
        main_comp_name=main_name,
        text_comp_name=text_name,
        composition_dur=comp_dur,
        precomp_z_index=int(AE_PROJECT.get("root_precomp_z_index", 9999)),
        precomp_placement=AE_PROJECT.get("root_precomp_placement"),
    )

    # 2) Text layers
    text_layers = build_text_layers(
        full_edit_config=full_edit_config,
        text_comp_name=text_name,
        mine_comp_name=mine_name,
    )

    payload: Dict[str, Any] = {
        "project": {"mainCompName": main_name},
        "comps": [main_comp, text_comp, mine_comp],
        "footage_layers": footage_layers,
        "text_layers": text_layers,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)

    out_json = out_dir / "final_render_instructions_full.json"
    out_jsx = out_dir / "render_full.jsx"

    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ✅ IMPORTANT: add tojson filter so templates can safely embed JSON into JSX
    env = Environment(loader=FileSystemLoader(str(repo_root / "templates")), autoescape=False)
    env.filters["tojson"] = _tojson_filter

    tpl = env.get_template("project_template.j2")
    jsx = tpl.render(**payload)
    out_jsx.write_text(jsx, encoding="utf-8")

    return out_json, out_jsx
