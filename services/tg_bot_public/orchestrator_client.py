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
        reuse_stage2_footage: bool = False,
        stage2_selection_seed_override: str | None = None,
        exclude_file_names: List[str] | None = None,
        variant_index: int | None = None,
        variants_total: int | None = None,
        maintenance_bypass_token: str | None = None,
        rotation_theme: str = "",
        rotation_tags_group: str = "",
        bg_mode: str = "footage",
        bg_solid_color: str = "",
        hook_enabled: bool = False,
        user_drop_t: float | None = None,
        hook_device: str | None = None,
        f4_device: str | None = None,
        f4_bpm: float | None = None,
        effect_hook: str | None = None,
        effect_transition: str | None = None,
        effect_extra: str | None = None,
        effect_extra_full: bool = False,
        effect_hook_extend: str | None = None,
        f2_shape: str | None = None,
        f1_sound_url: str | None = None,
        f1_sound_text: str | None = None,
        photo_style: str | None = None,
        photo_transition: str | None = None,
        subtitle_color_hex: str | None = None,
        accent_color_hex: str | None = None,
        render_engine: str = "ae",
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
            # Schema parity with tg_bot_botapi; public bot never sets these to
            # non-default (BIGTEST_ENABLED=False), but the payload must mirror.
            "reuse_stage2_footage": bool(reuse_stage2_footage),
            "stage2_selection_seed_override": (str(stage2_selection_seed_override).strip() or None) if stage2_selection_seed_override else None,
            "exclude_file_names": [str(x).strip() for x in list(exclude_file_names or []) if str(x).strip()],
            "variant_index": int(variant_index) if variant_index is not None else None,
            "variants_total": int(variants_total) if variants_total is not None else None,
            "maintenance_bypass_token": str(maintenance_bypass_token or "") or None,
            "rotation_theme": str(rotation_theme or "").strip(),
            "rotation_tags_group": str(rotation_tags_group or "").strip(),
            "bg_mode": str(bg_mode or "footage").strip() or "footage",
            "bg_solid_color": str(bg_solid_color or "").strip(),
            "hook_enabled": bool(hook_enabled),
            "user_drop_t": float(user_drop_t) if user_drop_t is not None else None,
            "hook_device": (str(hook_device).strip() or None) if hook_device is not None else None,
            "f4_device": (str(f4_device).strip() or None) if f4_device is not None else None,
            "f4_bpm": (float(f4_bpm) if f4_bpm is not None else None),
            "effect_hook": (str(effect_hook).strip() or None) if effect_hook is not None else None,
            "effect_transition": (str(effect_transition).strip() or None) if effect_transition is not None else None,
            "effect_extra": (str(effect_extra).strip() or None) if effect_extra is not None else None,
            "effect_extra_full": bool(effect_extra_full),
            "effect_hook_extend": (str(effect_hook_extend).strip() or None) if effect_hook_extend is not None else None,
            "f2_shape": (str(f2_shape).strip() or None) if f2_shape is not None else None,
            "f1_sound_url": (str(f1_sound_url).strip() or None) if f1_sound_url is not None else None,
            "f1_sound_text": (str(f1_sound_text).strip() or None) if f1_sound_text is not None else None,
            "photo_style": (str(photo_style).strip() or None) if photo_style is not None else None,
            "photo_transition": (str(photo_transition).strip() or None) if photo_transition is not None else None,
            "subtitle_color_hex": (str(subtitle_color_hex).strip() or None) if subtitle_color_hex is not None else None,
            "accent_color_hex": (str(accent_color_hex).strip() or None) if accent_color_hex is not None else None,
            "render_engine": str(render_engine or "ae").strip().lower() or "ae",
        }
        resp = await self._client.post(f"{self._base_url}/send_audio_s3", json=payload)
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator /send_audio_s3 failed status={resp.status_code} body={resp.text}")
        out = resp.json()
        if not isinstance(out, dict):
            raise RuntimeError(f"orchestrator /send_audio_s3 returned non-object: {out!r}")
        return out

    async def rank_buckets(
        self,
        *,
        lyrics: str,
        mood: str = "",
        top: int = 0,
    ) -> Dict[str, Any]:
        """Mirror of tg_bot_botapi: footage precision flow ranks the bucket
        catalog by lyrics relevance (one cheap LLM call on the orchestrator with
        heuristic fallback). Returns {buckets:[...], used_llm}. Present for
        schema parity; the vibe UX is gated behind FOOTAGE_VIBE_FLOW_ENABLED
        (default off in public)."""
        payload = {
            "lyrics": str(lyrics or ""),
            "mood": str(mood or "").strip(),
            "top": int(top or 0),
        }
        resp = await self._client.post(f"{self._base_url}/footage/rank-buckets", json=payload)
        if resp.status_code >= 300:
            raise RuntimeError(
                f"orchestrator /footage/rank-buckets failed status={resp.status_code} body={resp.text}"
            )
        out = resp.json()
        if not isinstance(out, dict):
            raise RuntimeError(f"orchestrator /footage/rank-buckets returned non-object: {out!r}")
        return out

    async def analyze_hook(
        self,
        *,
        audio_s3_url: str,
        clip_start_sec: float,
        clip_end_sec: float,
    ) -> Dict[str, Any]:
        """Mirror of tg_bot_botapi: F4 «Движение» picker asks the orchestrator
        (it has librosa) for {bpm, drop_candidates}. Keeps librosa out of the
        slim bot image."""
        payload = {
            "audio_s3_url": str(audio_s3_url),
            "clip_start_sec": float(clip_start_sec),
            "clip_end_sec": float(clip_end_sec),
        }
        resp = await self._client.post(f"{self._base_url}/hook/analyze", json=payload)
        if resp.status_code >= 300:
            raise RuntimeError(
                f"orchestrator /hook/analyze failed status={resp.status_code} body={resp.text}"
            )
        out = resp.json()
        if not isinstance(out, dict):
            raise RuntimeError(f"orchestrator /hook/analyze returned non-object: {out!r}")
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

    async def kill_job(self, job_id: str, *, reason: str = "") -> Dict[str, Any]:
        """Parity with tg_bot_botapi (used by /bigtest safety-breaker, which is
        team-only). Best-effort kill of a RUNNING/QUEUED job."""
        jid = str(job_id or "").strip()
        if not jid:
            raise RuntimeError("kill_job requires non-empty job_id")
        resp = await self._client.post(
            f"{self._base_url}/jobs/{jid}/kill",
            json={"reason": str(reason or "")},
        )
        if resp.status_code >= 300:
            raise RuntimeError(
                f"orchestrator /jobs/{jid}/kill failed status={resp.status_code} body={resp.text}"
            )
        out = resp.json()
        if not isinstance(out, dict):
            raise RuntimeError(f"kill_job returned non-object: {out!r}")
        return out

    async def get_jobs(self, job_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        cleaned: List[str] = []
        seen: set[str] = set()
        for raw in list(job_ids or []):
            jid = str(raw or "").strip()
            if not jid or jid in seen:
                continue
            seen.add(jid)
            cleaned.append(jid)
        if not cleaned:
            raise RuntimeError("get_jobs requires non-empty job_ids")
        resp = await self._client.post(f"{self._base_url}/jobs/batch", json={"job_ids": cleaned})
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator /jobs/batch failed status={resp.status_code} body={resp.text}")
        out = resp.json()
        if not isinstance(out, dict):
            raise RuntimeError(f"orchestrator /jobs/batch returned non-object: {out!r}")
        rows = out.get("jobs")
        if not isinstance(rows, list):
            raise RuntimeError(f"orchestrator /jobs/batch returned invalid jobs payload: {out!r}")
        jobs_by_id: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            jid = str(row.get("job_id") or "").strip()
            if jid:
                jobs_by_id[jid] = row
        missing = [jid for jid in cleaned if jid not in jobs_by_id]
        if missing:
            raise RuntimeError(f"orchestrator /jobs/batch missing jobs: {', '.join(missing[:20])}")
        return jobs_by_id

    async def get_health(self) -> Dict[str, Any]:
        return await self._get_json("/health")

    async def get_llm_workers(self) -> Dict[str, Any]:
        return await self._get_json("/llm-workers")

    async def get_windows_nodes(self) -> Dict[str, Any]:
        return await self._get_json("/windows-nodes")

    async def get_metrics(self) -> Dict[str, Any]:
        return await self._get_json("/metrics")
