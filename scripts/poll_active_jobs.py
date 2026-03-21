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

import redis


ROOT = Path(__file__).resolve().parents[1]


ACTIVE_STATUSES = {"NEW", "QUEUED", "RUNNING"}
FINAL_STATUSES = {"SUCCEEDED", "FAILED"}


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


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key, default) or "").strip()


def _require_env(key: str) -> str:
    v = _env(key)
    if not v:
        raise RuntimeError(f"Missing required env var: {key}")
    return v


def _redis_client_from_env() -> redis.Redis:
    host = _env("REDIS_HOST", "localhost")
    port = int(_env("REDIS_PORT", "6379") or "6379")
    username = _env("REDIS_USERNAME", "")
    password = _env("REDIS_PASSWORD", "")
    return redis.Redis(
        host=host,
        port=port,
        username=username or None,
        password=password or None,
        decode_responses=True,
    )


def _job_key_prefix() -> str:
    # must match JobStore.from_env() default
    return _env("JOBSTORE_PREFIX", "blast")


def _parse_state(raw: str) -> Dict[str, Any]:
    d = json.loads(raw)
    if not isinstance(d, dict):
        raise ValueError("job state is not an object")
    return d


def _job_id_from_key(key: str, *, prefix: str) -> str:
    # key format: "<prefix>:job:<job_id>"
    p = f"{prefix}:job:"
    if not key.startswith(p):
        return ""
    return key[len(p) :].strip()


def list_active_job_ids(r: redis.Redis, *, prefix: str, limit: int) -> List[str]:
    pat = f"{prefix}:job:*"
    ids: List[str] = []
    for key in r.scan_iter(match=pat, count=1000):
        if not isinstance(key, str):
            continue
        jid = _job_id_from_key(key, prefix=prefix)
        if not jid:
            continue
        raw = r.get(key)
        if not raw:
            continue
        try:
            st = _parse_state(raw)
        except Exception:
            continue
        status = str(st.get("status") or "").strip().upper()
        if status in ACTIVE_STATUSES:
            ids.append(jid)
            if limit > 0 and len(ids) >= limit:
                break
    ids.sort()
    return ids


def get_state(r: redis.Redis, *, prefix: str, job_id: str) -> Dict[str, Any]:
    key = f"{prefix}:job:{job_id}"
    raw = r.get(key)
    if not raw:
        return {"job_id": job_id, "status": "MISSING"}
    try:
        st = _parse_state(raw)
    except Exception as e:
        return {"job_id": job_id, "status": "BAD_JSON", "error": repr(e)}
    st.setdefault("job_id", job_id)
    return st


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


def _fmt_one_line(st: Dict[str, Any]) -> str:
    jid = str(st.get("job_id") or "")
    status = str(st.get("status") or "")
    stage = st.get("stage")
    err = st.get("error")
    out_url = _extract_output_url(st)
    bits = [f"job_id={jid}", f"status={status}", f"stage={stage}"]
    if out_url:
        bits.append("output_url=yes")
    if err:
        bits.append("err=yes")
    return " ".join(bits)


def main() -> int:
    load_env()

    ap = argparse.ArgumentParser("poll_active_jobs.py — list active jobs in Redis and poll until completion")
    ap.add_argument("--poll-interval-s", type=float, default=2.0, help="Polling interval")
    ap.add_argument("--poll-timeout-s", type=float, default=7200.0, help="Polling timeout total")
    ap.add_argument("--concurrency", type=int, default=12, help="How many jobs to fetch in parallel")
    ap.add_argument("--limit", type=int, default=0, help="Limit active jobs (0 = no limit)")
    ap.add_argument("--follow", action="store_true", help="Keep discovering new active jobs during polling")
    args = ap.parse_args()

    # explicit env contract: we rely on Redis to list jobs
    _require_env("REDIS_HOST")

    prefix = _job_key_prefix()
    r = _redis_client_from_env()

    t0 = time.time()
    seen: set[str] = set()

    def discover() -> List[str]:
        ids = list_active_job_ids(r, prefix=prefix, limit=int(args.limit))
        for jid in ids:
            seen.add(jid)
        return ids

    ids0 = discover()
    if not ids0:
        print("[ok] no active jobs found")
        return 0

    print(f"[redis] host={_env('REDIS_HOST')} prefix={prefix}")
    print(f"[poll] active_jobs={len(ids0)} follow={('yes' if args.follow else 'no')}")
    print("[active]")
    for jid in ids0:
        st = get_state(r, prefix=prefix, job_id=jid)
        print(f"- {_fmt_one_line(st)}")

    while True:
        if (time.time() - t0) > float(args.poll_timeout_s):
            print(f"\n[ERR] poll timeout after {args.poll_timeout_s}s")
            return 2

        if args.follow:
            discover()

        job_ids = sorted(seen)
        if not job_ids:
            print("\n[ok] no jobs to poll")
            return 0

        finals: List[Dict[str, Any]] = []
        still_active = 0

        def _one(jid: str) -> Dict[str, Any]:
            return get_state(r, prefix=prefix, job_id=jid)

        with ThreadPoolExecutor(max_workers=max(1, int(args.concurrency))) as ex:
            futs = [ex.submit(_one, jid) for jid in job_ids]
            for fut in as_completed(futs):
                st = fut.result()
                status = str(st.get("status") or "").strip().upper()
                if status in FINAL_STATUSES:
                    finals.append(st)
                elif status in ACTIVE_STATUSES:
                    still_active += 1

        finals.sort(key=lambda x: str(x.get("job_id") or ""))

        print("\n[progress]")
        print(f"active={still_active} done={len(finals)} total_seen={len(seen)}")
        for st in finals[:20]:
            # keep console readable; user can query full /jobs/{id} if needed
            print(f"- {_fmt_one_line(st)}")

        if still_active == 0:
            print("\n=== FINAL STATES ===")
            for st in finals:
                jid = str(st.get("job_id") or "")
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
            print("\n[done] ok")
            return 0

        time.sleep(float(args.poll_interval_s))


if __name__ == "__main__":
    raise SystemExit(main())

