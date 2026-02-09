#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]


_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".mov", ".mp4"}


def _load_dotenv_fallback(env_file: Path) -> None:
    """
    Minimal .env loader (no dependencies).
    - Supports KEY=VALUE
    - Ignores comments and empty lines
    - Doesn't override existing env vars
    """
    if not env_file.exists() or not env_file.is_file():
        return

    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            os.environ.setdefault(k, v)


def load_env() -> Optional[Path]:
    """
    Load env vars from .env.
    Priority:
      1) ENV_PATH env var (absolute or relative)
      2) repo root .env
    Uses python-dotenv if available; otherwise fallback parser.
    """
    raw = (os.environ.get("ENV_PATH") or "").strip()
    env_path = Path(raw).expanduser() if raw else (ROOT / ".env")

    if not env_path.is_absolute():
        env_path = (ROOT / env_path).resolve()

    if not env_path.exists():
        return None

    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(dotenv_path=env_path, override=False)
    except Exception:
        _load_dotenv_fallback(env_path)

    return env_path


def _require_env(key: str) -> str:
    v = (os.environ.get(key, "") or "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var: {key}")
    return v


def _sha1_file(p: Path) -> str:
    h = hashlib.sha1()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _guess_content_type(p: Path) -> str:
    ct, _ = mimetypes.guess_type(str(p))
    return ct or "application/octet-stream"


def _now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime())


def _http_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None, timeout_s: float = 30.0) -> Dict[str, Any]:
    import urllib.request

    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, headers=headers, method=method.upper())
    with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        if not raw:
            return {}
        out = json.loads(raw)
        if not isinstance(out, dict):
            raise RuntimeError(f"Expected JSON object from {url}, got: {out!r}")
        return out


def upload_audio_and_presign(audio_path: Path) -> str:
    """
    Upload local file to S3_BUCKET_RAW_AUDIO and return presigned HTTPS URL.

    Required env:
      S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_BUCKET_RAW_AUDIO
    Optional:
      S3_REGION (default ru-1)
    """
    try:
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "boto3 is required to upload audio.\n"
            "Install locally: pip install boto3\n"
            f"Import error: {e!r}"
        ) from e

    endpoint_url = _require_env("S3_ENDPOINT_URL")
    access_key = _require_env("S3_ACCESS_KEY_ID")
    secret_key = _require_env("S3_SECRET_ACCESS_KEY")
    bucket = _require_env("S3_BUCKET_RAW_AUDIO")
    region = (os.environ.get("S3_REGION", "ru-1") or "ru-1").strip()

    sha1 = _sha1_file(audio_path)[:12]
    ext = audio_path.suffix.lower() or ".bin"
    key = f"raw_audio/{_now_ts()}_{sha1}_{audio_path.stem}{ext}"
    content_type = _guess_content_type(audio_path)

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4"),
    )

    s3.upload_file(
        Filename=str(audio_path),
        Bucket=bucket,
        Key=key,
        ExtraArgs={"ContentType": content_type},
    )

    return str(
        s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=60 * 60,  # 1 hour
        )
    )


def _list_audio_files(audio_dir: Path) -> List[Path]:
    if not audio_dir.exists():
        return []
    files = [p for p in sorted(audio_dir.iterdir()) if p.is_file() and p.suffix.lower() in _AUDIO_EXTS]
    return files


def _normalize_files(files: Iterable[str]) -> List[Path]:
    out: List[Path] = []
    for raw in files:
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        else:
            p = p.resolve()
        if not p.exists() or not p.is_file():
            raise RuntimeError(f"--file not found: {p}")
        if p.suffix.lower() not in _AUDIO_EXTS:
            raise RuntimeError(f"--file unsupported extension: {p}")
        out.append(p)
    # stable order + dedupe
    uniq = sorted({str(p): p for p in out}.values(), key=lambda x: str(x))
    return uniq


def enqueue_job(*, orch_base_url: str, audio_url: str, job_mode: str, timeout_s: float) -> Dict[str, Any]:
    payload = {
        "audio_s3_url": audio_url,
        "mode": job_mode,  # with_gemini | no_gemini
        "idempotency_key": None,
        "project_id": None,
    }
    url = f"{orch_base_url.rstrip('/')}/send_audio_s3"
    return _http_json("POST", url, payload=payload, timeout_s=timeout_s)


def poll_job(
    *,
    orch_base_url: str,
    job_id: str,
    poll_interval_s: float,
    poll_timeout_s: float,
) -> Dict[str, Any]:
    job_id = str(job_id).strip()
    if not job_id:
        raise RuntimeError("poll_job: empty job_id")

    url = f"{orch_base_url.rstrip('/')}/jobs/{job_id}"
    t0 = time.time()

    while True:
        st = _http_json("GET", url, payload=None, timeout_s=20.0)
        status = str(st.get("status") or "").strip().upper()
        if status in {"SUCCEEDED", "FAILED"}:
            return st

        if (time.time() - t0) > float(poll_timeout_s):
            raise RuntimeError(f"poll timeout job_id={job_id} after {poll_timeout_s}s")

        time.sleep(float(poll_interval_s))


def _one_file(
    audio_path: Path,
    *,
    orch_base_url: str,
    job_mode: str,
    http_timeout_s: float,
    dry_run: bool,
) -> Tuple[str, Dict[str, Any]]:
    audio_url = upload_audio_and_presign(audio_path)
    if dry_run:
        return str(audio_path), {"dry_run": True, "audio_url": audio_url}
    res = enqueue_job(orch_base_url=orch_base_url, audio_url=audio_url, job_mode=job_mode, timeout_s=http_timeout_s)
    return str(audio_path), {"audio_url": audio_url, "enqueue": res}


