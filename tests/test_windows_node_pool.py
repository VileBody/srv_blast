from __future__ import annotations

import json

from services.orchestrator.windows_node_pool import (
    WindowsNodePool,
    normalize_windows_urls,
    parse_windows_urls_csv,
)


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value):
        self._data[key] = str(value)
        return True

    def delete(self, key: str):
        self._data.pop(key, None)
        return 1

    def mget(self, keys: list[str]):
        return [self._data.get(k) for k in keys]

    def eval(self, _script: str, numkeys: int, *args):
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        if numkeys == 1:
            # release script
            key = keys[0]
            cur = int(self._data.get(key, "0") or 0)
            if cur <= 1:
                self._data.pop(key, None)
                return 0
            cur -= 1
            self._data[key] = str(cur)
            return cur

        # reserve script: KEYS[1]=cursor, KEYS[2..]=inflight
        cursor_key = keys[0]
        inflight_keys = keys[1:]
        n = len(inflight_keys)
        if n <= 0:
            return [0, 0]
        cursor = int(self._data.get(cursor_key, "0") or 0)
        if cursor < 0:
            cursor = 0

        min_val = None
        best: list[int] = []
        for offset in range(1, n + 1):
            idx = ((cursor + offset - 1) % n) + 1
            key = inflight_keys[idx - 1]
            val = int(self._data.get(key, "0") or 0)
            if min_val is None or val < min_val:
                min_val = val
                best = [idx]
            elif val == min_val:
                best.append(idx)

        chosen = best[0]
        if len(best) > 1:
            rr = (cursor % len(best)) + 1
            chosen = best[rr - 1]

        inflight_key = inflight_keys[chosen - 1]
        new_val = int(self._data.get(inflight_key, "0") or 0) + 1
        self._data[inflight_key] = str(new_val)
        self._data[cursor_key] = str(chosen)
        return [chosen, new_val]


def test_parse_windows_urls_csv_deduplicates_and_normalizes() -> None:
    raw = " http://10.0.0.1:8000/ ,http://10.0.0.1:8000, https://10.0.0.2:8000 "
    urls = parse_windows_urls_csv(raw)
    assert urls == ["http://10.0.0.1:8000", "https://10.0.0.2:8000"]

    normalized = normalize_windows_urls(["", "localhost:8000", "http://10.0.0.3:8000/"])
    assert normalized == ["http://10.0.0.3:8000"]


def test_runtime_pool_storage_and_fallback() -> None:
    r = _FakeRedis()
    pool = WindowsNodePool(redis_client=r, key_prefix="blast", lease_ttl_s=3600)

    env_defaults = ["http://env-1:8000", "http://env-2:8000"]
    assert pool.get_active_urls(default_urls=env_defaults) == env_defaults

    saved = pool.set_active_urls(["http://10.0.0.5:8000/", "http://10.0.0.5:8000"])
    assert saved == ["http://10.0.0.5:8000"]
    raw = r.get(pool.runtime_key)
    assert json.loads(raw) == ["http://10.0.0.5:8000"]
    assert pool.get_active_urls(default_urls=env_defaults) == ["http://10.0.0.5:8000"]

    pool.set_active_urls([])
    assert pool.get_active_urls(default_urls=env_defaults) == env_defaults


def test_reserve_round_robin_and_release() -> None:
    r = _FakeRedis()
    pool = WindowsNodePool(redis_client=r, key_prefix="blast", lease_ttl_s=3600)
    urls = ["http://10.0.0.10:8000", "http://10.0.0.11:8000"]

    # Equal inflight => reserve alternates on ties.
    assert pool.reserve_best(urls) == urls[0]
    assert pool.reserve_best(urls) == urls[1]

    snap = pool.inflight_snapshot(urls)
    assert snap[urls[0]] == 1
    assert snap[urls[1]] == 1

    assert pool.release(urls[0]) == 0
    snap2 = pool.inflight_snapshot(urls)
    assert snap2[urls[0]] == 0
    assert snap2[urls[1]] == 1
