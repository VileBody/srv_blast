from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.render_plan import build_render_plan_v1


REPO_ROOT = Path(__file__).resolve().parents[1]
TEAM_BOT_APP = REPO_ROOT / "services" / "tg_bot_botapi" / "app.py"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "native_render_plan_corpus" / "bigtest_f1_f5"
STATIC_DROP_TIME = 3.2
STATIC_BPM = 128.0


def load_static_bigtest_cases(source: Path = TEAM_BOT_APP) -> List[Dict[str, Any]]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "_BIGTEST_CASES":
            return list(ast.literal_eval(node.value))
        if isinstance(node, ast.Assign):
            if any(getattr(target, "id", None) == "_BIGTEST_CASES" for target in node.targets):
                return list(ast.literal_eval(node.value))
    raise RuntimeError(f"_BIGTEST_CASES not found in {source}")


def export_bigtest_corpus(out_dir: Path = DEFAULT_OUT_DIR) -> Dict[str, Any]:
    out_dir = Path(out_dir)
    requests_dir = out_dir / "requests"
    requests_dir.mkdir(parents=True, exist_ok=True)

    source_cases = load_static_bigtest_cases()
    cases: List[Dict[str, Any]] = []
    for index, case in enumerate(source_cases, start=1):
        cases.append(_export_case(requests_dir, index, "static_bigtest", case))

    # F1 is production code, but not part of the static /bigtest matrix: the
    # battery path adds it only when the user uploads a sound. Keep an explicit
    # representative so F1 never disappears from native readiness coverage.
    cases.append(
        _export_case(
            requests_dir,
            len(cases) + 1,
            "battery_f1_representative",
            {
                "label": "F1/звук: supplied SFX",
                "hook_enabled": True,
                "hook_category": "sound",
                "f1_sound_url": "s3://native-renderer-corpus/audio/f1-supplied-sfx.wav",
                "f1_sound_text": "звук",
                "hook_drop_t": STATIC_DROP_TIME,
            },
        )
    )

    manifest: Dict[str, Any] = {
        "schema": "blast.native-render-plan-corpus.v1",
        "description": "Canonical render-request exports for the team bot /bigtest hook matrix plus the production F1 battery branch.",
        "source": {
            "static_bigtest_source": str(TEAM_BOT_APP.relative_to(REPO_ROOT)),
            "static_bigtest_count": len(source_cases),
            "f1_note": "F1 is emitted by the battery flow when f1_sound_url is present; it is represented here with a supplied SFX asset.",
        },
        "defaults": {
            "drop_time": STATIC_DROP_TIME,
            "bpm": STATIC_BPM,
            "subtitles_mode": "impulse_2nd",
        },
        "coverage": _coverage_summary(cases),
        "cases": cases,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _export_case(
    requests_dir: Path,
    index: int,
    source: str,
    case: Dict[str, Any],
) -> Dict[str, Any]:
    family = _case_family(case)
    case_id = f"{index:02d}_{family.lower()}_{_slug(case.get('label') or family)}"
    cfg = _full_edit_config(case_id, case)
    plan = build_render_plan_v1(
        main_comp_name="Comp 1",
        subtitles_mode=str(cfg["subtitles_mode"]),
        comps=[{"name": "Comp 1", "w": 1080, "h": 1920, "fps": 24, "dur": 8.0}],
        footage_layers=[
            {
                "name": "corpus_clip",
                "type": "footage",
                "in_point": 0.0,
                "out_point": 8.0,
                "z_index": 100,
                "text_data": {
                    "source_footage": {
                        "file_name": "corpus_clip.mp4",
                        "remote_url": "s3://native-renderer-corpus/footage/corpus_clip.mp4",
                    }
                },
            }
        ],
        text_layers=[],
        full_edit_config=cfg,
        f3_media=[],
    )
    request = plan.to_native_request(
        request_id=case_id,
        output_directory=f"out/{case_id}",
        output_video=f"{case_id}.mp4",
    )
    request_path = requests_dir / f"{case_id}.json"
    request_path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "id": case_id,
        "source": source,
        "family": family,
        "label": str(case.get("label") or ""),
        "request": f"requests/{request_path.name}",
        "visual_ops": [op["type"] for op in request.get("visualOps", [])],
        "required_assets": sorted(
            {
                asset["role"]
                for op in request.get("visualOps", [])
                for asset in op.get("assets", [])
                if not asset.get("optional", False)
            }
        ),
        "raw_bigtest_case": case,
    }


