from __future__ import annotations

import asyncio
from dataclasses import replace

from services.tg_bot_public.config import SETTINGS
from services.tg_bot_public.state_store import (
    ChatState,
    RedisChatStateStore,
    STAGE_KEEP_IN_TOUCH,
    STAGE_PROCESSING,
    STAGE_WAIT_AUDIO,
)


class _FakeRedis:
    def __init__(self, *, keys: list[str], values: dict[str, str], errors: dict[str, Exception] | None = None):
        self._keys = list(keys)
        self._values = dict(values)
        self._errors = dict(errors or {})
        self.get_calls: list[str] = []

    async def scan_iter(self, match: str | None = None, count: int = 200):
        prefix = ""
        if isinstance(match, str) and "[0-9]*" in match:
            prefix = match.split("[0-9]*", 1)[0]
        for key in self._keys:
            if prefix and not key.startswith(prefix):
                continue
            yield key

    async def get(self, key: str):
        key_s = str(key)
        self.get_calls.append(key_s)
        err = self._errors.get(key_s)
        if err is not None:
            raise err
        return self._values.get(key_s)

    async def aclose(self) -> None:
        return None


def _state_json(*, chat_id: int, stage: str, active_job_id: str = "", reminder_at: float = 0.0) -> str:
    return ChatState(
        chat_id=chat_id,
        stage=stage,
        active_job_id=active_job_id,
        reminder_at=reminder_at,
    ).model_dump_json()


def _make_store(fake_redis: _FakeRedis, *, prefix: str = "blast:tg:public:chat_state") -> RedisChatStateStore:
    settings = replace(SETTINGS, tg_state_prefix=prefix)
    store = RedisChatStateStore(settings)
    store._redis = fake_redis
    return store


def test_list_processing_ignores_non_chat_keys() -> None:
    prefix = "blast:tg:public:chat_state"
    good_key = f"{prefix}:101"
    bad_index_key = f"{prefix}:idx:processing"
    idle_key = f"{prefix}:202"
    fake = _FakeRedis(
        keys=[good_key, bad_index_key, idle_key],
        values={
            good_key: _state_json(chat_id=101, stage=STAGE_PROCESSING, active_job_id="job-1"),
            idle_key: _state_json(chat_id=202, stage=STAGE_WAIT_AUDIO),
        },
    )
    store = _make_store(fake, prefix=prefix)

    rows = asyncio.run(store.list_processing())

    assert [s.chat_id for s in rows] == [101]
    assert bad_index_key not in fake.get_calls


def test_list_processing_skips_wrongtype_get_errors() -> None:
    prefix = "blast:tg:public:chat_state"
    good_key = f"{prefix}:301"
    bad_key = f"{prefix}:302"
    fake = _FakeRedis(
        keys=[good_key, bad_key],
        values={good_key: _state_json(chat_id=301, stage=STAGE_PROCESSING, active_job_id="job-2")},
        errors={bad_key: RuntimeError("WRONGTYPE Operation against a key holding the wrong kind of value")},
    )
    store = _make_store(fake, prefix=prefix)

    rows = asyncio.run(store.list_processing())

    assert [s.chat_id for s in rows] == [301]


def test_list_pending_reminders_skips_invalid_entries() -> None:
    prefix = "blast:tg:public:chat_state"
    due_key = f"{prefix}:401"
    future_key = f"{prefix}:402"
    broken_key = f"{prefix}:403"
    fake = _FakeRedis(
        keys=[due_key, future_key, broken_key],
        values={
            due_key: _state_json(chat_id=401, stage=STAGE_KEEP_IN_TOUCH, reminder_at=50.0),
            future_key: _state_json(chat_id=402, stage=STAGE_KEEP_IN_TOUCH, reminder_at=5000.0),
        },
        errors={broken_key: RuntimeError("WRONGTYPE")},
    )
    store = _make_store(fake, prefix=prefix)

    rows = asyncio.run(store.list_pending_reminders(now=100.0))

    assert [s.chat_id for s in rows] == [401]
