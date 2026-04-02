from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Iterable, Sequence

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

    def get_active_urls(self, *, default_urls: Sequence[str]) -> list[str]:
        try:
            raw = self._r.get(self.runtime_key)
        except Exception:
            raw = None
        if raw:
            try:
                obj = json.loads(raw)
            except Exception:
                obj = None
            if isinstance(obj, list):
                urls = normalize_windows_urls(str(x) for x in obj)
                if urls:
                    return urls
        return normalize_windows_urls(default_urls)

    def set_active_urls(self, urls: Sequence[str]) -> list[str]:
        normalized = normalize_windows_urls(urls)
        if normalized:
            self._r.set(self.runtime_key, json.dumps(normalized, ensure_ascii=False))
        else:
            self._r.delete(self.runtime_key)
        return normalized

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
