"""Рендер-воркер: claim джобы из store → прогон 3 слоёв → статусы.

В моке гоняется фоновым потоком поверх InMemoryRenderStore (тайминги имитируются).
В прод — тот же цикл на AE-ноде поверх PgRenderStore; вместо sleep реальные вызовы:
  1) Assembly  — python script_jakson.py (текст+футаж в комп)
  2) Effects   — run_job.jsx (пишем срез job.effects → afterfx headless)  ← spec §5.3
  3) Render    — aerender → mp4 → S3

Запуск отдельным процессом на ноде: `python -m app.render_worker`.
Spec: backend/docs/RENDER_JOB_SPEC.md §6.
"""
from __future__ import annotations

import threading
import time

from .render_store import RenderStore, get_store

WORKER_ID = "mock-worker-1"
_ASSEMBLE_S = 2.0   # демо-темп слоя Assembly
_RENDER_S = 3.0     # демо-темп слоёв Effects+Render
_TICK = 0.25
_IDLE_SLEEP = 0.2

_started = False
_start_lock = threading.Lock()


def ensure_started(store: RenderStore | None = None) -> None:
    """Идемпотентно поднять фоновый воркер (для мока — из create_job)."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    t = threading.Thread(target=run_forever, args=(store or get_store(),),
                         name="blast-render-worker", daemon=True)
    t.start()


def run_forever(store: RenderStore) -> None:
    while True:
        job = store.claim(WORKER_ID)
        if not job:
            time.sleep(_IDLE_SLEEP)
            continue
        try:
            _process(store, job)
        except Exception as exc:  # noqa: BLE001
            store.finish_job(job["id"], "FAILED", error=str(exc))
            from . import analytics
            analytics.track("generation_failed", job.get("userId") or "", {"jobId": job["id"], "error": str(exc)[:120]})
            _refund(store, job)
            # упавший батч и возвращённые кредиты тоже должны пережить рестарт
            _persist_job(job["id"])
            _persist_workspace(job.get("userId") or "")


def _app_url() -> str:
    """Адрес фронта для диплинка в уведомлении (тот же источник, что у OAuth-возврата)."""
    import os
    from . import tiktok_config

    tiktok_config.load_env()
    return os.getenv("APP_URL", "http://localhost:5173")


def _notify(job: dict, *, index: int | None = None, total: int = 0) -> None:
    """Написать владельцу в Telegram: «Ролик N готов» либо сводку по батчу.

    Сам ролик в бот не шлём — только уведомление с кнопкой на страницу батча:
    смотреть и выкладывать всё равно нужно в приложении.
    """
    from . import auth_store, telegram_bot

    chat_id = auth_store.chat_id_for_user(job.get("userId") or "")
    if not chat_id:
        return
    project_id = job.get("projectId") or ""
    if index is None:
        telegram_bot.notify_batch_done(chat_id, total, project_id, _app_url())
    else:
        telegram_bot.notify_video_ready(chat_id, index, total, project_id, _app_url())


def _refund(store: RenderStore, job: dict) -> None:
    """Вернуть кредиты за не отрендеренные ролики: за брак юзер платить не должен."""
    from . import mock_store

    # воркер живёт вне HTTP-запроса, поэтому воркспейс берём по владельцу джоба,
    # а не из контекста (там был бы демо-юзер)
    space = mock_store.workspace(job.get("userId") or mock_store.DEMO_USER_ID)
    snap = store.snapshot(job["id"]) or {"videos": []}
    delivered = sum(1 for v in snap["videos"] if v.get("status") == "COMPLETED")
    lost = max(0, len(job.get("videos", [])) - delivered)
    if lost:
        space.subscription["creditsUsed"] = max(0, space.subscription["creditsUsed"] - lost)


def _ramp(store: RenderStore, job_id: str, idx: int, lo: int, hi: int, secs: float) -> None:
    steps = max(1, int(secs / _TICK))
    for i in range(1, steps + 1):
        time.sleep(_TICK)
        store.update_variation(job_id, idx, progress=int(lo + (hi - lo) * i / steps))


def _process(store: RenderStore, job: dict) -> None:
    from . import mock_store, render_layers
    from .mock_store import BASE_S3, iso, utcnow

    # см. _refund: владелец джоба, а не контекст запроса
    owner = mock_store.workspace(job.get("userId") or mock_store.DEMO_USER_ID)

    jid = job["id"]
    variations = {v["index"]: v for v in job.get("renderJob", {}).get("variations", [])}
    for v in job["videos"]:
        idx = v["index"]
        if render_layers.MODE == "real":
            variation = variations.get(idx, {"index": idx})
            store.update_variation(jid, idx, status="PROCESSING", stage="assembling")
            aep = render_layers.run_assembly(job, variation)          # слой 1
            store.update_variation(jid, idx, stage="rendering", progress=40)
            render_layers.run_effects(job, variation, aep)            # слой 2 (run_job.jsx)
            url = render_layers.run_render(job, variation, aep)       # слой 3 (aerender → S3)
        else:
            # мок: имитируем тайминги слоёв, тот же итог
            store.update_variation(jid, idx, status="PROCESSING", stage="assembling")
            _ramp(store, jid, idx, 0, 40, _ASSEMBLE_S)
            store.update_variation(jid, idx, stage="rendering")
            _ramp(store, jid, idx, 40, 100, _RENDER_S)
            url = f"{BASE_S3}/videos/{job['userId']}/{jid}/{idx}.mp4"
        store.update_variation(jid, idx, status="COMPLETED", stage="done", progress=100, downloadUrl=url)
        store.heartbeat(jid, WORKER_ID)
        # Воркер живёт вне цикла запроса, поэтому сохраняется сам: иначе рестарт посреди
        # батча терял уже отрендеренные ролики.
        _persist_job(jid)
        # поштучное уведомление (первые NOTIFY_LIMIT штук) — человек видит прогресс, не сидя на вкладке
        _notify(job, index=idx, total=len(job["videos"]))

    snap = store.snapshot(jid) or {"videos": []}
    outputs = [x.get("downloadUrl") for x in snap["videos"] if x.get("downloadUrl")]
    store.finish_job(jid, "COMPLETED", completedAt=iso(utcnow()), outputUrls=outputs)
    from . import analytics
    analytics.track("generation_completed", owner.user["id"], {"jobId": jid, "videos": len(outputs)})
    _notify(job, total=len(outputs))

    proj = next((p for p in owner.projects if p["id"] == job.get("projectId")), None)
    if proj:
        # generated считается по JOBS в _enrich_project — поле тут только для сида/дампа.
        # Статус НЕ трогаем: законченный батч не завершает проект — к нему всегда можно
        # добавить ещё один. «Завершён» проект становится только когда человек ушёл
        # генерировать в другой (см. mock_store.create_job).
        proj["generated"] = proj.get("generated", 0) + job["versions"]
    _persist_job(jid)
    _persist_workspace(owner.user["id"])


def _persist_job(job_id: str) -> None:
    from . import persistence

    persistence.save_job(job_id)


def _persist_workspace(user_id: str) -> None:
    from . import persistence

    persistence.flush_user(user_id)


if __name__ == "__main__":  # запуск воркера как отдельного процесса (прод-нода)
    run_forever(get_store())
