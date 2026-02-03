#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# -----------------------------------------------------------------------------
# Paths (repo root)
# -----------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent
WORK_ROOT = Path(os.environ.get("WORK_DIR", str(REPO_ROOT / "work"))).resolve()
OUT_ROOT = Path(os.environ.get("OUTPUT_DIR", str(REPO_ROOT / "output"))).resolve()

# -----------------------------------------------------------------------------
# .env loader (strict enough, no surprises)
# -----------------------------------------------------------------------------
def load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(dotenv_path=env_path, override=False)
    except Exception:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                os.environ.setdefault(k, v)

# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------
def _require_env(key: str) -> str:
    v = (os.environ.get(key, "") or "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var: {key}")
    return v

def _now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime())

def _guess_content_type(p: Path) -> str:
    ct, _ = mimetypes.guess_type(str(p))
    return ct or "application/octet-stream"

def _sha1_file(p: Path) -> str:
    h = hashlib.sha1()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _run(cmd: str, *, cwd: Path, env: Dict[str, str]) -> None:
    args = shlex.split(cmd)
    proc = subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        out = (proc.stdout or "")[-8000:]
        err = (proc.stderr or "")[-8000:]
        raise RuntimeError(
            f"command_failed rc={proc.returncode}\ncmd={cmd}\n"
            f"--- stdout (tail) ---\n{out}\n"
            f"--- stderr (tail) ---\n{err}\n"
        )

def make_job_dirs(job_id: str) -> Tuple[Path, Path, Path]:
    data_dir = (WORK_ROOT / "jobs" / job_id / "data").resolve()
    out_dir = (OUT_ROOT / "jobs" / job_id / "out").resolve()
    logs_dir = (out_dir / "logs").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, out_dir, logs_dir

# -----------------------------------------------------------------------------
# Optional: upload audio to S3 and get presigned https url (like your .test.py)
# -----------------------------------------------------------------------------
def upload_audio_and_presign(audio_path: Path) -> str:
    try:
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "boto3 is required for --upload-audio.\n"
            "Install: pip install boto3\n"
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

    print(f"[s3] upload -> bucket={bucket} key={key} content_type={content_type}")
    s3.upload_file(
        Filename=str(audio_path),
        Bucket=bucket,
        Key=key,
        ExtraArgs={"ContentType": content_type},
    )

    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=60 * 60,  # 1 hour
    )

    print(f"[s3] presigned_url: {url}")
    return url

# -----------------------------------------------------------------------------
# Core actions
# -----------------------------------------------------------------------------
def run_gemini_only(*, job_id: str, audio_local_path: Path) -> Tuple[Path, Path]:
    """
    Generates configs into:
      work/jobs/<job_id>/data/{full_edit_config.json,footage_config.json}
    Also writes out copies into:
      output/jobs/<job_id>/out/
    """
    data_dir, out_dir, _ = make_job_dirs(job_id)

    env = os.environ.copy()
    env["DATA_DIR"] = str(data_dir)
    env["OUT_DIR"] = str(out_dir)

    # Make Gemini use THIS audio
    env["AUDIO_FILE_PATH"] = str(audio_local_path)
    env["AUDIO_DIR"] = str(audio_local_path.parent)

    cmd = (
        "python run.py "
        f"--skip-ae "
        f"--out-dir {out_dir.as_posix()} "
        f"--full-edit {data_dir.as_posix()}/full_edit_config.json "
        f"--footage {data_dir.as_posix()}/footage_config.json"
    )

    print(f"[gemini] cmd: {cmd}")
    _run(cmd, cwd=REPO_ROOT, env=env)

    return (data_dir / "full_edit_config.json"), (data_dir / "footage_config.json")

def run_builder_only(*, job_id: str, full_edit: Path, footage: Path, audio_local_path: Optional[Path]) -> Tuple[Path, Path]:
    """
    Builds AE payload+JSX into output/jobs/<job_id>/out/ using existing configs.
    """
    data_dir, out_dir, _ = make_job_dirs(job_id)

    # Normalize: put configs exactly where run.py expects (job-scoped)
    full_edit_dst = data_dir / "full_edit_config.json"
    footage_dst = data_dir / "footage_config.json"
    full_edit_dst.write_text(full_edit.read_text(encoding="utf-8"), encoding="utf-8")
    footage_dst.write_text(footage.read_text(encoding="utf-8"), encoding="utf-8")

    env = os.environ.copy()
    env["DATA_DIR"] = str(data_dir)
    env["OUT_DIR"] = str(out_dir)

    if audio_local_path is not None:
        env["AUDIO_FILE_PATH"] = str(audio_local_path)
        env["AUDIO_DIR"] = str(audio_local_path.parent)

    cmd = (
        "python run.py "
        f"--skip-llm "
        f"--out-dir {out_dir.as_posix()} "
        f"--full-edit {full_edit_dst.as_posix()} "
        f"--footage {footage_dst.as_posix()}"
    )

    print(f"[builder] cmd: {cmd}")
    _run(cmd, cwd=REPO_ROOT, env=env)

    # Outputs created by project_builder:
    render_payload = out_dir / "final_render_instructions_full.json"
    render_jsx = out_dir / "render_full.jsx"
    return render_payload, render_jsx

