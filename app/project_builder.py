# app/project_builder.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from jinja2 import Environment, FileSystemLoader

from app.project_config import AE_PROJECT
from app.footage_comp import build_footage_layers, resolve_text_duration_sec
from app.text_comp import build_text_layers
from core.subtitles_mode import SUBTITLES_MODE_LEGACY_BLOCKS, normalize_subtitles_mode

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
    subtitles_mode = normalize_subtitles_mode(
        str(full_edit_config.get("subtitles_mode") or ""),
        default=SUBTITLES_MODE_LEGACY_BLOCKS,
    )

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
        subtitles_mode=subtitles_mode,
    )

    # 2) Text layers
    text_layers = build_text_layers(
        full_edit_config=full_edit_config,
        text_comp_name=text_name,
        mine_comp_name=mine_name,
    )

    # ----------------------------------------------------------
    # Origin-bot gate (botapi-only experimental features).
    # Public-bot requests arrive with SOURCE_BOT="" → all experimental
    # features are disabled regardless of other flags. This isolation is
    # a hard constraint: cold/warm color grade + uniqueness pass activate
    # only on botapi until validated there.
    # ----------------------------------------------------------
    source_bot = (os.environ.get("SOURCE_BOT") or "").strip().lower()
    is_botapi_origin = (source_bot == "botapi")
    job_id_for_log = str(os.environ.get("JOB_ID") or footage_cfg.get("job_id") or "default")
    LOGGER.info(
        "origin gate job_id=%r SOURCE_BOT=%r is_botapi_origin=%s "
        "(experimental features: %s)",
        job_id_for_log,
        source_bot,
        is_botapi_origin,
        "ENABLED" if is_botapi_origin else "DISABLED (public/unknown path — stable contract)",
    )

    # The cold/warm adjustment-effects sidecar is disabled globally.
    # It can hang headless AE while initializing third-party plugins before
    # ae_status.txt is written, so render.jsx must not inline or eval it.
    color_grade = str(footage_cfg.get("color_grade") or "").strip().lower() or None
    adjustment_sidecar_source: str | None = None
    LOGGER.warning(
        "adjustment_sidecar disabled: color_grade=%r ignored (job_id=%r)",
        color_grade,
        job_id_for_log,
    )

    # --- Uniqueness pass (per-clip geometric drift + optional mirror + color jitter) ---
    # Inlined into render.jsx (same reason as cold/warm sidecar — render-node sparse-checkout).
    # Fully controllable via env kill-switches for easy rollback without redeploy:
    #   UNIQUENESS_ENABLED=0                  → whole pass disabled (master)
    #   UNIQUENESS_GEOMETRY_ENABLED=0         → scale+offset disabled
    #   UNIQUENESS_MIRROR_ENABLED=0           → mirror disabled (even if allow_mirror=True)
    #   UNIQUENESS_COLOR_JITTER_ENABLED=0     → hue/sat/exp/gamma disabled
    # Both build-time flags (here) and runtime flags (inside uniqueness_pass.jsx) are OR'd —
    # any OFF wins. Default: all ON.
    def _env_flag_on(name: str, default: bool = True) -> bool:
        raw = (os.environ.get(name) or "").strip().lower()
        if raw in ("0", "false", "off", "no"):
            return False
        if raw in ("1", "true", "on", "yes"):
            return True
        return default

    # Origin-bot gate AND'd with every flag: public-path jobs get uniqueness OFF
    # across the board regardless of env flags. This keeps the public pipeline
    # on the stable rendering contract.
    uniqueness_master_on = _env_flag_on("UNIQUENESS_ENABLED", True) and is_botapi_origin
    uniqueness_geometry_on = _env_flag_on("UNIQUENESS_GEOMETRY_ENABLED", True) and uniqueness_master_on
    uniqueness_mirror_on = _env_flag_on("UNIQUENESS_MIRROR_ENABLED", True) and uniqueness_master_on
    uniqueness_color_jitter_on = _env_flag_on("UNIQUENESS_COLOR_JITTER_ENABLED", True) and uniqueness_master_on

    uniqueness_pass_source: str | None = None
    if uniqueness_master_on:
        _uniq_path = repo_root / "render_templates" / "uniqueness_pass.jsx"
        try:
            uniqueness_pass_source = _uniq_path.read_text(encoding="utf-8")
            LOGGER.info(
                "uniqueness_pass inlined: %s (%d chars) job_id=%r",
                _uniq_path.name,
                len(uniqueness_pass_source),
                job_id_for_log,
            )
        except Exception as e:
            LOGGER.error(
                "uniqueness_pass read failed: %s — skipping uniqueness pass (job_id=%r)",
                e, job_id_for_log,
            )
            uniqueness_pass_source = None
    else:
        _reason = (
            "public/unknown origin (SOURCE_BOT=%r)" % source_bot
            if not is_botapi_origin
            else "UNIQUENESS_ENABLED=0 (master kill switch)"
        )
        LOGGER.warning(
            "uniqueness_pass skipped: %s (job_id=%r)",
            _reason, job_id_for_log,
        )

    _job_id_str = job_id_for_log
    uniqueness_seed = int(hashlib.md5(_job_id_str.encode("utf-8")).hexdigest()[:8], 16)
    uniqueness_allow_mirror = bool(footage_cfg.get("allow_mirror") or False)
    LOGGER.info(
        "uniqueness flags: origin_botapi=%s master=%s geo=%s mirror=%s color=%s "
        "| seed=%d allow_mirror=%s (job_id=%r)",
        is_botapi_origin,
        uniqueness_master_on,
        uniqueness_geometry_on,
        uniqueness_mirror_on,
        uniqueness_color_jitter_on,
        uniqueness_seed,
        uniqueness_allow_mirror,
        _job_id_str,
    )

    payload: Dict[str, Any] = {
        "project": {"mainCompName": main_name, "subtitlesMode": subtitles_mode},
        "comps": [main_comp, text_comp, mine_comp],
        "footage_layers": footage_layers,
        "text_layers": text_layers,
        "adjustment_sidecar_source": adjustment_sidecar_source,
        "uniqueness_pass_source": uniqueness_pass_source,
        "uniqueness_seed": uniqueness_seed,
        "uniqueness_allow_mirror": uniqueness_allow_mirror,
        "uniqueness_enabled": uniqueness_master_on,
        "uniqueness_geometry_enabled": uniqueness_geometry_on,
        "uniqueness_mirror_enabled": uniqueness_mirror_on,
        "uniqueness_color_jitter_enabled": uniqueness_color_jitter_on,
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
