from __future__ import annotations

import urllib.error
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.orchestrator import tasks


@dataclass
class _FakePaths:
    render_jsx: Path
    render_payload: Path


class _RetryCalled(Exception):
    pass


class _FakeStore:
    def __init__(self, *, job_id: str, request: dict[str, Any], result: dict[str, Any] | None = None) -> None:
        self.key_prefix = "blast_test"
        self.r = object()
        self._job_id = str(job_id)
        self._state = SimpleNamespace(
            job_id=str(job_id),
            request=dict(request),
            status="NEW",
            stage=None,
            result=dict(result or {}),
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


def test_dispatch_auto_disables_node_on_transient_streak(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = "job_auto_disable_dispatch"
    store = _FakeStore(job_id=job_id, request={"audio_s3_url": "s3://bucket/raw/audio.mp3"})
    paths = _make_paths(tmp_path)

    monkeypatch.setattr(tasks.JobStore, "from_env", classmethod(lambda cls: store))
    monkeypatch.setattr(tasks, "WindowsNodePool", _FakeNodePool)
    monkeypatch.setattr(tasks, "make_job_paths", lambda **kwargs: paths)
    monkeypatch.setattr(tasks, "_windows_default_urls", lambda: ["http://win-node:8000"])
    monkeypatch.setattr(tasks, "build_windows_job_payload", lambda **kwargs: {"job_id": kwargs["job_id"]})
    monkeypatch.setattr(tasks, "_probe_windows_node_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_try_recover_dispatch_from_existing_output", lambda **kwargs: None)
    monkeypatch.setattr(tasks, "_inc_dispatch_fail_streak", lambda *_a, **_k: 1)

    auto_disable_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tasks,
        "_auto_disable_node",
        lambda **kwargs: auto_disable_calls.append(kwargs) or True,
    )

    class _FailingWindowsClient:
        def __init__(self, _base_url: str, *, timeout_s: float = 30.0, api_mode: str = "jobs") -> None:
            _ = (timeout_s, api_mode)

        def dispatch_render(self, payload):
            _ = payload
            raise urllib.error.URLError("timed out")

    monkeypatch.setattr(tasks, "WindowsRenderClient", _FailingWindowsClient)
    monkeypatch.setattr(
        tasks,
        "SETTINGS",
        SimpleNamespace(
            work_dir="/tmp/work",
            output_dir="/tmp/output",
            windows_node_lease_ttl_s=7200,
            windows_timeout_s=300.0,
            windows_poll_interval_s=2.0,
            windows_render_api_mode="render",
            windows_node_disable_after_dispatch_errors=1,
        ),
    )

    def _fake_retry(*args, **kwargs):
        _ = args
        raise _RetryCalled(str(kwargs.get("exc") or "retry_called"))

    monkeypatch.setattr(tasks.dispatch_to_windows, "retry", _fake_retry)

    with pytest.raises(_RetryCalled):
        tasks.dispatch_to_windows.run(job_id)

    assert len(auto_disable_calls) == 1
    assert auto_disable_calls[0]["node_url"] == "http://win-node:8000"
    assert auto_disable_calls[0]["reason"] == "dispatch_transient_streak_1"


def test_poll_timeout_auto_disables_node(monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = "job_auto_disable_poll_timeout"
    render_id = "rid_123"
    store = _FakeStore(
        job_id=job_id,
        request={},
        result={
            "dispatch": {"windows_url": "http://win-node:8000"},
            "poll_started_at": 100.0,
        },
    )

    monkeypatch.setattr(tasks.JobStore, "from_env", classmethod(lambda cls: store))
    monkeypatch.setattr(tasks, "WindowsNodePool", _FakeNodePool)
    monkeypatch.setattr(tasks, "_windows_default_urls", lambda: ["http://win-node:8000"])
    monkeypatch.setattr(tasks, "time", SimpleNamespace(time=lambda: 200.0))

    auto_disable_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tasks,
        "_auto_disable_node",
        lambda **kwargs: auto_disable_calls.append(kwargs) or True,
    )

    monkeypatch.setattr(
        tasks,
        "SETTINGS",
        SimpleNamespace(
            windows_node_lease_ttl_s=7200,
            windows_timeout_s=30.0,
            windows_render_api_mode="render",
            windows_poll_timeout_s=30.0,
            windows_poll_interval_s=2.0,
            windows_node_disable_on_poll_timeout=True,
        ),
    )

    with pytest.raises(RuntimeError, match="windows_poll_timeout"):
        tasks.poll_windows_render.run(job_id, render_id)

    assert len(auto_disable_calls) == 1
    assert auto_disable_calls[0]["node_url"] == "http://win-node:8000"
    assert auto_disable_calls[0]["reason"] == "poll_timeout_before_poll"
