# mlcore/hooks/f5_cognition/orchestrator_hook.py
"""
Точка вызова F5 («Мысль») из mlcore.gemini_orchestrator между Stage 2 (футаж)
и Stage 3 (сборка JSON для AE).

Контракт:
    build_f5_block_if_requested(...) -> dict | None

  - Если F5-хук НЕ запрошен (нет env F5_HOOK_DEVICE) — возвращает None.
    Оркестратор просто не добавляет блок "f5" → обычные job'ы не затронуты.
  - Если запрошен — гоняет F5 pipeline (Stage1 текст + Stage2 TTS), грузит
    готовый .wav в S3 и возвращает блок для full_edit_config["f5"], который
    project_builder.build_full_project читает и передаёт в apply_f5().

Вход (из бота, через env):
  F5_HOOK_DEVICE        — одно из 5 устройств (punchline/missing_word/...).
                          Отсутствует → хук выключен.
  F5_HOOK_INJECT_FOCAL_MS — где в РЕНДЕРНОЙ композиции (clip-zero) звучит TTS.
                          Дефолт 0 = с первого кадра ролика (это и есть «крючок»).
  F5_HOOK_SEED          — опц. seed для воспроизводимости (в проде не задаём).
  F5_HOOK_S3_UPLOAD     — "1"/"0"; дефолт = (MODE=prod). В dev можно оставить
                          локальный путь (AE на той же машине).

Два разных focal:
  - АБСОЛЮТНЫЙ (clip_start_abs * 1000) → во F5Request: pipeline читает трек для
    проверки границ + Stage1 понимает, какую строку лирики «зацепить».
  - CLIP-ОТНОСИТЕЛЬНЫЙ (F5_HOOK_INJECT_FOCAL_MS, дефолт 0) → в to_config_block:
    inject.py ставит TTS-слой именно сюда на ленте отрендеренной композиции.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from mlcore.hooks.f5_cognition.models import F5Device, F5Request, LyricsTiming
from mlcore.hooks.f5_cognition.pipeline import generate as f5_generate

logger = logging.getLogger(__name__)


def _env_truthy(name: str, *, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _resolve_device() -> Optional[F5Device]:
    raw = (os.environ.get("F5_HOOK_DEVICE") or "").strip().lower()
    if not raw:
        return None
    try:
        return F5Device(raw)
    except ValueError as e:
        allowed = [d.value for d in F5Device]
        raise RuntimeError(
            f"Invalid F5_HOOK_DEVICE={raw!r}; allowed={allowed}"
        ) from e


def _optional_int_env(name: str) -> Optional[int]:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"Invalid {name}={raw!r}") from e


def _resolve_drop_at_sec(clip_start_abs_sec: float) -> Optional[float]:
    """
    Дроп для F5Request.drop_at_sec — ОТНОСИТЕЛЬНО начала фокуса (см.
    prompts/stage1.py: «дроп на Xс от начала фокуса»).

    Бот кладёт в env USER_DROP_T абсолютный момент дропа внутри трека
    (тот же, что выбирает hook_drop пикер). Переводим в относительный:
        rel = abs - clip_start_abs.
    Возвращаем None, если дроп не задан или попадает в/до начала фокуса
    (тогда подсказка про дроп в промте Stage1 не нужна).
    """
    raw = (os.environ.get("USER_DROP_T") or "").strip()
    if not raw:
        return None
    try:
        abs_sec = float(raw)
    except ValueError as e:
        raise RuntimeError(f"Invalid USER_DROP_T={raw!r}") from e
    rel = abs_sec - float(clip_start_abs_sec)
    return rel if rel > 0.0 else None


# ─────────────────────────────────────────────────────────────────────────────
# S3 upload (env-схема как у overlay-клиента в gemini_postprocess)
# ─────────────────────────────────────────────────────────────────────────────

def _make_s3_client():
    import boto3  # type: ignore
    from botocore.config import Config  # type: ignore

    endpoint = (os.environ.get("S3_ENDPOINT_URL") or "").strip() or None
    access_key = (os.environ.get("S3_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.environ.get("S3_SECRET_ACCESS_KEY") or "").strip()
    region = (os.environ.get("S3_REGION") or "ru-1").strip() or "ru-1"

    if bool(access_key) != bool(secret_key):
        raise RuntimeError(
            "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be both set or both empty"
        )

    kwargs: dict[str, Any] = {
        "service_name": "s3",
        "region_name": region,
        "config": Config(signature_version="s3v4"),
    }
    if endpoint is not None:
        kwargs["endpoint_url"] = endpoint
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client(**kwargs)


def _resolve_bucket() -> str:
    for name in ("F5_HOOK_S3_BUCKET", "S3_BUCKET_JOB_ARTIFACTS", "S3_BUCKET_ASSET_STORAGE"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    raise RuntimeError(
        "No S3 bucket configured for F5 hook "
        "(set F5_HOOK_S3_BUCKET / S3_BUCKET_JOB_ARTIFACTS / S3_BUCKET_ASSET_STORAGE)"
    )


def _upload_wav_to_s3(local_path: str, *, job_tag: str) -> str:
    """Грузит .wav в S3, возвращает s3://bucket/key (схема, которую читает AE-нода)."""
    bucket = _resolve_bucket()
    prefix = (os.environ.get("F5_HOOK_S3_PREFIX") or "f5_hooks").strip().strip("/")
    file_name = Path(local_path).name
    key = f"{prefix}/{job_tag}/{file_name}" if job_tag else f"{prefix}/{file_name}"

    client = _make_s3_client()
    client.upload_file(str(local_path), bucket, key)
    url = f"s3://{bucket}/{key}"
    logger.info("f5.hook uploaded wav -> %s", url)
    return url


