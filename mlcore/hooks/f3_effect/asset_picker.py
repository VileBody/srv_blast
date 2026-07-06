"""S3-based asset picker for F3 «Эффект» overlays.

Reads the F3 manifest, resolves the chosen effects' sound files (and the brand
logo for hooks with branding) into concrete S3 URLs, and returns both:

  - "assets" — relpath strings the AE overlay reads (under __APP_DIR/media/...);
  - "media"  — [{url, relpath}, ...] download list the render node grabs
               alongside the regular footage media[].

Determinism:
  - One file is picked per pool via random.Random(seed) so re-running the same
    job (same JOB_ID + variant) lands the same SFX. seed is derived from
    STAGE2_SELECTION_SEED / JOB_ID by the orchestrator.

No env (FX_ASSETS_S3_BUCKET unset) => empty assets/media. The overlay builder
just skips each slot in that case — the visual still works, just silent. This
keeps F3 a pure visual upgrade until the asset catalog is uploaded.

S3 layout (mirror of Кирилл's upload instructions):

    s3://<FX_ASSETS_S3_BUCKET>/<FX_ASSETS_S3_PREFIX>/
      sounds/
        camera_flash/        <- pool: shutter / slow shutter
        glitch/              <- pool: transitions + extras
        subdrop/             <- reserve (not used by pipeline)
        signature/           <- reserve
        car/                 <- reserve
        light_sound/
          myinstants.mp3     <- single file for hook_light (impact_at=0.5)
      logo/
        group_1245.png       <- brand stamp

Manifest pool/file paths are S3-keys relative to FX_ASSETS_S3_PREFIX.
"""

from __future__ import annotations

import json
import logging
import os
import random
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LOGGER = logging.getLogger(__name__)

_F3_DIR = Path(__file__).resolve().parent
_MANIFEST_PATH = _F3_DIR / "manifest.json"
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff", ".ogg"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _asset_root() -> Tuple[str, str]:
    """Return (bucket, prefix). Prefix stripped of leading/trailing '/'."""
    bucket = (os.environ.get("FX_ASSETS_S3_BUCKET") or "").strip()
    prefix = (os.environ.get("FX_ASSETS_S3_PREFIX") or "").strip().strip("/")
    return bucket, prefix


