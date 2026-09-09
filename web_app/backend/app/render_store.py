"""Хранилище рендер-очереди с двумя взаимозаменяемыми бэкендами.

- InMemoryRenderStore — мок-рантайм (работает поверх mock_store.JOBS, его читает /api/jobs).
- PgRenderStore — прод (Postgres, claim через FOR UPDATE SKIP LOCKED). DDL: db/migrations/001_render_jobs.sql.

Переключение: переменная окружения DATABASE_URL. Интерфейс один → воркер (render_worker.py)
не знает, на каком бэкенде работает. Spec: backend/docs/RENDER_JOB_SPEC.md §6/§9.
"""
from __future__ import annotations

import os
import threading
from collections import deque
from copy import deepcopy
from typing import Any, Protocol


class RenderStore(Protocol):
    def enqueue(self, job: dict[str, Any]) -> None: ...
    def claim(self, worker_id: str) -> dict[str, Any] | None: ...   # None = очередь пуста
    def update_variation(self, job_id: str, idx: int, **fields: Any) -> None: ...
    def finish_job(self, job_id: str, status: str, **fields: Any) -> None: ...
    def heartbeat(self, job_id: str, worker_id: str) -> None: ...
    def snapshot(self, job_id: str) -> dict[str, Any] | None: ...


class InMemoryRenderStore:
    """FIFO поверх mock_store.JOBS — статусы видны фронту без отдельного слоя чтения."""

    def __init__(self) -> None:
        self._q: deque[str] = deque()
        self._lock = threading.Lock()

    def enqueue(self, job: dict[str, Any]) -> None:
        from .mock_store import JOBS
        with self._lock:
            job["stage"] = "queued"
            job["status"] = "PENDING"
            for v in job["videos"]:
                v.update(status="PENDING", progress=0, stage="queued")
            JOBS[job["id"]] = job
            self._q.append(job["id"])

    def claim(self, worker_id: str) -> dict[str, Any] | None:
        from .mock_store import JOBS
        with self._lock:
            if not self._q:
                return None
            job = JOBS.get(self._q.popleft())
            if job:
                job.update(stage="processing", status="PROCESSING", workerId=worker_id)
            return job

    def update_variation(self, job_id: str, idx: int, **fields: Any) -> None:
        from .mock_store import JOBS
        job = JOBS.get(job_id)
        if not job:
            return
        with self._lock:
            for v in job["videos"]:
                if v["index"] == idx:
                    v.update(fields)

    def finish_job(self, job_id: str, status: str, **fields: Any) -> None:
        from .mock_store import JOBS
        job = JOBS.get(job_id)
        if not job:
            return
        with self._lock:
            job["stage"] = "done" if status == "COMPLETED" else "failed"
            job["status"] = status
            job.update(fields)

    def heartbeat(self, job_id: str, worker_id: str) -> None:  # in-mem — не требуется
        return None

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        from .mock_store import JOBS
        job = JOBS.get(job_id)
        return deepcopy(job) if job else None


class PgRenderStore:
    """Postgres-бэкенд (референс, включается при DATABASE_URL). Требует psycopg[binary]."""

    def __init__(self, url: str) -> None:
        import psycopg  # noqa: F401 — падение здесь → get_store() уйдёт на InMemory
        from psycopg.types.json import Json

        self._psycopg = psycopg
        self._Json = Json
        self._url = url

    def _conn(self):
        return self._psycopg.connect(self._url, autocommit=True)

    def enqueue(self, job: dict[str, Any]) -> None:
        rj = job["renderJob"]
        with self._conn() as c:
            c.execute(
                "INSERT INTO render_jobs (id, project_id, user_id, idempotency_key, status, render_job) "
                "VALUES (%s,%s,%s,%s,'queued',%s) ON CONFLICT (user_id, idempotency_key) DO NOTHING",
                (job["id"], job.get("projectId"), job["userId"], rj.get("idempotencyKey"), self._Json(rj)),
            )
            for v in rj["variations"]:
                c.execute(
                    "INSERT INTO render_variations (job_id, idx, spec) VALUES (%s,%s,%s) "
                    "ON CONFLICT (job_id, idx) DO NOTHING",
                    (job["id"], v["index"], self._Json(v)),
                )

    def claim(self, worker_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "UPDATE render_jobs SET status='processing', worker_id=%s, started_at=now(), "
                "heartbeat_at=now(), attempts=attempts+1 WHERE id = ("
                "  SELECT id FROM render_jobs WHERE status='queued' ORDER BY created_at "
                "  FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id, render_job",
                (worker_id,),
            ).fetchone()
            if not row:
                return None
            job_id, render_job = row
            idxs = c.execute(
                "SELECT idx FROM render_variations WHERE job_id=%s ORDER BY idx", (job_id,)
            ).fetchall()
        return {
            "id": job_id, "projectId": render_job.get("projectId"), "userId": render_job["userId"],
            "versions": len(idxs), "renderJob": render_job,
            "videos": [{"index": i[0]} for i in idxs],
        }

    _VCOLS = {"status": "status", "stage": "stage", "progress": "progress",
              "downloadUrl": "download_url", "error": "error"}

    def update_variation(self, job_id: str, idx: int, **fields: Any) -> None:
        cols = [(self._VCOLS[k], v) for k, v in fields.items() if k in self._VCOLS]
        if not cols:
            return
        sets = ", ".join(f"{c}=%s" for c, _ in cols) + ", updated_at=now()"
        with self._conn() as c:
            c.execute(f"UPDATE render_variations SET {sets} WHERE job_id=%s AND idx=%s",
                      (*[v for _, v in cols], job_id, idx))

    def finish_job(self, job_id: str, status: str, **fields: Any) -> None:
        st = "done" if status == "COMPLETED" else "failed"
        with self._conn() as c:
            c.execute("UPDATE render_jobs SET status=%s, completed_at=now(), error=%s WHERE id=%s",
                      (st, fields.get("error"), job_id))

    def heartbeat(self, job_id: str, worker_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE render_jobs SET heartbeat_at=now() WHERE id=%s AND worker_id=%s",
                      (job_id, worker_id))

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            head = c.execute("SELECT status FROM render_jobs WHERE id=%s", (job_id,)).fetchone()
            if not head:
                return None
            vs = c.execute(
                "SELECT idx, status, stage, progress, download_url FROM render_variations "
                "WHERE job_id=%s ORDER BY idx", (job_id,)
            ).fetchall()
        return {"id": job_id, "status": head[0],
                "videos": [{"index": v[0], "status": v[1], "stage": v[2],
                            "progress": v[3], "downloadUrl": v[4]} for v in vs]}


_INMEM = InMemoryRenderStore()
_store: RenderStore | None = None


def get_store() -> RenderStore:
    global _store
    if _store is not None:
        return _store
    url = os.getenv("DATABASE_URL")
    if url:
        try:
            _store = PgRenderStore(url)
            return _store
        except Exception:  # noqa: BLE001 — нет psycopg/БД → мок in-memory
            pass
    _store = _INMEM
    return _store
