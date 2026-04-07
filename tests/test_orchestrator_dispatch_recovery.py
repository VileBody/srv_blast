from __future__ import annotations

import urllib.error
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.orchestrator import tasks
from services.orchestrator.observability_metrics import get_counter_map


class _FakeRedis:
    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, int]] = {}

    def hincrby(self, key: str, field: str, amount: int) -> int:
        bucket = self._hashes.setdefault(key, {})
        bucket[field] = int(bucket.get(field, 0)) + int(amount)
        return int(bucket[field])

    def hgetall(self, key: str) -> dict[str, str]:
        bucket = self._hashes.get(key, {})
        return {k: str(v) for k, v in bucket.items()}


class _FakeStore:
    def __init__(self, *, job_id: str, request: dict[str, Any]) -> None:
        self.key_prefix = "blast_test"
        self.r = _FakeRedis()
        self._job_id = str(job_id)
        self._state = SimpleNamespace(
            job_id=str(job_id),
            request=dict(request),
            status="NEW",
            stage=None,
            result=None,
            error=None,
        )

    def _redis_call(self, _op: str, fn):
        return fn()

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


class _RetryCalled(Exception):
    pass


def _make_paths(tmp_path: Path) -> _FakePaths:
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    render_jsx = out_dir / "render_full.jsx"
    render_payload = out_dir / "final_render_instructions_full.json"
    render_jsx.write_text("// jsx", encoding="utf-8")
    render_payload.write_text("{}", encoding="utf-8")
    return _FakePaths(render_jsx=render_jsx, render_payload=render_payload)


def _patch_dispatch_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path: Path,
    store: _FakeStore,
    output_exists: bool,
) -> None:
    paths = _make_paths(tmp_path)
    monkeypatch.setattr(tasks.JobStore, "from_env", classmethod(lambda cls: store))
    monkeypatch.setattr(tasks, "WindowsNodePool", _FakeNodePool)
    monkeypatch.setattr(tasks, "make_job_paths", lambda **kwargs: paths)
    monkeypatch.setattr(tasks, "_windows_default_urls", lambda: ["http://win-node:8000"])
    monkeypatch.setattr(tasks, "build_windows_job_payload", lambda **kwargs: {"job_id": kwargs["job_id"]})
    monkeypatch.setattr(tasks, "_s3_head_exists", lambda **kwargs: output_exists)

    class _FailingWindowsClient:
        def __init__(
            self,
            _base_url: str,
            *,
            timeout_s: float = 30.0,
            api_mode: str = "jobs",
        ) -> None:
            _ = (timeout_s, api_mode)

        def dispatch_render(self, payload):
            _ = payload
            raise urllib.error.URLError("timed out")

    monkeypatch.setattr(tasks, "WindowsRenderClient", _FailingWindowsClient)
    monkeypatch.setenv("DISPATCH_RECOVERY_FROM_S3_ENABLED", "1")
    monkeypatch.setenv("S3_BUCKET_OUTPUT_VIDEO", "output-bucket")


def test_dispatch_transient_all_nodes_failed_recovers_if_output_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_id = "job_dispatch_recovery_ok"
    store = _FakeStore(job_id=job_id, request={"audio_s3_url": "s3://bucket/raw/audio.mp3"})
    _patch_dispatch_common(monkeypatch, tmp_path=tmp_path, store=store, output_exists=True)

    monkeypatch.setattr(
        tasks.dispatch_to_windows,
        "retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unexpected_retry")),
    )
    monkeypatch.setattr(tasks.dispatch_to_windows, "request", SimpleNamespace(retries=1), raising=False)

    out = tasks.dispatch_to_windows.run(job_id)
    assert out["ok"] is True
    assert out["mode"] == "dispatch_recovered_existing_output"
    assert out["output_url"] == f"s3://output-bucket/renders/{job_id}/output.mp4"

    st = store.get(job_id)
    assert st is not None
    assert st.status == "SUCCEEDED"
    assert st.stage == "render"
    assert st.result["output_url"] == f"s3://output-bucket/renders/{job_id}/output.mp4"
    assert st.result["dispatch_recovery"]["marker"] == "dispatch_timeout_but_output_exists"
    assert get_counter_map(store, metric="dispatch_recovery_outcomes") == {"true": 1}


def test_dispatch_transient_all_nodes_failed_retries_if_output_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_id = "job_dispatch_recovery_miss"
    store = _FakeStore(job_id=job_id, request={"audio_s3_url": "s3://bucket/raw/audio.mp3"})
    _patch_dispatch_common(monkeypatch, tmp_path=tmp_path, store=store, output_exists=False)

    retry_calls: list[dict[str, Any]] = []

    def _fake_retry(*args, **kwargs):
        retry_calls.append({"args": args, "kwargs": kwargs})
        raise _RetryCalled("retry_called")

    monkeypatch.setattr(tasks.dispatch_to_windows, "retry", _fake_retry)
    monkeypatch.setattr(tasks.dispatch_to_windows, "request", SimpleNamespace(retries=0), raising=False)

    with pytest.raises(_RetryCalled):
        tasks.dispatch_to_windows.run(job_id)

    assert len(retry_calls) == 1
    kwargs = retry_calls[0]["kwargs"]
    assert float(kwargs["countdown"]) == 5.0
    assert "windows_dispatch_transient" in str(kwargs["exc"])
    assert "all_nodes_failed" in str(kwargs["exc"])
    assert get_counter_map(store, metric="dispatch_recovery_outcomes") == {"false": 1}
