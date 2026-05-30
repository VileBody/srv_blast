from __future__ import annotations

from typing import Any, Dict, List

import httpx
from core.subtitles_mode import SUBTITLES_MODE_LEGACY_BLOCKS


class OrchestratorClient:
    def __init__(self, *, base_url: str, timeout_s: float = 60.0):
        self._base_url = (base_url or "").rstrip("/")
        if not self._base_url:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        self._client = httpx.AsyncClient(timeout=float(timeout_s))

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_json(self, path: str) -> Dict[str, Any]:
        resp = await self._client.get(f"{self._base_url}{path}")
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator {path} failed status={resp.status_code} body={resp.text}")
        out = resp.json()
        if not isinstance(out, dict):
            raise RuntimeError(f"orchestrator {path} returned non-object: {out!r}")
        return out

    async def send_audio_s3(
        self,
        *,
        audio_s3_url: str,
        mode: str,
        lyrics_text: str,
        target_fragment: str,
        subtitles_mode: str = SUBTITLES_MODE_LEGACY_BLOCKS,
        footage_artist_id: str = "",
        user_clip_start_sec: float | None = None,
        user_clip_end_sec: float | None = None,
        idempotency_key: str | None = None,
        project_id: str | None = None,
        reuse_text_job_id: str | None = None,
        exclude_file_names: List[str] | None = None,
        variant_index: int | None = None,
        variants_total: int | None = None,
        rotation_theme: str = "",
        rotation_tags_group: str = "",
        bg_mode: str = "footage",
        bg_solid_color: str = "",
        hook_enabled: bool = False,
        user_drop_t: float | None = None,
        hook_device: str | None = None,
        f4_device: str | None = None,
    ) -> Dict[str, Any]:
        payload = {
            "audio_s3_url": str(audio_s3_url),
            "mode": str(mode),
            "lyrics_text": str(lyrics_text or ""),
            "target_fragment": str(target_fragment or ""),
            "subtitles_mode": str(subtitles_mode or SUBTITLES_MODE_LEGACY_BLOCKS),
            "footage_artist_id": str(footage_artist_id or ""),
            "user_clip_start_sec": (
                float(user_clip_start_sec)
                if user_clip_start_sec is not None
                else None
            ),
            "user_clip_end_sec": (
                float(user_clip_end_sec)
                if user_clip_end_sec is not None
                else None
            ),
            "idempotency_key": idempotency_key,
            "project_id": project_id,
            "reuse_text_job_id": str(reuse_text_job_id or "") or None,
            "exclude_file_names": [str(x).strip() for x in list(exclude_file_names or []) if str(x).strip()],
            "variant_index": int(variant_index) if variant_index is not None else None,
            "variants_total": int(variants_total) if variants_total is not None else None,
            "rotation_theme": str(rotation_theme or "").strip(),
            "rotation_tags_group": str(rotation_tags_group or "").strip(),
            "bg_mode": str(bg_mode or "footage").strip() or "footage",
            "bg_solid_color": str(bg_solid_color or "").strip(),
            "hook_enabled": bool(hook_enabled),
            "user_drop_t": float(user_drop_t) if user_drop_t is not None else None,
            "hook_device": (str(hook_device).strip() or None) if hook_device is not None else None,
            "f4_device": (str(f4_device).strip() or None) if f4_device is not None else None,
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
        return await self._get_json(f"/jobs/{jid}")

    async def get_queue_estimate(self, job_id: str) -> Dict[str, Any]:
        jid = str(job_id or "").strip()
        if not jid:
            raise RuntimeError("get_queue_estimate requires non-empty job_id")
        return await self._get_json(f"/jobs/{jid}/queue-estimate")
