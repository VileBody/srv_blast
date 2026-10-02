"""«Докрутить на сайте»: состояние монтажа бот-джобы + клон Stage 1 в asr_preview-джобу."""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import pytest

from services.orchestrator import tasks
from services.orchestrator.schemas import JobEditStateResponse, JobState


def _stage1_asr() -> dict:
    words = [
        {"text": "раз", "t_start": 10.2, "t_end": 10.6},
        {"text": "два", "t_start": 10.7, "t_end": 11.1},
    ]
    return {
        "transcript_words": words,
        "pause_spans": [],
        "srt_items": [],
        "selected_fragment": {
            "audio": {"clip_start_abs": 10.0, "clip_end_abs": 14.0},
            "transcript_words": words,
            "pause_spans": [],
            "srt_items": [],
            "fragment_analytics": {
                "target_fragment": "раз два",
                "working_fragment": "раз два",
                "working_start_abs": 10.0,
                "working_end_abs": 13.5,
                "working_start_text": "user_clip_start",
                "working_end_text": "user_clip_end",
                "relation_to_target": "inside_13_18",
                "chosen_action": "none",
                "rationale": "local_ctc_user_window_is_source_of_truth",
            },
        },
    }


def _resume_state(mode: str = "local_ctc") -> dict:
    return {
        "stage1_asr": _stage1_asr(),
        "stage1_asr_mode": mode,
        "stage1_asr_reference_text": "раз два",
        "stage1_alignment_backend": mode if mode == "local_ctc" else "gemini",
        "stage1_alignment_metadata": {"model_revision": "r1"},
        "stage1_plan": {"secret": "not for the clone"},
        "stage2_switch_timestamps": {"switch_points_abs": [11.0, 12.5]},
        "stage2_footage_plan": {
            "version": 1, "clip_start_abs": 10.0, "clip_end_abs": 13.5,
            "switch_points_abs": [11.0, 12.5], "clips": [],
        },
        "stage2_footage_plan_meta": {"bg_mode": "footage", "exact_slot": True},
    }


class _Store:
    def __init__(self) -> None:
        self.jobs: dict[str, JobState] = {}
        self.idem: dict[str, str] = {}

    def add(self, job_id: str, request: dict[str, Any], status: str = "SUCCEEDED") -> None:
        now = time.time()
        self.jobs[job_id] = JobState(job_id=job_id, status=status, created_at=now, updated_at=now, request=request)

    def get(self, job_id: str) -> JobState | None:
        return self.jobs.get(job_id)

    def new_job(self, *, request: dict[str, Any], idempotency_key: str | None):
        if idempotency_key and idempotency_key in self.idem:
            return self.jobs[self.idem[idempotency_key]], False
        job_id = f"job{len(self.jobs) + 1}"
        self.add(job_id, request, status="NEW")
        if idempotency_key:
            self.idem[idempotency_key] = job_id
        return self.jobs[job_id], True

    def set_status(self, job_id: str, status: str, *, stage=None, error=None, result=None):
        st = self.jobs[job_id]
        merged = {**(st.result or {}), **(result or {})}
        self.jobs[job_id] = st.model_copy(update={"status": status, "stage": stage, "result": merged})
        return self.jobs[job_id]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    store = _Store()
    runtime: dict[str, dict] = {}
    monkeypatch.setattr(tasks, "_load_resume_state_from_runtime_db", lambda *, job_id: dict(runtime.get(job_id) or {}))
    monkeypatch.setattr(tasks, "SETTINGS", dataclasses.replace(tasks.SETTINGS, work_dir=str(tmp_path), credits_db_url=""))
    return store, runtime


