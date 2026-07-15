from __future__ import annotations

import json

from services.orchestrator.rust_gen_client import RustGenClient


class _Response:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


def test_rust_gen_client_uses_manager_contract_and_bearer(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["authorization"] = request.get_header("Authorization")
        seen["payload"] = json.loads((request.data or b"{}").decode("utf-8"))
        seen["timeout"] = timeout
        return _Response({"status": "accepted", "render_id": "rust-job-1"})

    class _PrivateOpener:
        open = staticmethod(_urlopen)

    monkeypatch.setattr("urllib.request.build_opener", lambda *_handlers: _PrivateOpener())
    client = RustGenClient("https://rust-gen.internal/", token="manager-token", timeout_s=12)
    out = client.dispatch_render({"schema": "ae-native-renderer.manager-request.v1", "job_id": "job-1"})

    assert seen["url"] == "https://rust-gen.internal/render"
    assert seen["method"] == "POST"
    assert seen["authorization"] == "Bearer manager-token"
    assert seen["payload"] == {"schema": "ae-native-renderer.manager-request.v1", "job_id": "job-1"}
    assert seen["timeout"] == 12.0
    assert out["_api"] == "rust-gen"
