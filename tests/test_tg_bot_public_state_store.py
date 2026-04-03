from __future__ import annotations

import asyncio
import sys
import types

import pytest

# The local test env may not have redis installed; state_store only needs the symbol at import time.
if "redis.asyncio" not in sys.modules:
    redis_module = types.ModuleType("redis")
    redis_asyncio = types.ModuleType("redis.asyncio")

    class _RedisStub:  # pragma: no cover - import-time compatibility shim
        pass

    redis_asyncio.Redis = _RedisStub
    redis_module.asyncio = redis_asyncio
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_asyncio

from services.tg_bot_public.state_store import ChatState, RedisChatStateStore


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del ex
        self.data[key] = value

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.data:
                removed += 1
                self.data.pop(key, None)
        return removed

    async def scan_iter(self, match: str, count: int = 200):
        del count
        if match.endswith("*"):
            prefix = match[:-1]
            for key in list(self.data.keys()):
                if key.startswith(prefix):
                    yield key
            return
        if match in self.data:
            yield match



def _make_store(fake_redis: _FakeRedis) -> RedisChatStateStore:
    store = object.__new__(RedisChatStateStore)
    store._prefix = "blast:tg:public:chat_state"
    store._username_index_prefix = f"{store._prefix}:username_index"
    store._chat_username_prefix = f"{store._prefix}:chat_username"
    store._redis = fake_redis
    return store


def test_username_index_updates_when_username_changes() -> None:
    async def _run() -> None:
        redis = _FakeRedis()
        store = _make_store(redis)

        st = ChatState(chat_id=777, chat_username="@alice")
        await store.set(st)
        assert await store.find_chat_id_by_username("alice") == 777

        st.chat_username = "@bob"
        await store.set(st)

        assert await store.find_chat_id_by_username("@alice") is None
        assert await store.find_chat_id_by_username("@bob") == 777

    asyncio.run(_run())


def test_username_index_deletes_mapping_when_username_cleared() -> None:
    async def _run() -> None:
        redis = _FakeRedis()
        store = _make_store(redis)

        st = ChatState(chat_id=888, chat_username="@charlie")
        await store.set(st)
        assert await store.find_chat_id_by_username("@charlie") == 888

        st.chat_username = ""
        await store.set(st)

        assert await store.find_chat_id_by_username("charlie") is None

    asyncio.run(_run())


def test_get_raises_runtime_error_for_broken_json() -> None:
    redis = _FakeRedis()
    store = _make_store(redis)
    redis.data[store._key(1)] = "{broken-json"

    with pytest.raises(RuntimeError, match="Corrupted chat state"):
        asyncio.run(store.get(1))


def test_get_raises_runtime_error_for_invalid_payload() -> None:
    redis = _FakeRedis()
    store = _make_store(redis)
    redis.data[store._key(2)] = '{"stage":"WAIT_AUDIO"}'

    with pytest.raises(RuntimeError, match="Corrupted chat state"):
        asyncio.run(store.get(2))
