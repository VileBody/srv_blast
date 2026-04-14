#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import redis

from services.tg_bot_public.job_recovery_policy import (
    decide_job_recovery,
    is_forbidden_delivery_error,
)


def _uniq(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        s = str(it or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _iter_chat_state_keys(r: redis.Redis, *, prefix: str):
    pat = f"{prefix}:*"
    for key in r.scan_iter(match=pat, count=2000):
        suffix = key[len(prefix) + 1:] if key.startswith(prefix + ":") else ""
        if suffix.isdigit():
            yield key


def _http_get_json(url: str, timeout: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _http_post_json(url: str, payload: dict[str, Any], timeout: float = 20.0) -> tuple[int, dict[str, Any] | str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", errors="replace")
            try:
                return int(r.status), json.loads(txt)
            except Exception:
                return int(r.status), txt
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        try:
            return int(e.code), json.loads(body)
        except Exception:
            return int(e.code), body
    except Exception as e:
        return 0, repr(e)


def _resolve_video_source(job: dict[str, Any], *, bucket: str) -> str:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    windows = result.get("windows") if isinstance(result.get("windows"), dict) else {}
    candidates = [
        str(result.get("output_url") or "").strip(),
        str(windows.get("output_url") or "").strip(),
        str(windows.get("output_s3_url") or "").strip(),
    ]
    for u in candidates:
        if u:
            return u
    job_id = str(job.get("job_id") or "").strip()
    if bucket and job_id:
        return f"s3://{bucket}/renders/{job_id}/output.mp4"
    return ""


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"sent": {}, "requeued": {}, "runs": []}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"sent": {}, "requeued": {}, "runs": []}
    if not isinstance(obj, dict):
        return {"sent": {}, "requeued": {}, "runs": []}
    if not isinstance(obj.get("sent"), dict):
        obj["sent"] = {}
    if not isinstance(obj.get("requeued"), dict):
        obj["requeued"] = {}
    if not isinstance(obj.get("runs"), list):
        obj["runs"] = []
    return obj


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(
        "reconcile_wait_audio_jobs.py",
        description=(
            "Policy runner for WAIT_AUDIO tails: "
            "SUCCEEDED->send, FAILED(retriable)->requeue, "
            "FAILED(non-retriable)->skip, active->wait."
        ),
    )
    ap.add_argument("--execute", action="store_true", help="Apply actions. Default is dry-run.")
    ap.add_argument("--max-chats", type=int, default=500, help="Limit WAIT_AUDIO chats with job tails to scan.")
    ap.add_argument("--max-send", type=int, default=200, help="Max send attempts per run.")
    ap.add_argument("--max-requeue", type=int, default=200, help="Max requeue attempts per run.")
    args = ap.parse_args()

    execute = bool(args.execute)

    tg_state_prefix = str(os.environ.get("TG_STATE_PREFIX") or "blast:tg:public:chat_state").rstrip(":")
    orc = str(
        os.environ.get("ORCHESTRATOR_PUBLIC_URL")
        or os.environ.get("ORCHESTRATOR_URL")
        or "http://orchestrator-api:8000"
    ).rstrip("/")
    out_bucket = str(os.environ.get("S3_BUCKET_OUTPUT_VIDEO") or "").strip()
    bot_token = str(os.environ.get("TG_BOT_TOKEN") or "").strip()
    state_path = Path(
        str(os.environ.get("WAIT_AUDIO_POLICY_STATE_PATH") or "/app/work/wait_audio_policy_state.json").strip()
    )

    r = redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", "6379") or "6379"),
        username=(os.environ.get("REDIS_USERNAME") or None),
        password=(os.environ.get("REDIS_PASSWORD") or None),
        db=int(os.environ.get("REDIS_DB", "0") or "0"),
        decode_responses=True,
    )

    state = _load_state(state_path)
    sent: dict[str, Any] = state.get("sent") if isinstance(state.get("sent"), dict) else {}
    requeued: dict[str, Any] = state.get("requeued") if isinstance(state.get("requeued"), dict) else {}
    state["sent"] = sent
    state["requeued"] = requeued

    counters = {
        "wait_audio_with_jobs": 0,
        "jobs_seen": 0,
        "decision_wait": 0,
        "decision_send_only": 0,
        "decision_requeue_retriable_failed": 0,
        "decision_skip_failed_non_retriable": 0,
        "decision_skip_unknown": 0,
        "send_ok": 0,
        "send_failed": 0,
        "send_forbidden": 0,
        "send_skipped_already_sent": 0,
        "send_skipped_no_source": 0,
        "requeue_ok": 0,
        "requeue_failed": 0,
        "requeue_skipped_already_requeued": 0,
    }
    send_attempts = 0
    requeue_attempts = 0
    rows: list[dict[str, Any]] = []

    scanned_wait_audio = 0
    for key in _iter_chat_state_keys(r, prefix=tg_state_prefix):
        raw = r.get(key)
        if not raw:
            continue
        try:
            st = json.loads(raw)
        except Exception:
            continue
        if str(st.get("stage") or "") != "WAIT_AUDIO":
            continue

        ids = list(st.get("active_job_ids") or [])
        one = str(st.get("active_job_id") or "").strip()
        if one:
            ids.append(one)
        job_ids = _uniq([str(x) for x in ids])
        if not job_ids:
            continue
        counters["wait_audio_with_jobs"] += 1
        scanned_wait_audio += 1
        if scanned_wait_audio > max(1, int(args.max_chats)):
            break

        chat_id = int(st.get("chat_id") or 0)
        for jid in job_ids:
            counters["jobs_seen"] += 1
            try:
                job = _http_get_json(f"{orc}/jobs/{jid}", timeout=8.0)
            except Exception as e:
                rows.append(
                    {
                        "chat_id": chat_id,
                        "job_id": jid,
                        "decision": "skip_unknown",
                        "reason": f"job_fetch_error:{e!r}",
                    }
                )
                counters["decision_skip_unknown"] += 1
                continue

            status = str(job.get("status") or "").upper()
            stage = str(job.get("stage") or "")
            error_text = str(job.get("error") or "")
            decision = decide_job_recovery(status=status, stage=stage, error_text=error_text)
            counters[f"decision_{decision.action}"] += 1

            row = {
                "chat_id": chat_id,
                "job_id": jid,
                "status": status,
                "stage": stage,
                "decision": decision.action,
                "reason": decision.reason,
            }

            if decision.action == "send_only":
                if jid in sent:
                    counters["send_skipped_already_sent"] += 1
                    rows.append({**row, "result": "skip_already_sent"})
                    continue
                source = _resolve_video_source(job, bucket=out_bucket)
                if not source:
                    counters["send_skipped_no_source"] += 1
                    rows.append({**row, "result": "skip_no_source"})
                    continue
                if send_attempts >= max(0, int(args.max_send)):
                    rows.append({**row, "result": "skip_send_limit"})
                    continue
                send_attempts += 1
                text = (
                    "Приносим извинения за задержку. Ваш ролик готов. "
                    "Вот ссылка на видео:\n"
                    f"{source}"
                )
                if not execute:
                    rows.append({**row, "result": "dry_run_send", "source": source})
                    continue
                if not bot_token:
                    counters["send_failed"] += 1
                    rows.append({**row, "result": "send_failed_no_token"})
                    continue
                code, body = _http_post_json(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    {"chat_id": int(chat_id), "text": text, "disable_notification": True},
                    timeout=25.0,
                )
                body_text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                if code == 200 and isinstance(body, dict) and bool(body.get("ok")):
                    message_id = None
                    try:
                        message_id = int(((body.get("result") or {}).get("message_id")))
                    except Exception:
                        message_id = None
                    sent[jid] = {
                        "chat_id": int(chat_id),
                        "sent_at": int(time.time()),
                        "message_id": message_id,
                        "source": source,
                        "policy": "send_only",
                    }
                    counters["send_ok"] += 1
                    rows.append({**row, "result": "send_ok", "message_id": message_id})
                else:
                    counters["send_failed"] += 1
                    if is_forbidden_delivery_error(body_text):
                        counters["send_forbidden"] += 1
                    rows.append({**row, "result": "send_failed", "code": code, "body": body_text[:240]})
                continue

            if decision.action == "requeue_retriable_failed":
                if jid in requeued:
                    counters["requeue_skipped_already_requeued"] += 1
                    rows.append({**row, "result": "skip_already_requeued"})
                    continue
                if requeue_attempts >= max(0, int(args.max_requeue)):
                    rows.append({**row, "result": "skip_requeue_limit"})
                    continue
                requeue_attempts += 1
                if not execute:
                    rows.append({**row, "result": "dry_run_requeue"})
                    continue
                code, body = _http_post_json(
                    f"{orc}/jobs/{jid}/requeue",
                    {"reason": "policy_requeue_retriable_failed_wait_audio"},
                    timeout=20.0,
                )
                if code == 200:
                    requeued[jid] = {
                        "chat_id": int(chat_id),
                        "requeued_at": int(time.time()),
                        "policy": "requeue_retriable_failed",
                    }
                    counters["requeue_ok"] += 1
                    rows.append({**row, "result": "requeue_ok"})
                else:
                    counters["requeue_failed"] += 1
                    body_text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                    rows.append({**row, "result": "requeue_failed", "code": code, "body": body_text[:240]})
                continue

            rows.append({**row, "result": "skip_by_policy"})

    run = {
        "run_at": int(time.time()),
        "execute": bool(execute),
        "counters": counters,
        "rows_sample": rows[:200],
    }
    runs = state.get("runs") if isinstance(state.get("runs"), list) else []
    runs.append(run)
    state["runs"] = runs[-40:]
    _save_state(state_path, state)

    print("summary", json.dumps(counters, ensure_ascii=False))
    print("state_path", str(state_path))
    print("sent_total", len(sent))
    print("requeued_total", len(requeued))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

