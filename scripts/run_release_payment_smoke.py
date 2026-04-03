#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]


def _http_json(
    method: str,
    url: str,
    *,
    payload: Dict[str, Any] | None = None,
    headers: Dict[str, str] | None = None,
    timeout_s: float = 20.0,
) -> tuple[int, Dict[str, Any] | str]:
    data = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, headers=req_headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            code = int(resp.status)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        code = int(e.code)
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error calling {url}: {e}") from e

    body = body.strip()
    if not body:
        return code, {}
    try:
        return code, json.loads(body)
    except Exception:
        return code, body


def _write_report(path_s: str, payload: Dict[str, Any]) -> None:
    out_path = Path(path_s).expanduser()
    if not out_path.is_absolute():
        out_path = (ROOT / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser("release payment smoke: /payments/activate idempotency + auth")
    ap.add_argument("--orch", default=(os.environ.get("ORCHESTRATOR_PUBLIC_URL") or "").strip(), help="Orchestrator base URL")
    ap.add_argument("--chat-id", type=int, required=True, help="Test chat id")
    ap.add_argument("--credits", type=int, default=1, help="Credits to activate")
    ap.add_argument("--admin-token", default=(os.environ.get("PAYMENT_ADMIN_TOKEN") or "").strip(), help="Payment admin token")
    ap.add_argument("--activation-id", default="", help="Optional explicit activation id")
    ap.add_argument("--timeout-s", type=float, default=20.0)
    ap.add_argument("--report-json", default="out/release_payment_smoke_report.json")
    args = ap.parse_args()

    orch = str(args.orch or "").strip().rstrip("/")
    if not orch:
        raise SystemExit("[ERR] --orch is required (or set ORCHESTRATOR_PUBLIC_URL)")
    token = str(args.admin_token or "").strip()
    if not token:
        raise SystemExit("[ERR] --admin-token is required (or set PAYMENT_ADMIN_TOKEN)")
    if int(args.credits) <= 0:
        raise SystemExit("[ERR] --credits must be > 0")

    activation_id = str(args.activation_id or "").strip()
    if not activation_id:
        activation_id = f"release-smoke-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    endpoint = f"{orch}/payments/activate"
    payload = {
        "activation_id": activation_id,
        "chat_id": int(args.chat_id),
        "credits": int(args.credits),
        "note": "release_smoke_activate",
    }

    invalid_code, invalid_body = _http_json(
        "POST",
        endpoint,
        payload=payload,
        headers={"X-Admin-Token": "invalid-token"},
        timeout_s=float(args.timeout_s),
    )
    if invalid_code != 403:
        raise SystemExit(f"[ERR] expected 403 for invalid token, got {invalid_code} body={invalid_body}")

    first_code, first_body = _http_json(
        "POST",
        endpoint,
        payload=payload,
        headers={"X-Admin-Token": token},
        timeout_s=float(args.timeout_s),
    )
    if first_code != 200 or not isinstance(first_body, dict):
        raise SystemExit(f"[ERR] first activate failed: code={first_code} body={first_body}")
    if not bool(first_body.get("ok")):
        raise SystemExit(f"[ERR] first activate returned ok=false: {first_body}")
    if bool(first_body.get("already_done")):
        raise SystemExit(f"[ERR] first activate unexpectedly already_done=true: {first_body}")

    second_code, second_body = _http_json(
        "POST",
        endpoint,
        payload=payload,
        headers={"X-Admin-Token": token},
        timeout_s=float(args.timeout_s),
    )
    if second_code != 200 or not isinstance(second_body, dict):
        raise SystemExit(f"[ERR] second activate failed: code={second_code} body={second_body}")
    if not bool(second_body.get("ok")) or not bool(second_body.get("already_done")):
        raise SystemExit(f"[ERR] second activate is not idempotent: {second_body}")

    report = {
        "ok": True,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "orch": orch,
        "chat_id": int(args.chat_id),
        "activation_id": activation_id,
        "invalid_token": {"status_code": invalid_code, "body": invalid_body},
        "first_activate": {"status_code": first_code, "body": first_body},
        "second_activate": {"status_code": second_code, "body": second_body},
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    _write_report(str(args.report_json), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
