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
    with pytest.raises(RuntimeError, match="reuse_text_source_resume_unavailable"):
        tasks._seed_resume_state_from_source_job(
            work_dir=tmp_path / "work",
            source_job_id="missing_job",
            target_resume_state_path=tmp_path / "work" / "jobs" / "dst" / "data" / "llm_resume_state.json",
        )


def test_seed_resume_state_from_source_job_fails_only_when_stage1_asr_missing(tmp_path: Path) -> None:
    """stage1_asr is the only mandatory reuse key (the expensive ASR the
    orchestrator cannot rebuild). A source without it is unusable -> raise."""
    work_dir = tmp_path / "work"
    src_job = "src_job"
    src = work_dir / "jobs" / src_job / "data" / "llm_resume_state.json"
    # Present keys but NO stage1_asr.
    _write_json(src, {"stage2_subtitles_mode": "scenes_3rd"})

    with pytest.raises(RuntimeError, match=r"missing=\['stage1_asr'\]"):
        tasks._seed_resume_state_from_source_job(
            work_dir=work_dir,
            source_job_id=src_job,
            target_resume_state_path=work_dir / "jobs" / "dst" / "data" / "llm_resume_state.json",
        )


def test_seed_resume_state_tolerates_missing_stage1_plan(tmp_path: Path) -> None:
    """Regression: a SUCCEEDED non-legacy (scenes_3rd) reuse source can persist
    WITHOUT stage1_plan/stage1_plan_source (rebuilt for free at runtime from
    stage1_asr.selected_fragment). Seeding must NOT crash — it copies the present
    keys and skips the absent rebuildable ones. (Prod: /bigtest case-19 crashed
    with reuse_text_source_resume_missing_keys missing stage1_plan.)"""
    work_dir = tmp_path / "work"
    src_job = "src_job"
    src = work_dir / "jobs" / src_job / "data" / "llm_resume_state.json"
    state = _valid_source_state()
    # Mimic the prod source 1e7b5b39: stage1_plan absent, everything else present.
    state.pop("stage1_plan", None)
    state.pop("stage1_plan_source", None)
    _write_json(src, state)

    dst = work_dir / "jobs" / "dst_job" / "data" / "llm_resume_state.json"
    # Must not raise.
    tasks._seed_resume_state_from_source_job(
        work_dir=work_dir,
        source_job_id=src_job,
        target_resume_state_path=dst,
    )
    out = json.loads(dst.read_text(encoding="utf-8"))
    # Present keys copied; absent rebuildable keys simply not present.
    assert out["stage1_asr"] == state["stage1_asr"]
    assert out["stage2_subtitles_mode"] == "impulse_2nd"
    assert "stage1_plan" not in out
    assert "stage1_plan_source" not in out


def test_seed_resume_state_drops_window_bound_payloads_when_clip_window_changes(tmp_path: Path) -> None:
    """F4/F5 reframe the clip around the user drop before enqueueing.

    Reusing stage2 switch/subtitle payloads from the source clip leaks cut
    points from the old window into the new one and crashes the footage picker.
    The ASR and style can still be reused; window-bound stages must rebuild.
    """
    work_dir = tmp_path / "work"
    src_job = "src_job"
    src = work_dir / "jobs" / src_job / "data" / "llm_resume_state.json"
    state = _valid_source_state()
    state["stage1_plan"]["audio"]["clip_start_abs"] = 0.0
    state["stage1_plan"]["audio"]["clip_end_abs"] = 12.0
    state["stage2_subtitles"]["clip"] = {"start": 0.0, "end": 12.0}
    state["stage2_switch_timestamps"] = {
        "clip_start_abs": 0.0,
        "clip_end_abs": 12.0,
        "fast_start_seconds": 6.0,
        "switch_points_abs": [0.998, 3.0, 6.0],
    }
    _write_json(src, state)

    dst = work_dir / "jobs" / "dst_job" / "data" / "llm_resume_state.json"
    tasks._seed_resume_state_from_source_job(
        work_dir=work_dir,
        source_job_id=src_job,
        target_resume_state_path=dst,
        include_footage=True,
        destination_clip_window=(2.0, 12.0),
    )

    out = json.loads(dst.read_text(encoding="utf-8"))
    assert out["stage1_asr"] == state["stage1_asr"]
    assert out["stage2_style"] == state["stage2_style"]
    assert "stage1_plan" not in out
    assert "stage1_plan_source" not in out
    assert "stage2_subtitles" not in out
    assert "stage2_subtitles_mode" not in out
    assert "stage2_switch_timestamps" not in out
    assert "stage2_timing_mode" not in out
    assert "stage2_fast_start_seconds" not in out
