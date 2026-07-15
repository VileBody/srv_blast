# services/orchestrator/render_manifest.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List
from urllib.parse import unquote

_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


def _read_json(p: Path) -> Dict[str, Any]:
    obj = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"JSON root must be object: {p}")
    return obj


def _is_remote(u: str) -> bool:
    s = (u or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("s3://")


def _is_audio_by_meta(layer: Dict[str, Any]) -> bool:
    td = layer.get("text_data") if isinstance(layer.get("text_data"), dict) else {}
    meta = td.get("layer_meta") if isinstance(td.get("layer_meta"), dict) else {}
    return bool(meta.get("audioEnabled")) is True


def _is_audio_by_ext(file_name: str) -> bool:
    return Path(file_name).suffix.lower() in _AUDIO_EXTS


def _expected_audio_name_from_payload(footage_layers: List[Dict[str, Any]]) -> str:
    for layer in footage_layers:
        if not isinstance(layer, dict):
            continue
        td = layer.get("text_data") if isinstance(layer.get("text_data"), dict) else {}
        src = td.get("source_footage") if isinstance(td.get("source_footage"), dict) else {}
        fn = str(src.get("file_name") or "").strip()
        if not fn:
            continue
        if _is_audio_by_meta(layer) or _is_audio_by_ext(fn):
            return fn
    return ""


def collect_media_urls_from_render_payload(
    render_payload_path: Path,
    *,
    audio_url: str,
) -> List[Dict[str, str]]:
    d = _read_json(render_payload_path)

    footage_layers = d.get("footage_layers")
    if not isinstance(footage_layers, list):
        raise ValueError("render payload missing footage_layers[]")

    out: List[Dict[str, str]] = []
    seen: set[str] = set()

    aurl = (audio_url or "").strip()
    if aurl:
        if not _is_remote(aurl):
            raise RuntimeError(f"audio_url must be remote (http/https/s3). got={aurl!r}")
        # Use the same name that JSX expects in source_footage.file_name.
        audio_name = _expected_audio_name_from_payload(footage_layers)
        if not audio_name:
            raw_name = (aurl.split("?")[0].rstrip("/").split("/")[-1] or "audio").strip()
            audio_name = (unquote(raw_name) or raw_name).strip()
        rel_audio = f"media/audio/{audio_name}"
        out.append({"url": aurl, "relpath": rel_audio})
        seen.add(rel_audio)

    for layer in footage_layers:
        if not isinstance(layer, dict):
            continue

        td = layer.get("text_data") if isinstance(layer.get("text_data"), dict) else {}
        src = td.get("source_footage") if isinstance(td.get("source_footage"), dict) else None
        if not src:
            continue

        fn = str(src.get("file_name") or "").strip()
        if not fn:
            continue

        remote_url = str(src.get("remote_url") or "").strip()
        file_path = str(src.get("file_path") or "").strip()

        if _is_audio_by_meta(layer) or _is_audio_by_ext(fn):
            # The main track is fetched separately via `audio_url` (above) and
            # its layer carries no remote source — skip it here. Any *extra*
            # audio layer that does carry a remote source (e.g. the F5 «Мысль»
            # TTS wav injected by mlcore.hooks.f5_cognition) must be downloaded
            # into media/audio/<file_name> so AE can resolve it.
            audio_src = (
                remote_url if _is_remote(remote_url)
                else file_path if _is_remote(file_path)
                else ""
            )
            if not audio_src:
                continue
            rel = f"media/audio/{fn}"
            if rel in seen:
                continue
            seen.add(rel)
            out.append({"url": audio_src, "relpath": rel})
            continue

        url = ""
        if _is_remote(remote_url):
            url = remote_url
        elif _is_remote(file_path):
            url = file_path
        else:
            raise RuntimeError(
                "Footage has no remote url (s3/http). "
                f"file_name={fn!r} "
                f"remote_url={remote_url!r} "
                f"file_path={file_path!r}"
            )

        rel = f"media/video/{fn}"
        if rel in seen:
            continue
        seen.add(rel)
        out.append({"url": url, "relpath": rel})

    # F3 «Эффект» sound/logo assets: resolved by build-worker asset_picker into
    # full_edit_config["f3"]._media, copied into payload["f3_media"] by
    # project_builder._extract_f3_media. The render node downloads them into
    # __APP_DIR/media/... where overlay.py expects them.
    for it in d.get("f3_media") or []:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or "").strip()
        rel = str(it.get("relpath") or "").strip().strip("/")
        if not url or not rel or not _is_remote(url):
            continue
        if rel in seen:
            continue
        seen.add(rel)
        out.append({"url": url, "relpath": rel})

    return out


