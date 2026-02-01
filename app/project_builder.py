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

    main_comp = AE_PROJECT["main_comp"]
    text_comp = AE_PROJECT["text_comp"]
    mine_comp = AE_PROJECT["mine_comp"]

    # 1) Footage layers (Comp 1 + precomp "Текст" в Comp 1)
    footage_layers = build_footage_layers(
        repo_root=repo_root,
        footage_cfg=footage_cfg,
        main_comp_name=str(main_comp["name"]),
        text_comp_name=str(text_comp["name"]),
        precomp_z_index=int(AE_PROJECT.get("root_precomp_z_index", 9999)),
        precomp_placement=AE_PROJECT.get("root_precomp_placement"),
    )

    # 2) Text layers ("Текст" + Mine-inner routed to Mine-precomp)
    text_layers = build_text_layers(
        full_edit_config=full_edit_config,
        text_comp_name=str(text_comp["name"]),
        mine_comp_name=str(mine_comp["name"]),
    )

    payload: Dict[str, Any] = {
        "project": {"mainCompName": str(main_comp["name"])},
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
