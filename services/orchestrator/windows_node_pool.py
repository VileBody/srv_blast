from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

if TYPE_CHECKING:
    import redis


def normalize_windows_url(raw: str) -> str:
    url = str(raw or "").strip()
    if not url:
        return ""
    if "://" not in url:
        return ""
    return url.rstrip("/")


def normalize_windows_urls(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        url = normalize_windows_url(str(raw or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def parse_windows_urls_csv(raw: str) -> list[str]:
    s = str(raw or "").strip()
    if not s:
        return []
    return normalize_windows_urls(part for part in s.split(","))


def _to_float_or_none(raw: Any) -> float | None:
    try:
        val = float(raw)
    except Exception:
        return None
    if val <= 0:
        return None
    return val


def _normalize_disabled_reason(raw: Any) -> str:
    txt = str(raw or "").strip()
    if not txt:
        return ""
    return txt[:500]


def normalize_windows_nodes(values: Iterable[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    now = time.time()
    for raw in values:
        if isinstance(raw, str):
            url = normalize_windows_url(raw)
            enabled = True
            reason = ""
            disabled_at: float | None = None
        elif isinstance(raw, Mapping):
            url = normalize_windows_url(str(raw.get("url") or ""))
            enabled = bool(raw.get("enabled", True))
            reason = _normalize_disabled_reason(raw.get("disabled_reason"))
            disabled_at = _to_float_or_none(raw.get("disabled_at"))
        else:
            continue

        if not url or url in seen:
            continue
        seen.add(url)

        if enabled:
            reason = ""
            disabled_at = None
        else:
            if not reason:
                reason = "manual_disabled"
            if disabled_at is None:
                disabled_at = now

        out.append(
            {
                "url": url,
                "enabled": bool(enabled),
                "disabled_reason": reason,
                "disabled_at": disabled_at,
            }
        )
    return out


def runtime_windows_urls_key(*, key_prefix: str) -> str:
    return f"{str(key_prefix or 'blast').strip()}:windows:active_urls"


_LUA_RESERVE_BEST = """
local n = #KEYS - 1
if n <= 0 then
  return {0, 0}
end

local cursor = tonumber(redis.call("GET", KEYS[1]) or "0") or 0
if cursor < 0 then
  cursor = 0
end

local min_val = nil
local best = {}
for offset = 1, n do
  local idx = ((cursor + offset - 1) % n) + 1
  local key = KEYS[idx + 1]
  local val = tonumber(redis.call("GET", key) or "0") or 0
  if (min_val == nil) or (val < min_val) then
    min_val = val
    best = {idx}
  elseif val == min_val then
    table.insert(best, idx)
  end
end

local chosen = best[1]
if #best > 1 then
  local rr = (cursor % #best) + 1
  chosen = best[rr]
end

local inflight_key = KEYS[chosen + 1]
local new_val = redis.call("INCR", inflight_key)
local ttl = tonumber(ARGV[1]) or 0
if ttl > 0 then
  redis.call("EXPIRE", inflight_key, ttl)
end
redis.call("SET", KEYS[1], chosen)
return {chosen, new_val}
"""


_LUA_RELEASE = """
local raw = redis.call("GET", KEYS[1])
if not raw then
  return 0
end
local cur = tonumber(raw) or 0
if cur <= 1 then
  redis.call("DEL", KEYS[1])
  return 0
end
local new_val = redis.call("DECR", KEYS[1])
if new_val < 0 then
  redis.call("DEL", KEYS[1])
  return 0
end
local ttl = tonumber(ARGV[1]) or 0
if ttl > 0 then
  redis.call("EXPIRE", KEYS[1], ttl)
end
return new_val
"""


class WindowsNodePool:
    def __init__(self, *, redis_client: Any, key_prefix: str, lease_ttl_s: int = 7200):
        self._r = redis_client
        self._prefix = str(key_prefix or "blast").strip()
        ttl = int(lease_ttl_s or 0)
        self._lease_ttl_s = ttl if ttl > 0 else 7200

    @property
    def runtime_key(self) -> str:
        return runtime_windows_urls_key(key_prefix=self._prefix)

    @property
    def cursor_key(self) -> str:
        return f"{self._prefix}:windows:rr_cursor"

    def _inflight_key(self, url: str) -> str:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        return f"{self._prefix}:windows:inflight:{digest}"

    def get_runtime_nodes(self) -> list[dict[str, Any]]:
        try:
            raw = self._r.get(self.runtime_key)
        except Exception:
            raw = None
        if not raw:
            return []
        try:
            obj = json.loads(raw)
        except Exception:
            return []
        if isinstance(obj, list):
            # Backward-compatible format: ["http://node-a:8000", ...]
            return normalize_windows_nodes(obj)
        if not isinstance(obj, dict):
            return []
        nodes_raw = obj.get("nodes")
        if not isinstance(nodes_raw, list):
            return []
        return normalize_windows_nodes(nodes_raw)

    def get_runtime_urls(self) -> list[str]:
        nodes = self.get_runtime_nodes()
        return [str(node["url"]) for node in nodes if bool(node.get("enabled", True))]

    def get_effective_nodes(self, *, default_urls: Sequence[str]) -> list[dict[str, Any]]:
        runtime_nodes = self.get_runtime_nodes()
        if runtime_nodes:
            return runtime_nodes
        return normalize_windows_nodes(default_urls)

    def get_active_urls(self, *, default_urls: Sequence[str]) -> list[str]:
        runtime_nodes = self.get_runtime_nodes()
        if runtime_nodes:
            # A present runtime pool is authoritative. If every runtime node is
            # disabled, return [] instead of resurrecting a dead env target.
            return [
                str(node["url"])
                for node in runtime_nodes
                if bool(node.get("enabled", True))
            ]
        # Static URLs are only a bootstrap when no runtime pool exists at all.
        return [
            str(node["url"])
            for node in normalize_windows_nodes(default_urls)
            if bool(node.get("enabled", True))
        ]

    def set_runtime_nodes(self, nodes: Sequence[Any]) -> list[dict[str, Any]]:
        normalized = normalize_windows_nodes(nodes)
        if normalized:
            payload = {"nodes": normalized}
            self._r.set(self.runtime_key, json.dumps(payload, ensure_ascii=False))
        else:
            self._r.delete(self.runtime_key)
        return normalized

    def set_active_urls(self, urls: Sequence[str]) -> list[str]:
        normalized = normalize_windows_urls(urls)
        if normalized:
            self.set_runtime_nodes([{"url": u, "enabled": True} for u in normalized])
            return normalized
        self._r.delete(self.runtime_key)
        return []

    def _set_node_enabled(
        self,
        *,
        url: str,
        enabled: bool,
        reason: str,
        default_urls: Sequence[str],
    ) -> tuple[list[dict[str, Any]], bool]:
        normalized = normalize_windows_url(url)
        if not normalized:
            return self.get_effective_nodes(default_urls=default_urls), False

        nodes = self.get_effective_nodes(default_urls=default_urls)
        changed = False
        found = False
        disabled_reason = _normalize_disabled_reason(reason)
        if not disabled_reason and not enabled:
            disabled_reason = "auto_disabled"

        for node in nodes:
            if str(node.get("url") or "") != normalized:
                continue
            found = True
            prev_enabled = bool(node.get("enabled", True))
            prev_reason = str(node.get("disabled_reason") or "")
            if prev_enabled != bool(enabled):
                changed = True
            if not enabled and prev_reason != disabled_reason:
                changed = True
            if enabled:
                node["enabled"] = True
                node["disabled_reason"] = ""
                node["disabled_at"] = None
            else:
                node["enabled"] = False
                node["disabled_reason"] = disabled_reason
                node["disabled_at"] = time.time()

        if not found:
            changed = True
            nodes.append(
                {
                    "url": normalized,
                    "enabled": bool(enabled),
                    "disabled_reason": "" if enabled else disabled_reason,
                    "disabled_at": None if enabled else time.time(),
                }
            )

        if changed:
            nodes = self.set_runtime_nodes(nodes)
        return nodes, changed

    def disable_node(
        self,
        *,
        url: str,
        reason: str,
        default_urls: Sequence[str],
    ) -> tuple[list[dict[str, Any]], bool]:
        return self._set_node_enabled(
            url=url,
            enabled=False,
            reason=reason,
            default_urls=default_urls,
        )

    def enable_node(
        self,
        *,
        url: str,
        default_urls: Sequence[str],
    ) -> tuple[list[dict[str, Any]], bool]:
        return self._set_node_enabled(
            url=url,
            enabled=True,
            reason="",
            default_urls=default_urls,
        )

    def reserve_best(self, candidate_urls: Sequence[str]) -> str:
        urls = normalize_windows_urls(candidate_urls)
        if not urls:
            return ""
        keys = [self.cursor_key] + [self._inflight_key(u) for u in urls]
        try:
            resp = self._r.eval(_LUA_RESERVE_BEST, len(keys), *keys, str(self._lease_ttl_s))
        except Exception:
            return ""
        if not isinstance(resp, (list, tuple)) or not resp:
            return ""
        try:
            idx = int(resp[0]) - 1
        except Exception:
            return ""
        if idx < 0 or idx >= len(urls):
            return ""
        return urls[idx]

    def release(self, url: str) -> int:
        normalized = normalize_windows_url(url)
        if not normalized:
            return 0
        key = self._inflight_key(normalized)
        try:
            resp = self._r.eval(_LUA_RELEASE, 1, key, str(self._lease_ttl_s))
        except Exception:
            return 0
        try:
            return max(0, int(resp or 0))
        except Exception:
            return 0

    def inflight_snapshot(self, urls: Sequence[str]) -> dict[str, int]:
        normalized = normalize_windows_urls(urls)
        if not normalized:
            return {}
        keys = [self._inflight_key(u) for u in normalized]
        try:
            values = self._r.mget(keys)
        except Exception:
            values = [None for _ in keys]
        out: dict[str, int] = {}
        for url, raw in zip(normalized, values):
            try:
                out[url] = max(0, int(raw or 0))
            except Exception:
                out[url] = 0
        return out
