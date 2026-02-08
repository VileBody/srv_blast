#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.project_builder import build_full_project  # noqa: E402
from mlcore.gemini_postprocess import render_all_steps  # noqa: E402
from mlcore.models.full_plan import FullPlanPayload  # noqa: E402
from mlcore.models.stage1_plan import Stage1PlanPayload  # noqa: E402


def _load_env() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
        print(f"[env] loaded: {env_path}")


def _abs_path(p: str) -> Path:
    pp = Path(p).expanduser()
    if not pp.is_absolute():
        pp = (Path.cwd() / pp).resolve()
    return pp.resolve()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stage3+Build: merge stage1+stage2 JSON -> render configs -> build AE payload+JSX."
    )
    ap.add_argument("--audio", required=True, help="Local audio file path (for step3 audio layer path)")
    ap.add_argument("--stage1-plan", required=True, help="Path to stage1_plan_merged.json")
    ap.add_argument("--stage2-subtitles", required=True, help="Path to stage2_subtitles.json (BlocksTokensPayload)")
    ap.add_argument("--stage2-footage", required=True, help="Path to stage2_footage.json (FootageSelectionPayload)")
    ap.add_argument("--out-dir", required=True, help="Output directory (will contain render_full.jsx)")
    ap.add_argument("--data-dir", default="", help="Where to write full_edit_config.json/footage_config.json (default: out-dir)")
    ap.add_argument(
        "--inventory",
        default="",
        help="Path to footage_inventory.json (default: $FOOTAGE_INVENTORY_JSON or data/footage_inventory.json)",
    )
    args = ap.parse_args()

    _load_env()

    out_dir = _abs_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data_dir = _abs_path(args.data_dir) if args.data_dir.strip() else out_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    audio_path = _abs_path(args.audio)
    if not audio_path.exists():
        raise FileNotFoundError(f"audio missing: {audio_path}")

    # Make step3 pick THIS audio path/name deterministically.
    os.environ["AUDIO_FILE_PATH"] = str(audio_path)
    os.environ["AUDIO_DIR"] = str(audio_path.parent)

    stage1_path = _abs_path(args.stage1_plan)
    subs_path = _abs_path(args.stage2_subtitles)
    foot_path = _abs_path(args.stage2_footage)

    if not stage1_path.exists():
        raise FileNotFoundError(f"stage1 missing: {stage1_path}")
    if not subs_path.exists():
        raise FileNotFoundError(f"stage2 subtitles missing: {subs_path}")
    if not foot_path.exists():
        raise FileNotFoundError(f"stage2 footage missing: {foot_path}")

    stage1_obj = json.loads(stage1_path.read_text(encoding="utf-8"))
    stage1 = Stage1PlanPayload.model_validate(stage1_obj)

    subs_obj = json.loads(subs_path.read_text(encoding="utf-8"))
    foot_obj = json.loads(foot_path.read_text(encoding="utf-8"))

    # Build FullPlanPayload (Stage3 expects absolute token times).
    full_obj = {
        "audio": stage1.audio.model_dump(mode="json"),
        "subtitles": subs_obj,
        "footage": foot_obj,
    }
    plan = FullPlanPayload.model_validate(full_obj)

    inv_path_raw = (args.inventory or os.environ.get("FOOTAGE_INVENTORY_JSON") or "").strip()
    if inv_path_raw:
        inv_path = _abs_path(inv_path_raw)
    else:
        inv_path = (ROOT / "data" / "footage_inventory.json").resolve()
    if not inv_path.exists():
        raise FileNotFoundError(f"footage inventory missing: {inv_path}")

    # Stage3: write configs into data_dir + out_dir
    outputs = render_all_steps(
        repo_root=ROOT,
        plan=plan,
        footage_inventory_json=inv_path,
        out_dir=out_dir,
        data_dir=data_dir,
    )

    full_edit_path = outputs["full_edit_config"]
    footage_cfg_path = outputs["footage_config"]

    # Build AE payload+JSX using those configs.
    out_json, out_jsx = build_full_project(
        repo_root=ROOT,
        full_edit_config_path=full_edit_path,
        footage_config_path=footage_cfg_path,
        out_dir=out_dir,
    )

    print("[ok] stage3+build:")
    print(f"  - full_edit: {full_edit_path}")
    print(f"  - footage:   {footage_cfg_path}")
    print(f"  - payload:   {out_json}")
    print(f"  - jsx:       {out_jsx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

