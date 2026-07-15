from __future__ import annotations

import json
import urllib.request
from urllib.error import HTTPError
from typing import Any, Dict


class RustGenClient:
    """Small client for the standalone ae-native-renderer render-manager API."""

    def __init__(self, base_url: str, *, token: str = "", timeout_s: float = 30.0):
        self.base_url = str(base_url or "").rstrip("/")
        self.token = str(token or "").strip()
        self.timeout_s = float(timeout_s)

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request_json(self, *, method: str, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("RustGenClient.base_url is empty")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = self._headers()
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url=f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            # The manager is a private worker address; never route render
            # payloads through process-wide HTTP(S) proxy settings.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"rust-gen HTTP {exc.code}: {body or exc.reason}") from exc
        decoded = json.loads(raw) if raw else {}
        if not isinstance(decoded, dict):
            raise RuntimeError(f"rust-gen returned non-object: {decoded!r}")
        decoded.setdefault("_api", "rust-gen")
        return decoded

    def dispatch_render(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_json(method="POST", path="/render", payload=payload)

    def get_render_status(self, render_id: str) -> Dict[str, Any]:
        rid = str(render_id or "").strip()
        if not rid:
            raise ValueError("render_id is empty")
        return self._request_json(method="GET", path=f"/render/{rid}")

    def cancel_render(self, render_id: str) -> Dict[str, Any]:
        rid = str(render_id or "").strip()
        if not rid:
            raise ValueError("render_id is empty")
        return self._request_json(method="DELETE", path=f"/render/{rid}")
