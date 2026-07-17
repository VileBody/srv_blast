from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Sequence


def _normalize_url(raw: str) -> str:
    return str(raw or "").strip().rstrip("/")


def probe_render_capacity(
    urls: Sequence[str],
    *,
    timeout_s: float = 2.5,
    retry_after_seconds: int = 15,
) -> dict[str, Any]:
    """Synchronously probe configured AE nodes and fail closed on dead URLs."""
    configured = [url for url in (_normalize_url(v) for v in urls) if url]
    healthy: list[str] = []
    errors: dict[str, str] = {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for base_url in configured:
        try:
            req = urllib.request.Request(
                f"{base_url}/health",
                headers={"Accept": "application/json"},
                method="GET",
            )
            with opener.open(req, timeout=max(0.2, float(timeout_s))) as resp:
                status_code = int(getattr(resp, "status", 0) or 0)
                body = resp.read(65536)
            if status_code < 200 or status_code >= 300:
                raise RuntimeError(f"http_{status_code}")
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("invalid_health_payload")
            is_ready = (
                payload.get("ready") is True
                or payload.get("ok") is True
                or str(payload.get("status") or "").strip().lower() == "ok"
            )
            if not is_ready:
                raise RuntimeError("node_not_ready")
            healthy.append(base_url)
        except Exception as exc:
            errors[base_url] = type(exc).__name__
    return {
        "ready": bool(healthy),
        "reason": "" if healthy else "no_healthy_render_nodes",
        "healthy_urls": healthy,
        "configured_count": len(configured),
        "retry_after_seconds": max(1, int(retry_after_seconds)),
        "observed_at": time.time(),
        "errors": errors,
    }
