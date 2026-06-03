from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from core.telegram_api import make_telegram_api

log = logging.getLogger("orchestrator.ops_alert_subscribers")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_alert_subscribers (
    chat_id         BIGINT           PRIMARY KEY,
    username        TEXT             NOT NULL DEFAULT '',
    first_name      TEXT             NOT NULL DEFAULT '',
    last_name       TEXT             NOT NULL DEFAULT '',
    chat_type       TEXT             NOT NULL DEFAULT '',
    is_active       BOOLEAN          NOT NULL DEFAULT TRUE,
    source          TEXT             NOT NULL DEFAULT 'blastbugsbot',
    activated_at    DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
    deactivated_at  DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_seen_at    DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
);

CREATE INDEX IF NOT EXISTS idx_ops_alert_subscribers_active
    ON ops_alert_subscribers (is_active, last_seen_at DESC);
"""


def _to_str(value: object) -> str:
    return str(value or "").strip()


def _import_asyncpg() -> Any:
    try:
        import asyncpg  # type: ignore

        return asyncpg
    except Exception as exc:  # pragma: no cover - depends on runtime env
        raise RuntimeError("asyncpg is required for ops alert subscribers") from exc


def parse_subscriber_command(text: str) -> str:
    value = _to_str(text).lower()
    if not value:
        return ""
    if value.startswith("/start") or value.startswith("/subscribe"):
        return "activate"
    if value.startswith("/stop") or value.startswith("/unsubscribe"):
        return "deactivate"
    return ""


class OpsAlertSubscriberStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def init_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA)

    async def upsert_active(
        self,
        *,
        chat_id: int,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
        chat_type: str = "",
        source: str = "blastbugsbot",
    ) -> None:
        now = time.time()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ops_alert_subscribers (
                    chat_id,
                    username,
                    first_name,
                    last_name,
                    chat_type,
                    is_active,
                    source,
                    activated_at,
                    deactivated_at,
                    last_seen_at
                )
                VALUES ($1, $2, $3, $4, $5, TRUE, $6, $7, 0, $7)
                ON CONFLICT (chat_id) DO UPDATE
                    SET username = EXCLUDED.username,
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name,
                        chat_type = EXCLUDED.chat_type,
                        is_active = TRUE,
                        source = EXCLUDED.source,
                        last_seen_at = EXCLUDED.last_seen_at,
                        deactivated_at = 0
                """,
                int(chat_id),
                _to_str(username),
                _to_str(first_name),
                _to_str(last_name),
                _to_str(chat_type),
                _to_str(source) or "blastbugsbot",
                float(now),
            )

    async def deactivate(self, *, chat_id: int) -> None:
        now = time.time()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ops_alert_subscribers
                SET is_active = FALSE,
                    deactivated_at = $2,
                    last_seen_at = $2
                WHERE chat_id = $1
                """,
                int(chat_id),
                float(now),
            )

    async def touch(
        self,
        *,
        chat_id: int,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
        chat_type: str = "",
    ) -> None:
        now = time.time()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ops_alert_subscribers
                SET username = CASE WHEN $2 != '' THEN $2 ELSE username END,
                    first_name = CASE WHEN $3 != '' THEN $3 ELSE first_name END,
                    last_name = CASE WHEN $4 != '' THEN $4 ELSE last_name END,
                    chat_type = CASE WHEN $5 != '' THEN $5 ELSE chat_type END,
                    last_seen_at = $6
                WHERE chat_id = $1
                """,
                int(chat_id),
                _to_str(username),
                _to_str(first_name),
                _to_str(last_name),
                _to_str(chat_type),
                float(now),
            )

    async def list_active_chat_ids(self, *, limit: int = 200) -> list[str]:
        lim = max(1, int(limit))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT chat_id
                FROM ops_alert_subscribers
                WHERE is_active = TRUE
                ORDER BY last_seen_at DESC, chat_id DESC
                LIMIT $1
                """,
                lim,
            )
        out: list[str] = []
        for row in rows:
            try:
                out.append(str(int(row["chat_id"])))
            except Exception:
                continue
        return out

    async def list_active(self, *, limit: int = 500) -> list[dict[str, Any]]:
        lim = max(1, int(limit))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    chat_id,
                    username,
                    first_name,
                    last_name,
                    chat_type,
                    source,
                    activated_at,
                    last_seen_at
                FROM ops_alert_subscribers
                WHERE is_active = TRUE
                ORDER BY last_seen_at DESC, chat_id DESC
                LIMIT $1
                """,
                lim,
            )
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "chat_id": int(row["chat_id"]),
                    "username": _to_str(row["username"]),
                    "first_name": _to_str(row["first_name"]),
                    "last_name": _to_str(row["last_name"]),
                    "chat_type": _to_str(row["chat_type"]),
                    "source": _to_str(row["source"]),
                    "activated_at": float(row["activated_at"] or 0.0),
                    "last_seen_at": float(row["last_seen_at"] or 0.0),
                }
            )
        return out


