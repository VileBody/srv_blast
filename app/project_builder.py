# app/project_builder.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Tuple

from jinja2 import Environment, FileSystemLoader

from app.project_config import AE_PROJECT
from app.footage_comp import build_footage_layers
from app.text_comp import build_text_layers


def _safe_float(x: Any, default: float) -> float:
    try:
        v = float(x)
        return v
    except Exception:
        return float(default)


def _apply_comp_duration_overrides(
    *,
    comps: list[Dict[str, Any]],
    main_comp_name: str,
    text_comp_name: str,
    comp_dur: float,
) -> list[Dict[str, Any]]:
    """
    Make compsSpec consistent with real timeline length.
    This prevents AE from clamping outPoint/keyframes beyond comp duration.

    Rules:
      - Text comp duration MUST be >= comp_dur (we set it exactly).
      - Text comp workAreaDuration MUST be >= comp_dur (we set it exactly).
      - Main comp duration can stay large (60s), but workAreaDuration MUST be >= comp_dur
        so the active area covers the clip.
    """
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
            # keep start at 0 unless user changes it explicitly
            cc.setdefault("workAreaStart", 0.0)
            cc.setdefault("displayStartTime", 0.0)

        if name == main_comp_name:
            # keep cc["dur"] as is (typically 60s), but extend work area
            wa = _safe_float(cc.get("workAreaDuration"), 0.0)
            if wa < comp_dur:
                cc["workAreaDuration"] = comp_dur
            cc.setdefault("workAreaStart", 0.0)
            cc.setdefault("displayStartTime", 0.0)

        out.append(cc)

    return out


def build_full_project(
    *,
    repo_root: Path,
    full_edit_config_path: Path,
    footage_config_path: Path,
    out_dir: Path,
) -> Tuple[Path, Path]:
    """
    Единственная точка сборки проекта.

    Вход:
      - data/full_edit_config.json (Gemini или статичный)
      - data/footage_config.json

    Выход:
      - out/final_render_instructions_full.json
      - out/render_full.jsx (FULL PROJECT script)
    """
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
    # IMPORTANT: align compsSpec duration with full_edit_config composition.dur
    # ----------------------------------------------------------
    comp_meta = full_edit_config.get("composition") if isinstance(full_edit_config, dict) else None
    comp_dur = None
    if isinstance(comp_meta, dict):
        d = comp_meta.get("dur")
        if d is not None:
            try:
                comp_dur = float(d)
            except Exception:
                comp_dur = None

    # if not present, fallback to footage_cfg text_dur_hint (already comp timeline seconds)
    if comp_dur is None:
        try:
            comp_dur = float(footage_cfg.get("text_dur_hint", text_comp.get("dur", 0.0)))
        except Exception:
            comp_dur = float(text_comp.get("dur", 0.0))

    # Apply overrides to comps specs (prevents AE clamping)
    comps_list = [main_comp, text_comp, mine_comp]
    comps_list = _apply_comp_duration_overrides(
        comps=comps_list,
        main_comp_name=main_name,
        text_comp_name=text_name,
        comp_dur=float(comp_dur),
    )

    # Re-bind after overrides
    main_comp = next((c for c in comps_list if c.get("name") == main_name), main_comp)
    text_comp = next((c for c in comps_list if c.get("name") == text_name), text_comp)
    mine_comp = next((c for c in comps_list if c.get("name") == mine_name), mine_comp)

    # 1) Footage layers (Comp 1 + precomp "Текст" в Comp 1)
    footage_layers = build_footage_layers(
        repo_root=repo_root,
        footage_cfg=footage_cfg,
        main_comp_name=main_name,
        text_comp_name=text_name,
        precomp_z_index=int(AE_PROJECT.get("root_precomp_z_index", 9999)),
        precomp_placement=AE_PROJECT.get("root_precomp_placement"),
    )

    # 2) Text layers ("Текст" + Mine-inner routed to Mine-precomp)
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

    env = Environment(loader=FileSystemLoader(str(repo_root / "templates")), autoescape=False)
    tpl = env.get_template("project_template.j2")
    jsx = tpl.render(**payload)
    out_jsx.write_text(jsx, encoding="utf-8")

    return out_json, out_jsx
