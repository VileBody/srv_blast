#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.telegram_api import TELEGRAM_API_ENV_PROD, TELEGRAM_API_ENV_TEST, make_telegram_api


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def _merged_env(test_env_file: Path) -> dict[str, str]:
    merged = _load_env_file(Path(".env"))
    merged.update(os.environ)
    merged.update(_load_env_file(test_env_file))
    return merged


def _is_placeholder(value: str) -> bool:
    clean = str(value or "").strip()
    return not clean or clean.startswith("<") or clean.endswith(">")


def _require(env: dict[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if _is_placeholder(value):
        raise SystemExit(f"{name} is required; fill it in the test env file")
    return value


def _mask(value: str) -> str:
    raw = str(value or "")
    if len(raw) <= 12:
        return "***"
    return f"{raw[:6]}...{raw[-4:]}"


def _http_json(url: str, *, method: str = "GET", data: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any]:
    encoded = None
    headers: dict[str, str] = {}
    if data is not None:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url=url, data=encoded, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "ignore")
    parsed = json.loads(body or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError(f"unexpected JSON response from {url}: {parsed!r}")
    return parsed


def _telegram_call(*, token: str, api_env: str, method: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    api = make_telegram_api(api_env, name="telegram_api_env")
    url = api.method_url(token=token, method=method)
    return _http_json(url, method="POST" if data is not None else "GET", data=data, timeout=25.0)


def _delete_webhook(*, token: str, api_env: str, dry_run: bool, label: str) -> None:
    print(f"[telegram-test-switch] deleteWebhook label={label} env={api_env} token={_mask(token)}")
    if dry_run:
        return
    payload = _telegram_call(
        token=token,
        api_env=api_env,
        method="deleteWebhook",
        data={"drop_pending_updates": "false"},
    )
    if not bool(payload.get("ok")):
        raise RuntimeError(f"deleteWebhook failed label={label} payload={payload!r}")


def _print_webhook_info(*, token: str, api_env: str, label: str) -> None:
    payload = _telegram_call(token=token, api_env=api_env, method="getWebhookInfo")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    print(
        "[telegram-test-switch] webhook "
        f"label={label} env={api_env} url={result.get('url', '')!r} "
        f"pending={result.get('pending_update_count', '')!r} last_error={result.get('last_error_message', '')!r}"
    )


def _check_queue_empty(*, orchestrator_url: str) -> None:
    base = str(orchestrator_url or "").strip().rstrip("/")
    if not base:
        raise SystemExit("orchestrator URL is empty")
    url = f"{base}/jobs/active?min_age_seconds=0&limit=1"
    payload = _http_json(url, timeout=20.0)
    total = int(payload.get("total_active") or 0)
    print(f"[telegram-test-switch] active_jobs total={total} url={url}")
    if total != 0:
        raise SystemExit("active jobs are not empty; wait for drain or pass --skip-queue-check")


def _docker_compose(args: argparse.Namespace, parts: list[str], *, dry_run: bool) -> None:
    cmd = shlex.split(str(args.compose_cmd or "docker compose")) + parts
    print("[telegram-test-switch]", shlex.join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, check=True)


def _validate_test_env(env: dict[str, str]) -> None:
    if str(env.get("TG_BOT_API_ENV") or "").strip().lower() != TELEGRAM_API_ENV_TEST:
        raise SystemExit("TG_BOT_API_ENV must be test in the test env file")
    if str(env.get("TG_DELIVERY_MODE") or "").strip().lower() != "webhook":
        raise SystemExit("TG_DELIVERY_MODE must be webhook for this test switch profile")
    _require(env, "TG_TEST_BOT_TOKEN")
    _require(env, "TG_TEST_BOT_USERNAME")
    _require(env, "TG_TEST_CREDITS_DB_URL")
    _require(env, "TG_WEBHOOK_URL")
    _require(env, "TG_WEBHOOK_SECRET")
    if str(env.get("TG_TEST_BYPASS_SUBSCRIPTION") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise SystemExit("TG_TEST_BYPASS_SUBSCRIPTION=1 is required for the planned load harness")


def _prod_token(env: dict[str, str]) -> str:
    token = str(env.get("TG_BOT_PUBLIC_TOKEN") or env.get("TG_BOT_TOKEN") or "").strip()
    if _is_placeholder(token):
        raise SystemExit("TG_BOT_PUBLIC_TOKEN or TG_BOT_TOKEN is required in .env to manage prod webhook")
    return token


def _enter(args: argparse.Namespace) -> None:
    env = _merged_env(args.env_file)
    _validate_test_env(env)
    if not args.skip_queue_check:
        _check_queue_empty(orchestrator_url=args.orchestrator_url or env.get("TG_TEST_ORCHESTRATOR_URL") or "http://127.0.0.1:18000")
    _delete_webhook(token=_prod_token(env), api_env=TELEGRAM_API_ENV_PROD, dry_run=args.dry_run, label="prod")
    compose_parts = ["--env-file", str(args.env_file), "up", "-d"]
    if not args.no_build:
        compose_parts.append("--build")
    compose_parts.append("tg-bot-public")
    _docker_compose(args, compose_parts, dry_run=args.dry_run)
    if not args.dry_run:
        _print_webhook_info(token=_require(env, "TG_TEST_BOT_TOKEN"), api_env=TELEGRAM_API_ENV_TEST, label="test")


def _exit(args: argparse.Namespace) -> None:
    env = _merged_env(args.env_file)
    _delete_webhook(token=_require(env, "TG_TEST_BOT_TOKEN"), api_env=TELEGRAM_API_ENV_TEST, dry_run=args.dry_run, label="test")
    compose_parts = ["up", "-d"]
    if not args.no_build:
        compose_parts.append("--build")
    compose_parts.append("tg-bot-public")
    _docker_compose(args, compose_parts, dry_run=args.dry_run)
    if not args.dry_run:
        _print_webhook_info(token=_prod_token(env), api_env=TELEGRAM_API_ENV_PROD, label="prod")


def _status(args: argparse.Namespace) -> None:
    env = _merged_env(args.env_file)
    if not args.skip_queue_check:
        _check_queue_empty(orchestrator_url=args.orchestrator_url or env.get("TG_TEST_ORCHESTRATOR_URL") or "http://127.0.0.1:18000")
    prod_token = str(env.get("TG_BOT_PUBLIC_TOKEN") or env.get("TG_BOT_TOKEN") or "").strip()
    if prod_token and not _is_placeholder(prod_token):
        _print_webhook_info(token=prod_token, api_env=TELEGRAM_API_ENV_PROD, label="prod")
    test_token = str(env.get("TG_TEST_BOT_TOKEN") or "").strip()
    if test_token and not _is_placeholder(test_token):
        _print_webhook_info(token=test_token, api_env=TELEGRAM_API_ENV_TEST, label="test")


def main() -> None:
    parser = argparse.ArgumentParser(description="Switch tg-bot-public between prod and Telegram test environment.")
    parser.add_argument("action", choices=["enter-test", "exit-test", "status"])
    parser.add_argument("--env-file", type=Path, default=Path(".env.telegram-test"))
    parser.add_argument("--orchestrator-url", default="")
    parser.add_argument("--compose-cmd", default=os.environ.get("TG_TEST_DOCKER_COMPOSE", "docker compose"))
    parser.add_argument("--skip-queue-check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        if args.action == "enter-test":
            _enter(args)
        elif args.action == "exit-test":
            _exit(args)
        else:
            _status(args)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(int(exc.returncode or 1)) from exc


if __name__ == "__main__":
    main()
