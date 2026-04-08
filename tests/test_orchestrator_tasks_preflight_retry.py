from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.orchestrator import tasks


@dataclass
class _FakePaths:
    data_dir: Path
    out_dir: Path
    logs_dir: Path
    render_jsx: Path
    render_payload: Path
    footage_config: Path

    def manifest(self) -> dict:
        return {"data_dir": str(self.data_dir), "out_dir": str(self.out_dir)}


class _FakeStore:
    def __init__(self, *, job_id: str, request: dict) -> None:
        self._job_id = job_id
        self._state = SimpleNamespace(
            job_id=job_id,
            request=request,
            status="NEW",
            stage=None,
            result=None,
            error=None,
        )

    def get(self, job_id: str):
        if str(job_id) != self._job_id:
            return None
        return self._state

    def set_status(self, job_id: str, status: str, *, stage=None, error=None, result=None):
        assert str(job_id) == self._job_id
        self._state.status = status
        if stage is not None:
            self._state.stage = stage
        if error is not None:
            self._state.error = error
        if result is not None:
            base = dict(self._state.result or {})
            base.update(result)
            self._state.result = base
        return self._state


def _make_paths(tmp_path: Path, job_id: str) -> _FakePaths:
    data_dir = tmp_path / "work" / "jobs" / job_id / "data"
    out_dir = tmp_path / "output" / "jobs" / job_id / "out"
    logs_dir = out_dir / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    render_jsx = out_dir / "render_full.jsx"
    render_payload = out_dir / "final_render_instructions_full.json"
    footage_config = data_dir / "footage_config.json"

    render_jsx.write_text("// ok", encoding="utf-8")
    render_payload.write_text("{}", encoding="utf-8")
    footage_config.write_text(
        json.dumps(
            {
                "layers": [
                    {
                        "type": "audio_only",
                        "file_name": "audio_source.mp3",
                        "file_path": "s3://bucket/audio_source.mp3",
                        "enabled": True,
                        "audio_enabled": True,
                        "video_enabled": False,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return _FakePaths(
        data_dir=data_dir,
        out_dir=out_dir,
        logs_dir=logs_dir,
        render_jsx=render_jsx,
        render_payload=render_payload,
        footage_config=footage_config,
    )


def _patch_common(monkeypatch: pytest.MonkeyPatch, *, tmp_path: Path, job_id: str, store: _FakeStore) -> tuple[_FakePaths, list[str]]:
    paths = _make_paths(tmp_path, job_id)
    dispatch_calls: list[str] = []

    monkeypatch.setattr(tasks, "get_runtime_mode", lambda: tasks.MODE_PROD)
    monkeypatch.setattr(tasks, "_ensure_shared_catalog", lambda _repo_root: None)
    monkeypatch.setattr(tasks, "make_job_paths", lambda **kwargs: paths)
    monkeypatch.setattr(
        tasks,
        "_download",
        lambda _url, dest, timeout_s=300.0: dest.parent.mkdir(parents=True, exist_ok=True) or dest.write_bytes(b"a"),
    )
    monkeypatch.setattr(tasks.dispatch_to_windows, "delay", lambda jid: dispatch_calls.append(str(jid)))
    monkeypatch.setattr(tasks.JobStore, "from_env", classmethod(lambda cls: store))
    monkeypatch.setattr(tasks.build_job, "request", SimpleNamespace(retries=0), raising=False)

    return paths, dispatch_calls


def test_build_job_preflight_first_fail_second_success_continues(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    job_id = "job_preflight_retry_ok"
    store = _FakeStore(
        job_id=job_id,
        request={"audio_s3_url": "s3://bucket/raw/audio.mp3", "mode": "no_gemini", "lyrics_text": ""},
    )
    _paths, dispatch_calls = _patch_common(monkeypatch, tmp_path=tmp_path, job_id=job_id, store=store)

    retry_calls: list[dict] = []
    monkeypatch.setattr(
        tasks.build_job,
        "retry",
        lambda *args, **kwargs: retry_calls.append({"args": args, "kwargs": kwargs}) or (_ for _ in ()).throw(RuntimeError("unexpected_retry")),
    )

    run_calls: list[int] = []

    def _fake_run(*args, **kwargs):
        run_calls.append(1)
        if len(run_calls) == 1:
            return subprocess.CompletedProcess(
                args=kwargs.get("args", []),
                returncode=1,
                stdout="",
                stderr="ValueError: Preflight: out<=in in layer 'X': 2.0..1.0",
            )
        return subprocess.CompletedProcess(args=kwargs.get("args", []), returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(tasks.subprocess, "run", _fake_run)

    out = tasks.build_job.run(job_id)
    assert out["ok"] is True
    assert len(run_calls) == 2
    assert not retry_calls
    assert dispatch_calls == [job_id]


def test_build_job_preflight_two_fails_terminal_without_celery_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_id = "job_preflight_retry_fail"
    store = _FakeStore(
        job_id=job_id,
        request={"audio_s3_url": "s3://bucket/raw/audio.mp3", "mode": "no_gemini", "lyrics_text": ""},
    )
    _paths, dispatch_calls = _patch_common(monkeypatch, tmp_path=tmp_path, job_id=job_id, store=store)

    retry_calls: list[dict] = []
    monkeypatch.setattr(
        tasks.build_job,
        "retry",
        lambda *args, **kwargs: retry_calls.append({"args": args, "kwargs": kwargs}) or (_ for _ in ()).throw(RuntimeError("unexpected_retry")),
    )

    run_calls: list[int] = []

    def _fake_run(*args, **kwargs):
        run_calls.append(1)
        return subprocess.CompletedProcess(
            args=kwargs.get("args", []),
            returncode=1,
            stdout="",
            stderr="ValueError: Preflight: out<=in in layer 'X': 2.0..1.0",
        )

    monkeypatch.setattr(tasks.subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError, match="build_preflight_validation_error_after_immediate_retry"):
        tasks.build_job.run(job_id)

    assert len(run_calls) == 2
    assert not retry_calls
    assert dispatch_calls == []


def test_build_job_preflight_with_gemini_triggers_targeted_subtitles_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_id = "job_preflight_retry_with_gemini"
    store = _FakeStore(
        job_id=job_id,
        request={"audio_s3_url": "s3://bucket/raw/audio.mp3", "mode": "with_gemini", "lyrics_text": ""},
    )
    _paths, dispatch_calls = _patch_common(monkeypatch, tmp_path=tmp_path, job_id=job_id, store=store)

    retry_calls: list[dict] = []
    monkeypatch.setattr(
        tasks.build_job,
        "retry",
        lambda *args, **kwargs: retry_calls.append({"args": args, "kwargs": kwargs}) or (_ for _ in ()).throw(RuntimeError("unexpected_retry")),
    )

    run_calls: list[int] = []

    def _fake_run(*args, **kwargs):
        run_calls.append(1)
        if len(run_calls) == 1:
            return subprocess.CompletedProcess(
                args=kwargs.get("args", []),
                returncode=1,
                stdout="",
                stderr="ValueError: Preflight: out<=in in layer 'X': 2.0..1.0",
            )
        return subprocess.CompletedProcess(args=kwargs.get("args", []), returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(tasks.subprocess, "run", _fake_run)
    monkeypatch.delenv("STAGE2_SUBTITLES_RETRY_HINT", raising=False)

    from mlcore import gemini_orchestrator as go

    llm_calls: list[dict] = []

    def _fake_build_all(**kwargs):
        resume_state_path = kwargs["resume_state_path"]
        snapshot = {
            "hint": os.environ.get("STAGE2_SUBTITLES_RETRY_HINT"),
            "resume_state_path": str(resume_state_path),
            "data_dir": os.environ.get("DATA_DIR"),
            "out_dir": os.environ.get("OUT_DIR"),
            "audio_file_path": os.environ.get("AUDIO_FILE_PATH"),
            "audio_dir": os.environ.get("AUDIO_DIR"),
            "audio_file_name": os.environ.get("AUDIO_FILE_NAME"),
            "provider_mode": os.environ.get("LLM_PROVIDER_MODE"),
        }
        if len(llm_calls) == 0:
            resume_state_path.write_text(
                json.dumps(
                    {
                        "stage1_asr": {"transcript_words": [{"text": "a", "t_start": 0.0, "t_end": 0.5}], "srt_items": []},
                        "stage1_plan": {
                            "audio": {"clip_start_abs": 0.0, "clip_end_abs": 14.0},
                            "transcript_words": [{"text": "a", "t_start": 0.0, "t_end": 0.5}],
                            "draft_blocks": {},
                        },
                        "stage2_subtitles": {"dummy": True},
                        "stage2_style": {"genre": "Rock", "tag": "dark_forest"},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        else:
            snapshot["resume_state"] = json.loads(resume_state_path.read_text(encoding="utf-8"))
        llm_calls.append(snapshot)
        return {
            "audio_plan": tmp_path / "audio_plan.json",
            "full_edit_config": tmp_path / "full_edit_config.json",
            "footage_config": tmp_path / "footage_config.json",
        }

    monkeypatch.setattr(go, "build_all_via_gemini_one_call", _fake_build_all)

    out = tasks.build_job.run(job_id)
    assert out["ok"] is True
    assert len(run_calls) == 2
    assert not retry_calls
    assert dispatch_calls == [job_id]

    assert len(llm_calls) == 2
    assert llm_calls[0]["hint"] is None
    assert isinstance(llm_calls[1]["hint"], str) and "impossible layer timings" in llm_calls[1]["hint"]
    assert "DETECTED_PREFLIGHT_ISSUE" in llm_calls[1]["hint"]
    assert "layer_name: X" in llm_calls[1]["hint"]
    assert "layer_in_point: 2.000000" in llm_calls[1]["hint"]
    assert "layer_out_point: 1.000000" in llm_calls[1]["hint"]
    state_after_drop = llm_calls[1]["resume_state"]
    assert "stage2_subtitles" not in state_after_drop
    assert "stage2_style" in state_after_drop
    expected_audio = _paths.data_dir / "inputs" / "audio" / "audio.mp3"
    for call in llm_calls:
        assert call["data_dir"] == str(_paths.data_dir)
        assert call["out_dir"] == str(_paths.out_dir)
        assert call["audio_file_path"] == str(expected_audio)
        assert call["audio_dir"] == str(expected_audio.parent)
        assert call["audio_file_name"] == "audio_source.mp3"
        assert call["provider_mode"] == "gemini"
    assert os.environ.get("STAGE2_SUBTITLES_RETRY_HINT") is None


def test_build_job_openrouter_pins_provider_mode_openrouter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_id = "job_openrouter_provider_pin"
    store = _FakeStore(
        job_id=job_id,
        request={
            "audio_s3_url": "s3://bucket/raw/audio.mp3",
            "mode": "with_gemini",
            "lyrics_text": "",
            "llm_worker_type": "openrouter",
        },
    )
    _paths, dispatch_calls = _patch_common(monkeypatch, tmp_path=tmp_path, job_id=job_id, store=store)

    monkeypatch.setattr(tasks.build_job_openrouter, "request", SimpleNamespace(retries=0), raising=False)

    def _fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=kwargs.get("args", []), returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(tasks.subprocess, "run", _fake_run)
    monkeypatch.delenv("LLM_PROVIDER_MODE", raising=False)

    from mlcore import gemini_orchestrator as go

    seen_provider_modes: list[str | None] = []

    def _fake_build_all(**kwargs):
        _ = kwargs
        seen_provider_modes.append(os.environ.get("LLM_PROVIDER_MODE"))
        return {
            "audio_plan": tmp_path / "audio_plan.json",
            "full_edit_config": tmp_path / "full_edit_config.json",
            "footage_config": tmp_path / "footage_config.json",
        }

    monkeypatch.setattr(go, "build_all_via_gemini_one_call", _fake_build_all)

    out = tasks.build_job_openrouter.run(job_id)
    assert out["ok"] is True
    assert dispatch_calls == [job_id]
    assert seen_provider_modes == ["openrouter"]


def test_build_job_openrouter_retries_on_internal_500(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_id = "job_openrouter_internal_500_retry"
    store = _FakeStore(
        job_id=job_id,
        request={
            "audio_s3_url": "s3://bucket/raw/audio.mp3",
            "mode": "with_gemini",
            "lyrics_text": "",
            "llm_worker_type": "openrouter",
        },
    )
    _paths, dispatch_calls = _patch_common(monkeypatch, tmp_path=tmp_path, job_id=job_id, store=store)
    monkeypatch.setattr(tasks.build_job_openrouter, "request", SimpleNamespace(retries=0), raising=False)

    from mlcore import gemini_orchestrator as go

    def _fake_build_all(**kwargs):
        _ = kwargs
        raise RuntimeError(
            "Stage2 failed: stage2_subtitles=RuntimeError: "
            "openrouter_bad_response_no_choices: {'error': {'message': 'Internal Server Error', 'code': 500}}"
        )

    retry_calls: list[dict] = []

    class _RetryCalled(Exception):
        pass

    def _fake_retry(*args, **kwargs):
        retry_calls.append({"args": args, "kwargs": kwargs})
        raise _RetryCalled("retry_called")

    monkeypatch.setattr(go, "build_all_via_gemini_one_call", _fake_build_all)
    monkeypatch.setattr(tasks.build_job_openrouter, "retry", _fake_retry)

    with pytest.raises(_RetryCalled):
        tasks.build_job_openrouter.run(job_id)

    assert len(retry_calls) == 1
    kwargs = retry_calls[0]["kwargs"]
    assert float(kwargs["countdown"]) == 10.0
    assert "openrouter_internal_500" in str(kwargs["exc"])
    assert dispatch_calls == []