def dispatch_to_windows_sync(*, job_id: str, audio_url: str, windows_timeout_s: float) -> Dict[str, Any]:
    """
    Dispatch render to Windows node using your existing client+manifest logic.
    This is the same idea as orchestrator.dispatch_to_windows, but sync and local.
    """
    from services.orchestrator.render_manifest import build_windows_job_payload
    from services.orchestrator.windows_client import WindowsRenderClient

    data_dir, out_dir, _ = make_job_dirs(job_id)

    windows_url = (os.environ.get("WINDOWS_RENDER_URL", "") or "").strip()
    if not windows_url:
        raise RuntimeError("WINDOWS_RENDER_URL is not set")

    payload = build_windows_job_payload(
        job_id=job_id,
        render_jsx_path=out_dir / "render_full.jsx",
        render_payload_path=out_dir / "final_render_instructions_full.json",
        audio_url=audio_url,
        entry_comp="Main Render",
        output_relpath="work/output.mp4",
        output_s3_bucket=os.environ.get("S3_BUCKET_OUTPUT_VIDEO", ""),
        output_s3_key=f"renders/{job_id}/output.mp4",
    )

    print(f"[win] dispatch -> {windows_url} timeout_s={windows_timeout_s}")
    client = WindowsRenderClient(windows_url, timeout_s=float(windows_timeout_s))
    res = client.dispatch_render(payload)
    return res

# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def main() -> int:
    load_env()

    ap = argparse.ArgumentParser("dev.py — local debug runner (no celery)")
    ap.add_argument("--mode", required=True, choices=["builder", "gemini", "full", "dispatch"], help="What to run")
    ap.add_argument("--audio", required=True, help="Path to local audio file")
    ap.add_argument("--job-id", default="", help="Optional job id (otherwise generated)")
    ap.add_argument("--full-edit", default="", help="Path to existing full_edit_config.json (for --mode builder)")
    ap.add_argument("--footage", default="", help="Path to existing footage_config.json (for --mode builder)")
    ap.add_argument("--upload-audio", action="store_true", help="Upload audio to S3 and use presigned https url for Windows node")
    ap.add_argument("--windows-timeout-s", type=float, default=300.0, help="Timeout for Windows render POST (default 300s)")
    args = ap.parse_args()

    job_id = args.job_id.strip() or uuid.uuid4().hex
    audio_local = Path(args.audio).expanduser().resolve()
    if not audio_local.exists():
        raise RuntimeError(f"audio not found: {audio_local}")

    print(f"[job] id={job_id}")
    print(f"[audio] local={audio_local}")

    audio_url = ""
    if args.upload_audio:
        audio_url = upload_audio_and_presign(audio_local)
    else:
        # for builder/gemini it can stay local; for Windows dispatch you usually want https/s3
        audio_url = (os.environ.get("AUDIO_REMOTE_URL", "") or "").strip()

    # 1) gemini-only
    if args.mode == "gemini":
        fe, fc = run_gemini_only(job_id=job_id, audio_local_path=audio_local)
        print("[ok] gemini generated:")
        print(f"  - {fe}")
        print(f"  - {fc}")
        print(f"  - out: {OUT_ROOT / 'jobs' / job_id / 'out'}")
        return 0

    # 2) builder-only
    if args.mode == "builder":
        if not args.full_edit or not args.footage:
            raise RuntimeError("--mode builder requires --full-edit and --footage")
        fe = Path(args.full_edit).expanduser().resolve()
        fc = Path(args.footage).expanduser().resolve()
        if not fe.exists() or not fc.exists():
            raise RuntimeError("full_edit/footage config file not found")

        payload, jsx = run_builder_only(job_id=job_id, full_edit=fe, footage=fc, audio_local_path=audio_local)
        print("[ok] built:")
        print(f"  - payload: {payload}")
        print(f"  - jsx:     {jsx}")
        return 0

    # 3) dispatch-only (assumes out/render_* already exist for job_id)
    if args.mode == "dispatch":
        if not audio_url:
            raise RuntimeError("dispatch needs audio URL. Use --upload-audio or set AUDIO_REMOTE_URL in env")
        res = dispatch_to_windows_sync(job_id=job_id, audio_url=audio_url, windows_timeout_s=args.windows_timeout_s)
        print("[ok] windows response:")
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    # 4) full: gemini -> build -> dispatch
    if args.mode == "full":
        # step A: gemini generate
        run_gemini_only(job_id=job_id, audio_local_path=audio_local)

        # step B: build from generated configs
        data_dir, _, _ = make_job_dirs(job_id)
        payload, jsx = run_builder_only(
            job_id=job_id,
            full_edit=data_dir / "full_edit_config.json",
            footage=data_dir / "footage_config.json",
            audio_local_path=audio_local,
        )
        print("[ok] built:")
        print(f"  - payload: {payload}")
        print(f"  - jsx:     {jsx}")

        # step C: dispatch (needs url)
        if not audio_url:
            raise RuntimeError("full mode needs audio URL for Windows. Use --upload-audio or set AUDIO_REMOTE_URL")
        res = dispatch_to_windows_sync(job_id=job_id, audio_url=audio_url, windows_timeout_s=args.windows_timeout_s)
        print("[ok] windows response:")
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    raise RuntimeError("unreachable")

if __name__ == "__main__":
    raise SystemExit(main())