def build_windows_job_payload(
    *,
    job_id: str,
    render_jsx_path: Path,
    render_payload_path: Path,
    audio_url: str,
    entry_comp: str = "Main Render",
    output_relpath: str = "work/output.mp4",
    output_s3_bucket: str = "",
    output_s3_key: str = "",
) -> Dict[str, Any]:
    jsx = render_jsx_path.read_text(encoding="utf-8")
    media = collect_media_urls_from_render_payload(render_payload_path, audio_url=audio_url)

    payload: Dict[str, Any] = {
        "job_id": str(job_id),
        "render_jsx": jsx,
        "media": media,
        "entry_comp": str(entry_comp),
        "output_relpath": str(output_relpath),
    }
    if output_s3_bucket:
        payload["output_s3_bucket"] = str(output_s3_bucket)
    if output_s3_key:
        payload["output_s3_key"] = str(output_s3_key)
    return payload


def _s3_bucket_key(url: str) -> tuple[str, str]:
    raw = str(url or "").strip()
    if not raw.startswith("s3://"):
        raise ValueError(f"expected s3 url, got {url!r}")
    tail = raw[5:]
    if "/" not in tail:
        raise ValueError(f"invalid s3 url, missing key: {url!r}")
    bucket, key = tail.split("/", 1)
    if not bucket or not key:
        raise ValueError(f"invalid s3 url: {url!r}")
    return bucket, key


def build_rust_gen_job_payload(
    *,
    job_id: str,
    render_payload_path: Path,
    audio_url: str,
    output_s3_bucket: str,
    presign_ttl_s: int = 7200,
    presign_download: Callable[[str, str, int], str] | None = None,
    presign_upload: Callable[[str, str, int], str] | None = None,
) -> Dict[str, Any]:
    """Turn the bot's canonical render payload into a render-manager request."""
    if not str(output_s3_bucket or "").strip():
        raise RuntimeError("S3_BUCKET_OUTPUT_VIDEO is required for rust-gen dispatch")

    if presign_download is None or presign_upload is None:
        from src.storage.s3 import generate_presigned_upload_url, generate_presigned_url

        presign_download = presign_download or generate_presigned_url
        presign_upload = presign_upload or generate_presigned_upload_url

    native_payload = _read_json(render_payload_path)
    assets: List[Dict[str, Any]] = []
    for media in collect_media_urls_from_render_payload(render_payload_path, audio_url=audio_url):
        source_url = str(media["url"])
        if source_url.startswith("s3://"):
            bucket, key = _s3_bucket_key(source_url)
            source_url = presign_download(bucket, key, int(presign_ttl_s))
        assets.append(
            {
                "role": "audio" if str(media["relpath"]).startswith("media/audio/") else "overlay",
                "url": source_url,
                "destination": str(media["relpath"]),
            }
        )

    job_key = f"renders/{str(job_id).strip()}"
    base_key = f"{job_key}/rust-gen"
    uploads: Dict[str, Dict[str, str]] = {}
    for name, filename in (
        ("video", "output.mp4"),
        ("manifest", "output-manifest.json"),
        ("response", "render-response.json"),
        ("logs", "render.log"),
    ):
        key = f"{job_key}/{filename}" if name == "video" else f"{base_key}/{filename}"
        uploads[name] = {
            "url": presign_upload(str(output_s3_bucket), key, int(presign_ttl_s)),
            "artifact_ref": f"s3://{output_s3_bucket}/{key}",
        }

    return {
        "schema": "ae-native-renderer.manager-request.v1",
        "job_id": str(job_id),
        "input": {"kind": "bot_payload", "inline": native_payload},
        "assets": assets,
        "uploads": uploads,
    }
