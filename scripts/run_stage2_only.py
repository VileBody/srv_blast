#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mlcore.footage_picker import (  # noqa: E402
    build_style_groups_from_assets,
    load_picker_assets_from_inventory,
    pick_footage_clips_deterministic,
    validate_style_pick_in_groups,
)
from mlcore.gemini_call import call_footage_style_once, call_subtitles_plan_once  # noqa: E402
from mlcore.gemini_client import GeminiClient, GeminiSettings  # noqa: E402
from mlcore.models.footage_plan import FootageSelectionPayload  # noqa: E402
from mlcore.models.stage1_plan import Stage1PlanPayload  # noqa: E402
from mlcore.prompts import (  # noqa: E402
    build_stage2_footage_system_instruction,
    build_stage2_footage_user_prompt,
    build_stage2_subtitles_system_instruction,
    build_stage2_subtitles_user_prompt,
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_env() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
        print(f"[env] loaded: {env_path}")


def _require_env(key: str) -> str:
    v = (os.environ.get(key) or "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var: {key}")
    return v


def _make_client(*, api_key: str, model: str, proxy: str, temperature: float, timeout_s: float) -> GeminiClient:
    return GeminiClient(
        GeminiSettings(
            api_key=api_key,
            model=model,
            temperature=temperature,
            proxy=proxy,
            timeout_s=timeout_s,
            max_attempts=1,
        )
    )


def _validate_footage_coverage_abs(payload: FootageSelectionPayload, *, clip_start_abs: float, clip_end_abs: float) -> None:
    cs = float(clip_start_abs)
    ce = float(clip_end_abs)
    clips = sorted(list(payload.clips), key=lambda c: float(c.in_point))
    if not clips:
        raise ValueError("footage.clips is empty")
    if abs(float(clips[0].in_point) - cs) > 1e-6:
        raise ValueError(f"first.in_point != clip_start_abs ({clips[0].in_point} != {cs})")
    for i in range(len(clips) - 1):
        a = clips[i]
        b = clips[i + 1]
        if abs(float(a.out_point) - float(b.in_point)) > 1e-6:
            raise ValueError(f"gap/overlap clip[{i}].out={a.out_point} clip[{i+1}].in={b.in_point}")
    if abs(float(clips[-1].out_point) - ce) > 1e-6:
        raise ValueError(f"last.out_point != clip_end_abs ({clips[-1].out_point} != {ce})")


def _resolve_seed_key(*, stage1_path: Path, out_dir: Path) -> str:
    key = (os.environ.get("STAGE2_SELECTION_SEED") or "").strip()
    if key:
        return key
    job_id = (os.environ.get("JOB_ID") or "").strip()
    if job_id:
        return job_id
    return f"{stage1_path.resolve()}::{out_dir.resolve()}"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run Stage2 pipeline (subtitles + style pick + deterministic footage), using existing Stage1 JSON."
    )
    ap.add_argument("--audio", default="", help="Optional path to local audio file (NOT uploaded by default)")
    ap.add_argument("--attach-audio", action="store_true", help="Attach audio file to Gemini (usually not needed)")
    ap.add_argument("--stage1-plan", required=True, help="Path to stage1_plan_merged.json")
    ap.add_argument("--out-dir", required=True, help="Output directory (stage2 artifacts + logs)")
    ap.add_argument("--subtitles-only", action="store_true", help="Run only stage2 subtitles")
    ap.add_argument("--footage-only", action="store_true", help="Run only stage2 style+footage")
    ap.add_argument("--no-validate", action="store_true", help="Do not validate strict footage coverage (debug)")
    args = ap.parse_args()

    if args.subtitles_only and args.footage_only:
        raise SystemExit("Pick at most one: --subtitles-only or --footage-only")

    _load_env()
    api_key = _require_env("GEMINI_API_KEY")
    model_subtitles = _require_env("GEMINI_MODEL_SUBTITLES")
    model_footage = _require_env("GEMINI_MODEL_FOOTAGE")

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = out_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()

    audio_paths: List[Path] = []
    if args.attach_audio:
        if not args.audio.strip():
            raise RuntimeError("--attach-audio requires --audio")
        audio_path = Path(args.audio).expanduser()
        if not audio_path.is_absolute():
            audio_path = (Path.cwd() / audio_path).resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"audio missing: {audio_path}")
        audio_paths = [audio_path]

    stage1_path = Path(args.stage1_plan).expanduser()
    if not stage1_path.is_absolute():
        stage1_path = (Path.cwd() / stage1_path).resolve()
    if not stage1_path.exists():
        raise FileNotFoundError(f"stage1 plan missing: {stage1_path}")

    stage1_obj: Dict[str, Any] = json.loads(stage1_path.read_text(encoding="utf-8"))
    stage1 = Stage1PlanPayload.model_validate(stage1_obj)
    stage1_json = stage1.model_dump(mode="json")

    proxy = (os.environ.get("OUTBOUND_PROXY") or "").strip()
    temperature = float(os.environ.get("GEMINI_TEMPERATURE", "0") or "0")
    timeout_s = float(os.environ.get("GEMINI_TIMEOUT_S", "120") or "120")
    cache_path = out_dir / "gemini_files_cache.json"

    print(f"[stage2] audio_attached={'YES' if audio_paths else 'NO'}")
    print(f"[stage2] stage1={stage1_path}")
    print(f"[stage2] out_dir={out_dir}")
    print(f"[stage2] subtitles_model={model_subtitles}")
    print(f"[stage2] footage_style_model={model_footage}")

    if not args.footage_only:
        c_sub = _make_client(
            api_key=api_key, model=model_subtitles, proxy=proxy, temperature=temperature, timeout_s=timeout_s
        )
        sub_payload = call_subtitles_plan_once(
            client=c_sub,
            system_instruction=build_stage2_subtitles_system_instruction(),
            user_prompt=build_stage2_subtitles_user_prompt(stage1_json=stage1_json, schema_name="BlocksTokensPayload"),
            audio_paths=audio_paths,
            raw_response_path=logs_dir / f"gemini_raw_stage2_subtitles_{stamp}.json",
            cache_path=cache_path,
            prompt_dump_path=logs_dir / f"gemini_prompt_stage2_subtitles_{stamp}.txt",
            system_dump_path=logs_dir / f"gemini_system_stage2_subtitles_{stamp}.txt",
        )
        (out_dir / "stage2_subtitles.json").write_text(
            json.dumps(sub_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[ok] stage2 subtitles: {out_dir / 'stage2_subtitles.json'}")

    if not args.subtitles_only:
        c_style = _make_client(
            api_key=api_key, model=model_footage, proxy=proxy, temperature=temperature, timeout_s=timeout_s
        )

        inv_path = Path(os.environ.get("FOOTAGE_INVENTORY_JSON", str(ROOT / "data" / "footage_inventory.json"))).resolve()
        if not inv_path.exists():
            raise FileNotFoundError(f"FOOTAGE_INVENTORY_JSON missing: {inv_path}")
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
        picker_assets = load_picker_assets_from_inventory(inv)
        style_groups = build_style_groups_from_assets(picker_assets)

        style_prompt = build_stage2_footage_user_prompt(
            stage1_json=stage1_json,
            style_groups=style_groups,
            schema_name="FootageStylePickPayload",
        )
        style_payload = call_footage_style_once(
            client=c_style,
            system_instruction=build_stage2_footage_system_instruction(),
            user_prompt=style_prompt,
            audio_paths=audio_paths,
            extra_file_paths=None,
            raw_response_path=logs_dir / f"gemini_raw_stage2_style_{stamp}.json",
            cache_path=cache_path,
            prompt_dump_path=logs_dir / f"gemini_prompt_stage2_style_{stamp}.txt",
            system_dump_path=logs_dir / f"gemini_system_stage2_style_{stamp}.txt",
        )
        validate_style_pick_in_groups(style_payload, style_groups)

        seed_key = _resolve_seed_key(stage1_path=stage1_path, out_dir=out_dir)
        footage_payload, diag = pick_footage_clips_deterministic(
            style_pick=style_payload,
            assets=picker_assets,
            clip_start_abs=float(stage1.audio.clip_start_abs),
            clip_end_abs=float(stage1.audio.clip_end_abs),
            seed_key=seed_key,
        )
        if not args.no_validate:
            _validate_footage_coverage_abs(
                footage_payload,
                clip_start_abs=float(stage1.audio.clip_start_abs),
                clip_end_abs=float(stage1.audio.clip_end_abs),
            )

        (out_dir / "stage2_style.json").write_text(
            json.dumps(style_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / "stage2_footage.json").write_text(
            json.dumps(footage_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[ok] stage2 style:      {out_dir / 'stage2_style.json'}")
        print(f"[ok] stage2 footage:    {out_dir / 'stage2_footage.json'}")
        print(
            "[ok] picker: "
            f"style={diag.genre}/{diag.tag} "
            f"widen={diag.widened_to_genre} repeats={diag.repeats_used} "
            f"seed={diag.deterministic_seed}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