def test_edit_state_reads_request_subset_plan_and_words(env) -> None:
    store, runtime = env
    store.add("bot1", {
        "audio_s3_url": "s3://raw/a.mp3", "user_clip_start_sec": 10.0, "user_clip_end_sec": 14.0,
        "rotation_theme": "visual", "rotation_tags_group": "night", "effect_transition": "minimax",
        "frame_id": "rounded", "maintenance_bypass_token": "SECRET", "build_queue": "build",
    })
    runtime["bot1"] = _resume_state()

    out = tasks.job_edit_state(store=store, job_id="bot1")

    JobEditStateResponse(**out)  # схема ответа принимает то, что отдаёт функция
    assert out["status"] == "SUCCEEDED"
    assert out["resume_state_source"] == "runtime_db"
    # токены/очереди наружу не уходят
    assert "maintenance_bypass_token" not in out["request"] and "build_queue" not in out["request"]
    assert out["request"]["frame_id"] == "rounded"
    # окно — рабочее окно выравнивателя (уже пользовательского)
    assert out["window"] == {"clip_start_abs": 10.0, "clip_end_abs": 13.5}
    assert out["switch_points_abs"] == [11.0, 12.5]
    assert out["footage_plan"]["switch_points_abs"] == [11.0, 12.5]
    assert out["footage_plan_meta"]["exact_slot"] is True
    assert [w["text"] for w in out["words"]] == ["раз", "два"]
    assert out["asr"] == {"available": True, "mode": "local_ctc", "alignment_backend": "local_ctc", "reference_text": "раз два"}


def test_edit_state_survives_store_ttl_via_runtime_db(env) -> None:
    store, runtime = env
    runtime["gone"] = _resume_state()
    out = tasks.job_edit_state(store=store, job_id="gone")
    # джобу вычистил TTL стора: настроек нет, сайт возьмёт снимок из ссылки бота
    assert out["request"] is None and out["status"] is None
    assert out["switch_points_abs"] == [11.0, 12.5]


def test_edit_state_unknown_job_is_explicit(env) -> None:
    store, _ = env
    with pytest.raises(tasks.EditStateUnavailable):
        tasks.job_edit_state(store=store, job_id="nope")


def test_clone_creates_succeeded_asr_preview_with_stage1_only(env, tmp_path: Path) -> None:
    store, runtime = env
    store.add("bot1", {"audio_s3_url": "s3://raw/a.mp3"})
    runtime["bot1"] = _resume_state()

    out = tasks.clone_asr_preview_from_job(store=store, source_job_id="bot1")

    assert out["created"] is True and out["status"] == "SUCCEEDED"
    job = store.get(out["job_id"])
    assert job.request["job_kind"] == "asr_preview"
    assert job.request["target_fragment"] == "раз два"
    assert job.result["words"][0]["text"] == "раз"
    assert job.result["clip_start_abs"] == 10.0 and job.result["clip_end_abs"] == 13.5
    saved = json.loads((tmp_path / "jobs" / out["job_id"] / "data" / "llm_resume_state.json").read_text(encoding="utf-8"))
    # Stage 1 с метадатой cache-compat — да; Stage 2 и прочее бот-джобы — нет
    assert set(saved) == {"stage1_asr", "stage1_asr_mode", "stage1_asr_reference_text",
                          "stage1_alignment_backend", "stage1_alignment_metadata"}

    # правки слов на сайте идут поверх клона тем же путём, что после обычной примерки
    edited = tasks.apply_asr_words_edit(store=store, job_id=out["job_id"], words=[
        {"text": "раз", "t_start": 10.25, "t_end": 10.6},
        {"text": "два", "t_start": 10.7, "t_end": 11.1},
    ])
    assert edited["words"][0]["t_start"] == 10.25

    # повторный клон той же джобы — та же asr-джоба
    again = tasks.clone_asr_preview_from_job(store=store, source_job_id="bot1")
    assert again == {"job_id": out["job_id"], "status": "SUCCEEDED", "created": False}


def test_clone_refuses_non_local_ctc_source(env) -> None:
    store, runtime = env
    store.add("bot1", {})
    runtime["bot1"] = _resume_state(mode="forced_alignment")
    with pytest.raises(ValueError, match="local_ctc"):
        tasks.clone_asr_preview_from_job(store=store, source_job_id="bot1")


def test_footage_plan_is_persisted_into_resume_state_source() -> None:
    # запись плана живёт рядом с stage2_footage.json и в форме storyboard_plan.build_plan
    src = Path(tasks.__file__).resolve().parents[2] / "mlcore" / "gemini_orchestrator.py"
    text = src.read_text(encoding="utf-8")
    assert 'resume_state["stage2_footage_plan"] = _build_storyboard_plan(' in text
    # и ни reuse, ни llm-кэш его не переносят
    assert "stage2_footage_plan" not in tasks._REUSE_RESUME_STATE_KEYS
