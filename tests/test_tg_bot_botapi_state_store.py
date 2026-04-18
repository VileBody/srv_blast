from __future__ import annotations

import asyncio
import sys
import types

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

from services.tg_bot_botapi.state_store import (
    ChatState,
    RedisChatStateStore,
    STAGE_PROCESSING,
    STAGE_WAIT_AUDIO,
    STAGE_WAITING_REFERRAL,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

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
            if key in self.sets:
                removed += 1
                self.sets.pop(key, None)
            if key in self.zsets:
                removed += 1
                self.zsets.pop(key, None)
        return removed

    async def sadd(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        for member in members:
            bucket.add(str(member))
        return len(bucket) - before

    async def srem(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        removed = 0
        for member in members:
            if str(member) in bucket:
                bucket.remove(str(member))
                removed += 1
        return removed

    async def smembers(self, key: str):
        return set(self.sets.get(key, set()))

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        bucket = self.zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if str(member) not in bucket:
                added += 1
            bucket[str(member)] = float(score)
        return added

    async def zrem(self, key: str, *members: str) -> int:
        bucket = self.zsets.setdefault(key, {})
        removed = 0
        for member in members:
            if str(member) in bucket:
                bucket.pop(str(member), None)
                removed += 1
        return removed

    async def zrangebyscore(self, key: str, min: float, max: float, start: int = 0, num: int | None = None):
        lower = float(min)
        upper = float(max)
        rows = [
            (member, score)
            for member, score in self.zsets.get(key, {}).items()
            if lower <= float(score) <= upper
        ]
        rows.sort(key=lambda x: (x[1], x[0]))
        members = [member for member, _ in rows]
        start_i = int(start) if int(start) > 0 else 0
        if num is None:
            return members[start_i:]
        size = int(num) if int(num) > 0 else 0
        return members[start_i : start_i + size]

    async def zrange(self, key: str, start: int, end: int):
        rows = sorted(self.zsets.get(key, {}).items(), key=lambda x: (x[1], x[0]))
        members = [member for member, _ in rows]
        s = max(0, int(start))
        e = int(end)
        if e < 0:
            return members[s:]
        return members[s : e + 1]

    async def aclose(self) -> None:
        return None

    async def scan_iter(self, match: str, count: int = 200):  # pragma: no cover
        del match, count
        raise AssertionError("scan_iter must not be used in indexed state store")


def _make_store(fake_redis: _FakeRedis) -> RedisChatStateStore:
    store = object.__new__(RedisChatStateStore)
    store._prefix = "blast:tg:chat_state"
    store._all_ids_key = f"{store._prefix}:idx:all"
    store._processing_ids_key = f"{store._prefix}:idx:processing"
    store._processing_set_key = f"{store._prefix}:__index:processing"
    store._updated_at_zset_key = f"{store._prefix}:idx:updated_at"
    store._state_ttl_s = 86400
    store._redis = fake_redis
    return store


def test_list_processing_reads_from_processing_index() -> None:
    async def _run() -> None:
        redis = _FakeRedis()
        store = _make_store(redis)

        await store.set(ChatState(chat_id=11, stage=STAGE_PROCESSING, active_job_ids=["job-a"]))
        await store.set(ChatState(chat_id=12, stage=STAGE_WAIT_AUDIO))

        rows = await store.list_processing()
        assert [s.chat_id for s in rows] == [11]

    asyncio.run(_run())


def test_cleanup_index_members_removes_orphan_entries() -> None:
    async def _run() -> None:
        redis = _FakeRedis()
        store = _make_store(redis)

        await store.set(ChatState(chat_id=99, stage=STAGE_WAIT_AUDIO))
        redis.data.pop(store._key(99), None)

        removed = await store.cleanup_index_members(limit=10)
        assert removed == 1
        assert "99" not in await redis.smembers(store._all_ids_key)

    asyncio.run(_run())


def test_list_waiting_referral_reads_all_ids_index() -> None:
    async def _run() -> None:
        redis = _FakeRedis()
        store = _make_store(redis)

        await store.set(ChatState(chat_id=21, stage=STAGE_WAITING_REFERRAL, waiting_referral_since=1.0))
        await store.set(ChatState(chat_id=22, stage=STAGE_WAIT_AUDIO))

        rows = await store.list_waiting_referral()
        assert [s.chat_id for s in rows] == [21]

    asyncio.run(_run())
