#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv_fallback(env_file: Path) -> None:
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


def _extract_output_url(st: Dict[str, Any]) -> str:
    res = st.get("result") if isinstance(st.get("result"), dict) else {}
    if not isinstance(res, dict):
        return ""
    out_url = res.get("output_url")
    if isinstance(out_url, str) and out_url.strip():
        return out_url.strip()
    win = res.get("windows") if isinstance(res.get("windows"), dict) else {}
    if isinstance(win, dict):
        u2 = win.get("output_url") or win.get("output_s3_url")
        if isinstance(u2, str) and u2.strip():
            return u2.strip()
    return ""


def main() -> int:
    load_env()

    ap = argparse.ArgumentParser("poll_jobs.py — poll orchestrator /jobs/{id} until done")
    ap.add_argument("--orch", default=os.environ.get("ORCHESTRATOR_PUBLIC_URL", "http://localhost:8000"), help="Orchestrator public URL")
    ap.add_argument("--concurrency", type=int, default=6, help="How many jobs to poll in parallel")
    ap.add_argument("--poll-interval-s", type=float, default=2.0, help="Polling interval")
    ap.add_argument("--poll-timeout-s", type=float, default=7200.0, help="Polling timeout per job")
    ap.add_argument("job_ids", nargs="+", help="Job IDs to poll")
    args = ap.parse_args()

    orch = str(args.orch).rstrip("/")
    job_ids = sorted({str(x).strip() for x in (args.job_ids or []) if str(x).strip()})
    if not job_ids:
        raise SystemExit("[ERR] no job_ids provided")

    conc = int(args.concurrency)
    if conc <= 0:
        raise SystemExit("[ERR] --concurrency must be > 0")

    print(f"[orch] base={orch}")
    print(f"[poll] jobs={len(job_ids)} concurrency={conc} interval={args.poll_interval_s}s timeout={args.poll_timeout_s}s")

    failed = 0
    finals: List[Tuple[str, Dict[str, Any]]] = []

    def _poll_one(jid: str) -> Tuple[str, Dict[str, Any]]:
        st = poll_job(
            orch_base_url=orch,
            job_id=jid,
            poll_interval_s=float(args.poll_interval_s),
            poll_timeout_s=float(args.poll_timeout_s),
        )
        return jid, st

    with ThreadPoolExecutor(max_workers=min(len(job_ids), conc)) as ex:
        futs = [ex.submit(_poll_one, jid) for jid in job_ids]
        for fut in as_completed(futs):
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
        out_url = _extract_output_url(st)
        print(f"- job_id={jid} status={status} stage={stage} err={('yes' if err else 'no')}")
        if out_url:
            print(f"  output_url={out_url}")
        if err:
            err_s = str(err)
            tail = err_s[-800:] if len(err_s) > 800 else err_s
            print(f"  error_tail={tail}")

    if failed:
        print(f"\n[done] failed={failed}")
        return 2

    print("\n[done] ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

