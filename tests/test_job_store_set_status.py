from __future__ import annotations

import json
import threading
import time

from services.orchestrator.job_store import JobStore
from services.orchestrator.schemas import JobState


class _FakeRedis:
    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            return self._kv.get(key)

    def set(self, key: str, value, ex=None, nx: bool = False):  # noqa: ANN001
        with self._lock:
            if nx and key in self._kv:
                return False
            self._kv[key] = str(value)
            return True

    def delete(self, key: str) -> int:
        with self._lock:
            existed = key in self._kv
            self._kv.pop(key, None)
            return 1 if existed else 0

    def mget(self, keys):
        with self._lock:
            return [self._kv.get(k) for k in keys]

    def scan_iter(self, match: str, count: int = 500):  # noqa: ARG002
        prefix = str(match).replace("*", "")
        with self._lock:
            keys = [k for k in self._kv.keys() if k.startswith(prefix)]
        for key in keys:
            yield key

    def eval(self, script: str, numkeys: int, *args):  # noqa: ARG002
        if "obj.version" in script:
            key = str(args[0])
            status = str(args[1])
            stage_arg = str(args[2])
            error_arg = str(args[3])
            patch_raw = str(args[4] or "{}")
            now = float(args[5])

            with self._lock:
                raw = self._kv.get(key)
                if not raw:
                    return None
                obj = json.loads(raw)
                prev = str(obj.get("status") or "")

                obj["status"] = status
                obj["updated_at"] = now
                obj["version"] = int(obj.get("version", 0) or 0) + 1
                if stage_arg:
                    obj["stage"] = stage_arg
                if status == "QUEUED" and obj.get("queued_at") is None:
                    obj["queued_at"] = now
                if status == "RUNNING" and obj.get("started_at") is None:
                    obj["started_at"] = now
                if status in {"SUCCEEDED", "FAILED"} and obj.get("finished_at") is None:
                    obj["finished_at"] = now

                if error_arg == "__CLEAR__":
                    obj["error"] = None
                elif error_arg != "__NONE__":
                    obj["error"] = error_arg

                patch = json.loads(patch_raw)
                if patch:
                    merged = dict(obj.get("result") or {})
                    merged.update(patch)
                    obj["result"] = merged

                encoded = json.dumps(obj, ensure_ascii=False)
                self._kv[key] = encoded
                return [prev, encoded]

        if "redis.call('DECR'" in script:
            key = str(args[0])
            with self._lock:
                cur = int(self._kv.get(key, "0") or "0")
                if cur <= 0:
                    self._kv[key] = "0"
                    return 0
                cur -= 1
                self._kv[key] = str(cur)
                return cur

        raise AssertionError(f"Unexpected eval script: {script}")


def _make_store() -> JobStore:
    return JobStore(r=_FakeRedis(), key_prefix="test")


def _seed_job(store: JobStore, *, status: str = "NEW") -> None:
    st = JobState(
        job_id="job-1",
        status=status,  # type: ignore[arg-type]
        version=1,
        created_at=1.0,
        updated_at=1.0,
        queued_at=None,
        started_at=None,
        finished_at=None,
        stage=None,
        idempotency_key=None,
        request={"llm_worker_type": "sdk"},
        result=None,
        error=None,
    )
    store._put(st)


def test_set_status_updates_version_and_timestamps() -> None:
    store = _make_store()
    _seed_job(store, status="NEW")

    st_q = store.set_status("job-1", "QUEUED", stage="build", result={"step": "queued"})
    st_r = store.set_status("job-1", "RUNNING", stage="llm", result={"step": "running"})
    st_s = store.set_status("job-1", "SUCCEEDED", stage="render", result={"output_url": "https://x"})

    assert st_q is not None and st_q.version == 2 and st_q.queued_at is not None
    assert st_r is not None and st_r.version == 3 and st_r.started_at is not None
    assert st_s is not None and st_s.version == 4 and st_s.finished_at is not None
    assert st_s.result == {"step": "running", "output_url": "https://x"}
    assert st_s.error is None


def test_set_status_releases_slot_only_on_active_to_terminal() -> None:
    store = _make_store()
    _seed_job(store, status="RUNNING")
    inflight_key = store._k_llm_inflight("sdk")
    store.r.set(inflight_key, "1")

    st_failed = store.set_status("job-1", "FAILED", stage="build", error="boom")
    st_failed_again = store.set_status("job-1", "FAILED", stage="build", error="boom2")

    assert st_failed is not None and st_failed.status == "FAILED"
    assert st_failed_again is not None and st_failed_again.status == "FAILED"
    assert int(store.r.get(inflight_key) or 0) == 0


def test_set_status_concurrent_updates_merge_results_without_lost_update() -> None:
    store = _make_store()
    _seed_job(store, status="RUNNING")
    barrier = threading.Barrier(3)

    def _worker(stage: str, key: str) -> None:
        barrier.wait(timeout=2)
        store.set_status("job-1", "RUNNING", stage=stage, result={key: True})

    t1 = threading.Thread(target=_worker, args=("stage_a", "a"), daemon=True)
    t2 = threading.Thread(target=_worker, args=("stage_b", "b"), daemon=True)
    t1.start()
    t2.start()
    barrier.wait(timeout=2)
    t1.join(timeout=2)
    t2.join(timeout=2)

    final = store.get("job-1")
    assert final is not None
    assert final.version >= 3
    assert final.result is not None
    assert final.result.get("a") is True
    assert final.result.get("b") is True
    assert float(final.updated_at) >= 1.0