def _full_edit_config(case_id: str, case: Dict[str, Any]) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "job_id": case_id,
        "composition": {"dur": 8.0},
        "subtitles_mode": "impulse_2nd",
        "subtitle_flow_plan": {
            "segments": [
                {
                    "id": 1,
                    "text": "native render corpus",
                    "start": 0.5,
                    "end": 2.2,
                    "word_timings": [
                        {"word": "native", "start": 0.5, "end": 0.95},
                        {"word": "render", "start": 0.95, "end": 1.45},
                        {"word": "corpus", "start": 1.45, "end": 2.2},
                    ],
                }
            ]
        },
    }
    drop_time = float(case.get("hook_drop_t") or STATIC_DROP_TIME)
    category = str(case.get("hook_category") or "")
    hook = str(case.get("effect_hook") or "")
    transition = str(case.get("effect_transition") or "")
    extra = str(case.get("effect_extra") or "")
    if category == "effect" and (hook or transition or extra):
        cfg["f3"] = {
            "hook": hook,
            "transition": transition,
            "extra": extra,
            "hook_extend": str(case.get("effect_hook_extend") or ""),
            "extra_full": bool(case.get("effect_extra_full")),
            "drop_time": drop_time,
        }
    if category == "object" and str(case.get("f2_shape") or ""):
        cfg["f2"] = {
            "shape": str(case["f2_shape"]),
            "drop_time": drop_time,
            "seed": _stable_seed(case_id, 7_000),
        }
    if category == "motion" and str(case.get("hook_device") or ""):
        cfg["f4"] = {
            "device": str(case["hook_device"]),
            "bpm": STATIC_BPM,
            "drop_time": drop_time,
        }
    if category == "thought" and str(case.get("hook_device") or ""):
        cfg["f5"] = {
            "chosen_device": str(case["hook_device"]),
            "audio_url": f"s3://native-renderer-corpus/tts/{case_id}.wav",
            "tts_text": _tts_text_for_device(str(case["hook_device"])),
            "word_timings": [
                {"word": "native", "start": drop_time - 0.5, "end": drop_time - 0.2},
                {"word": "thought", "start": drop_time - 0.2, "end": drop_time + 0.2},
            ],
            "drop_rel_sec": drop_time,
            "audio_duration_ms": 900,
            "combo_seed": _stable_seed(case_id, 8_000),
        }
    if category == "sound" and str(case.get("f1_sound_url") or ""):
        cfg["f1"] = {
            "sound_url": str(case["f1_sound_url"]),
            "text": str(case.get("f1_sound_text") or ""),
            "drop_time": drop_time,
            "seed": _stable_seed(case_id, 9_000),
        }
    return cfg


def _case_family(case: Dict[str, Any]) -> str:
    category = str(case.get("hook_category") or "")
    if not case.get("hook_enabled"):
        return "Baseline"
    if category == "sound":
        return "F1"
    if category == "object":
        return "F2"
    if category == "effect":
        return "F3"
    if category == "motion":
        return "F4"
    if category == "thought":
        return "F5"
    return "Unknown"


def _coverage_summary(cases: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    summary = {"Baseline": 0, "F1": 0, "F2": 0, "F3": 0, "F4": 0, "F5": 0, "Unknown": 0}
    for case in cases:
        summary[str(case["family"])] = summary.get(str(case["family"]), 0) + 1
    return summary


def _tts_text_for_device(device: str) -> str:
    return {
        "punchline": "панчлайн",
        "missing_word": "пропущенное слово",
        "lyric_echo": "эхо",
        "question_to_track": "вопрос к треку",
        "inverse_lyric": "инверсия",
    }.get(device, device)


def _stable_seed(case_id: str, base: int) -> int:
    return base + sum((index + 1) * ord(ch) for index, ch in enumerate(case_id)) % 1_000


def _slug(value: Any) -> str:
    raw = str(value or "").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return slug or "case"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export team bot /bigtest cases as native RenderPlan requests.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    manifest = export_bigtest_corpus(args.out)
    print(f"exported {len(manifest['cases'])} native render-plan cases to {args.out}")


if __name__ == "__main__":
    main()
