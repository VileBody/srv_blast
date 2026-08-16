"""Сохранение состояния приложения в БД и подъём его обратно на старте.

До этого всё жило в памяти: рестарт процесса стирал проекты, батчи, подписку и аналитику —
деплоить в таком виде было нельзя.

Модель хранения — документная (см. `db/migrations/002_app_state.*.sql`): приложение мутирует
обычные словари на месте, и в БД уезжает ровно тот же словарь. Это сознательный шаг: он
даёт долговечность без переписывания всех 700 строк стора. Разложить проекты и треки по
колонкам можно следующей миграцией — интерфейс этого модуля при этом не изменится.

Запись идёт не из каждой функции стора (легко пропустить вызов), а одним «сбросом» после
любого мутирующего запроса — см. `flush_user`. Фоновый рендер-воркер сбрасывает свои джобы
сам, потому что живёт вне цикла запроса.
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import asdict
from typing import Any

from . import analytics, auth_store, db
from . import mock_store as store

_lock = threading.RLock()
_loaded = False
# Счётчик вставок событий: подрезаем хвост не на каждой записи, а раз в N
_events_since_trim = 0
_TRIM_EVERY = 500


def enabled() -> bool:
    """BLAST_PERSIST=0 — работать целиком в памяти (тесты, одноразовые демо)."""
    return os.getenv("BLAST_PERSIST", "1") != "0"


# ------------------------------------------------------------------ загрузка

def load_all() -> None:
    """Поднять состояние из БД. Вызывается один раз на старте приложения."""
    global _loaded
    if not enabled():
        return
    with _lock:
        if _loaded:
            return
        db.migrate()
        _import_legacy_users_file()
        _load_users()
        _load_workspaces()
        _load_jobs()
        _load_iterations()
        _load_events()
        _loaded = True


def _import_legacy_users_file() -> None:
    """Разовый перенос реестра из `backend/data/users.json` в таблицу app_users.

    Файл после переноса не удаляем: пусть останется как бэкап первой миграции.
    """
    import json
    from pathlib import Path

    legacy = Path(__file__).resolve().parent.parent / "data" / "users.json"
    if not legacy.exists():
        return
    with db.read() as cursor:
        cursor.execute("SELECT COUNT(*) FROM app_users")
        if (cursor.fetchone() or [0])[0]:
            return
    try:
        users = json.loads(legacy.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — битый файл не должен ронять старт
        return
    if not isinstance(users, dict):
        return
    with db.transaction() as cursor:
        for key, user in users.items():
            cursor.execute(
                db.upsert("app_users", ["key"], ["key", "user_id", "data"]),
                (key, user.get("id", ""), db.json_param(user)),
            )


def _load_users() -> None:
    with db.read() as cursor:
        cursor.execute("SELECT key, data FROM app_users")
        rows = cursor.fetchall()
    auth_store.USERS.clear()
    for key, data in rows:
        auth_store.USERS[key] = db.json_value(data)


def _load_workspaces() -> None:
    with db.read() as cursor:
        cursor.execute("SELECT user_id, data FROM workspaces")
        rows = cursor.fetchall()
    for user_id, data in rows:
        payload = db.json_value(data) or {}
        store.WORKSPACES[user_id] = _workspace_from_dict(payload)


def _workspace_from_dict(payload: dict[str, Any]) -> store.Workspace:
    """Собрать Workspace, игнорируя поля, которых в текущей версии дата-класса уже нет."""
    known = {field for field in store.Workspace.__dataclass_fields__}
    return store.Workspace(**{key: value for key, value in payload.items() if key in known})


def _load_jobs() -> None:
    with db.read() as cursor:
        cursor.execute("SELECT id, idempotency_key, data FROM jobs")
        rows = cursor.fetchall()
    for job_id, idempotency_key, data in rows:
        store.JOBS[job_id] = db.json_value(data)
        if idempotency_key:
            store.JOB_IDEMPOTENCY[idempotency_key] = job_id


def _load_iterations() -> None:
    with db.read() as cursor:
        cursor.execute("SELECT project_id, data FROM iterations")
        rows = cursor.fetchall()
    for project_id, data in rows:
        store.ITERATIONS[project_id] = db.json_value(data) or []


def _load_events() -> None:
    # Берём ПОСЛЕДНИЕ MAX_EVENTS и разворачиваем обратно в хронологический порядок:
    # обрезать надо старый хвост, а не свежие события.
    with db.read() as cursor:
        cursor.execute(
            db.sql("SELECT id, name, user_id, ts, props FROM analytics_events ORDER BY ts DESC LIMIT %s"),
            (analytics.MAX_EVENTS,),
        )
        rows = list(reversed(cursor.fetchall()))
    analytics.EVENTS.clear()
    for event_id, name, user_id, ts, props in rows:
        analytics.EVENTS.append({
            "id": event_id, "name": name, "userId": user_id,
            "ts": ts, "props": db.json_value(props) or {},
        })


# ------------------------------------------------------------------- запись

def save_user(key: str) -> None:
    if not enabled():
        return
    user = auth_store.USERS.get(key)
    if user is None:
        return
    with _lock, db.transaction() as cursor:
        cursor.execute(
            db.upsert("app_users", ["key"], ["key", "user_id", "data"]),
            (key, user.get("id", ""), db.json_param(user)),
        )


def save_users() -> None:
    """Полный сброс реестра — им пользуется auth_store вместо записи в JSON."""
    def write() -> None:
        with _lock, db.transaction() as cursor:
            for key, user in auth_store.USERS.items():
                cursor.execute(
                    db.upsert("app_users", ["key"], ["key", "user_id", "data"]),
                    (key, user.get("id", ""), db.json_param(user)),
                )

    _guarded("save_users", write)


def delete_user(key: str) -> None:
    """Убрать запись реестра и из памяти, и из БД (save_users умеет только upsert)."""
    auth_store.USERS.pop(key, None)

    def write() -> None:
        with _lock, db.transaction() as cursor:
            cursor.execute(db.sql("DELETE FROM app_users WHERE key = %s"), (key,))

    _guarded("delete_user", write)


def _guarded(action: str, fn: Any) -> None:
    """Выполнить запись, не роняя запрос.

    Сброс состояния идёт ПОСЛЕ того, как действие уже выполнено в памяти и ответ собран.
    Если на этом шаге упадёт БД, поднимать 500 бессмысленно — для пользователя операция
    прошла. Поэтому пишем в stderr и продолжаем: видно в логах, не видно пользователю.
    """
    if not enabled():
        return
    try:
        db.migrate()
        fn()
    except Exception as exc:  # noqa: BLE001
        print(f"[persistence] {action} failed: {exc}", file=sys.stderr)


def save_event(event: dict[str, Any]) -> None:
    """Событие пишем сразу: поток аналитики — это лог, его нельзя терять при падении."""
    _guarded("save_event", lambda: _write_event(event))


def _write_event(event: dict[str, Any]) -> None:
    global _events_since_trim
    with _lock, db.transaction() as cursor:
        cursor.execute(
            db.sql("INSERT INTO analytics_events (id, name, user_id, ts, props) VALUES (%s,%s,%s,%s,%s)"),
            (event["id"], event["name"], event.get("userId"), event["ts"], db.json_param(event.get("props") or {})),
        )
        _events_since_trim += 1
        if _events_since_trim >= _TRIM_EVERY:
            _events_since_trim = 0
            cursor.execute(
                db.sql(
                    "DELETE FROM analytics_events WHERE id IN ("
                    " SELECT id FROM analytics_events ORDER BY ts DESC LIMIT -1 OFFSET %s)"
                    if db.dialect() == "sqlite" else
                    "DELETE FROM analytics_events WHERE id IN ("
                    " SELECT id FROM analytics_events ORDER BY ts DESC OFFSET %s)"
                ),
                (analytics.MAX_EVENTS,),
            )


def save_job(job_id: str) -> None:
    """Сохранить один батч. Вызывается рендер-воркером — он вне цикла запроса."""
    job = store.JOBS.get(job_id)
    if job is None:
        return

    def write() -> None:
        with _lock, db.transaction() as cursor:
            _write_job(cursor, job)

    _guarded("save_job", write)


def _write_job(cursor: Any, job: dict[str, Any]) -> None:
    idempotency_key = next((key for key, value in store.JOB_IDEMPOTENCY.items() if value == job["id"]), None)
    cursor.execute(
        db.upsert("jobs", ["id"], ["id", "user_id", "project_id", "idempotency_key", "data"]),
        (job["id"], job.get("userId", ""), job.get("projectId"), idempotency_key, db.json_param(job)),
    )


def flush_user(user_id: str | None) -> None:
    """Записать всё, что принадлежит юзеру: воркспейс, его батчи и итерации его проектов.

    Сверка на удаление обязательна: удалённый проект уносит свои батчи и итерации,
    и без неё в таблицах оставались бы сироты.
    """
    if not user_id:
        return
    space = store.WORKSPACES.get(user_id)
    if space is None:
        return
    _guarded("flush_user", lambda: _write_user_state(user_id, space))


def _write_user_state(user_id: str, space: store.Workspace) -> None:
    jobs = [job for job in store.JOBS.values() if job.get("userId") == user_id]
    project_ids = {project["id"] for project in space.projects}
    with _lock, db.transaction() as cursor:
        cursor.execute(
            db.upsert("workspaces", ["user_id"], ["user_id", "data"]),
            (user_id, db.json_param(asdict(space))),
        )
        for job in jobs:
            _write_job(cursor, job)
        alive_jobs = [job["id"] for job in jobs]
        _delete_missing(cursor, "jobs", "id", "user_id", user_id, alive_jobs)

        for project_id in project_ids:
            iterations = store.ITERATIONS.get(project_id)
            if iterations is None:
                continue
            cursor.execute(
                db.upsert("iterations", ["project_id"], ["project_id", "data"]),
                (project_id, db.json_param(iterations)),
            )
        cursor.execute(db.sql("SELECT project_id FROM iterations"))
        stale = [row[0] for row in cursor.fetchall()
                 if row[0] not in project_ids and row[0] not in _all_known_project_ids()]
        for project_id in stale:
            cursor.execute(db.sql("DELETE FROM iterations WHERE project_id = %s"), (project_id,))


def _all_known_project_ids() -> set[str]:
    return {project["id"] for space in store.WORKSPACES.values() for project in space.projects}


def _delete_missing(cursor: Any, table: str, id_column: str, owner_column: str,
                    owner: str, alive: list[str]) -> None:
    if alive:
        placeholders = ", ".join(["%s"] * len(alive))
        cursor.execute(
            db.sql(f"DELETE FROM {table} WHERE {owner_column} = %s AND {id_column} NOT IN ({placeholders})"),
            (owner, *alive),
        )
    else:
        cursor.execute(db.sql(f"DELETE FROM {table} WHERE {owner_column} = %s"), (owner,))
