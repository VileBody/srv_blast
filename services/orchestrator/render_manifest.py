# services/orchestrator/render_manifest.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List
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

        if _is_audio_by_meta(layer) or _is_audio_by_ext(fn):
            continue

        remote_url = str(src.get("remote_url") or "").strip()
        file_path = str(src.get("file_path") or "").strip()

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
