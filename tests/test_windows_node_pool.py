from __future__ import annotations

import json

from services.orchestrator.windows_node_pool import (
    WindowsNodePool,
    normalize_windows_nodes,
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
    assert pool.get_runtime_urls() == ["http://10.0.0.5:8000"]
    raw = r.get(pool.runtime_key)
    assert json.loads(raw) == {
        "nodes": [
            {
                "url": "http://10.0.0.5:8000",
                "enabled": True,
                "disabled_reason": "",
                "disabled_at": None,
            }
        ]
    }
    assert pool.get_active_urls(default_urls=env_defaults) == ["http://10.0.0.5:8000"]

    pool.set_active_urls([])
    assert pool.get_runtime_urls() == []
    assert pool.get_active_urls(default_urls=env_defaults) == env_defaults


def test_runtime_nodes_disable_keeps_node_in_pool() -> None:
    r = _FakeRedis()
    pool = WindowsNodePool(redis_client=r, key_prefix="blast", lease_ttl_s=3600)
    env_defaults = ["http://85.239.48.31:8000", "http://72.56.246.24:8000"]

    nodes, changed = pool.disable_node(
        url="http://85.239.48.31:8000",
        reason="poll_timeout_before_poll",
        default_urls=env_defaults,
    )
    assert changed is True
    assert len(nodes) == 2
    assert pool.get_active_urls(default_urls=[]) == ["http://72.56.246.24:8000"]

    runtime_nodes = pool.get_runtime_nodes()
    assert runtime_nodes[0]["url"] == "http://85.239.48.31:8000"
    assert runtime_nodes[0]["enabled"] is False
    assert runtime_nodes[0]["disabled_reason"] == "poll_timeout_before_poll"
    assert runtime_nodes[0]["disabled_at"] is not None

    nodes2, changed2 = pool.enable_node(
        url="http://85.239.48.31:8000",
        default_urls=env_defaults,
    )
    assert changed2 is True
    assert pool.get_active_urls(default_urls=[]) == env_defaults
    assert any(n["url"] == "http://85.239.48.31:8000" and n["enabled"] for n in nodes2)


def test_normalize_windows_nodes_back_compat() -> None:
    out = normalize_windows_nodes(
        [
            "http://10.0.0.1:8000/",
            {"url": "http://10.0.0.2:8000", "enabled": False, "disabled_reason": "manual"},
            {"url": "http://10.0.0.2:8000", "enabled": True},
        ]
    )
    assert out[0]["url"] == "http://10.0.0.1:8000"
    assert out[0]["enabled"] is True
    assert out[1]["url"] == "http://10.0.0.2:8000"
    assert out[1]["enabled"] is False
    assert out[1]["disabled_reason"] == "manual"


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


# ── env fallback when runtime pool has no ENABLED node (fix: one disabled node
#    must not block all dispatch) ───────────────────────────────────────────────

def _pool() -> WindowsNodePool:
    return WindowsNodePool(redis_client=_FakeRedis(), key_prefix="blast", lease_ttl_s=7200)


def test_get_active_urls_all_disabled_falls_back_to_env() -> None:
    pool = _pool()
    # Runtime pool: a single DISABLED node (e.g. auto-disabled on a render failure).
    pool.set_runtime_nodes([{"url": "http://192.168.0.7:18000", "enabled": False,
                             "disabled_reason": "broken AE"}])
    env = ["http://fallback-node:18000"]
    assert pool.get_active_urls(default_urls=env) == ["http://fallback-node:18000"], (
        "all runtime nodes disabled -> must fall back to env default_urls"
    )


def test_get_active_urls_prefers_enabled_runtime_over_env() -> None:
    pool = _pool()
    pool.set_runtime_nodes([
        {"url": "http://node-a:18000", "enabled": True},
        {"url": "http://node-b:18000", "enabled": False},
    ])
    # An enabled runtime node exists -> env is ignored entirely (unchanged behaviour).
    assert pool.get_active_urls(default_urls=["http://fallback-node:18000"]) == ["http://node-a:18000"]


def test_get_active_urls_empty_runtime_uses_env() -> None:
    pool = _pool()  # nothing set
    assert pool.get_active_urls(default_urls=["http://fallback-node:18000"]) == ["http://fallback-node:18000"]


def test_get_active_urls_empty_runtime_and_empty_env_is_empty() -> None:
    pool = _pool()
    assert pool.get_active_urls(default_urls=[]) == []


def test_get_active_urls_all_disabled_and_empty_env_is_empty() -> None:
    pool = _pool()
    pool.set_runtime_nodes([{"url": "http://192.168.0.7:18000", "enabled": False}])
    # No enabled runtime node AND no env -> still empty (same loud failure as before).
    assert pool.get_active_urls(default_urls=[]) == []


def test_get_effective_nodes_unchanged_for_enable_disable_bookkeeping() -> None:
    """get_effective_nodes (used by enable/disable) must still return the runtime
    list as-is when present, so the disabled-node record is preserved."""
    pool = _pool()
    pool.set_runtime_nodes([{"url": "http://192.168.0.7:18000", "enabled": False, "disabled_reason": "x"}])
    nodes = pool.get_effective_nodes(default_urls=["http://fallback-node:18000"])
    assert [n["url"] for n in nodes] == ["http://192.168.0.7:18000"]
    assert nodes[0]["enabled"] is False
