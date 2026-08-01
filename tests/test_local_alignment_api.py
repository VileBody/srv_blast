from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mlcore.alignment.api import create_app
from mlcore.alignment.core import (
    AlignmentFailure,
    AlignmentResult,
    ERROR_INTERNAL,
    ERROR_MODEL_UNAVAILABLE,
    ERROR_TIMEOUT,
    ERROR_WINDOW_MISMATCH,
)
from mlcore.alignment.contracts import (
    ERROR_PRONUNCIATION_UNAVAILABLE,
    ERROR_SEPARATOR_UNAVAILABLE,
    ERROR_SOURCE_SEPARATION_FAILED,
)
from mlcore.alignment.runtime import AlignmentRuntime, AlignmentSettings
from mlcore.models.stage1_asr import Stage1AsrPayload


def _payload() -> Stage1AsrPayload:
    return Stage1AsrPayload.model_validate(
        {
            "transcript_words": [
                {"text": "тест", "t_start": 10.0, "t_end": 10.5},
            ],
            "pause_spans": [],
            "srt_items": [],
            "selected_fragment": {
                "audio": {"clip_start_abs": 10.0, "clip_end_abs": 20.0},
                "transcript_words": [
                    {"text": "тест", "t_start": 10.0, "t_end": 10.5},
                ],
            },
        }
    )


class _FakeRuntime:
    def __init__(
        self,
        *,
        failure: AlignmentFailure | None = None,
        ready: bool = True,
    ):
        self.failure = failure
        self.started = False
        self.ready = ready

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.started = False

    def status(self):
        return {
            "ready": self.ready,
            "model_revision": "rev-test",
            "load_error": "" if self.ready else "weights unavailable",
        }

    async def align(self, **_kwargs):
        if self.failure is not None:
            raise self.failure
        return AlignmentResult(
            stage1_asr=_payload(),
            diagnostics={"word_count": 1, "warnings": []},
            backend={"type": "local_ctc_viterbi", "model_revision": "rev-test"},
        )


def test_alignment_api_health_ready_and_align() -> None:
    app = create_app(_FakeRuntime())
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").status_code == 200
        response = client.post(
            "/align",
            json={
                "audio_path": "/app/work/jobs/a/data/track.mp3",
                "target_fragment": "тест",
                "clip_start_abs": 10.0,
                "clip_end_abs": 20.0,
                "request_id": "job-a",
            },
        )
    assert response.status_code == 200
    assert response.json()["stage1_asr"]["transcript_words"][0]["text"] == "тест"


