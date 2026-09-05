"""Production S3 and orchestrator adapter for the web application."""
from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

import boto3
import httpx
from botocore.config import Config

from .runtime import SETTINGS


class ProductionBackendError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise ProductionBackendError(f"production_backend: {name} is required")
    return value


def _json_mapping(name: str) -> dict[str, str]:
    raw = _required(name)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProductionBackendError(f"production_backend: invalid {name}: {exc}") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ProductionBackendError(f"production_backend: {name} must be a non-empty object")
    return {str(key): str(value) for key, value in parsed.items() if str(key) and str(value)}


def _json_catalog(name: str) -> tuple[dict[str, Any], ...]:
    raw = _required(name)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProductionBackendError(f"production_backend: invalid {name}: {exc}") from exc
    if not isinstance(parsed, list) or not parsed:
        raise ProductionBackendError(f"production_backend: {name} must be a non-empty array")

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(parsed):
        if not isinstance(value, dict):
            raise ProductionBackendError(
                f"production_backend: {name}[{index}] must be an object"
            )
        item_id = str(value.get("id") or "").strip()
        item_name = str(value.get("name") or "").strip()
        locator = str(value.get("previewUrl") or "").strip()
        if not item_id or not item_name or not locator:
            raise ProductionBackendError(
                f"production_backend: {name}[{index}] requires id, name and previewUrl"
            )
        if item_id in seen_ids:
            raise ProductionBackendError(
                f"production_backend: duplicate id {item_id!r} in {name}"
            )
        if not locator.startswith(("s3://", "https://")):
            raise ProductionBackendError(
                f"production_backend: {name}[{index}].previewUrl must use s3:// or https://"
            )
        seen_ids.add(item_id)
        item: dict[str, Any] = {
            "id": item_id,
            "name": item_name,
            "previewUrl": locator,
            "score": float(value.get("score", 1.0)),
        }
        if value.get("plane"):
            item["plane"] = str(value["plane"])
        selector = value.get("selector")
        if selector is not None:
            if not isinstance(selector, dict):
                raise ProductionBackendError(
                    f"production_backend: {name}[{index}].selector must be an object"
                )
            if name == "WEB_FX_CATALOG_JSON":
                allowed = {"effectHook", "effectTransition", "effectExtra", "f4Device", "f2Shape"}
                if len(selector) != 1 or not set(selector).issubset(allowed):
                    raise ProductionBackendError(
                        f"production_backend: {name}[{index}].selector must contain exactly one supported FX field"
                    )
                item["selector"] = {key: str(value) for key, value in selector.items()}
            else:
                item["selector"] = _catalog_selector(f"{name}[{index}]", selector)
        items.append(item)
    return tuple(items)


_RENDER_PRESETS = {"vertical", "wide", "square"}
_BG_MODES = {"footage", "photo", "solid", "solid_strobe"}


def _catalog_selector(where: str, raw: dict[str, Any]) -> dict[str, str]:
    """Разобрать `selector` записи каталога.

    Запись каталога — это единственное место, где сайт узнаёт, ЧЕМ является
    выбранный бакет: пару (theme, tags_group) он передаёт оркестратору, а
    render_preset задаёт геометрию выдачи. Раньше эти поля молча терялись при
    разборе, и выбор бакета в визарде ни на что не влиял.

    Проверяем строго: пара rotation нужна целиком (половина пары оркестратору
    ничего не скажет), а неизвестная геометрия хуже исторической — 16:9 в
    вертикальном кадре центр-кропается в треть ширины.
    """
    out: dict[str, str] = {}
    theme = str(raw.get("rotationTheme") or "").strip()
    group = str(raw.get("rotationTagsGroup") or "").strip()
    if bool(theme) != bool(group):
        raise ProductionBackendError(
            f"production_backend: {where}.selector needs both rotationTheme and rotationTagsGroup"
        )
    if theme:
        out["rotationTheme"] = theme
        out["rotationTagsGroup"] = group
    preset = str(raw.get("renderPreset") or "").strip()
    if preset:
        if preset not in _RENDER_PRESETS:
            raise ProductionBackendError(
                f"production_backend: {where}.selector.renderPreset must be one of {sorted(_RENDER_PRESETS)}"
            )
        out["renderPreset"] = preset
    bg_mode = str(raw.get("bgMode") or "").strip()
    if bg_mode:
        if bg_mode not in _BG_MODES:
            raise ProductionBackendError(
                f"production_backend: {where}.selector.bgMode must be one of {sorted(_BG_MODES)}"
            )
        out["bgMode"] = bg_mode
    return out