def main() -> int:
    load_env()

    ap = argparse.ArgumentParser("enqueue_audio_batch.py — upload local audio files to S3 and enqueue jobs")
    ap.add_argument("--orch", default=os.environ.get("ORCHESTRATOR_PUBLIC_URL", "http://localhost:8000"), help="Orchestrator public URL")
    ap.add_argument("--audio-dir", default=str(ROOT / "audio"), help="Directory with audio files to enqueue")
    ap.add_argument(
        "--file",
        action="append",
        default=[],
        help="Audio file path to enqueue (repeatable). If provided, ignores --audio-dir.",
    )
    ap.add_argument("--job-mode", choices=["with_gemini", "no_gemini"], default="with_gemini", help="Pipeline mode for jobs")
    ap.add_argument("--concurrency", type=int, default=5, help="How many files to upload/enqueue in parallel")
    ap.add_argument("--timeout-s", type=float, default=30.0, help="HTTP timeout for orchestrator enqueue")
    ap.add_argument("--poll", action="store_true", help="Poll /jobs/{id} until SUCCEEDED/FAILED")
    ap.add_argument("--poll-interval-s", type=float, default=2.0, help="Polling interval for --poll")
    ap.add_argument("--poll-timeout-s", type=float, default=3600.0, help="Polling timeout for --poll")
    ap.add_argument("--dry-run", action="store_true", help="Upload + presign only, do not enqueue")
    args = ap.parse_args()

    audio_dir = Path(args.audio_dir).expanduser().resolve()
    if args.file:
        files = _normalize_files(args.file)
        if not files:
            raise SystemExit("[ERR] no --file values provided")
    else:
        files = _list_audio_files(audio_dir)
        if not files:
            raise SystemExit(f"[ERR] no audio files in: {audio_dir}")

    # strict: prod pipeline requires remote URL; this script always uploads to S3
    # so we must have S3 vars configured.
    for k in ("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET_RAW_AUDIO"):
        _require_env(k)

    orch = str(args.orch).rstrip("/")
    conc = int(args.concurrency)
    if conc <= 0:
        raise SystemExit("[ERR] --concurrency must be > 0")

    print(f"[orch] base={orch}")
    if args.file:
        print(f"[audio] files={len(files)} (via --file)")
    else:
        print(f"[audio] dir={audio_dir} files={len(files)}")
    print(
        f"[job] mode={args.job_mode} concurrency={conc} dry_run={('yes' if args.dry_run else 'no')} "
        f"poll={('yes' if args.poll else 'no')}"
    )

    ok = 0
    failed = 0
    results: List[Tuple[str, Dict[str, Any]]] = []

    with ThreadPoolExecutor(max_workers=conc) as ex:
        futs = [
            ex.submit(
                _one_file,
                p,
                orch_base_url=orch,
                job_mode=args.job_mode,
                http_timeout_s=float(args.timeout_s),
                dry_run=bool(args.dry_run),
            )
            for p in files
        ]
        for fut in as_completed(futs):
            try:
                item = fut.result()
                results.append(item)
                ok += 1
            except Exception as e:
                failed += 1
                print(f"[ERR] {e!r}")

    # stable output (sorted by path)
    results.sort(key=lambda x: x[0])

    print("\n=== RESULTS ===")
    job_ids: List[str] = []
    for path_s, info in results:
        if info.get("dry_run"):
            print(f"- {path_s}")
            print(f"  audio_url={info.get('audio_url')}")
            continue

        enqueue = info.get("enqueue") if isinstance(info.get("enqueue"), dict) else {}
        job_id = str(enqueue.get("job_id") or "")
        if job_id:
            job_ids.append(job_id)
        print(f"- {path_s}")
        print(f"  audio_url={str(info.get('audio_url'))}")
        print(f"  job_id={job_id}")
        if job_id:
            print(f"  status_url={orch}/jobs/{job_id}")

    if args.poll and job_ids:
        print("\n=== POLL ===")

        def _poll_one(jid: str) -> Tuple[str, Dict[str, Any]]:
            st = poll_job(
                orch_base_url=orch,
                job_id=jid,
                poll_interval_s=float(args.poll_interval_s),
                poll_timeout_s=float(args.poll_timeout_s),
            )
            return jid, st

        finals: List[Tuple[str, Dict[str, Any]]] = []
        with ThreadPoolExecutor(max_workers=min(len(job_ids), max(1, conc))) as ex:
            futs2 = [ex.submit(_poll_one, jid) for jid in job_ids]
            for fut in as_completed(futs2):
                try:
                    finals.append(fut.result())
                except Exception as e:
                    failed += 1
                    print(f"[POLL_ERR] {e!r}")

        finals.sort(key=lambda x: x[0])
        print("\n=== FINAL STATES ===")
        for jid, st in finals:
            status = str(st.get("status") or "")
            stage = st.get("stage")
            err = st.get("error")
            print(f"- job_id={jid} status={status} stage={stage} err={('yes' if err else 'no')}")
            if err:
                # don't spam the full traceback
                err_s = str(err)
                tail = err_s[-800:] if len(err_s) > 800 else err_s
                print(f"  error_tail={tail}")
            res = st.get("result") if isinstance(st.get("result"), dict) else None
            if res:
                out_url = res.get("output_url") or (res.get("windows") or {}).get("output_url")
                if out_url:
                    print(f"  output_url={out_url}")

    if failed:
        print(f"\n[done] ok={ok} failed={failed}")
        return 2
    print(f"\n[done] ok={ok} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
