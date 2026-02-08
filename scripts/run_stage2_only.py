#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mlcore.gemini_call import call_footage_plan_once, call_subtitles_plan_once  # noqa: E402
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


def _load_inventory_assets(inv_path: Path) -> List[Dict[str, object]]:
    """
    Returns list of assets with duration_sec for Stage2 footage prompt.
    We keep this local (script-only), matching orchestrator behavior.
    """
    d = json.loads(inv_path.read_text(encoding="utf-8"))
    assets = d.get("assets") if isinstance(d, dict) else None
    if not isinstance(assets, list):
        raise ValueError(f"Invalid inventory JSON (no assets[]): {inv_path}")

    # If duration is missing, we still provide a fallback to keep the prompt schema stable,
    # but we do NOT auto-correct coverage: stage2 validation will catch feasibility.
    fallback_dur = float(os.environ.get("FOOTAGE_DURATION_FALLBACK_SEC", "60") or "60")

    out: List[Dict[str, object]] = []
    for it in assets:
        if not isinstance(it, dict):
            continue
        fn = str(it.get("file_name") or "").strip()
        sw = it.get("src_w")
        sh = it.get("src_h")
        if not fn or sw is None or sh is None:
            continue

        meta = it.get("meta") if isinstance(it.get("meta"), dict) else {}
        dur = it.get("duration_sec")
        if dur is None:
            dur = it.get("duration")
        if dur is None:
            dur = meta.get("duration_sec")
        if dur is None:
            dur = meta.get("duration")

        try:
            dur_f = float(dur)
        except Exception:
            dur_f = 0.0
        if dur_f <= 0:
            dur_f = fallback_dur

        out.append(
            {
                "file_name": fn,
                "src_w": int(sw),
                "src_h": int(sh),
                "duration_sec": float(dur_f),
            }
        )

    if not out:
        raise RuntimeError(f"No valid assets for prompt in inventory: {inv_path}")
    return out


def _read_bundle_inline(bundle_path: Path, *, max_chars: int) -> str:
    s = bundle_path.read_text(encoding="utf-8")
    if len(s) <= max_chars:
        return s
    return s[:max_chars]


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


def main() -> int:
    ap = argparse.ArgumentParser(description="Run only Stage2 pipeline (subtitles + footage), using existing Stage1 JSON.")
    ap.add_argument("--audio", default="", help="Optional path to local audio file (NOT uploaded by default)")
    ap.add_argument("--attach-audio", action="store_true", help="Attach audio file to Gemini (usually not needed)")
    ap.add_argument("--stage1-plan", required=True, help="Path to stage1_plan_merged.json")
    ap.add_argument("--out-dir", required=True, help="Output directory (stage2 artifacts + logs)")
    ap.add_argument("--subtitles-only", action="store_true", help="Run only stage2 subtitles")
    ap.add_argument("--footage-only", action="store_true", help="Run only stage2 footage")
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

    audio_paths = []
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
    print(f"[stage2] footage_model={model_footage}")

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
        c_foot = _make_client(
            api_key=api_key, model=model_footage, proxy=proxy, temperature=temperature, timeout_s=timeout_s
        )

        inv_path = Path(os.environ.get("FOOTAGE_INVENTORY_JSON", str(ROOT / "data" / "footage_inventory.json"))).resolve()
        if not inv_path.exists():
            raise FileNotFoundError(f"FOOTAGE_INVENTORY_JSON missing: {inv_path}")
        assets_with_duration = _load_inventory_assets(inv_path)

        foot_prompt = build_stage2_footage_user_prompt(
            stage1_json=stage1_json,
            assets_with_duration=assets_with_duration,
            schema_name="FootageSelectionPayload",
        )

        # Optional descriptions bundle context (preferred path: ./pins/descriptions_bundle.json).
        bundle_path = Path(
            (os.environ.get("DESCRIPTIONS_BUNDLE_PATH") or str(ROOT / "pins" / "descriptions_bundle.json"))
        ).resolve()
        if bundle_path.exists():
            cap = int(os.environ.get("DESCRIPTIONS_BUNDLE_INLINE_MAX_CHARS", "250000") or "250000")
            bundle_inline = _read_bundle_inline(bundle_path, max_chars=cap)
            foot_prompt = foot_prompt + "\n\nDESCRIPTIONS_BUNDLE_JSON:\n" + bundle_inline

        foot_payload = call_footage_plan_once(
            client=c_foot,
            system_instruction=build_stage2_footage_system_instruction(),
            user_prompt=foot_prompt,
            audio_paths=audio_paths,
            extra_file_paths=None,
            raw_response_path=logs_dir / f"gemini_raw_stage2_footage_{stamp}.json",
            cache_path=cache_path,
            prompt_dump_path=logs_dir / f"gemini_prompt_stage2_footage_{stamp}.txt",
            system_dump_path=logs_dir / f"gemini_system_stage2_footage_{stamp}.txt",
        )

        if not args.no_validate:
            _validate_footage_coverage_abs(
                foot_payload,
                clip_start_abs=float(stage1.audio.clip_start_abs),
                clip_end_abs=float(stage1.audio.clip_end_abs),
            )

        (out_dir / "stage2_footage.json").write_text(
            json.dumps(foot_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[ok] stage2 footage:    {out_dir / 'stage2_footage.json'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
