# services/orchestrator/windows_client.py
from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any, Dict


def normalize_windows_render_api_mode(raw: str) -> str:
    mode = str(raw or "").strip().lower()
    if mode not in {"render", "jobs"}:
        raise RuntimeError(
            "WINDOWS_RENDER_API_MODE must be one of: render, jobs "
            f"(got {raw!r})"
        )
    return mode


def _post_json(url: str, payload: Dict[str, Any], *, timeout_s: float) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def _get_json(url: str, *, timeout_s: float) -> Dict[str, Any]:
    req = urllib.request.Request(url=url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


class WindowsRenderClient:
    """
    Supports explicit contracts:
    A) Async contract:
      POST {base}/render  -> {"status":"accepted","render_id":"..."}
      GET  {base}/render/{id} -> {"status":"running"|"succeeded"|"failed", ...}
    B) Sync contract:
      POST {base}/jobs -> {"job_id": "...", "success": true/false, "output_url": "...", ...}
    """

    def __init__(self, base_url: str, *, timeout_s: float = 30.0, api_mode: str = "jobs"):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_s = float(timeout_s)
        self.api_mode = normalize_windows_render_api_mode(api_mode)

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def dispatch_render(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("WindowsRenderClient.base_url is empty")
        endpoint = "/render" if self.api_mode == "render" else "/jobs"
        res = _post_json(f"{self.base_url}{endpoint}", payload, timeout_s=self.timeout_s)
        if isinstance(res, dict):
            res.setdefault("_api", self.api_mode)
        return res

    def get_render_status(self, render_id: str) -> Dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("WindowsRenderClient.base_url is empty")
        if self.api_mode != "render":
            raise RuntimeError("get_render_status requires WINDOWS_RENDER_API_MODE=render")
        rid = str(render_id).strip()
        if not rid:
            raise ValueError("render_id is empty")
        res = _get_json(f"{self.base_url}/render/{rid}", timeout_s=self.timeout_s)
        if isinstance(res, dict):
            res.setdefault("_api", "render")
        return res
