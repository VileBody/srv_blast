from __future__ import annotations

from typing import Any, Dict

import httpx


class OrchestratorClient:
    def __init__(self, *, base_url: str, timeout_s: float = 60.0):
        self._base_url = (base_url or "").rstrip("/")
        if not self._base_url:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        self._client = httpx.AsyncClient(timeout=float(timeout_s))

    async def close(self) -> None:
        await self._client.aclose()

    async def send_audio_s3(
        self,
        *,
        audio_s3_url: str,
        mode: str,
        lyrics_text: str,
        idempotency_key: str | None = None,
        project_id: str | None = None,
    ) -> Dict[str, Any]:
        payload = {
            "audio_s3_url": str(audio_s3_url),
            "mode": str(mode),
            "lyrics_text": str(lyrics_text or ""),
            "idempotency_key": idempotency_key,
            "project_id": project_id,
        }
        resp = await self._client.post(f"{self._base_url}/send_audio_s3", json=payload)
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator /send_audio_s3 failed status={resp.status_code} body={resp.text}")
        out = resp.json()
        if not isinstance(out, dict):
            raise RuntimeError(f"orchestrator /send_audio_s3 returned non-object: {out!r}")
        return out

    async def get_job(self, job_id: str) -> Dict[str, Any]:
        jid = str(job_id or "").strip()
        if not jid:
            raise RuntimeError("get_job requires non-empty job_id")
        resp = await self._client.get(f"{self._base_url}/jobs/{jid}")
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator /jobs/{jid} failed status={resp.status_code} body={resp.text}")
        out = resp.json()
        if not isinstance(out, dict):
            raise RuntimeError(f"orchestrator /jobs/{jid} returned non-object: {out!r}")
        return out