class OpsAlertBotPoller:
    def __init__(
        self,
        *,
        bot_token: str,
        store: OpsAlertSubscriberStore,
        api_env: str = "prod",
        proxy_url: str = "",
        poll_timeout_s: float = 25.0,
        retry_sleep_s: float = 2.0,
    ) -> None:
        self._token = _to_str(bot_token)
        self._telegram_api = make_telegram_api(api_env, name="ALERT_TELEGRAM_API_ENV")
        self._store = store
        self._opener = self._build_opener(_to_str(proxy_url))
        self._poll_timeout_s = max(1.0, float(poll_timeout_s))
        self._retry_sleep_s = max(0.2, float(retry_sleep_s))
        self._offset = 0

    @staticmethod
    def _build_opener(proxy_url: str) -> urllib.request.OpenerDirector:
        if not proxy_url:
            return urllib.request.build_opener()
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )

    def _api_url(self, method: str) -> str:
        return self._telegram_api.method_url(token=self._token, method=method)

    def _get_updates(self, *, offset: int, timeout_s: float) -> list[dict[str, Any]]:
        params = {"timeout": str(int(max(1.0, timeout_s)))}
        if offset > 0:
            params["offset"] = str(int(offset))
        url = self._api_url("getUpdates") + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url=url, method="GET")
        with self._opener.open(req, timeout=float(timeout_s) + 8.0) as resp:
            body = resp.read()
        payload = json.loads(body.decode("utf-8", "ignore"))
        if not isinstance(payload, dict) or not bool(payload.get("ok", False)):
            raise RuntimeError(f"telegram_get_updates_failed payload={payload!r}")
        result = payload.get("result")
        if not isinstance(result, list):
            return []
        out: list[dict[str, Any]] = []
        for item in result:
            if isinstance(item, dict):
                out.append(item)
        return out

    def _send_message(self, *, chat_id: int, text: str) -> None:
        payload = {
            "chat_id": int(chat_id),
            "text": _to_str(text)[:3500],
            "disable_web_page_preview": True,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url=self._api_url("sendMessage"),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self._opener.open(req, timeout=10.0) as resp:
            _ = resp.read()

    async def _handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat")
        if not isinstance(chat, dict):
            return
        raw_chat_id = chat.get("id")
        try:
            chat_id = int(raw_chat_id)
        except Exception:
            return
        if chat_id == 0:
            return
        from_user = message.get("from") if isinstance(message.get("from"), dict) else {}
        text = _to_str(message.get("text"))
        cmd = parse_subscriber_command(text)
        username = _to_str((from_user or {}).get("username") or chat.get("username"))
        first_name = _to_str((from_user or {}).get("first_name") or chat.get("first_name"))
        last_name = _to_str((from_user or {}).get("last_name") or chat.get("last_name"))
        chat_type = _to_str(chat.get("type"))
        if cmd == "activate":
            await self._store.upsert_active(
                chat_id=chat_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                chat_type=chat_type,
                source="blastbugsbot",
            )
            await asyncio.to_thread(
                self._send_message,
                chat_id=chat_id,
                text=(
                    "Alerts are enabled for this chat.\n"
                    "Use /stop to unsubscribe and /start to subscribe again."
                ),
            )
            return
        if cmd == "deactivate":
            await self._store.deactivate(chat_id=chat_id)
            await asyncio.to_thread(
                self._send_message,
                chat_id=chat_id,
                text="Alerts are disabled for this chat. Use /start to enable again.",
            )
            return
        await self._store.touch(
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            chat_type=chat_type,
        )

    async def run(self, stop_event: asyncio.Event) -> None:
        if not self._token:
            log.info("ops_alert_poller_disabled reason=empty_token")
            return
        log.info("ops_alert_poller_started")
        while not stop_event.is_set():
            try:
                updates = await asyncio.to_thread(
                    self._get_updates,
                    offset=int(self._offset),
                    timeout_s=float(self._poll_timeout_s),
                )
                for item in updates:
                    uid = item.get("update_id")
                    try:
                        update_id = int(uid)
                    except Exception:
                        update_id = 0
                    if update_id > 0:
                        self._offset = max(self._offset, update_id + 1)
                    msg = item.get("message")
                    if isinstance(msg, dict):
                        await self._handle_message(msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ops_alert_poller_iteration_failed err=%r", exc)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self._retry_sleep_s)
                except asyncio.TimeoutError:
                    pass
        log.info("ops_alert_poller_stopped")


async def _fetch_active_chat_ids(db_url: str, *, limit: int) -> list[str]:
    asyncpg = _import_asyncpg()
    conn = await asyncpg.connect(dsn=str(db_url or "").strip())
    try:
        rows = await conn.fetch(
            """
            SELECT chat_id
            FROM ops_alert_subscribers
            WHERE is_active = TRUE
            ORDER BY last_seen_at DESC, chat_id DESC
            LIMIT $1
            """,
            max(1, int(limit)),
        )
    finally:
        await conn.close()
    out: list[str] = []
    for row in rows:
        try:
            out.append(str(int(row["chat_id"])))
        except Exception:
            continue
    return out


def fetch_active_chat_ids_sync(db_url: str, *, limit: int = 200) -> list[str]:
    dsn = _to_str(db_url)
    if not dsn:
        return []

    def _runner() -> list[str]:
        return asyncio.run(_fetch_active_chat_ids(dsn, limit=limit))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            return _runner()
        except Exception as exc:
            log.warning("ops_alert_fetch_subscribers_failed err=%r", exc)
            return []
    out: list[str] = []
    err: list[BaseException] = []

    def _thread_target() -> None:
        try:
            out.extend(_runner())
        except BaseException as exc:  # pragma: no cover
            err.append(exc)

    t = threading.Thread(target=_thread_target, daemon=True)
    t.start()
    t.join(timeout=10.0)
    if err:
        log.warning("ops_alert_fetch_subscribers_failed err=%r", err[0])
    return out


async def _deactivate_chat_id(db_url: str, *, chat_id: int) -> None:
    asyncpg = _import_asyncpg()
    conn = await asyncpg.connect(dsn=str(db_url or "").strip())
    try:
        await conn.execute(
            """
            UPDATE ops_alert_subscribers
            SET is_active = FALSE,
                deactivated_at = $2,
                last_seen_at = $2
            WHERE chat_id = $1
            """,
            int(chat_id),
            float(time.time()),
        )
    finally:
        await conn.close()


def deactivate_chat_id_sync(db_url: str, *, chat_id: int) -> None:
    dsn = _to_str(db_url)
    if not dsn:
        return
    try:
        asyncio.run(_deactivate_chat_id(dsn, chat_id=int(chat_id)))
    except Exception as exc:
        log.warning("ops_alert_deactivate_chat_failed chat_id=%s err=%r", chat_id, exc)


def is_terminal_telegram_delivery_error(*, status_code: int, description: str) -> bool:
    desc = _to_str(description).lower()
    if int(status_code) != 400:
        return False
    return ("chat not found" in desc) or ("bot was blocked by the user" in desc)
