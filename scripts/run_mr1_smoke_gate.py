#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".mov", ".mp4"}


def _load_dotenv_fallback(env_file: Path) -> None:
    if not env_file.exists() or not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _load_env() -> Optional[Path]:
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
    value = (os.environ.get(key) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {key}")
    return value


def _require_audio_file(path_s: str, *, arg_name: str) -> Path:
    p = Path(path_s).expanduser()
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    else:
        p = p.resolve()
    if not p.exists() or not p.is_file():
        raise RuntimeError(f"{arg_name} not found: {p}")
    if p.suffix.lower() not in _AUDIO_EXTS:
        raise RuntimeError(f"{arg_name} has unsupported extension: {p}")
    return p


def _http_json(method: str, url: str, *, payload: Optional[Dict[str, Any]] = None, timeout_s: float = 30.0) -> Dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {url}: {body[:1000]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error {url}: {e}") from e

    if not raw:
        return {}
    try:
        out = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Non-JSON response from {url}: {raw[:400]}") from e
    if not isinstance(out, dict):
        raise RuntimeError(f"Expected JSON object from {url}, got: {type(out).__name__}")
    return out


def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _guess_content_type(path: Path) -> str:
    content_type, _ = mimetypes.guess_type(str(path))
    return content_type or "application/octet-stream"


def _upload_audio_and_presign(audio_path: Path, *, expires_s: int) -> str:
    try:
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "boto3 is required for smoke gate uploads. "
            "Install dependencies before running this gate."
        ) from e

    endpoint_url = _require_env("S3_ENDPOINT_URL")
    access_key = _require_env("S3_ACCESS_KEY_ID")
    secret_key = _require_env("S3_SECRET_ACCESS_KEY")
    bucket = _require_env("S3_BUCKET_RAW_AUDIO")
    region = (os.environ.get("S3_REGION") or "ru-1").strip() or "ru-1"

    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    sha1 = _sha1_file(audio_path)[:12]
    ext = audio_path.suffix.lower() or ".bin"
    key = f"raw_audio/mr1_smoke/{stamp}_{sha1}_{audio_path.stem}{ext}"

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
        ExtraArgs={"ContentType": _guess_content_type(audio_path)},
    )

    return str(
        s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=int(expires_s),
        )
    )


def _enqueue_job(*, orch_base_url: str, audio_url: str, job_mode: str, timeout_s: float) -> Dict[str, Any]:
    payload = {
        "audio_s3_url": audio_url,
        "mode": job_mode,
        "idempotency_key": None,
        "project_id": None,
    }
    return _http_json(
        "POST",
        f"{orch_base_url.rstrip('/')}/send_audio_s3",
        payload=payload,
        timeout_s=timeout_s,
    )


def _poll_job(
    *,
    orch_base_url: str,
    job_id: str,
    poll_interval_s: float,
    poll_timeout_s: float,
) -> Dict[str, Any]:
    url = f"{orch_base_url.rstrip('/')}/jobs/{job_id}"
    t0 = time.time()

    while True:
        state = _http_json("GET", url, timeout_s=20.0)
        status = str(state.get("status") or "").strip().upper()
        if status in {"SUCCEEDED", "FAILED"}:
            return state
        if (time.time() - t0) > float(poll_timeout_s):
            raise RuntimeError(f"poll timeout for job_id={job_id} after {poll_timeout_s}s")
        time.sleep(float(poll_interval_s))


def _check_health(
    *,
    orch_base_url: str,
    checks: int,
    timeout_s: float,
    interval_s: float,
) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    for idx in range(checks):
        payload = _http_json("GET", f"{orch_base_url.rstrip('/')}/health", timeout_s=timeout_s)
        ok = bool(payload.get("ok") is True)
        rec = {"index": idx + 1, "ok": ok, "payload": payload}
        out.append(rec)
        print(f"[health] check={idx + 1}/{checks} ok={ok} payload={json.dumps(payload, ensure_ascii=False)}")
        if not ok:
            raise RuntimeError(f"health check failed at attempt {idx + 1}/{checks}: {payload}")
        if idx + 1 < checks:
            time.sleep(float(interval_s))
    return out


def _write_silent_wav(path: Path, *, duration_s: float, sample_rate: int) -> None:
    if duration_s <= 0:
        raise RuntimeError("synthetic duration must be > 0")
    if sample_rate <= 0:
        raise RuntimeError("synthetic sample_rate must be > 0")

    frames = int(round(duration_s * float(sample_rate)))
    silence = b"\x00\x00" * frames
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(silence)


