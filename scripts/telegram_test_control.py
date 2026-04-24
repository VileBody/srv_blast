#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.telegram_api import TELEGRAM_API_ENV_PROD, TELEGRAM_API_ENV_TEST, make_telegram_api
from scripts import telegram_test_botfather


DEFAULT_CONTROL_ENV_FILE = Path("/opt/blast/telegram-test/.env")
REMOTE_ENV_KEYS = [
    "TG_BOT_API_ENV",
    "TG_DELIVERY_MODE",
    "TG_WEBHOOK_URL",
    "TG_WEBHOOK_SECRET",
    "TG_WEBHOOK_PATH",
    "TG_WEBHOOK_BIND_HOST",
    "TG_WEBHOOK_PORT",
    "TG_WEBHOOK_DEDUP_TTL_S",
    "TG_TEST_BOT_TOKEN",
    "TG_TEST_BOT_USERNAME",
    "TG_TEST_BYPASS_SUBSCRIPTION",
    "TG_TEST_CREDITS_DB_URL",
    "TG_TEST_PREVIEW_SOURCE_BOT_TOKEN",
    "TG_PUBLIC_STATE_PREFIX",
    "TG_MAINTENANCE_STATE_KEY",
    "BOT_TMP_DIR_PUBLIC",
    "ALERT_TELEGRAM_API_ENV",
]
CONFIG_ENV_KEY_PREFIX = "TG_TEST_CONFIG_"
CONFIGURE_ENV_KEYS = [
    "TG_TEST_API_ID",
    "TG_TEST_API_HASH",
    "TG_TEST_OWNER_SESSION_STRING",
    "TG_TEST_CREDITS_DB_URL",
    "TG_TEST_ADMIN_DB_URL",
    "TG_WEBHOOK_SECRET",
    "TG_TEST_AUDIO_PATH",
    "TG_TEST_FOOTAGE_GENRE_LABEL",
    "TG_TEST_FOOTAGE_ARTIST_LABEL",
    "TG_TEST_BOT_TOKEN",
    "TG_TEST_BOT_USERNAME",
    "TG_TEST_BOT_NAME",
    "TG_TEST_BOT_USERNAME_CANDIDATE",
]
CONFIGURE_NODE_KEYS = [
    "TG_TEST_NODE0_HOST",
    "TG_TEST_NODE0_USER",
    "TG_TEST_NODE0_PORT",
    "TG_TEST_NODE0_REPO_DIR",
    "TG_TEST_NODE0_SSH_KEY_PATH",
    "TG_TEST_NODE1_HOST",
    "TG_TEST_NODE1_USER",
    "TG_TEST_NODE1_PORT",
    "TG_TEST_NODE1_REPO_DIR",
    "TG_TEST_NODE1_SSH_KEY_PATH",
]


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


def _merged_env(env_file: Path) -> dict[str, str]:
    merged = _load_env_file(REPO_ROOT / ".env")
    merged.update(_load_env_file(env_file))
    merged.update(os.environ)
    return merged


def _is_placeholder(value: str) -> bool:
    clean = str(value or "").strip()
    return not clean or clean.startswith("<") or clean.endswith(">")