def _time_value(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        nums = [int(part) for part in parts]
    except ValueError as exc:
        raise ProductionBackendError(f"invalid timing value {value!r}") from exc
    if len(nums) == 3:
        return round(nums[0] * 60 + nums[1] + nums[2] / 100.0, 3)
    if len(nums) == 2:
        return float(nums[0] * 60 + nums[1])
    if len(nums) == 1:
        return float(nums[0])
    raise ProductionBackendError(f"invalid timing value {value!r}")


@dataclass(frozen=True)
class ProductionConfig:
    orchestrator_url: str
    s3_endpoint_url: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_region: str
    raw_audio_bucket: str
    raw_audio_prefix: str
    asset_bucket: str
    asset_prefix: str
    stage1_backend: str
    subtitle_modes: dict[str, str]
    footage_artists: dict[str, str]
    footage_catalog: tuple[dict[str, Any], ...]
    photo_catalog: tuple[dict[str, Any], ...]
    subtitle_catalog: tuple[dict[str, Any], ...]
    # background_mode -> name -> selector записи каталога. Карта РАЗДЕЛЕНА по режиму
    # фона намеренно: у футажа и фото есть одинаковые подписи («Тёмный лес / туман»,
    # «Портрет девушки / светлый»), и в общем словаре фото затирало бы футаж — выбор
    # футажа уезжал бы в фото-рендер (bg_mode=photo, геометрия 4:3).
    # С дефолтами: конфиг собирают и тесты, и старые артистовые каталоги — им эти
    # поля не нужны, и требовать их значило бы ломать обратную совместимость.
    selector_by_mode: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    # базовый профиль артиста: с закреплённой парой rotation он больше не выбирает
    # бакет, но Stage 2 без него не планирует футаж (в т.ч. на solid-фоне)
    default_artist_id: str = ""
    fx_catalog: tuple[dict[str, Any], ...] = ()

    @classmethod
    def load(cls) -> "ProductionConfig":
        if SETTINGS.backend != "production":
            raise ProductionBackendError(
                "production_backend: BLAST_BACKEND_MODE is not production"
            )
        stage1_backend = _required("WEB_STAGE1_ALIGNMENT_BACKEND")
        if stage1_backend not in {"gemini", "local_ctc"}:
            raise ProductionBackendError(
                "production_backend: WEB_STAGE1_ALIGNMENT_BACKEND must be gemini or local_ctc"
            )
        subtitle_modes = _json_mapping("WEB_SUBTITLE_MODE_MAP_JSON")
        footage_artists = _json_mapping("WEB_FOOTAGE_ARTIST_MAP_JSON")
        footage_catalog = _json_catalog("WEB_FOOTAGE_CATALOG_JSON")
        photo_catalog = _json_catalog("WEB_PHOTO_CATALOG_JSON")
        subtitle_catalog = _json_catalog("WEB_SUBTITLE_CATALOG_JSON")
        fx_catalog = _json_catalog("WEB_FX_CATALOG_JSON")

        # Запись каталога может нести `selector` — точную пару (theme, tags_group),
        # которой закрепляется бакет. Такой записи карта артистов не нужна: артист
        # больше не решает, из чего брать клипы. Требуем маппинг только у записей
        # СТАРОГО, артистового формата — иначе фильмы и коллекции 16:9 просто
        # невозможно было бы завести (в карте артистов их нет и быть не может).
        selector_by_mode: dict[str, dict[str, dict[str, Any]]] = {"footage": {}, "photo": {}}
        for mode, catalog in (("footage", footage_catalog), ("photo", photo_catalog)):
            for item in catalog:
                selector = item.get("selector")
                if isinstance(selector, dict) and selector:
                    selector_by_mode[mode][str(item["name"])] = dict(selector)
        missing_artists = sorted(
            {
                str(item["name"])
                for mode, catalog in (("footage", footage_catalog), ("photo", photo_catalog))
                for item in catalog
                if str(item["name"]) not in footage_artists
                and str(item["name"]) not in selector_by_mode[mode]
            }
        )
        if missing_artists:
            raise ProductionBackendError(
                "production_backend: preview catalog entries have neither a "
                f"selector nor a WEB_FOOTAGE_ARTIST_MAP_JSON mapping: {missing_artists}"
            )
        default_artist_id = str(os.getenv("WEB_DEFAULT_FOOTAGE_ARTIST_ID") or "").strip()
        if not default_artist_id and footage_artists:
            default_artist_id = str(next(iter(footage_artists.values())))
        if not default_artist_id:
            raise ProductionBackendError(
                "production_backend: set WEB_DEFAULT_FOOTAGE_ARTIST_ID or provide "
                "WEB_FOOTAGE_ARTIST_MAP_JSON — Stage 2 needs a base artist profile"
            )
        missing_subtitles = sorted(
            {
                str(item["name"])
                for item in subtitle_catalog
                if str(item["name"]) not in subtitle_modes
            }
        )
        if missing_subtitles:
            raise ProductionBackendError(
                "production_backend: preview catalog names missing from "
                f"WEB_SUBTITLE_MODE_MAP_JSON: {missing_subtitles}"
            )

        return cls(
            orchestrator_url=_required("ORCHESTRATOR_PUBLIC_URL").rstrip("/"),
            s3_endpoint_url=_required("S3_ENDPOINT_URL"),
            s3_access_key_id=_required("S3_ACCESS_KEY_ID"),
            s3_secret_access_key=_required("S3_SECRET_ACCESS_KEY"),
            s3_region=_required("S3_REGION"),
            raw_audio_bucket=_required("S3_BUCKET_RAW_AUDIO"),
            raw_audio_prefix=_required("S3_RAW_AUDIO_PREFIX").strip("/"),
            asset_bucket=_required("S3_BUCKET_ASSET_STORAGE"),
            asset_prefix=_required("S3_WEB_ASSET_PREFIX").strip("/"),
            stage1_backend=stage1_backend,
            subtitle_modes=subtitle_modes,
            footage_artists=footage_artists,
            footage_catalog=footage_catalog,
            photo_catalog=photo_catalog,
            subtitle_catalog=subtitle_catalog,
            selector_by_mode=selector_by_mode,
            default_artist_id=default_artist_id,
            fx_catalog=fx_catalog,
        )


class ProductionBackend:
    def __init__(self, config: ProductionConfig) -> None:
        self.config = config
        self._s3 = boto3.client(
            "s3",
            endpoint_url=config.s3_endpoint_url,
            aws_access_key_id=config.s3_access_key_id,
            aws_secret_access_key=config.s3_secret_access_key,
            region_name=config.s3_region,
            config=Config(signature_version="s3v4"),
        )
        self._http = httpx.Client(timeout=httpx.Timeout(65.0, connect=10.0))

    def close(self) -> None:
        self._http.close()

    def healthcheck(self) -> None:
        response = self._http.get(f"{self.config.orchestrator_url}/health")
        if response.status_code >= 300:
            raise ProductionBackendError(
                f"orchestrator health failed status={response.status_code}"
            )
        self._s3.head_bucket(Bucket=self.config.raw_audio_bucket)
        for item in (
            *self.config.footage_catalog,
            *self.config.photo_catalog,
            *self.config.subtitle_catalog,
            *self.config.fx_catalog,
        ):
            locator = str(item["previewUrl"])
            if locator.startswith("s3://"):
                bucket, key = self._parse_s3_locator(locator)
                self._s3.head_object(Bucket=bucket, Key=key)

    def analyze_hook(
        self, *, audio_s3_url: str, clip_start_sec: float, clip_end_sec: float
    ) -> dict[str, Any]:
        """Кандидаты дропа + bpm для выбранного отрывка.

        Ровно та же ручка, что зовёт бот: librosa и `analyze_focus_clip` живут в
        оркестраторе, клиенты забирают готовый пикер (см. комментарий у
        `HookAnalyzeRequest` в `services/orchestrator/schemas.py`). Считать дроп
        на своей стороне нельзя — разошлись бы не только числа, но и то, к
        какому биту он приснапан.
        """
        response = self._http.post(
            f"{self.config.orchestrator_url}/hook/analyze",
            json={
                "audio_s3_url": str(audio_s3_url),
                "clip_start_sec": float(clip_start_sec),
                "clip_end_sec": float(clip_end_sec),
            },
        )
        if response.status_code >= 300:
            raise ProductionBackendError(
                f"orchestrator /hook/analyze failed status={response.status_code}"
            )
        return dict(response.json())

    def preview_catalog(self, kind: str) -> list[dict[str, Any]]:
        source = {
            "footage": self.config.footage_catalog,
            "photo": self.config.photo_catalog,
            "subtitle": self.config.subtitle_catalog,
            "fx": self.config.fx_catalog,
        }.get(kind)
        if source is None:
            raise ProductionBackendError(f"unsupported preview catalog {kind!r}")
        return [
            {
                **item,
                "previewUrl": self._preview_url(
                    str(item["previewUrl"]),
                    filename=f"{item['id']}-preview",
                ),
            }
            for item in source
        ]

    def upload_track(
        self,
        *,
        content: bytes,
        user_id: str,
        filename: str,
        content_type: str | None,
    ) -> dict[str, str]:
        suffix = Path(filename).suffix.lower() or ".mp3"
        key = (
            f"{self.config.raw_audio_prefix}/web/{quote(user_id, safe='')}/"
            f"{uuid4().hex}{suffix}"
        )
        resolved_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self._s3.put_object(
            Bucket=self.config.raw_audio_bucket,
            Key=key,
            Body=content,
            ContentType=resolved_type,
        )
        return {
            "s3_url": f"s3://{self.config.raw_audio_bucket}/{key}",
            "playback_url": self._presign(
                self.config.raw_audio_bucket,
                key,
                filename=filename,
                attachment=False,
            ),
            "key": key,
        }

    def upload_source(
        self,
        *,
        content: bytes,
        user_id: str,
        filename: str,
        content_type: str | None,
    ) -> dict[str, str]:
        suffix = Path(filename).suffix.lower() or ".mp4"
        key = (
            f"{self.config.asset_prefix}/users/{quote(user_id, safe='')}/sources/"
            f"{uuid4().hex}{suffix}"
        )
        self._s3.put_object(
            Bucket=self.config.asset_bucket,
            Key=key,
            Body=content,
            ContentType=content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        )
        return {
            "s3_url": f"s3://{self.config.asset_bucket}/{key}",
            "playback_url": self._presign(
                self.config.asset_bucket,
                key,
                filename=filename,
                attachment=False,
            ),
            "key": key,
        }

    def upload_hook_sound(
        self,
        *,
        content: bytes,
        user_id: str,
        filename: str,
        content_type: str | None,
    ) -> dict[str, str]:
        suffix = Path(filename).suffix.lower() or ".mp3"
        key = (
            f"{self.config.asset_prefix}/users/{quote(user_id, safe='')}/hook-sounds/"
            f"{uuid4().hex}{suffix}"
        )
        resolved_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self._s3.put_object(
            Bucket=self.config.asset_bucket,
            Key=key,
            Body=content,
            ContentType=resolved_type,
        )
        return {
            "s3_url": f"s3://{self.config.asset_bucket}/{key}",
            "playback_url": self._presign(
                self.config.asset_bucket,
                key,
                filename=filename,
                attachment=False,
            ),
            "key": key,
        }

    def upload_user_image(
        self,
        *,
        content: bytes,
        user_id: str,
        filename: str,
        content_type: str | None,
        kind: str,
    ) -> dict[str, str]:
        if kind not in {"avatars", "covers"}:
            raise ProductionBackendError(f"unsupported user image kind {kind!r}")
        suffix = Path(filename).suffix.lower() or ".jpg"
        key = (
            f"{self.config.asset_prefix}/users/{quote(user_id, safe='')}/{kind}/"
            f"{uuid4().hex}{suffix}"
        )
        self._s3.put_object(
            Bucket=self.config.asset_bucket,
            Key=key,
            Body=content,
            ContentType=content_type or mimetypes.guess_type(filename)[0] or "image/jpeg",
        )
        return {
            "s3_url": f"s3://{self.config.asset_bucket}/{key}",
            "playback_url": self._presign(
                self.config.asset_bucket,
                key,
                filename=filename,
                attachment=False,
            ),
            "key": key,
        }

    def delete_user_objects(self, user_id: str) -> None:
        prefixes = (
            (self.config.raw_audio_bucket, f"{self.config.raw_audio_prefix}/web/{quote(user_id, safe='')}/"),
            (self.config.asset_bucket, f"{self.config.asset_prefix}/users/{quote(user_id, safe='')}/"),
        )
        for bucket, prefix in prefixes:
            paginator = self._s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if keys:
                    self._s3.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})

    def delete_uploaded_asset(self, locator: str) -> None:
        """Delete one server-owned upload after its ownership was checked in the DB."""
        bucket, key = self._parse_s3_locator(locator)
        if bucket != self.config.asset_bucket or not key.startswith(f"{self.config.asset_prefix}/users/"):
            raise ProductionBackendError("upload locator is outside the managed asset prefix")
        self._s3.delete_object(Bucket=bucket, Key=key)

    def validate_stage(self, stage: dict[str, Any]) -> set[str]:
        from .batch_geometry import selected_geometry
        catalogs = {mode: {item["name"]: item for item in source} for mode, source in (
            ("footage", self.config.footage_catalog), ("photo", self.config.photo_catalog))}
        return selected_geometry(stage, catalogs)

    def validate_job(self, job: dict[str, Any]) -> None:
        variations = job["renderJob"].get("variations") or []
        if not variations:
            raise ProductionBackendError("Empty render batch")
        for index, variation in enumerate(variations, 1):
            self._request_payload(job=job, variation=variation, index=index, total=len(variations), master_id=None)

    def enqueue_job(self, job: dict[str, Any]) -> dict[str, Any]:
        self._enqueue_next(job)
        job["status"] = "PROCESSING"
        job["mock"] = False
        return job

    def _enqueue_next(self, job: dict[str, Any]) -> None:
        orchestrator_ids = [
            str(video.get("orchestratorJobId"))
            for video in job.get("videos", [])
            if video.get("orchestratorJobId")
        ]
        master_id: str | None = orchestrator_ids[0] if orchestrator_ids else None
        variations = list(job.get("renderJob", {}).get("variations") or [])
        videos = list(job.get("videos") or [])
        for index, (variation, video) in enumerate(zip(variations, videos, strict=True), start=1):
            if video.get("orchestratorJobId"):
                continue
            prior = videos[: index - 1]
            if any(item.get("status") != "COMPLETED" for item in prior):
                return
            payload = self._request_payload(
                job=job,
                variation=variation,
                index=index,
                total=len(variations),
                master_id=master_id,
            )
            response = self._http.post(
                f"{self.config.orchestrator_url}/send_audio_s3",
                json=payload,
            )
            if response.status_code >= 300:
                raise ProductionBackendError(
                    "orchestrator /send_audio_s3 failed "
                    f"status={response.status_code} body={response.text[:1000]}"
                )
            body = response.json()
            external_id = str(body.get("job_id") or "").strip()
            if not external_id:
                raise ProductionBackendError("orchestrator returned empty job_id")
            master_id = master_id or external_id
            orchestrator_ids.append(external_id)
            video["orchestratorJobId"] = external_id
            video["status"] = "PENDING"
            video["stage"] = "queued"
            video["progress"] = 1
            job["orchestratorJobIds"] = list(orchestrator_ids)
            job["orchestratorJobId"] = master_id
            return

    def sync_job(self, job: dict[str, Any]) -> dict[str, Any]:
        stage_progress = {
            "build": 25,
            "alignment": 20,
            "dispatch": 55,
            "render": 75,
            "poll": 90,
        }
        any_failed = False
        for video in job.get("videos", []):
            external_id = str(video.get("orchestratorJobId") or "")
            if not external_id:
                video.update(status="PENDING", stage="waiting_previous", progress=0)
                continue
            response = self._http.get(f"{self.config.orchestrator_url}/jobs/{external_id}")
            if response.status_code >= 300:
                raise ProductionBackendError(
                    f"orchestrator /jobs/{external_id} failed status={response.status_code}"
                )
            state = response.json()
            status = str(state.get("status") or "").upper()
            stage = str(state.get("stage") or "queued")
            if status == "SUCCEEDED":
                output_url = str((state.get("result") or {}).get("output_url") or "")
                video.update(
                    status="COMPLETED",
                    stage="done",
                    progress=100,
                    downloadUrl=self.download_url(output_url, video.get("id") or "video.mp4"),
                )
            elif status == "FAILED":
                any_failed = True
                video.update(
                    status="FAILED",
                    stage=stage,
                    progress=stage_progress.get(stage, video.get("progress", 0)),
                    error=str(state.get("error") or "render failed")[:2000],
                )
            else:
                video.update(
                    status="PENDING" if status in {"NEW", "QUEUED"} else "PROCESSING",
                    stage=stage,
                    progress=stage_progress.get(stage, max(1, int(video.get("progress") or 1))),
                )
        missing = [video for video in job.get("videos", []) if not video.get("orchestratorJobId")]
        if any_failed:
            for video in missing:
                video.update(
                    status="FAILED",
                    stage="skipped",
                    error="previous variation failed",
                    progress=0,
                )
        elif missing and all(
            video.get("status") == "COMPLETED"
            for video in job.get("videos", [])
            if video.get("orchestratorJobId")
        ):
            self._enqueue_next(job)

        terminal = all(
            video.get("status") in {"COMPLETED", "FAILED"}
            for video in job.get("videos", [])
        )
        any_failed = any(video.get("status") == "FAILED" for video in job.get("videos", []))
        job["status"] = "FAILED" if terminal and any_failed else "COMPLETED" if terminal else "PROCESSING"
        job["outputUrls"] = [
            video["downloadUrl"] for video in job.get("videos", []) if video.get("downloadUrl")
        ]
        return job

    def download_url(self, value: str, filename: str) -> str | None:
        if not value:
            return None
        if value.startswith("https://"):
            endpoint = urlparse(self.config.s3_endpoint_url)
            parsed = urlparse(value)
            bucket = ""
            key = ""
            if parsed.hostname == endpoint.hostname:
                path = parsed.path.lstrip("/")
                if "/" in path:
                    bucket, key = path.split("/", 1)
            elif endpoint.hostname and parsed.hostname and parsed.hostname.endswith(
                f".{endpoint.hostname}"
            ):
                bucket = parsed.hostname[: -(len(endpoint.hostname) + 1)]
                key = parsed.path.lstrip("/")
            if not bucket or not key:
                raise ProductionBackendError(
                    "orchestrator output HTTPS URL is not in the configured S3 endpoint"
                )
            return self._presign(
                unquote(bucket),
                unquote(key),
                filename=f"{filename}.mp4",
                attachment=True,
            )
        if not value.startswith("s3://") or "/" not in value[5:]:
            raise ProductionBackendError(f"unsupported output URL {value!r}")
        bucket, key = value[5:].split("/", 1)
        return self._presign(bucket, key, filename=f"{filename}.mp4", attachment=True)

    def download_video(self, value: str, destination: str | Path) -> Path:
        """Download a rendered S3 object for TikTok FILE_UPLOAD.

        Resolve only the configured S3 endpoint. This keeps the API from
        turning an internal video URL into an arbitrary server-side request.
        """
        if value.startswith("s3://"):
            bucket, key = self._parse_s3_locator(value)
        elif value.startswith("https://"):
            endpoint = urlparse(self.config.s3_endpoint_url)
            parsed = urlparse(value)
            bucket = ""
            key = ""
            if parsed.hostname == endpoint.hostname:
                path = parsed.path.lstrip("/")
                if "/" in path:
                    bucket, key = path.split("/", 1)
            elif endpoint.hostname and parsed.hostname and parsed.hostname.endswith(
                f".{endpoint.hostname}"
            ):
                bucket = parsed.hostname[: -(len(endpoint.hostname) + 1)]
                key = parsed.path.lstrip("/")
            if not bucket or not key:
                raise ProductionBackendError(
                    "TikTok source HTTPS URL is not in the configured S3 endpoint"
                )
            bucket, key = unquote(bucket), unquote(key)
        else:
            raise ProductionBackendError(f"unsupported TikTok video URL {value!r}")

        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(bucket, key, str(target))
        if not target.is_file() or target.stat().st_size <= 0:
            raise ProductionBackendError("downloaded TikTok video is empty")
        return target

    @staticmethod
    def _parse_s3_locator(value: str) -> tuple[str, str]:
        if not value.startswith("s3://") or "/" not in value[5:]:
            raise ProductionBackendError(f"invalid S3 locator {value!r}")
        bucket, key = value[5:].split("/", 1)
        if not bucket or not key:
            raise ProductionBackendError(f"invalid S3 locator {value!r}")
        return bucket, key

    def _preview_url(self, value: str, *, filename: str) -> str:
        if value.startswith("https://"):
            return value
        bucket, key = self._parse_s3_locator(value)
        suffix = Path(key).suffix
        return self._presign(
            bucket,
            key,
            filename=f"{filename}{suffix}",
            attachment=False,
        )

    def _presign(self, bucket: str, key: str, *, filename: str, attachment: bool) -> str:
        params: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if attachment:
            clean_name = Path(filename).name.replace('"', "")
            params["ResponseContentDisposition"] = f'attachment; filename="{clean_name}"'
        return str(
            self._s3.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=86400,
            )
        )

    def _request_payload(
        self,
        *,
        job: dict[str, Any],
        variation: dict[str, Any],
        index: int,
        total: int,
        master_id: str | None,
    ) -> dict[str, Any]:
        render_job = job["renderJob"]
        stage_data = job["stageData"]
        track = render_job["track"]
        segment = track.get("segment") or {}
        start = segment.get("from")
        end = segment.get("to")
        if self.config.stage1_backend == "local_ctc" and (start is None or end is None):
            raise ProductionBackendError("local_ctc requires an explicit clip window")

        subtitle_name = str(variation.get("subtitle", {}).get("style") or "")
        subtitles_mode = self.config.subtitle_modes.get(subtitle_name)
        if not subtitles_mode:
            raise ProductionBackendError(f"no subtitle mode mapping for {subtitle_name!r}")

        background = variation.get("background") or {}
        groups = list(background.get("groups") or [])
        background_mode = str(background.get("mode") or "footage")
        artist_id = ""
        selector: dict[str, Any] = {}
        custom_sources = background.get("sourceAssets") or []
        if custom_sources:
            artist_id = self.config.default_artist_id
            source_format = str(background.get("sourceFormat") or "")
            render_preset = {"9:16": "vertical", "16:9": "wide"}.get(source_format)
            if render_preset is None:
                raise ProductionBackendError(
                    f"unsupported personal-source geometry {source_format!r}; expected 9:16 or 16:9"
                )
            selector = {"renderPreset": render_preset}
        elif background_mode in {"footage", "photo"}:
            if not groups:
                raise ProductionBackendError("footage/photo variation has no selected group")
            group_name = str(groups[0])
            # Ищем в карте СВОЕГО режима: одинаковые подписи есть и у футажа, и у фото.
            selector = dict(
                self.config.selector_by_mode.get(background_mode, {}).get(group_name) or {}
            )
            artist_id = self.config.footage_artists.get(group_name, "") or self.config.default_artist_id
            if not selector and not artist_id:
                raise ProductionBackendError(f"no footage mapping for {group_name!r}")

        bg_mode = "photo" if background_mode == "photo" else "footage"
        bg_solid_color = ""
        if background_mode == "color":
            bg_mode = "solid"
            # Solid всё равно требует валидный artist_id: Stage 2 планирует футаж,
            # даже когда его не видно (см. SendAudioS3Request.bg_mode). Бот в этом
            # случае подставляет первый ключ из пресетов — делаем то же.
            artist_id = artist_id or self.config.default_artist_id
            color = str(background.get("color") or "").lower()
            color_map = {"#ffffff": "white", "#fff": "white", "#00ff00": "green", "#0f0": "green"}
            bg_solid_color = color_map.get(color, "")
            if not bg_solid_color:
                raise ProductionBackendError(
                    f"orchestrator supports only white/green solid backgrounds, got {color!r}"
                )

        hook = variation.get("hook") or {}
        family = hook.get("family")
        resolved = hook.get("resolved") or {}
        hook_config = hook.get("config") or {}
        f2_shape = None
        f4_device = None
        hook_device = None
        if family == "object":
            script = str(hook.get("family_script") or "")
            for token, value in {
                "elipse": "elipse", "square": "square", "rhomb": "rhomb",
                "star1": "star1", "star2": "star2",
            }.items():
                if token in script:
                    f2_shape = value
                    break
        if family == "motion":
            script = str(hook.get("family_script") or "")
            for token, value in {
                "swipe": "swipe", "tap": "tap", "pinch": "pinch",
                "holdfinger": "holdfinger", "head": "head",
            }.items():
                if token in script:
                    f4_device = value
                    break
        if family == "thought":
            hook_device = {
                "Панчлайн": "punchline",
                "Пропущенное слово": "missing_word",
                "Эхо": "lyric_echo",
                "Вопрос": "question_to_track",
                "Инверсия": "inverse_lyric",
            }.get(str(hook_config.get("thought") or ""))
            if not hook_device:
                raise ProductionBackendError(
                    f"unsupported thought hook {hook_config.get('thought')!r}"
                )
        f1_sound_url = None
        if family in {"sound", "warmup"} and hook_config.get("warmupKind") != "video":
            f1_sound_url = str(hook_config.get("soundUrl") or "").strip()
            if not f1_sound_url:
                raise ProductionBackendError("sound hook requires an uploaded sound URL")

        f6_fields: dict[str, Any] = {}
        if family == "warmup" and hook_config.get("warmupKind") == "video":
            if not hook_config.get("videoUrl") or not hook_config.get("videoDuration") or not hook_config.get("videoWidth") or not hook_config.get("videoHeight"):
                raise ProductionBackendError("Загрузите видео для прогрева заново")
            if start is None or end is None or hook.get("dropTime") is None:
                raise ProductionBackendError("Для прогрева нужны отрывок и тайминг дропа")
            f6_fields = {"f6_video_url": hook_config["videoUrl"], "f6_video_width": hook_config["videoWidth"],
                "f6_video_height": hook_config["videoHeight"], "f6_video_duration": hook_config["videoDuration"],
                "f6_video_has_audio": hook_config.get("videoHasAudio", True)}
        # `sound` is the legacy persisted family. Keep its established payload
        # contract so old drafts/jobs remain renderable. New uploads use
        # `warmup` and carry probe metadata required by the UI contract.
        if f1_sound_url and family == "warmup":
            duration = hook_config.get("soundDuration")
            drop = hook.get("dropTime")
            if not duration or drop is None or start is None or end is None:
                raise ProductionBackendError("Для звукового прогрева загрузите файл и выберите дроп")
            if float(drop) <= float(start) or float(drop) >= float(end):
                raise ProductionBackendError("Дроп должен находиться внутри выбранного отрывка")

        target_fragment = str(render_job.get("lyrics", {}).get("fragment") or "").strip()
        lyrics = str(render_job.get("lyrics", {}).get("full") or "").strip()
        if self.config.stage1_backend == "local_ctc" and not target_fragment:
            raise ProductionBackendError("local_ctc requires an exact target fragment")
        if not target_fragment:
            target_fragment = lyrics
        payload: dict[str, Any] = {
            **f6_fields,
            "audio_s3_url": str(track.get("s3Key") or ""),
            "project_id": str(job.get("projectId") or ""),
            "mode": "with_gemini",
            "render_engine": "ae",
            "idempotency_key": f"web:{job['id']}:{index}",
            "lyrics_text": lyrics,
            "target_fragment": target_fragment,
            "stage1_alignment_backend": self.config.stage1_backend,
            "subtitles_mode": subtitles_mode,
            "footage_artist_id": artist_id,
            "user_clip_start_sec": float(start) if start is not None else None,
            "user_clip_end_sec": float(end) if end is not None else None,
            "hook_enabled": bool(family),
            "user_drop_t": hook.get("dropTime"),
            "f4_device": f4_device,
            "hook_device": hook_device,
            "f1_sound_url": f1_sound_url,
            "effect_hook": resolved.get("hook"),
            "effect_transition": resolved.get("transition"),
            "effect_extra": resolved.get("extra"),
            "f2_shape": f2_shape,
            "subtitle_color_hex": variation.get("subtitle", {}).get("color"),
            "accent_color_hex": stage_data.get("final", {}).get("accentColor"),
            "bg_mode": str(selector.get("bgMode") or bg_mode),
            # Пара rotation — это точное указание группы: когда обе непусты,
            # оркестратор берёт клипы РОВНО из неё вместо выбора по профилю
            # артиста. Без неё выбор бакета на сайте ни на что не влиял.
            "rotation_theme": str(selector.get("rotationTheme") or ""),
            "rotation_tags_group": str(selector.get("rotationTagsGroup") or ""),
            # Геометрия выдачи: у коллекций 16:9 она wide, иначе кадр
            # центр-кропается в треть ширины (см. _render_preset_for_bucket в боте).
            "render_preset": str(selector.get("renderPreset") or "vertical"),
            "bg_solid_color": bg_solid_color,
            "photo_style": background.get("photoStyle"),
            "variant_index": index,
            "variants_total": total,
            "reuse_text_job_id": master_id,
        }
        if custom_sources:
            if start is None or end is None:
                raise ProductionBackendError("Для своих исходников нужен точный отрывок")
            preroll = max(0.0, float(hook_config.get("videoDuration") or 0) - (float(hook.get("dropTime") or 0)-float(start))) if f6_fields else 0.0
            needed = float(end)-float(start)+preroll
            if sum(float(item["duration"]) for item in custom_sources) + 0.001 < needed:
                raise ProductionBackendError(f"Исходников недостаточно: нужно {needed:.1f} с. Добавьте видео.")
            payload["custom_footage_sources"] = [{"url": item["s3Key"], "width": item["width"], "height": item["height"], "duration": item["duration"]} for item in custom_sources]
        return {key: value for key, value in payload.items() if value is not None and value != ""}


_BACKEND: ProductionBackend | None = None


def get_backend() -> ProductionBackend:
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = ProductionBackend(ProductionConfig.load())
    return _BACKEND


def close_backend() -> None:
    global _BACKEND
    if _BACKEND is not None:
        _BACKEND.close()
        _BACKEND = None
