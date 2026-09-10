"""Advance production batches without an open browser (one API worker).

The API and monitor share a lock: polling must not dispatch the next variation
twice or race its persisted state. Credit refunds use the job owner explicitly.
"""
from __future__ import annotations

import asyncio
import logging

from starlette.concurrency import run_in_threadpool

from . import analytics, auth_store, notifications, persistence
from . import mock_store as store

log = logging.getLogger(__name__)
_lock = asyncio.Lock()
_task: asyncio.Task | None = None
_notification_task: asyncio.Task | None = None


async def enqueue_job(job: dict) -> None:
    from .production_backend import get_backend

    async with _lock:
        await run_in_threadpool(get_backend().enqueue_job, job)
        job["productionNotifications"] = True
        await run_in_threadpool(persistence.save_job, job["id"])


async def sync_job(job: dict) -> None:
    from .billing_backend import get_billing
    from .production_backend import get_backend

    async with _lock:
        await run_in_threadpool(get_backend().sync_job, job)
        if job.get("status") in {"COMPLETED", "FAILED"}:
            owner = str(job.get("userId") or "")
            completed = sum(video.get("status") == "COMPLETED" for video in job.get("videos", []))
            failed = sum(video.get("status") == "FAILED" for video in job.get("videos", []))
            try:
                if completed:
                    await run_in_threadpool(
                        analytics.track_once,
                        "generation_completed",
                        owner,
                        f"job:{job['id']}:completed",
                        {"jobId": job["id"], "videos": completed, "projectId": job.get("projectId")},
                    )
                if failed:
                    await run_in_threadpool(
                        analytics.track_once,
                        "generation_failed",
                        owner,
                        f"job:{job['id']}:failed",
                        {"jobId": job["id"], "videos": failed, "projectId": job.get("projectId")},
                    )
            except Exception:
                # A failed analytics write is retried on the next poll. Refunds,
                # user-visible status and notifications must still advance.
                log.exception("production_monitor: analytics reconciliation failed job=%s", job.get("id"))
        if job.get("status") == "FAILED" and not job.get("failedCreditsRefunded"):
            failed = sum(v.get("status") == "FAILED" for v in job.get("videos", []))
            chat_id = auth_store.chat_id_for_user(job.get("userId") or "")
            if failed:
                if not chat_id:
                    raise RuntimeError(f"refund owner has no Telegram chat: job={job['id']}")
                await get_billing().refund(int(chat_id), job["id"], failed)
            job["failedCreditsRefunded"] = failed
        await run_in_threadpool(persistence.save_job, job["id"])
        if job.get("productionNotifications"):
            await run_in_threadpool(notifications.queue_job, job)
            job["notificationsQueued"] = job.get("status") in {"COMPLETED", "FAILED"}
            await run_in_threadpool(persistence.save_job, job["id"])


async def _run() -> None:
    while True:
        for job in list(store.JOBS.values()):
            if not job.get("orchestratorJobId"):
                continue
            terminal = job.get("status") in {"COMPLETED", "FAILED"}
            if terminal and (not job.get("productionNotifications") or job.get("notificationsQueued")):
                continue
            try:
                await sync_job(job)
            except Exception:
                log.exception("production_monitor: job sync failed job=%s", job.get("id"))
        await asyncio.sleep(5)


async def _deliver() -> None:
    from .billing_backend import get_billing

    while True:
        try:
            await get_billing().queue_payment_notifications()
        except Exception:
            log.exception("production_monitor: payment notification reconciliation failed")
        try:
            await run_in_threadpool(notifications.deliver_pending)
        except Exception:
            log.exception("production_monitor: notification delivery tick failed")
        await asyncio.sleep(5)


def start() -> None:
    global _task, _notification_task
    # Resume active batches after deploy; historical completed jobs must not
    # produce a burst of old Telegram notifications.
    for job in store.JOBS.values():
        if job.get("orchestratorJobId") and job.get("status") not in {"COMPLETED", "FAILED"}:
            job["productionNotifications"] = True
    _task = asyncio.create_task(_run(), name="web-production-monitor")
    _notification_task = asyncio.create_task(_deliver(), name="web-notification-delivery")


async def stop() -> None:
    global _task, _notification_task
    for task in (_task, _notification_task):
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _task = _notification_task = None
