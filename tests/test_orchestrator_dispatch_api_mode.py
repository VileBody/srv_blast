from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.orchestrator import tasks


class _FakeStore:
    def __init__(self, *, job_id: str, request: dict[str, Any]) -> None:
        self.key_prefix = "blast_test"
        self.r = object()
        self._job_id = str(job_id)
        self._state = SimpleNamespace(
            job_id=str(job_id),
            request=dict(request),
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


@dataclass
class _FakePaths:
    render_jsx: Path
    render_payload: Path


class _FakeNodePool:
    def __init__(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    def get_active_urls(self, default_urls=None):
        _ = default_urls
        return ["http://win-node:8000"]

    def reserve_best(self, remaining):
        return str(remaining[0]) if remaining else ""

    def release(self, _url: str) -> None:
        return None


def _make_paths(tmp_path: Path) -> _FakePaths:
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    render_jsx = out_dir / "render_full.jsx"
    render_payload = out_dir / "final_render_instructions_full.json"
    render_jsx.write_text("// jsx", encoding="utf-8")
    render_payload.write_text("{}", encoding="utf-8")
    return _FakePaths(render_jsx=render_jsx, render_payload=render_payload)


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path: Path,
    store: _FakeStore,
    api_mode: str,
) -> None:
    paths = _make_paths(tmp_path)
    monkeypatch.setattr(tasks.JobStore, "from_env", classmethod(lambda cls: store))
    monkeypatch.setattr(tasks, "WindowsNodePool", _FakeNodePool)
    monkeypatch.setattr(tasks, "make_job_paths", lambda **kwargs: paths)
    monkeypatch.setattr(tasks, "_windows_default_urls", lambda: ["http://win-node:8000"])
    monkeypatch.setattr(tasks, "build_windows_job_payload", lambda **kwargs: {"job_id": kwargs["job_id"]})
    monkeypatch.setattr(tasks, "_probe_windows_node_ready", lambda *args, **kwargs: None)

    monkeypatch.setattr(
        tasks,
        "SETTINGS",
        SimpleNamespace(
            work_dir="/tmp/work",
            output_dir="/tmp/output",
            windows_node_lease_ttl_s=7200,
            windows_timeout_s=300.0,
            windows_poll_interval_s=2.0,
            windows_render_api_mode=api_mode,
            celery_queue_render="render",
            celery_queue_render_poll="render_poll",
        ),
    )


def test_dispatch_jobs_mode_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_id = "job_sync_jobs_mode_rejected"
    store = _FakeStore(job_id=job_id, request={"audio_s3_url": "s3://bucket/raw/audio.mp3"})
    _patch_common(monkeypatch, tmp_path=tmp_path, store=store, api_mode="jobs")

    poll_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tasks.poll_windows_render,
        "apply_async",
        lambda *args, **kwargs: poll_calls.append({"args": args, "kwargs": kwargs}),
    )

    class _NeverCalledWindowsClient:
        def __init__(self, _base_url: str, *, timeout_s: float = 30.0, api_mode: str = "jobs") -> None:
            _ = (_base_url, timeout_s, api_mode)
            raise AssertionError("WindowsRenderClient should not be created for jobs mode")

        def dispatch_render(self, payload):
            raise AssertionError(f"dispatch_render should not be called: {payload}")

    monkeypatch.setattr(tasks, "WindowsRenderClient", _NeverCalledWindowsClient)

    with pytest.raises(RuntimeError, match="windows_dispatch_contract_mismatch"):
        tasks.dispatch_to_windows.run(job_id)

    assert poll_calls == []
    st = store.get(job_id)
    assert st is not None
    assert st.status == "NEW"
    assert st.stage is None
    assert st.result is None


def test_dispatch_render_mode_schedules_poll(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_id = "job_async_render_mode"
    store = _FakeStore(job_id=job_id, request={"audio_s3_url": "s3://bucket/raw/audio.mp3"})
    _patch_common(monkeypatch, tmp_path=tmp_path, store=store, api_mode="render")

    poll_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tasks.poll_windows_render,
        "apply_async",
        lambda *args, **kwargs: poll_calls.append({"args": args, "kwargs": kwargs}),
    )

    class _RenderWindowsClient:
        def __init__(self, _base_url: str, *, timeout_s: float = 30.0, api_mode: str = "jobs") -> None:
            _ = (timeout_s, api_mode)

        def dispatch_render(self, payload):
            _ = payload
            return {"_api": "render", "status": "accepted", "render_id": "rid_123"}

    monkeypatch.setattr(tasks, "WindowsRenderClient", _RenderWindowsClient)

    out = tasks.dispatch_to_windows.run(job_id)

    assert out["ok"] is True
    assert out["mode"] == "async_render"
    assert out["render_id"] == "rid_123"
    assert len(poll_calls) == 1
    assert poll_calls[0]["kwargs"]["countdown"] == 2.0
    assert poll_calls[0]["kwargs"]["args"] == [job_id, "rid_123"]

    st = store.get(job_id)
    assert st is not None
    assert st.status == "RUNNING"
    assert st.stage == "poll"
    assert st.result["render_id"] == "rid_123"


def test_dispatch_render_mode_rejects_sync_like_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_id = "job_async_render_contract_mismatch"
    store = _FakeStore(job_id=job_id, request={"audio_s3_url": "s3://bucket/raw/audio.mp3"})
    _patch_common(monkeypatch, tmp_path=tmp_path, store=store, api_mode="render")

    poll_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tasks.poll_windows_render,
        "apply_async",
        lambda *args, **kwargs: poll_calls.append({"args": args, "kwargs": kwargs}),
    )

    class _BadRenderWindowsClient:
        def __init__(self, _base_url: str, *, timeout_s: float = 30.0, api_mode: str = "jobs") -> None:
            _ = (_base_url, timeout_s, api_mode)

        def dispatch_render(self, payload):
            _ = payload
            return {"_api": "jobs", "success": True, "job_id": job_id}

    monkeypatch.setattr(tasks, "WindowsRenderClient", _BadRenderWindowsClient)

    with pytest.raises(RuntimeError, match="windows_dispatch_contract_mismatch"):
        tasks.dispatch_to_windows.run(job_id)
    assert poll_calls == []