# ─────────────────────────────────────────────────────────────────────────────
# Главная точка входа
# ─────────────────────────────────────────────────────────────────────────────

def build_f5_block_if_requested(
    *,
    track_path: str,
    lyrics: str,
    clip_start_abs_sec: float,
    out_dir: Path,
    job_tag: str = "",
    lyrics_timings: Optional[list[dict[str, Any]]] = None,
    is_prod: bool = False,
) -> Optional[dict]:
    """
    Возвращает блок для full_edit_config["f5"] или None (хук не запрошен).
    """
    device = _resolve_device()
    if device is None:
        logger.info("f5.hook not requested (no F5_HOOK_DEVICE) — skipping")
        return None

    if not track_path or not Path(track_path).exists():
        raise RuntimeError(f"F5 hook requested but track_path missing: {track_path!r}")
    if not lyrics or not lyrics.strip():
        raise RuntimeError("F5 hook requested but lyrics are empty")

    abs_focal_ms = max(0, int(round(float(clip_start_abs_sec) * 1000.0)))
    seed = _optional_int_env("F5_HOOK_SEED")

    timings_models: Optional[list[LyricsTiming]] = None
    if lyrics_timings:
        timings_models = [LyricsTiming(**t) for t in lyrics_timings]

    drop_at_sec = _resolve_drop_at_sec(float(clip_start_abs_sec))

    req = F5Request(
        track_path=track_path,
        lyrics=lyrics,
        lyrics_timings=timings_models,
        focal_start_ms=abs_focal_ms,
        device=device,
        drop_at_sec=drop_at_sec,
        seed=seed,
    )

    f5_dir = Path(out_dir) / "f5"
    f5_dir.mkdir(parents=True, exist_ok=True)
    out_wav = f5_dir / f"f5_hook_{device.value}.wav"

    logger.info(
        "f5.hook generate device=%s abs_focal_ms=%d track=%s",
        device.value, abs_focal_ms, Path(track_path).name,
    )
    resp = f5_generate(req, output_path=str(out_wav))

    # Куда лечь TTS на ленте отрендеренной композиции (clip-zero). 0 = с начала ролика.
    inject_focal_ms = _optional_int_env("F5_HOOK_INJECT_FOCAL_MS") or 0

    upload_enabled = _env_truthy("F5_HOOK_S3_UPLOAD", default=is_prod)
    audio_url: Optional[str] = None
    if upload_enabled:
        audio_url = _upload_wav_to_s3(resp.audio_path, job_tag=job_tag)
    elif is_prod:
        # No Fallback Policy: в проде локальный путь не доедет до рендер-ноды.
        raise RuntimeError(
            "F5 hook in MODE=prod requires S3 upload, but F5_HOOK_S3_UPLOAD is disabled"
        )

    block = resp.to_config_block(focal_start_ms=inject_focal_ms, audio_url=audio_url)
    logger.info(
        "f5.hook block ready device=%s tts_text=%r audio_dur_ms=%d inject_focal_ms=%d url=%s",
        device.value, resp.tts_text, resp.audio_duration_ms, inject_focal_ms, audio_url or "<local>",
    )
    return block
