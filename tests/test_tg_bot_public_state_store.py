from __future__ import annotations

import asyncio
import sys
import time
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

from services.tg_bot_public.state_store import (
    ChatState,
    RedisChatStateStore,
    STAGE_KEEP_IN_TOUCH,
    STAGE_PROCESSING,
    STAGE_WAIT_AUDIO,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool | None = None):
        del ex
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

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
            if key in self.hashes:
                removed += 1
                self.hashes.pop(key, None)
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

    async def sscan(self, key: str, cursor: int = 0, count: int = 10):
        members = sorted(self.sets.get(key, set()))
        if not members:
            return 0, []
        start = int(cursor)
        end = min(len(members), start + max(1, int(count)))
        next_cursor = 0 if end >= len(members) else end
        return next_cursor, members[start:end]

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

    async def hget(self, key: str, field: str):
        return self.hashes.get(key, {}).get(str(field))

    async def hset(self, key: str, field: str, value: str) -> int:
        bucket = self.hashes.setdefault(key, {})
        existed = str(field) in bucket
        bucket[str(field)] = str(value)
        return 0 if existed else 1

    async def hdel(self, key: str, *fields: str) -> int:
        bucket = self.hashes.setdefault(key, {})
        removed = 0
        for field in fields:
            if str(field) in bucket:
                bucket.pop(str(field), None)
                removed += 1
        return removed

    async def hgetall(self, key: str):
        return dict(self.hashes.get(key, {}))

    async def hmget(self, key: str, fields: list[str]):
        bucket = self.hashes.get(key, {})
        return [bucket.get(str(field)) for field in fields]

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        bucket = self.hashes.setdefault(key, {})
        current = int(bucket.get(str(field), "0"))
        new_val = current + int(amount)
        bucket[str(field)] = str(new_val)
        return new_val

    async def aclose(self) -> None:
        return None

    async def scan_iter(self, match: str, count: int = 200):  # pragma: no cover
        del match, count
        raise AssertionError("scan_iter must not be used in indexed state store")



def _make_store(fake_redis: _FakeRedis) -> RedisChatStateStore:
    store = object.__new__(RedisChatStateStore)
    store._prefix = "blast:tg:public:chat_state"
    store._username_index_prefix = f"{store._prefix}:username_index"
    store._chat_username_prefix = f"{store._prefix}:chat_username"
    store._all_ids_key = f"{store._prefix}:idx:all"
    store._processing_ids_key = f"{store._prefix}:idx:processing"
    store._processing_set_key = f"{store._prefix}:__index:processing"
    store._webhook_seen_prefix = f"{store._prefix}:webhook_seen"
    store._waiting_referral_ids_key = f"{store._prefix}:idx:waiting_referral"
    store._reminder_zset_key = f"{store._prefix}:idx:reminder_at"
    store._updated_at_zset_key = f"{store._prefix}:idx:updated_at"
    store._stage_counts_key = f"{store._prefix}:idx:stage_counts"
    store._stage_by_chat_key = f"{store._prefix}:idx:stage_by_chat"
    store._state_ttl_s = 86400
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


def test_get_raises_runtime_error_for_broken_json() -> None:
    redis = _FakeRedis()
    store = _make_store(redis)
    redis.data[store._key(1)] = "{broken-json"

    with pytest.raises(RuntimeError, match="Corrupted chat state"):
        asyncio.run(store.get(1))


def test_mark_webhook_update_seen_is_idempotent() -> None:
    async def _run() -> None:
        redis = _FakeRedis()
        store = _make_store(redis)

        first = await store.mark_webhook_update_seen(1001, ttl_s=60)
        second = await store.mark_webhook_update_seen(1001, ttl_s=60)

        assert first is True
        assert second is False

    asyncio.run(_run())


def test_list_processing_reads_from_processing_index() -> None:
    async def _run() -> None:
        redis = _FakeRedis()
        store = _make_store(redis)

        st_processing = ChatState(chat_id=100, stage=STAGE_PROCESSING, active_job_ids=["job-1"])
        st_idle = ChatState(chat_id=101, stage=STAGE_WAIT_AUDIO)
        await store.set(st_processing)
        await store.set(st_idle)

        got = await store.list_processing()
        assert [s.chat_id for s in got] == [100]

    asyncio.run(_run())


def test_list_processing_reads_from_legacy_processing_index() -> None:
    async def _run() -> None:
        redis = _FakeRedis()
        store = _make_store(redis)

        st_processing = ChatState(chat_id=111, stage=STAGE_PROCESSING, active_job_ids=["job-legacy"])
        await store.set(st_processing)
        await redis.srem(store._processing_ids_key, "111")

        got = await store.list_processing()
        assert [s.chat_id for s in got] == [111]

    asyncio.run(_run())


def test_list_pending_reminders_uses_reminder_index() -> None:
    async def _run() -> None:
        redis = _FakeRedis()
        store = _make_store(redis)

        now = time.time()
        due = ChatState(chat_id=201, stage=STAGE_KEEP_IN_TOUCH, reminder_at=now - 10)
        later = ChatState(chat_id=202, stage=STAGE_KEEP_IN_TOUCH, reminder_at=now + 3600)
        await store.set(due)
        await store.set(later)

        got = await store.list_pending_reminders(now)
        assert [s.chat_id for s in got] == [201]

    asyncio.run(_run())


def test_stage_counts_and_stage_lookup_for_page_ids() -> None:
    async def _run() -> None:
        redis = _FakeRedis()
        store = _make_store(redis)

        await store.set(ChatState(chat_id=301, stage=STAGE_WAIT_AUDIO))
        await store.set(ChatState(chat_id=302, stage=STAGE_PROCESSING))

        counts = await store.list_stage_counts()
        assert counts.get(STAGE_WAIT_AUDIO) == 1
        assert counts.get(STAGE_PROCESSING) == 1

        page_map = await store.get_stages_for_chat_ids([302, 999, 301])
        assert page_map == {302: STAGE_PROCESSING, 301: STAGE_WAIT_AUDIO}

    asyncio.run(_run())


def test_cleanup_index_members_purges_orphan_index_entries() -> None:
    async def _run() -> None:
        redis = _FakeRedis()
        store = _make_store(redis)

        await store.set(ChatState(chat_id=401, stage=STAGE_WAIT_AUDIO))
        redis.data.pop(store._key(401), None)  # simulate expired state key

        removed = await store.cleanup_index_members(limit=10)
        assert removed == 1
        assert "401" not in await redis.smembers(store._all_ids_key)

    asyncio.run(_run())
