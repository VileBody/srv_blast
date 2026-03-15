#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.clip_window import CLIP_WINDOW_RANGE_LABEL, CLIP_WINDOW_RANGE_S_LABEL  # noqa: E402
from mlcore.gemini_call import call_stage1_asr_once, call_stage1_scenario_once  # noqa: E402
from mlcore.gemini_client import GeminiClient, GeminiSettings  # noqa: E402
from mlcore.models.stage1_plan import Stage1PlanPayload  # noqa: E402
from mlcore.prompts import (  # noqa: E402
    build_stage1a_asr_system_instruction,
    build_stage1a_asr_user_prompt,
    build_stage1b_scenario_system_instruction,
    build_stage1b_scenario_user_prompt,
)
from mlcore.stage1_tools import align_stage1_draft_to_transcript, build_stage1_report  # noqa: E402


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_env() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
        print(f"[env] loaded: {env_path}")


def _resolve_model(name: str, fallback: Optional[str], default: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    if fallback:
        f = (os.environ.get(fallback) or "").strip()
        if f:
            return f
    return default


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


def _norm_compact(s: str) -> str:
    return " ".join(str(s or "").split())


def main() -> int:
    ap = argparse.ArgumentParser(description="Run only Stage1 pipeline (ASR + scenario), no stage2/3, no AE build.")
    ap.add_argument("--audio", required=True, help="Path to local audio file")
    ap.add_argument("--out-dir", required=True, help="Output directory")
    ap.add_argument("--asr-model", default="", help="Override ASR model")
    ap.add_argument("--scenario-model", default="", help="Override scenario model")
    ap.add_argument("--dump-srt", action="store_true", help="Write stage1_srt.json if available")
    ap.add_argument(
        "--target-fragment",
        default="",
        help=f"Optional requested fragment text; Stage1B keeps {CLIP_WINDOW_RANGE_S_LABEL} window and maximizes overlap.",
    )
    args = ap.parse_args()

    _load_env()
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = out_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()

    audio_path = Path(args.audio).expanduser()
    if not audio_path.is_absolute():
        audio_path = (Path.cwd() / audio_path).resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"audio missing: {audio_path}")

    proxy = (os.environ.get("OUTBOUND_PROXY") or "").strip()
    temperature = float(os.environ.get("GEMINI_TEMPERATURE", "0") or "0")
    timeout_s = float(os.environ.get("GEMINI_TIMEOUT_S", "120") or "120")
    cache_path = out_dir / "gemini_files_cache.json"

    asr_model = args.asr_model.strip() or _resolve_model(
        "GEMINI_MODEL_STAGE1_ASR", "GEMINI_MODEL_STAGE1", "gemini-2.5-pro"
    )
    scenario_model = args.scenario_model.strip() or _resolve_model(
        "GEMINI_MODEL_STAGE1_SCENARIO", None, "gemini-3-flash-preview"
    )

    print(f"[stage1] audio={audio_path}")
    print(f"[stage1] asr_model={asr_model}")
    print(f"[stage1] scenario_model={scenario_model}")

    c_asr = _make_client(api_key=api_key, model=asr_model, proxy=proxy, temperature=temperature, timeout_s=timeout_s)
    c_scn = _make_client(
        api_key=api_key, model=scenario_model, proxy=proxy, temperature=temperature, timeout_s=timeout_s
    )

    asr = call_stage1_asr_once(
        client=c_asr,
        system_instruction=build_stage1a_asr_system_instruction(),
        user_prompt=build_stage1a_asr_user_prompt(schema_name="Stage1AsrPayload"),
        audio_paths=[audio_path],
        raw_response_path=logs_dir / f"gemini_raw_stage1_asr_{stamp}.json",
        cache_path=cache_path,
        prompt_dump_path=logs_dir / f"gemini_prompt_stage1_asr_{stamp}.txt",
        system_dump_path=logs_dir / f"gemini_system_stage1_asr_{stamp}.txt",
    )
    asr_json = asr.model_dump(mode="json")
    (out_dir / "stage1_asr.json").write_text(json.dumps(asr_json, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.dump_srt:
        (out_dir / "stage1_srt.json").write_text(
            json.dumps(asr_json.get("srt_items", []), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    target_fragment = str(args.target_fragment or "").strip()
    base_prompt = build_stage1b_scenario_user_prompt(
        asr_json=asr_json,
        target_fragment=target_fragment,
        schema_name="Stage1ScenarioPayload",
    )
    stage1_plan: Stage1PlanPayload | None = None
    last_exc: Exception | None = None
    for attempt, strict in enumerate((False, True), start=1):
        strict_addendum = (
            "\n\nSTRICT_RETRY_RULES:\n"
            "- Every draft phrase must be copied from transcript words.\n"
            + f"- Keep selected window in {CLIP_WINDOW_RANGE_LABEL} sec with arc: 1..5 development, 6 fixation, 7 exit.\n"
            "- Keep segments balanced and concise (target <=6 words, hard cap <=8).\n"
            "- Avoid dangling leftovers: split only at natural phrase boundaries.\n"
            "- Repeats are OK in songs.\n"
        )
        prompt = base_prompt + strict_addendum if strict else base_prompt
        scenario = call_stage1_scenario_once(
            client=c_scn,
            system_instruction=build_stage1b_scenario_system_instruction(),
            user_prompt=prompt,
            # Stage1B is scenario planning based on Stage1A transcript_words; no need to attach audio.
            audio_paths=[],
            raw_response_path=logs_dir / f"gemini_raw_stage1_scenario{'_retry' if strict else ''}_{stamp}.json",
            cache_path=cache_path,
            prompt_dump_path=logs_dir / f"gemini_prompt_stage1_scenario{'_retry' if strict else ''}_{stamp}.txt",
            system_dump_path=logs_dir / f"gemini_system_stage1_scenario_{stamp}.txt",
        )
        scenario_json = scenario.model_dump(mode="json")
        (out_dir / "stage1_scenario.json").write_text(
            json.dumps(scenario_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            audio_obj = dict(scenario_json["audio"])
            fa = scenario_json.get("fragment_analytics")
            if target_fragment:
                if not isinstance(fa, dict):
                    raise RuntimeError("target_fragment branch requires fragment_analytics from Stage1B")
                af = _norm_compact(str(fa.get("target_fragment") or ""))
                tf = _norm_compact(target_fragment)
                if af != tf:
                    raise RuntimeError(
                        "fragment_analytics.target_fragment mismatch "
                        f"(got={af!r} expected={tf!r})"
                    )
                fs = float(fa.get("working_start_abs"))
                fe = float(fa.get("working_end_abs"))
                audio_obj["clip_start_abs"] = fs
                audio_obj["clip_end_abs"] = fe

            candidate = Stage1PlanPayload.model_validate(
                {
                    "audio": audio_obj,
                    "draft_blocks": scenario_json["draft_blocks"],
                    "transcript_words": asr_json["transcript_words"],
                    "fragment_analytics": scenario_json.get("fragment_analytics"),
                }
            )
            stage1_plan = candidate
            report_path = out_dir / "stage1_report.txt"
            try:
                align_rows = align_stage1_draft_to_transcript(candidate)
                report_path.write_text(build_stage1_report(candidate, align_rows), encoding="utf-8")
            except Exception as e:
                report_path.write_text(
                    "STAGE1 ALIGNMENT WARNING (non-fatal)\n"
                    f"err={e}\n\n"
                    "DRAFT_BLOCKS_JSON:\n"
                    + json.dumps(scenario_json.get("draft_blocks", {}), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            break
        except Exception as e:
            last_exc = e
            print(f"[stage1] scenario attempt={attempt} strict={strict} failed: {e}")

    if stage1_plan is None:
        raise RuntimeError(f"Stage1 scenario validation failed after retry: {last_exc}")

    plan_json = stage1_plan.model_dump(mode="json")
    (out_dir / "stage1_plan_merged.json").write_text(json.dumps(plan_json, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[ok] stage1 generated:")
    print(f"  - asr:      {out_dir / 'stage1_asr.json'}")
    print(f"  - scenario: {out_dir / 'stage1_scenario.json'}")
    print(f"  - merged:   {out_dir / 'stage1_plan_merged.json'}")
    print(f"  - report:   {out_dir / 'stage1_report.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
