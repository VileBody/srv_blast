from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
import time

import pytest

from services.orchestrator import tasks


class _Store:
    def __init__(self, request: dict[str, Any]) -> None:
        self.key_prefix = "test"
        self.r = object()
        self.state = SimpleNamespace(
            request=request,
            result=None,
            status="RUNNING",
            started_at=1.0,
            updated_at=1.0,
        )

    def get(self, _job_id: str):
        return self.state

    def set_status(self, _job_id: str, status: str, *, stage=None, error=None, result=None):
        self.state.status = status
        self.state.stage = stage
        if result is not None:
            merged = dict(self.state.result or {})
            merged.update(result)
            self.state.result = merged
        if error is not None:
            self.state.error = error
        return self.state


def _settings(*, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        work_dir="/work",
        output_dir="/output",
        celery_queue_render_poll="render-poll",
        rust_gen_enabled=enabled,
        rust_gen_manager_url="http://rust-gen:8090",
        rust_gen_manager_token="token",
        rust_gen_timeout_s=10.0,
        rust_gen_poll_interval_s=1.0,
        rust_gen_poll_timeout_s=300.0,
        rust_gen_presign_ttl_s=60,
        rust_gen_canary_enabled=False,
        rust_gen_canary_subtitle_modes=(),
    )


def _patch_dispatch(monkeypatch: pytest.MonkeyPatch, store: _Store, tmp_path: Path) -> None:
    payload = tmp_path / "render.json"
    payload.write_text("{}", encoding="utf-8")
    paths = SimpleNamespace(render_payload=payload)
    monkeypatch.setattr(tasks.JobStore, "from_env", classmethod(lambda _cls: store))
    monkeypatch.setattr(tasks, "SETTINGS", _settings())
    monkeypatch.setattr(tasks, "make_job_paths", lambda **_kwargs: paths)
    monkeypatch.setattr(tasks, "build_rust_gen_job_payload", lambda **kwargs: {"job_id": kwargs["job_id"]})
    monkeypatch.setenv("S3_BUCKET_OUTPUT_VIDEO", "rendered")


def test_rust_gen_dispatch_schedules_native_poll(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = _Store({"render_engine": "rust-gen", "audio_s3_url": "s3://raw/track.mp3", "subtitles_mode": "brat_5th"})
    _patch_dispatch(monkeypatch, store, tmp_path)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(tasks.poll_rust_gen_render, "apply_async", lambda **kwargs: calls.append(kwargs))

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def dispatch_render(self, _payload):
            return {"status": "accepted", "render_id": "rust-job-1"}

    monkeypatch.setattr(tasks, "RustGenClient", _Client)
    out = tasks.dispatch_to_rust_gen.run("job-1")

    assert out["mode"] == "rust-gen"
    assert calls[0]["args"] == ["job-1", "rust-job-1"]
    assert store.state.result["dispatch"]["engine"] == "rust-gen"


def test_rust_gen_poll_returns_manager_video_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store({"render_engine": "rust-gen", "audio_s3_url": "s3://raw/track.mp3", "subtitles_mode": "brat_5th"})
    store.state.result = {"poll_started_at": time.time()}
    monkeypatch.setattr(tasks.JobStore, "from_env", classmethod(lambda _cls: store))
    monkeypatch.setattr(tasks, "SETTINGS", _settings())

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_render_status(self, _render_id):
            return {
                "status": "succeeded",
                "job": {"artifact_refs": {"video": "s3://rendered/renders/job-1/output.mp4"}},
            }

    monkeypatch.setattr(tasks, "RustGenClient", _Client)
    out = tasks.poll_rust_gen_render.run("job-1", "rust-job-1")

    assert out["status"] == "succeeded"
    assert store.state.status == "SUCCEEDED"
    assert store.state.result["output_url"] == "s3://rendered/renders/job-1/output.mp4"


def test_rust_gen_never_falls_back_to_windows_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store({"render_engine": "rust-gen", "audio_s3_url": "s3://raw/track.mp3"})
    monkeypatch.setattr(tasks.JobStore, "from_env", classmethod(lambda _cls: store))
    monkeypatch.setattr(tasks, "SETTINGS", _settings(enabled=False))
    monkeypatch.setattr(tasks, "WindowsRenderClient", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AE fallback")))

    with pytest.raises(RuntimeError, match="rust_gen_disabled"):
        tasks.dispatch_to_rust_gen.run("job-1")