def test_alignment_api_returns_stable_error_code() -> None:
    app = create_app(
        _FakeRuntime(
            failure=AlignmentFailure(
                "ALIGNMENT_UNSUPPORTED_TEXT",
                "unsupported",
                details={"hard_valid_candidate_count": 0},
            )
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/align",
            json={
                "audio_path": "/app/work/jobs/a/data/track.mp3",
                "target_fragment": "test",
                "clip_start_abs": 10.0,
                "clip_end_abs": 20.0,
            },
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ALIGNMENT_UNSUPPORTED_TEXT"
    assert response.json()["error"]["details"] == {
        "hard_valid_candidate_count": 0
    }


@pytest.mark.parametrize(
    ("code", "expected_status"),
    [
        (ERROR_SEPARATOR_UNAVAILABLE, 503),
        (ERROR_PRONUNCIATION_UNAVAILABLE, 503),
        (ERROR_SOURCE_SEPARATION_FAILED, 500),
    ],
)


def test_alignment_api_maps_separator_errors(
    code: str,
    expected_status: int,
) -> None:
    app = create_app(_FakeRuntime(failure=AlignmentFailure(code, "failed")))
    with TestClient(app) as client:
        response = client.post(
            "/align",
            json={
                "audio_path": "/app/work/jobs/a/data/track.mp3",
                "target_fragment": "тест",
                "clip_start_abs": 10.0,
                "clip_end_abs": 20.0,
            },
        )

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == code


def test_alignment_api_readiness_reports_unavailable_weights() -> None:
    app = create_app(_FakeRuntime(ready=False))
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["ready"] is False


def _settings(tmp_path: Path, *, timeout_s: float = 5.0) -> AlignmentSettings:
    return AlignmentSettings(
        model_path=tmp_path / "model",
        model_revision="rev",
        allowed_audio_root=tmp_path / "jobs",
        ffmpeg_bin="ffmpeg",
        ffmpeg_timeout_s=120.0,
        timeout_s=timeout_s,
        padding_left_sec=0.5,
        padding_right_sec=0.5,
        min_word_confidence=0.05,
        pause_min_gap_sec=0.35,
        dynamic_window_max_adjust_sec=2.0,
        dynamic_window_step_sec=0.5,
        dynamic_window_min_edge_clearance_sec=0.12,
        dynamic_window_stability_tolerance_sec=0.12,
        dynamic_window_min_consensus_candidates=3,
        dynamic_window_score_tolerance=0.12,
        dynamic_window_min_boundary_duration_ratio=0.15,
        max_window_sec=120.0,
        max_reference_words=400,
        torch_threads=1,
        audio_preprocessor="demucs",
        demucs_model_repo=tmp_path / "demucs",
        demucs_model_name="htdemucs",
        demucs_model_revision="separator-rev",
        demucs_package_version="4.1.0",
        demucs_segment_sec=7.0,
        demucs_overlap=0.25,
        pronunciation_mode="espeak_en_to_ru",
        espeak_bin="espeak-ng",
        espeak_voice="en-us",
        espeak_expected_version="1.51",
        espeak_timeout_s=2.0,
        pronunciation_overrides_path=tmp_path / "pronunciations.json",
    )


def test_alignment_runtime_reports_missing_model_and_invalid_path(
    tmp_path: Path,
) -> None:
    runtime = AlignmentRuntime(_settings(tmp_path))
    runtime._load_model()
    assert runtime.ready is False
    assert "model directory is missing" in runtime.load_error
    assert runtime.status()["dynamic_window"]["max_adjust_sec"] == 2.0

    runtime._ready = True
    with pytest.raises(AlignmentFailure) as path_error:
        runtime.resolve_audio_path(str(tmp_path / "outside.mp3"))
    assert path_error.value.code == ERROR_INTERNAL


def test_alignment_runtime_rejects_invalid_dynamic_window_policy(
    tmp_path: Path,
) -> None:
    settings = replace(_settings(tmp_path), dynamic_window_max_adjust_sec=0.0)
    runtime = AlignmentRuntime(settings)

    runtime._load_model()

    assert runtime.ready is False
    assert runtime.status()["load_error_code"] == ERROR_INTERNAL
    assert "max adjustment must be positive" in runtime.load_error


def test_alignment_runtime_rejects_window_and_times_out(tmp_path: Path) -> None:
    runtime = AlignmentRuntime(_settings(tmp_path, timeout_s=0.01))
    runtime._ready = True
    with pytest.raises(AlignmentFailure) as window_error:
        runtime._align_sync(
            audio_path="unused",
            target_fragment="тест",
            clip_start_abs=10.0,
            clip_end_abs=10.0,
        )
    assert window_error.value.code == ERROR_WINDOW_MISMATCH

    def slow_align(**_kwargs):
        time.sleep(0.05)
        return AlignmentResult(_payload(), {}, {})

    runtime._align_sync = slow_align  # type: ignore[method-assign]

    async def run() -> None:
        with pytest.raises(AlignmentFailure) as timeout_error:
            await runtime.align(
                audio_path="unused",
                target_fragment="тест",
                clip_start_abs=10.0,
                clip_end_abs=20.0,
            )
        assert timeout_error.value.code == ERROR_TIMEOUT
        assert runtime.ready is False
        assert runtime.status()["load_error_code"] == ERROR_TIMEOUT
        with pytest.raises(AlignmentFailure) as busy_error:
            await runtime.align(
                audio_path="unused",
                target_fragment="тест",
                clip_start_abs=10.0,
                clip_end_abs=20.0,
            )
        assert busy_error.value.code == ERROR_TIMEOUT
        await asyncio.sleep(0.06)
        assert runtime.ready is True
        await runtime.close()

    asyncio.run(run())


def test_alignment_runtime_stays_unready_until_all_timed_out_jobs_finish(
    tmp_path: Path,
) -> None:
    runtime = AlignmentRuntime(_settings(tmp_path, timeout_s=0.01))
    runtime._ready = True
    call_lock = threading.Lock()
    call_index = 0
    release_jobs = [threading.Event(), threading.Event()]
    started_jobs = [threading.Event(), threading.Event()]

    def controlled_align(**_kwargs):
        nonlocal call_index
        with call_lock:
            index = call_index
            call_index += 1
        started_jobs[index].set()
        assert release_jobs[index].wait(timeout=2.0)
        return AlignmentResult(_payload(), {}, {})

    runtime._align_sync = controlled_align  # type: ignore[method-assign]

    async def run() -> None:
        async def request() -> AlignmentResult:
            return await runtime.align(
                audio_path="unused",
                target_fragment="test",
                clip_start_abs=10.0,
                clip_end_abs=20.0,
            )

        results = await asyncio.gather(request(), request(), return_exceptions=True)
        assert all(
            isinstance(result, AlignmentFailure) and result.code == ERROR_TIMEOUT
            for result in results
        )
        assert runtime.ready is False

        release_jobs[0].set()
        assert await asyncio.to_thread(started_jobs[1].wait, 1.0)
        assert runtime.ready is False

        release_jobs[1].set()
        for _ in range(100):
            if runtime.ready:
                break
            await asyncio.sleep(0.01)
        assert runtime.ready is True
        await runtime.close()

    asyncio.run(run())

def test_alignment_runtime_fails_when_model_is_not_ready(tmp_path: Path) -> None:
    runtime = AlignmentRuntime(_settings(tmp_path))

    async def run() -> None:
        with pytest.raises(AlignmentFailure) as unavailable:
            await runtime.align(
                audio_path="unused",
                target_fragment="тест",
                clip_start_abs=10.0,
                clip_end_abs=20.0,
            )
        assert unavailable.value.code == ERROR_MODEL_UNAVAILABLE
        await runtime.close()

    asyncio.run(run())


def test_alignment_runtime_serializes_inference(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    runtime = AlignmentRuntime(settings)
    runtime._ready = True
    active = 0
    peak = 0

    def fake_align_sync(**_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        time.sleep(0.05)
        active -= 1
        return AlignmentResult(_payload(), {}, {})

    runtime._align_sync = fake_align_sync  # type: ignore[method-assign]

    async def run() -> None:
        await asyncio.gather(
            runtime.align(
                audio_path="a",
                target_fragment="тест",
                clip_start_abs=10.0,
                clip_end_abs=20.0,
            ),
            runtime.align(
                audio_path="b",
                target_fragment="тест",
                clip_start_abs=10.0,
                clip_end_abs=20.0,
            ),
        )
        await runtime.close()

    asyncio.run(run())
    assert peak == 1
