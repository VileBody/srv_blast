from __future__ import annotations

import importlib.util
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def _load_runtime_module() -> ModuleType:
    runtime_dir = Path(__file__).resolve().parents[1] / "windows" / "render-node-runtime"
    module_name = "windows_render_node_main_backpressure_test"
    sys.path.insert(0, str(runtime_dir))
    try:
        spec = importlib.util.spec_from_file_location(module_name, runtime_dir / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(runtime_dir))


@pytest.fixture()
def runtime_module():
    module = _load_runtime_module()
    try:
        yield module
    finally:
        module.manager.shutdown(wait=True)
        sys.modules.pop(module.__name__, None)


def _wait_until(predicate, *, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


def test_render_manager_bounds_workers_and_pending_queue(
    runtime_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    max_active = 0

    class _BlockingRenderer:
        def run_job(self, spec):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            release.wait(timeout=3.0)
            with lock:
                active -= 1
            return SimpleNamespace(
                success=True,
                message="ok",
                app_dir=tmp_path / spec.job_id,
                output_path=None,
                output_s3_url=None,
            )

    monkeypatch.setattr(runtime_module, "renderer", _BlockingRenderer())
    monkeypatch.setattr(
        runtime_module,
        "make_job_spec_from_payload",
        lambda payload: SimpleNamespace(job_id=payload["job_id"]),
    )
    manager = runtime_module.RenderTaskManager(max_workers=2, max_pending=3)
    try:
        states = [manager.submit({"job_id": f"job-{idx}"}) for idx in range(3)]
        _wait_until(lambda: manager.stats()["running"] == 2)

        assert manager.stats() == {
            "running": 2,
            "queued": 1,
            "unfinished": 3,
            "max_workers": 2,
            "max_pending": 3,
            "available_slots": 0,
            "ready": False,
        }
        with pytest.raises(runtime_module.RenderQueueFullError, match="render_queue_full"):
            manager.submit({"job_id": "job-over-limit"})

        duplicate = manager.submit({"job_id": "job-0"})
        assert duplicate.render_id == states[0].render_id
        assert max_active == 2

        release.set()
        _wait_until(lambda: manager.stats()["unfinished"] == 0)
        assert all(manager.get(st.render_id).status == "succeeded" for st in states)
    finally:
        release.set()
        manager.shutdown(wait=True)


def test_render_api_reports_capacity_and_rejects_new_work_when_full(
    runtime_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = threading.Event()

    class _BlockingRenderer:
        def run_job(self, spec):
            release.wait(timeout=3.0)
            return SimpleNamespace(
                success=True,
                message="ok",
                app_dir=tmp_path / spec.job_id,
                output_path=None,
                output_s3_url=None,
            )

    monkeypatch.setattr(runtime_module, "renderer", _BlockingRenderer())
    monkeypatch.setattr(
        runtime_module,
        "make_job_spec_from_payload",
        lambda payload: SimpleNamespace(job_id=payload["job_id"]),
    )
    manager = runtime_module.RenderTaskManager(max_workers=1, max_pending=1)
    monkeypatch.setattr(runtime_module, "manager", manager)
    client = TestClient(runtime_module.app)
    try:
        first = client.post("/render", json={"job_id": "job-1"})
        assert first.status_code == 200
        _wait_until(lambda: manager.stats()["running"] == 1)

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["render"]["available_slots"] == 0

        ready = client.get("/ready")
        assert ready.status_code == 503
        assert ready.headers["retry-after"] == "15"
        assert ready.json()["detail"]["code"] == "render_queue_full"

        rejected = client.post("/render", json={"job_id": "job-2"})
        assert rejected.status_code == 503
        assert rejected.json()["detail"]["code"] == "render_queue_full"

        duplicate = client.post("/render", json={"job_id": "job-1"})
        assert duplicate.status_code == 200
        assert duplicate.json()["render_id"] == first.json()["render_id"]
    finally:
        release.set()
        manager.shutdown(wait=True)