def _load_manifest() -> Dict[str, Any]:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def _make_s3_client():
    """Boto3 client matching the rest of the repo's S3 wiring (S3_ENDPOINT_URL
    for MinIO, sigv4, optional explicit credentials)."""
    import boto3  # type: ignore
    from botocore.config import Config  # type: ignore

    endpoint = (os.environ.get("S3_ENDPOINT_URL") or "").strip() or None
    access_key = (os.environ.get("S3_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.environ.get("S3_SECRET_ACCESS_KEY") or "").strip()
    region = (os.environ.get("S3_REGION") or "ru-1").strip() or "ru-1"

    kwargs: Dict[str, Any] = {
        "service_name": "s3",
        "region_name": region,
        # proxies={}: S3 напрямую, мимо зарубежного OUTBOUND-прокси (до Timeweb 502).
        "config": Config(signature_version="s3v4", proxies={}),
    }
    if endpoint is not None:
        kwargs["endpoint_url"] = endpoint
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client(**kwargs)


# Process-local cache: one list_objects per pool prefix. Picker calls _list_pool
# multiple times per job for the same pool (e.g. transition + extra both use
# "glitch"). Cache key includes bucket/prefix so tests that change env get a
# fresh listing via reset_cache().
@lru_cache(maxsize=128)
def _list_pool_cached(bucket: str, root_prefix: str, pool_key: str) -> Tuple[Tuple[str, str], ...]:
    if not bucket:
        return tuple()
    base = (root_prefix + "/" + pool_key.strip("/")).strip("/") + "/"
    client = _make_s3_client()
    out: List[Tuple[str, str]] = []
    cont: Optional[str] = None
    while True:
        kw: Dict[str, Any] = {"Bucket": bucket, "Prefix": base, "MaxKeys": 1000}
        if cont:
            kw["ContinuationToken"] = cont
        resp = client.list_objects_v2(**kw)
        for it in resp.get("Contents") or []:
            key = str(it.get("Key") or "")
            if not key or key.endswith("/"):
                continue
            file_name = key.rsplit("/", 1)[-1]
            ext = Path(file_name).suffix.lower()
            if ext not in _AUDIO_EXTS and ext not in _IMAGE_EXTS:
                continue
            out.append((key, file_name))
        if not resp.get("IsTruncated"):
            break
        cont = resp.get("NextContinuationToken")
    out.sort()
    return tuple(out)


def reset_cache() -> None:
    """Drop the list_objects cache (tests / asset re-uploads)."""
    _list_pool_cached.cache_clear()


def _list_pool(pool_key: str) -> Tuple[Tuple[str, str], ...]:
    bucket, root_prefix = _asset_root()
    return _list_pool_cached(bucket, root_prefix, pool_key)


def _s3_url(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


def _slot_relpath(file_name: str) -> str:
    ext = Path(file_name).suffix.lower()
    sub = "audio" if ext in _AUDIO_EXTS else "img"
    return f"media/{sub}/{file_name}"


def _pick_pool(pool_key: str, *, seed: str) -> Optional[Dict[str, str]]:
    """Deterministically pick one file from a pool prefix."""
    bucket, _ = _asset_root()
    if not bucket:
        return None
    pool = _list_pool(pool_key)
    if not pool:
        LOGGER.warning("f3.asset_picker pool empty pool=%s", pool_key)
        return None
    rnd = random.Random(seed)
    key, file_name = rnd.choice(pool)
    return {
        "s3_url": _s3_url(bucket, key),
        "file_name": file_name,
        "relpath": _slot_relpath(file_name),
    }


def _pick_file(rel_key: str) -> Optional[Dict[str, str]]:
    """Resolve a specific S3 key (no listing). For singletons like
    sounds/light_sound/myinstants.mp3 or logo/group_1245.png. Returns None if
    the env is not configured or the key is malformed."""
    bucket, root_prefix = _asset_root()
    if not bucket:
        return None
    cleaned = rel_key.strip().strip("/")
    if not cleaned:
        return None
    full_key = (root_prefix + "/" + cleaned).strip("/")
    file_name = full_key.rsplit("/", 1)[-1]
    ext = Path(file_name).suffix.lower()
    if not file_name or (ext not in _AUDIO_EXTS and ext not in _IMAGE_EXTS):
        return None
    return {
        "s3_url": _s3_url(bucket, full_key),
        "file_name": file_name,
        "relpath": _slot_relpath(file_name),
    }


def resolve_assets(
    *,
    hook: Optional[str],
    transition: Optional[str],
    extra: Optional[str],
    seed: str,
) -> Dict[str, Any]:
    """Resolve f3 sound/logo assets for the chosen hook/transition/extra ids.

    Returns:
      {
        "assets": {"hook_sound"?, "transition_sound"?, "extra_sound"?, "logo"?}
                   keyed by overlay slot -> media-relpath inside __APP_DIR,
        "media":  [{"url": "s3://...", "relpath": "media/audio/..."}, ...]
                   download list for the render node.
      }

    No FX_ASSETS_S3_BUCKET => returns empty dicts (overlay still builds visual).
    Manifest pool/file resolution failures are logged and skipped (degraded but
    non-fatal: visual works, that slot is silent).
    """
    bucket, _ = _asset_root()
    if not bucket:
        return {"assets": {}, "media": []}

    manifest = _load_manifest()
    effects = {
        str(e.get("id")): e
        for e in (manifest.get("effects") or [])
        if isinstance(e, dict) and e.get("id")
    }
    pools = (manifest.get("sounds") or {}).get("pools") or {}
    branding = manifest.get("branding") or {}
    logo_key = str(branding.get("logo_default") or "").strip()

    assets: Dict[str, str] = {}
    media: List[Dict[str, str]] = []
    seen_relpath: set[str] = set()

    def _add(slot: str, picked: Optional[Dict[str, str]]) -> None:
        if not picked:
            return
        rel = picked["relpath"]
        assets[slot] = rel
        if rel in seen_relpath:
            return
        seen_relpath.add(rel)
        media.append({"url": picked["s3_url"], "relpath": rel})

    def _resolve_sound(slot: str, eff_id: Optional[str], seed_suffix: str) -> None:
        if not eff_id:
            return
        eff = effects.get(eff_id)
        if not eff:
            return
        snd = eff.get("sound") or {}
        # Singleton file (e.g. hook_light's myinstants.mp3) wins over pool.
        single = str(snd.get("file") or "").strip()
        if single:
            _add(slot, _pick_file(single))
            return
        pool_name = str(snd.get("pool") or "").strip()
        if not pool_name:
            return
        pool_key = pools.get(pool_name)
        if not pool_key:
            LOGGER.warning("f3.asset_picker unknown pool name=%s (slot=%s)", pool_name, slot)
            return
        _add(slot, _pick_pool(str(pool_key), seed=f"{seed}:{seed_suffix}"))

    _resolve_sound("hook_sound", hook, "hook")
    _resolve_sound("transition_sound", transition, "trans")
    _resolve_sound("extra_sound", extra, "extra")

    # Logo: only when the chosen hook needs a stamp (branding=true/built_in).
    if hook:
        eff = effects.get(hook)
        if eff and eff.get("branding") in (True, "built_in") and logo_key:
            _add("logo", _pick_file(logo_key))

    return {"assets": assets, "media": media}
