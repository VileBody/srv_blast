from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.orchestrator import tasks


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _valid_source_state() -> dict:
    return {
        "stage1_asr": {"transcript_words": []},
        "stage1_asr_mode": "forced_alignment",
        "stage1_asr_reference_text": "hello world",
        "stage1_plan": {"audio": {"clip_start_abs": 10.0, "clip_end_abs": 24.0}},
        "stage1_plan_source": "stage1a_selected_fragment",
        "stage2_subtitles": {"mode": "impulse_2nd", "clip": {"start": 10.0, "end": 24.0}, "segments": []},
        "stage2_subtitles_mode": "impulse_2nd",
        "stage2_switch_timestamps": {"clip_start_abs": 10.0, "clip_end_abs": 24.0, "switch_points_abs": [12.0]},
        "stage2_timing_mode": "prompts",
        "stage2_fast_start_seconds": 6.0,
        "stage2_style": {"genre": "Rock", "tag": "dark_forest"},
    }


def test_seed_resume_state_from_source_job_copies_whitelist_keys(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    src_job = "src_job"
    dst = work_dir / "jobs" / "dst_job" / "data" / "llm_resume_state.json"

    source_path = work_dir / "jobs" / src_job / "data" / "llm_resume_state.json"
    _write_json(source_path, _valid_source_state())
    _write_json(dst, {"foo": "bar", "stage2_style": {"genre": "Pop", "tag": "dream_aesthetic"}})

    tasks._seed_resume_state_from_source_job(
        work_dir=work_dir,
        source_job_id=src_job,
        target_resume_state_path=dst,
    )

    out = json.loads(dst.read_text(encoding="utf-8"))
    assert out["foo"] == "bar"
    assert out["stage2_style"] == {"genre": "Pop", "tag": "dream_aesthetic"}
    for k in tasks._REUSE_RESUME_STATE_KEYS:
        assert k in out
    assert out["stage2_subtitles_mode"] == "impulse_2nd"


def test_seed_resume_state_from_source_job_fails_when_source_missing(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="reuse_text_source_resume_missing"):
        tasks._seed_resume_state_from_source_job(
            work_dir=tmp_path / "work",
            source_job_id="missing_job",
            target_resume_state_path=tmp_path / "work" / "jobs" / "dst" / "data" / "llm_resume_state.json",
        )


def test_seed_resume_state_from_source_job_fails_when_source_missing_keys(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    src_job = "src_job"
    src = work_dir / "jobs" / src_job / "data" / "llm_resume_state.json"
    _write_json(src, {"stage1_asr": {"transcript_words": []}})

    with pytest.raises(RuntimeError, match="reuse_text_source_resume_missing_keys"):
        tasks._seed_resume_state_from_source_job(
            work_dir=work_dir,
            source_job_id=src_job,
            target_resume_state_path=work_dir / "jobs" / "dst" / "data" / "llm_resume_state.json",
        )