def _run_case(
    *,
    case_name: str,
    audio_path: Path,
    orch_base_url: str,
    job_mode: str,
    enqueue_timeout_s: float,
    poll_interval_s: float,
    poll_timeout_s: float,
    presign_expires_s: int,
) -> Dict[str, Any]:
    print(f"\n[case:{case_name}] audio={audio_path}")
    audio_url = _upload_audio_and_presign(audio_path, expires_s=presign_expires_s)
    print(f"[case:{case_name}] uploaded=yes")

    enqueue = _enqueue_job(
        orch_base_url=orch_base_url,
        audio_url=audio_url,
        job_mode=job_mode,
        timeout_s=enqueue_timeout_s,
    )
    job_id = str(enqueue.get("job_id") or "").strip()
    if not job_id:
        raise RuntimeError(f"case={case_name}: enqueue response has no job_id: {enqueue}")

    print(f"[case:{case_name}] job_id={job_id}")
    state = _poll_job(
        orch_base_url=orch_base_url,
        job_id=job_id,
        poll_interval_s=poll_interval_s,
        poll_timeout_s=poll_timeout_s,
    )
    status = str(state.get("status") or "").strip().upper()
    stage = str(state.get("stage") or "")
    err = state.get("error")
    print(f"[case:{case_name}] final_status={status} stage={stage} err={('yes' if err else 'no')}")

    result_payload = state.get("result") if isinstance(state.get("result"), dict) else {}
    output_url = result_payload.get("output_url") or (result_payload.get("windows") or {}).get("output_url")

    return {
        "case": case_name,
        "audio_path": str(audio_path),
        "job_id": job_id,
        "status": status,
        "stage": stage,
        "error": err,
        "output_url": output_url,
    }


def main() -> int:
    _load_env()

    ap = argparse.ArgumentParser(
        "run_mr1_smoke_gate.py — MR-1 runtime gate: health + synthetic no-speech + real archival"
    )
    ap.add_argument("--orch", default=(os.environ.get("ORCHESTRATOR_PUBLIC_URL") or "").strip(), help="Orchestrator public URL")
    ap.add_argument("--archival-file", required=True, help="Path to real archival audio file for with_gemini smoke")
    ap.add_argument("--job-mode", choices=["with_gemini", "no_gemini"], default="with_gemini")
    ap.add_argument("--health-checks", type=int, default=3)
    ap.add_argument("--health-timeout-s", type=float, default=10.0)
    ap.add_argument("--health-interval-s", type=float, default=2.0)
    ap.add_argument("--enqueue-timeout-s", type=float, default=30.0)
    ap.add_argument("--poll-interval-s", type=float, default=3.0)
    ap.add_argument("--poll-timeout-s", type=float, default=3600.0)
    ap.add_argument("--synthetic-duration-s", type=float, default=16.0)
    ap.add_argument("--synthetic-sample-rate", type=int, default=16000)
    ap.add_argument("--presign-expires-s", type=int, default=int((os.environ.get("S3_PRESIGN_EXPIRES_S") or "86400").strip() or "86400"))
    ap.add_argument("--report-json", default="", help="Optional path to write JSON report")
    args = ap.parse_args()

    orch = str(args.orch or "").strip().rstrip("/")
    if not orch:
        raise SystemExit("[ERR] --orch is required (or set ORCHESTRATOR_PUBLIC_URL in env)")

    archival_file = _require_audio_file(str(args.archival_file), arg_name="--archival-file")

    if int(args.health_checks) <= 0:
        raise SystemExit("[ERR] --health-checks must be > 0")

    if int(args.presign_expires_s) <= 0:
        raise SystemExit("[ERR] --presign-expires-s must be > 0")

    print(f"[gate] orch={orch}")
    print(f"[gate] job_mode={args.job_mode}")
    print(f"[gate] archival_file={archival_file}")

    # Explicit env contract for upload+presign, no hidden defaults.
    for key in ("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET_RAW_AUDIO"):
        _require_env(key)

    health_records = _check_health(
        orch_base_url=orch,
        checks=int(args.health_checks),
        timeout_s=float(args.health_timeout_s),
        interval_s=float(args.health_interval_s),
    )

    cases: list[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="mr1_smoke_") as td:
        synthetic_path = Path(td) / "synthetic_no_speech.wav"
        _write_silent_wav(
            synthetic_path,
            duration_s=float(args.synthetic_duration_s),
            sample_rate=int(args.synthetic_sample_rate),
        )

        cases.append(
            _run_case(
                case_name="synthetic_no_speech",
                audio_path=synthetic_path,
                orch_base_url=orch,
                job_mode=str(args.job_mode),
                enqueue_timeout_s=float(args.enqueue_timeout_s),
                poll_interval_s=float(args.poll_interval_s),
                poll_timeout_s=float(args.poll_timeout_s),
                presign_expires_s=int(args.presign_expires_s),
            )
        )

        cases.append(
            _run_case(
                case_name="real_archival",
                audio_path=archival_file,
                orch_base_url=orch,
                job_mode=str(args.job_mode),
                enqueue_timeout_s=float(args.enqueue_timeout_s),
                poll_interval_s=float(args.poll_interval_s),
                poll_timeout_s=float(args.poll_timeout_s),
                presign_expires_s=int(args.presign_expires_s),
            )
        )

    failures = [c for c in cases if str(c.get("status") or "").upper() != "SUCCEEDED"]
    report = {
        "ok": not failures,
        "gate": "mr1_no_speech_and_archival",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "orch": orch,
        "health": health_records,
        "cases": cases,
    }

    print("\n=== MR-1 GATE REPORT ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    report_path = str(args.report_json or "").strip()
    if report_path:
        out_path = Path(report_path).expanduser()
        if not out_path.is_absolute():
            out_path = (ROOT / out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[gate] report_json={out_path}")

    if failures:
        print("[gate][ERR] One or more smoke jobs did not reach SUCCEEDED")
        return 2

    print("[gate][OK] MR-1 smoke gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