def _require(env: dict[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if _is_placeholder(value):
        raise SystemExit(f"{name} is required in the blast-ops Telegram test env")
    return value


def _optional(env: dict[str, str], name: str, default: str = "") -> str:
    value = str(env.get(name) or "").strip()
    return default if _is_placeholder(value) else value


def _int_env(env: dict[str, str], name: str, default: int) -> int:
    try:
        return int(str(env.get(name) or default).strip())
    except Exception:
        return int(default)


def _float_env(env: dict[str, str], name: str, default: float) -> float:
    try:
        return float(str(env.get(name) or default).strip())
    except Exception:
        return float(default)


def _bool_env(env: dict[str, str], name: str, default: bool = False) -> bool:
    raw = str(env.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _mask(value: str) -> str:
    return telegram_test_botfather.mask_secret(value)


def _write_env_file(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    rendered: list[str] = []
    for raw in existing:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            rendered.append(raw)
            continue
        key = raw.split("=", 1)[0].strip()
        if key in updates:
            rendered.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            rendered.append(raw)
    for key, value in updates.items():
        if key not in seen:
            rendered.append(f"{key}={value}")
    path.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def _print_configured_keys(updates: dict[str, str]) -> None:
    secret_markers = ("TOKEN", "HASH", "SECRET", "PASSWORD", "SESSION", "URL")
    for key in sorted(updates):
        value = updates[key]
        rendered = _mask(value) if any(marker in key for marker in secret_markers) else value
        print(f"[telegram-test-control] configured {key}={rendered}")


def _write_synthetic_audio(path: Path) -> None:
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16_000
    seconds = 8
    amplitude = 10_000
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for i in range(sample_rate * seconds):
            freq = 440.0 if (i // sample_rate) % 2 == 0 else 660.0
            sample = int(amplitude * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))
    path.chmod(0o600)


def _init_env_file(env_file: Path, *, force: bool = False) -> None:
    example = REPO_ROOT / ".env.telegram-test.example"
    if not example.exists():
        raise SystemExit(f"Telegram test env example is missing: {example}")
    if env_file.exists() and not force:
        print(f"[telegram-test-control] env already exists: {env_file}")
        print("[telegram-test-control] not overwriting; fill missing values in-place or pass --yes to refresh")
        return
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    env_file.chmod(0o600)
    print(f"[telegram-test-control] initialized env from example: {env_file}")
    print("[telegram-test-control] fill TG_TEST_API_ID, TG_TEST_API_HASH, TG_TEST_CREDITS_DB_URL, TG_WEBHOOK_SECRET, TG_TEST_AUDIO_PATH, and labels before prepare")


def _configure_env_file(env_file: Path, *, dry_run: bool) -> None:
    if not env_file.exists():
        _init_env_file(env_file)
    existing = _load_env_file(env_file)
    updates: dict[str, str] = {
        "TG_BOT_API_ENV": TELEGRAM_API_ENV_TEST,
        "TG_DELIVERY_MODE": "webhook",
        "TG_WEBHOOK_URL": "https://blast808.com",
        "TG_WEBHOOK_PATH": "/telegram/webhook",
        "TG_TEST_BYPASS_SUBSCRIPTION": "1",
        "TG_TEST_PREVIEW_SOURCE_BOT_TOKEN": "",
        "TG_PUBLIC_STATE_PREFIX": "blast:tg:test:public:chat_state",
        "TG_MAINTENANCE_STATE_KEY": "blast:tg:test:maintenance_mode",
        "BOT_TMP_DIR_PUBLIC": "/app/work/tg_tmp_public_test",
        "TG_TEST_ORCHESTRATOR_URL": "https://blast808.com/orchestrator",
        "TG_TEST_REMOTE_ENV_PATH": ".env.telegram-test",
        "ALERT_TELEGRAM_API_ENV": TELEGRAM_API_ENV_PROD,
    }
    for key in CONFIGURE_ENV_KEYS:
        env_key = f"{CONFIG_ENV_KEY_PREFIX}{key}"
        value = str(os.environ.get(env_key) or "").strip()
        if value:
            updates[key] = value
    for key in CONFIGURE_NODE_KEYS:
        value = str(os.environ.get(key) or "").strip()
        if value:
            updates[key] = value

    candidate = str(updates.get("TG_TEST_BOT_USERNAME_CANDIDATE") or existing.get("TG_TEST_BOT_USERNAME_CANDIDATE") or "").strip()
    if not candidate or candidate == "blasttestbot" or _is_placeholder(candidate):
        updates["TG_TEST_BOT_USERNAME_CANDIDATE"] = f"blasttest{int(time.time())}bot"

    audio_path = str(updates.get("TG_TEST_AUDIO_PATH") or existing.get("TG_TEST_AUDIO_PATH") or "").strip()
    create_audio = _bool_env(os.environ, f"{CONFIG_ENV_KEY_PREFIX}CREATE_SAMPLE_AUDIO", False)

    _print_configured_keys(updates)
    if dry_run:
        if create_audio and audio_path and not _is_placeholder(audio_path):
            print(f"[telegram-test-control] dry-run would create synthetic audio at {audio_path}")
        return
    _write_env_file(env_file, updates)
    if create_audio and audio_path and not _is_placeholder(audio_path):
        audio = Path(audio_path).expanduser()
        if not audio.exists():
            _write_synthetic_audio(audio)
            print(f"[telegram-test-control] created synthetic audio at {audio}")
        else:
            print(f"[telegram-test-control] audio already exists at {audio}")


def _http_json(
    url: str,
    *,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    encoded = None
    headers: dict[str, str] = {}
    if data is not None:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url=url, data=encoded, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:500]}") from exc
    parsed = json.loads(body or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError(f"unexpected JSON response from {url}: {parsed!r}")
    return parsed


def _telegram_call(*, token: str, api_env: str, method: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    api = make_telegram_api(api_env, name="telegram_api_env")
    url = api.method_url(token=token, method=method)
    return _http_json(url, method="POST" if data is not None else "GET", data=data, timeout=25.0)


def _print_webhook_info(*, token: str, api_env: str, label: str) -> None:
    payload = _telegram_call(token=token, api_env=api_env, method="getWebhookInfo")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    print(
        "[telegram-test-control] webhook "
        f"label={label} env={api_env} url={result.get('url', '')!r} "
        f"pending={result.get('pending_update_count', '')!r} last_error={result.get('last_error_message', '')!r}"
    )


def _delete_webhook(*, token: str, api_env: str, dry_run: bool, label: str) -> None:
    print(f"[telegram-test-control] deleteWebhook label={label} env={api_env} token={_mask(token)}")
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


def _prod_token(env: dict[str, str]) -> str:
    token = str(env.get("TG_BOT_PUBLIC_TOKEN") or env.get("TG_BOT_TOKEN") or "").strip()
    if _is_placeholder(token):
        raise SystemExit("TG_BOT_PUBLIC_TOKEN or TG_BOT_TOKEN is required to manage prod webhook")
    return token


def _check_queue_empty(env: dict[str, str]) -> None:
    base = _optional(env, "TG_TEST_ORCHESTRATOR_URL", "https://blast808.com/orchestrator").rstrip("/")
    url = f"{base}/jobs/active?min_age_seconds=0&limit=1"
    payload = _http_json(url, timeout=20.0)
    total = int(payload.get("total_active") or 0)
    print(f"[telegram-test-control] active_jobs total={total} url={url}")
    if total != 0:
        raise SystemExit("active jobs are not empty; wait for drain or pass --skip-queue-check")


@dataclass(frozen=True)
class NodeConfig:
    name: str
    host: str
    user: str
    port: int
    repo_dir: str
    ssh_key_path: Path
    remote_env_path: str

    @property
    def remote_env_abs(self) -> str:
        if self.remote_env_path.startswith("/"):
            return self.remote_env_path
        return f"{self.repo_dir.rstrip('/')}/{self.remote_env_path}"


def _first_env(env: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return default


def _node_from_env(env: dict[str, str], index: int) -> NodeConfig:
    node = f"NODE{index}"
    deploy = f"DEPLOY_PROD_NODE{index}"
    name = _first_env(env, f"TG_TEST_{node}_NAME", default=f"orchestrator-{index}")
    host = _first_env(env, f"TG_TEST_{node}_HOST", f"{deploy}_HOST")
    user = _first_env(env, f"TG_TEST_{node}_USER", f"{deploy}_USER", default="deploy")
    port = _int_env(env, f"TG_TEST_{node}_PORT", _int_env(env, f"{deploy}_PORT", 22))
    repo_dir = _first_env(env, f"TG_TEST_{node}_REPO_DIR", f"{deploy}_REPO_DIR")
    key_path = _first_env(env, f"TG_TEST_{node}_SSH_KEY_PATH", f"{deploy}_SSH_KEY_PATH")
    remote_env_path = _first_env(env, "TG_TEST_REMOTE_ENV_PATH", default=".env.telegram-test")
    missing = [
        label
        for label, value in {
            "host": host,
            "repo_dir": repo_dir,
            "ssh_key_path": key_path,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(f"{name} missing required connection fields: {', '.join(missing)}")
    path = Path(key_path).expanduser()
    if not path.exists():
        raise SystemExit(f"{name} SSH key file not found: {path}")
    return NodeConfig(
        name=name,
        host=host,
        user=user,
        port=port,
        repo_dir=repo_dir,
        ssh_key_path=path,
        remote_env_path=remote_env_path,
    )


def _nodes(env: dict[str, str]) -> list[NodeConfig]:
    return [_node_from_env(env, 0), _node_from_env(env, 1)]


def _ssh_base(node: NodeConfig) -> list[str]:
    return [
        "ssh",
        "-F",
        "/dev/null",
        "-p",
        str(node.port),
        "-i",
        str(node.ssh_key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=20",
        f"{node.user}@{node.host}",
    ]


def _scp_base(node: NodeConfig) -> list[str]:
    return [
        "scp",
        "-F",
        "/dev/null",
        "-P",
        str(node.port),
        "-i",
        str(node.ssh_key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=20",
    ]


def _remote_run(node: NodeConfig, command: str, *, dry_run: bool = False) -> None:
    print(f"[telegram-test-control] remote node={node.name} cmd={command}")
    if dry_run:
        return
    subprocess.run(_ssh_base(node) + ["bash", "-lc", command], check=True)


def render_remote_env(env: dict[str, str]) -> str:
    lines = [
        "# Generated by blast-ops telegram_test_control.py.",
        "# Telethon/API credentials stay on blast-ops and are intentionally not copied.",
    ]
    for key in REMOTE_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if not value:
            continue
        if "\n" in value:
            raise RuntimeError(f"{key} contains a newline and cannot be written to env file")
        lines.append(f"{key}={value}")
    return "\n".join(lines).rstrip() + "\n"


def _copy_remote_env(node: NodeConfig, content: str, *, dry_run: bool = False) -> None:
    remote_path = node.remote_env_abs
    remote_dir = str(Path(remote_path).parent)
    _remote_run(node, f"mkdir -p {shlex.quote(remote_dir)}", dry_run=dry_run)
    print(f"[telegram-test-control] copy env node={node.name} path={remote_path}")
    if dry_run:
        return
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
        tmp_path = Path(fh.name)
        fh.write(content)
    try:
        tmp_path.chmod(0o600)
        subprocess.run(_scp_base(node) + [str(tmp_path), f"{node.user}@{node.host}:{remote_path}"], check=True)
        _remote_run(node, f"chmod 600 {shlex.quote(remote_path)}")
    finally:
        tmp_path.unlink(missing_ok=True)


def _remote_compose(node: NodeConfig, *, test_mode: bool, build: bool, dry_run: bool) -> None:
    parts = ["docker", "compose"]
    if test_mode:
        parts += ["--env-file", node.remote_env_abs]
    parts += ["up", "-d"]
    if build:
        parts.append("--build")
    parts.append("tg-bot-public")
    command = f"cd {shlex.quote(node.repo_dir)} && {shlex.join(parts)}"
    _remote_run(node, command, dry_run=dry_run)


def _remote_status(node: NodeConfig, *, dry_run: bool) -> None:
    command = f"cd {shlex.quote(node.repo_dir)} && docker compose ps tg-bot-public"
    _remote_run(node, command, dry_run=dry_run)


async def _check_test_db(env: dict[str, str]) -> None:
    import asyncpg

    test_url = _require(env, "TG_TEST_CREDITS_DB_URL")
    admin_url = _optional(env, "TG_TEST_ADMIN_DB_URL", "")
    try:
        conn = await asyncpg.connect(test_url)
        await conn.execute("select 1")
        await conn.close()
        print("[telegram-test-control] test DB connection OK")
        return
    except Exception as exc:
        if not admin_url:
            raise RuntimeError("TG_TEST_CREDITS_DB_URL is not reachable and TG_TEST_ADMIN_DB_URL is not set") from exc

    parsed = urllib.parse.urlparse(test_url)
    db_name = parsed.path.lstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9_]+", db_name or ""):
        raise RuntimeError(f"cannot auto-create unsafe test DB name: {db_name!r}")
    admin = await asyncpg.connect(admin_url)
    try:
        exists = await admin.fetchval("select 1 from pg_database where datname = $1", db_name)
        if not exists:
            await admin.execute(f'create database "{db_name}"')
            print(f"[telegram-test-control] created test DB {db_name}")
    finally:
        await admin.close()

    conn = await asyncpg.connect(test_url)
    try:
        await conn.execute("select 1")
    finally:
        await conn.close()
    print("[telegram-test-control] test DB connection OK")


def _validate_test_env(env: dict[str, str], *, require_token: bool) -> None:
    if str(env.get("TG_BOT_API_ENV") or "").strip().lower() != TELEGRAM_API_ENV_TEST:
        raise SystemExit("TG_BOT_API_ENV must be test in the blast-ops Telegram test env")
    if str(env.get("TG_DELIVERY_MODE") or "").strip().lower() != "webhook":
        raise SystemExit("TG_DELIVERY_MODE must be webhook for Telegram test switching")
    _require(env, "TG_TEST_API_ID")
    _require(env, "TG_TEST_API_HASH")
    _require(env, "TG_TEST_CREDITS_DB_URL")
    _require(env, "TG_WEBHOOK_URL")
    _require(env, "TG_WEBHOOK_SECRET")
    _require(env, "TG_TEST_AUDIO_PATH")
    if require_token:
        _require(env, "TG_TEST_BOT_TOKEN")
        _require(env, "TG_TEST_BOT_USERNAME")
    if str(env.get("TG_TEST_BYPASS_SUBSCRIPTION") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise SystemExit("TG_TEST_BYPASS_SUBSCRIPTION=1 is required and is valid only in test mode")
    if "test" not in str(env.get("TG_PUBLIC_STATE_PREFIX") or "").lower():
        raise SystemExit("TG_PUBLIC_STATE_PREFIX must be test-specific")
    if "test" not in str(env.get("TG_MAINTENANCE_STATE_KEY") or "").lower():
        raise SystemExit("TG_MAINTENANCE_STATE_KEY must be test-specific")
    prod_db = str(env.get("CREDITS_DB_URL") or "").strip()
    test_db = str(env.get("TG_TEST_CREDITS_DB_URL") or "").strip()
    if prod_db and prod_db == test_db:
        raise SystemExit("TG_TEST_CREDITS_DB_URL must not equal prod CREDITS_DB_URL")


def _verify_test_bot(env: dict[str, str]) -> None:
    token = _require(env, "TG_TEST_BOT_TOKEN")
    payload = _telegram_call(token=token, api_env=TELEGRAM_API_ENV_TEST, method="getMe")
    if not bool(payload.get("ok")):
        raise RuntimeError(f"test getMe failed: {payload!r}")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    username = str(result.get("username") or "")
    configured = _require(env, "TG_TEST_BOT_USERNAME").lstrip("@")
    if username and configured and username.lower() != configured.lower():
        raise RuntimeError(f"TG_TEST_BOT_USERNAME={configured!r} does not match test getMe username={username!r}")
    print(f"[telegram-test-control] test getMe OK username=@{username or configured} token={_mask(token)}")


def _ensure_test_bot(env_file: Path, env: dict[str, str], *, dry_run: bool) -> dict[str, str]:
    token = str(env.get("TG_TEST_BOT_TOKEN") or "").strip()
    username = str(env.get("TG_TEST_BOT_USERNAME") or "").strip()
    if token and username and not _is_placeholder(token) and not _is_placeholder(username):
        if dry_run:
            print(f"[telegram-test-control] dry-run would verify test bot username=@{username} token={_mask(token)}")
            return env
        _verify_test_bot(env)
        return env
    if dry_run:
        print("[telegram-test-control] dry-run would create test bot through test BotFather")
        return env

    cfg = telegram_test_botfather.BotFatherConfig.from_env(
        env,
        bot_name=str(env.get("TG_TEST_BOT_NAME") or ""),
        bot_username=str(env.get("TG_TEST_BOT_USERNAME_CANDIDATE") or ""),
    )
    result = asyncio.run(telegram_test_botfather.create_bot(cfg))
    updates = {
        "TG_TEST_BOT_TOKEN": result["token"],
        "TG_TEST_BOT_USERNAME": result["username"],
    }
    _write_env_file(env_file, updates)
    env.update(updates)
    print(
        "[telegram-test-control] created test bot "
        f"username=@{result['username']} token={_mask(result['token'])}"
    )
    _verify_test_bot(env)
    return env


def _push_remote_env(env: dict[str, str], *, dry_run: bool) -> None:
    content = render_remote_env(env)
    for node in _nodes(env):
        _copy_remote_env(node, content, dry_run=dry_run)


def _run_load_script(action: str, args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "telegram_test_load.py"),
        action,
        "--env-file",
        str(args.env_file),
    ]
    if action in {"provision", "run", "status", "cleanup"}:
        if int(args.user_count or 0) > 0:
            cmd += ["--user-count", str(args.user_count)]
        if int(args.concurrency or 0) > 0:
            cmd += ["--concurrency", str(args.concurrency)]
        if action == "run" and args.run_id:
            cmd += ["--run-id", str(args.run_id)]
        if action == "cleanup" and args.yes:
            cmd.append("--yes")
    print(f"[telegram-test-control] {shlex.join(cmd)}")
    subprocess.run(cmd, check=True)


def _prepare(args: argparse.Namespace) -> None:
    if not args.env_file.exists():
        raise SystemExit(f"Telegram test env file not found on blast-ops: {args.env_file}")
    env = _merged_env(args.env_file)
    _validate_test_env(env, require_token=False)
    env = _ensure_test_bot(args.env_file, env, dry_run=args.dry_run)
    _validate_test_env(env, require_token=not args.dry_run)
    if not args.skip_db_check and not args.dry_run:
        asyncio.run(_check_test_db(env))
    _push_remote_env(env, dry_run=args.dry_run)


def _status(args: argparse.Namespace) -> None:
    env = _merged_env(args.env_file)
    _validate_test_env(env, require_token=True)
    if not args.skip_queue_check:
        _check_queue_empty(env)
    _print_webhook_info(token=_prod_token(env), api_env=TELEGRAM_API_ENV_PROD, label="prod")
    _print_webhook_info(token=_require(env, "TG_TEST_BOT_TOKEN"), api_env=TELEGRAM_API_ENV_TEST, label="test")
    for node in _nodes(env):
        _remote_status(node, dry_run=args.dry_run)
    _run_load_script("status", args)


def _enter_test(args: argparse.Namespace) -> None:
    env = _merged_env(args.env_file)
    _validate_test_env(env, require_token=True)
    if args.dry_run:
        print(
            "[telegram-test-control] dry-run would verify test bot "
            f"username=@{_require(env, 'TG_TEST_BOT_USERNAME')} token={_mask(_require(env, 'TG_TEST_BOT_TOKEN'))}"
        )
    else:
        _verify_test_bot(env)
    if not args.skip_queue_check:
        _check_queue_empty(env)
    _push_remote_env(env, dry_run=args.dry_run)
    _delete_webhook(token=_prod_token(env), api_env=TELEGRAM_API_ENV_PROD, dry_run=args.dry_run, label="prod")
    for node in _nodes(env):
        _remote_compose(node, test_mode=True, build=args.build, dry_run=args.dry_run)
    if not args.dry_run:
        time.sleep(_float_env(env, "TG_TEST_SWITCH_VERIFY_DELAY_S", 5.0))
        _print_webhook_info(token=_require(env, "TG_TEST_BOT_TOKEN"), api_env=TELEGRAM_API_ENV_TEST, label="test")


def _exit_test(args: argparse.Namespace) -> None:
    env = _merged_env(args.env_file)
    _validate_test_env(env, require_token=True)
    if not args.skip_queue_check:
        _check_queue_empty(env)
    _delete_webhook(token=_require(env, "TG_TEST_BOT_TOKEN"), api_env=TELEGRAM_API_ENV_TEST, dry_run=args.dry_run, label="test")
    for node in _nodes(env):
        _remote_compose(node, test_mode=False, build=args.build, dry_run=args.dry_run)
    if not args.dry_run:
        time.sleep(_float_env(env, "TG_TEST_SWITCH_VERIFY_DELAY_S", 5.0))
        _print_webhook_info(token=_prod_token(env), api_env=TELEGRAM_API_ENV_PROD, label="prod")


def main() -> None:
    parser = argparse.ArgumentParser(description="Blast-ops control plane for Telegram test/prod bot switching.")
    parser.add_argument("action", choices=["init-env", "configure-env", "prepare", "status", "enter-test", "exit-test", "provision", "run", "cleanup"])
    parser.add_argument("--env-file", type=Path, default=DEFAULT_CONTROL_ENV_FILE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-queue-check", action="store_true")
    parser.add_argument("--skip-db-check", action="store_true")
    parser.add_argument("--build", action="store_true", help="Rebuild tg-bot-public during mode switch.")
    parser.add_argument("--user-count", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=0)
    parser.add_argument("--run-id", default=str(int(time.time())))
    parser.add_argument("--yes", action="store_true", help="Confirm cleanup of local Telethon session files.")
    args = parser.parse_args()

    try:
        if args.action == "init-env":
            _init_env_file(args.env_file, force=args.yes)
        elif args.action == "configure-env":
            _configure_env_file(args.env_file, dry_run=args.dry_run)
        elif args.action == "prepare":
            _prepare(args)
        elif args.action == "status":
            _status(args)
        elif args.action == "enter-test":
            _enter_test(args)
        elif args.action == "exit-test":
            _exit_test(args)
        elif args.action in {"provision", "run", "cleanup"}:
            _run_load_script(args.action, args)
        else:  # pragma: no cover - argparse enforces choices
            raise SystemExit(f"unsupported action: {args.action}")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(int(exc.returncode or 1)) from exc


if __name__ == "__main__":
    main()
