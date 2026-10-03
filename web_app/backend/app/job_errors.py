"""Причина падения ролика для клиента: категория вместо сырого текста ошибки.

В `video.error` лежит то, что вернул оркестратор: трейсбек Celery, пути рендер-ноды
(C:\\ae_jobs\\…), render_id, имена файлов и строки кода. Обычному пользователю это не
помогает и раскрывает внутренности — наружу ему уходит только код категории
(`failureKind`), а текст получают админы (за раскрытием «Подробности» на фронте).

Категории определяем по ТЕКСТУ ошибки, а не по стадии: стадия `poll` покрывает
разные причины (таймаут AE, рестарт ноды), а текстовые ошибки сборки — отдельный класс.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

# Причины сборки (стадия build), которые сам человек может поправить в визарде. Ищутся
# ТОЛЬКО в заголовке ошибки — до трейсбека: в хвосте трейсбека Celery видны строки кода
# (`USER_DROP_T`, `footage_plan` в исходниках), и по ним любое падение рядом с этим кодом
# выглядело бы как «дроп/раскадровка».
_HEADLINE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # футаж-план «Пула» не сошёлся с джобой (окно, клип вне пула/короче шота/в блэклисте)
    ("storyboard", ("footage_plan", "pinned_cuts", "storyboardplanerror")),
    # дроп вне окна или в позиции, где хук не собирается
    ("drop", ("user_drop_t", "drop_rel")),
    # сборка не приняла выбранный эффект/хук/рамку (неизвестный id и т.п.)
    ("hook", ("invalid effect_", "unknown f3_", "invalid f2_shape", "invalid f4_device",
              "invalid hook_device", "invalid frame_id", "f1_sound_url must be")),
)
# Порядок важен: первое совпадение побеждает (частные признаки — раньше общих).
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("previous", ("previous variation failed",)),
    ("cancelled", ("cancelled", "canceled", "revoked")),
    ("source", ("stage2_style_rotation_missing_artist_id",)),
    ("color", ("solid background",)),
    ("footage", ("footage file not found", "collection not found", "media not found",
                 "download error", "footage missing", "no footage")),
    ("lyrics", ("alignment_unsupported_text", "empty after tokenization")),
    # poll_404 — нода потеряла рендер; worker_lost — упал воркер сборки (рипер оркестратора)
    ("lost", ("poll_404", "worker_lost")),
    ("busy", ("dispatch_503", "status=503", "status_code=503", "render capacity")),
    ("timeout", ("timeout", "timed out")),
    ("service", ("orchestrator does not support", "contract check failed",
                 "/send_audio_s3 failed status=5", "returned empty job_id")),
    ("render", ("afterfx", "aerender", "after effects", "ae_jobs")),
)
_RENDER_STAGES = {"render", "poll", "dispatch"}
# Сырые тексты на уровне джоба (падение воркера, частичная постановка).
_JOB_RAW_KEYS = ("error", "enqueueError")
# Маркер хвоста трейсбека в ошибке оркестратора (celery_app.on_failure).
_TRACEBACK_MARK = "--- traceback"


def error_headline(error: str | None) -> str:
    """Заголовок ошибки оркестратора без хвоста трейсбека.

    `celery_failed stage=build exc=RuntimeError("invalid effect_transition=...")` —
    сама причина; всё после `--- traceback` — стек с путями и строками кода.
    """
    text = str(error or "")
    cut = text.find(_TRACEBACK_MARK)
    return (text[:cut] if cut >= 0 else text).strip()


def failure_kind(error: str | None, stage: str | None = None) -> str:
    """Код категории падения: по нему фронт берёт локализованную причину."""
    text = str(error or "").lower()
    if (stage or "") == "skipped" and not text:
        return "previous"
    headline = error_headline(error).lower()
    for kind, needles in _HEADLINE_RULES:
        if any(needle in headline for needle in needles):
            return kind
    for kind, needles in _RULES:
        if any(needle in text for needle in needles):
            return kind
    if (stage or "") in _RENDER_STAGES:
        return "render"
    # Сборка проекта (Stage 1–3) упала без узнаваемой причины: это не рендер-нода
    # и не «неизвестно что» — человеку говорим, на каком этапе остановилось.
    if (stage or "") == "build" or headline.startswith("celery_failed stage=build"):
        return "build"
    return "unknown"


def public_job(job: dict[str, Any] | None, *, admin: bool) -> dict[str, Any] | None:
    """Копия джоба для ответа: `failureKind` у упавших роликов, не-админу — без сырого текста.

    Копия, а не правка на месте: ручки иногда отдают живой джоб из store, и стёртая
    ошибка пропала бы у воркера и в БД.
    """
    if not job:
        return job
    job = deepcopy(job)
    for video in job.get("videos") or []:
        if video.get("status") == "FAILED" or video.get("error"):
            video["failureKind"] = failure_kind(video.get("error"), video.get("stage"))
        if not admin:
            video["error"] = None
    if not admin:
        for key in _JOB_RAW_KEYS:
            if key in job:
                job[key] = None
    return job


def public_project(project: dict[str, Any] | None, *, admin: bool) -> dict[str, Any] | None:
    if not project:
        return project
    project = dict(project)
    project["jobs"] = [public_job(job, admin=admin) for job in project.get("jobs") or []]
    return project
