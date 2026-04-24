#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


BTN_LETS_GO = "Едем!"
BTN_SUBSCRIBED = "Подписался!"
BTN_SKIP_LYRICS = "Пусть ИИ угадает"
BTN_SKIP_TIMING = "На усмотрение ИИ"
BTN_SUB_MODE_IMPULSE = "Impulse"
BTN_CONFIRM_YES = "Да"
BTN_LAUNCH = "Запустить"

TERMINAL_RE = re.compile(
    r"(ролик готов|видео готов|готов[ао]?\.|ошиб|не удалось|generation failed|ссылка на видео)",
    re.IGNORECASE,
)


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
    merged = _load_env_file(Path(".env"))
    merged.update(os.environ)
    merged.update(_load_env_file(env_file))
    return merged


def _is_placeholder(value: str) -> bool:
    clean = str(value or "").strip()
    return not clean or clean.startswith("<") or clean.endswith(">")


def _require(env: dict[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if _is_placeholder(value):
        raise SystemExit(f"{name} is required; fill it in the test env file")
    return value


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


@dataclass(frozen=True)
class TestLoadConfig:
    api_id: int
    api_hash: str
    bot_username: str
    session_dir: Path
    dc_id: int
    dc_host: str
    dc_port: int
    code_length: int
    user_count: int
    phone_suffix_start: int
    concurrency: int
    audio_path: Path
    footage_genre_label: str
    footage_artist_label: str
    timeout_s: float
    step_delay_s: float
    run_id: str

    @classmethod
    def from_env(cls, env: dict[str, str], *, run_id: str, require_scenario: bool) -> "TestLoadConfig":
        audio_path = Path(str(env.get("TG_TEST_AUDIO_PATH") or "")).expanduser()
        if require_scenario:
            audio_path = Path(_require(env, "TG_TEST_AUDIO_PATH")).expanduser()
        if require_scenario and not audio_path.exists():
            raise SystemExit(f"TG_TEST_AUDIO_PATH does not exist: {audio_path}")
        bot_username = _require(env, "TG_TEST_BOT_USERNAME").lstrip("@")
        return cls(
            api_id=int(_require(env, "TG_TEST_API_ID")),
            api_hash=_require(env, "TG_TEST_API_HASH"),
            bot_username=bot_username,
            session_dir=Path(str(env.get("TG_TEST_SESSION_DIR") or ".telegram-test-sessions")).expanduser(),
            dc_id=_int_env(env, "TG_TEST_DC_ID", 2),
            dc_host=str(env.get("TG_TEST_DC_HOST") or "149.154.167.40").strip(),
            dc_port=_int_env(env, "TG_TEST_DC_PORT", 80),
            code_length=max(5, min(6, _int_env(env, "TG_TEST_CODE_LENGTH", 5))),
            user_count=_int_env(env, "TG_TEST_USER_COUNT", 50),
            phone_suffix_start=_int_env(env, "TG_TEST_PHONE_SUFFIX_START", 1000),
            concurrency=max(1, min(50, _int_env(env, "TG_TEST_USER_CONCURRENCY", 10))),
            audio_path=audio_path,
            footage_genre_label=_require(env, "TG_TEST_FOOTAGE_GENRE_LABEL") if require_scenario else str(env.get("TG_TEST_FOOTAGE_GENRE_LABEL") or ""),
            footage_artist_label=_require(env, "TG_TEST_FOOTAGE_ARTIST_LABEL") if require_scenario else str(env.get("TG_TEST_FOOTAGE_ARTIST_LABEL") or ""),
            timeout_s=_float_env(env, "TG_TEST_RUN_TIMEOUT_S", 7200.0),
            step_delay_s=_float_env(env, "TG_TEST_STEP_DELAY_S", 1.5),
            run_id=run_id,
        )

    def phone_for_index(self, index: int) -> str:
        suffix = self.phone_suffix_start + int(index)
        return f"99966{self.dc_id}{suffix:04d}"

    @property
    def login_code(self) -> str:
        digit = str(int(self.dc_id))
        return digit * int(self.code_length)

    @property
    def login_codes(self) -> list[str]:
        digit = str(int(self.dc_id))
        seen: set[str] = set()
        codes: list[str] = []
        for code in [self.login_code, digit * 5, digit * 6]:
            if code and code not in seen:
                seen.add(code)
                codes.append(code)
        return codes


async def _sleep_jitter(base_s: float) -> None:
    await asyncio.sleep(max(0.0, float(base_s)) + random.random() * 0.4)


async def _with_flood_wait(coro_factory, *, label: str):
    from telethon.errors import FloodWaitError

    try:
        return await coro_factory()
    except FloodWaitError as exc:
        wait_s = int(getattr(exc, "seconds", 0) or 0) + 1
        print(f"[telegram-test-load] flood_wait label={label} seconds={wait_s}")
        await asyncio.sleep(wait_s)
        return await coro_factory()


async def _connect_user(cfg: TestLoadConfig, index: int):
    from telethon import TelegramClient
    from telethon import errors, functions

    cfg.session_dir.mkdir(parents=True, exist_ok=True)
    session_path = cfg.session_dir / f"user_{index:03d}"
    client = TelegramClient(str(session_path), cfg.api_id, cfg.api_hash)
    client.session.set_dc(cfg.dc_id, cfg.dc_host, cfg.dc_port)
    await client.connect()
    if not await client.is_user_authorized():
        phone = cfg.phone_for_index(index)
        print(f"[telegram-test-load] login index={index} phone={phone}")
        sent = await client.send_code_request(phone)
        last_error: Exception | None = None
        for code in cfg.login_codes:
            try:
                await client(functions.auth.SignInRequest(phone, sent.phone_code_hash, code))
                break
            except errors.PhoneNumberUnoccupiedError:
                await client(
                    functions.auth.SignUpRequest(
                        phone_number=phone,
                        phone_code_hash=sent.phone_code_hash,
                        first_name=f"Blast Test {index:03d}",
                        last_name="",
                    )
                )
                break
            except errors.PhoneCodeInvalidError as exc:
                last_error = exc
                continue
        else:
            raise RuntimeError(f"could not authorize test user phone={phone}; tried codes={cfg.login_codes}") from last_error
    return client


async def _send_text(client: Any, bot: Any, text: str, *, label: str) -> None:
    await _with_flood_wait(lambda: client.send_message(bot, text), label=label)


async def _send_audio(client: Any, bot: Any, path: Path, *, label: str) -> None:
    await _with_flood_wait(lambda: client.send_file(bot, str(path), force_document=False), label=label)


async def _wait_terminal(client: Any, bot: Any, *, since_ts: float, timeout_s: float) -> dict[str, Any]:
    deadline = time.time() + float(timeout_s)
    last_text = ""
    while time.time() < deadline:
        async for msg in client.iter_messages(bot, limit=12):
            msg_date = getattr(msg, "date", None)
            if msg_date is not None and getattr(msg_date, "timestamp", None):
                if float(msg_date.timestamp()) + 5.0 < since_ts:
                    continue
            text = str(getattr(msg, "raw_text", "") or "")
            if text:
                last_text = text[:500]
            if TERMINAL_RE.search(text):
                return {"status": "terminal", "text": text[:500]}
        await asyncio.sleep(5.0)
    return {"status": "timeout", "text": last_text}


async def _provision_one(cfg: TestLoadConfig, index: int) -> dict[str, Any]:
    client = await _connect_user(cfg, index)
    try:
        me = await client.get_me()
        return {
            "index": index,
            "status": "provisioned",
            "phone": cfg.phone_for_index(index),
            "user_id": int(getattr(me, "id", 0) or 0),
        }
    finally:
        await client.disconnect()


async def _run_one(cfg: TestLoadConfig, index: int) -> dict[str, Any]:
    client = await _connect_user(cfg, index)
    started = time.time()
    try:
        bot = await client.get_entity(cfg.bot_username)
        scenario = [
            ("/start " + f"tgtest_{cfg.run_id}_{index:03d}", "start"),
            (BTN_LETS_GO, "lets_go"),
            (BTN_SUBSCRIBED, "subscribed"),
        ]
        for text, label in scenario:
            await _send_text(client, bot, text, label=f"{index}:{label}")
            await _sleep_jitter(cfg.step_delay_s)

        await _send_audio(client, bot, cfg.audio_path, label=f"{index}:audio")
        await _sleep_jitter(max(2.0, cfg.step_delay_s))

        for text, label in [
            (BTN_SKIP_LYRICS, "skip_lyrics"),
            (BTN_SKIP_TIMING, "skip_timing"),
            (cfg.footage_genre_label, "genre"),
            (cfg.footage_artist_label, "artist"),
            (BTN_SUB_MODE_IMPULSE, "subtitles"),
            (BTN_CONFIRM_YES, "confirm_mode"),
            (BTN_LAUNCH, "launch"),
        ]:
            await _send_text(client, bot, text, label=f"{index}:{label}")
            await _sleep_jitter(cfg.step_delay_s)

        result = await _wait_terminal(client, bot, since_ts=started, timeout_s=cfg.timeout_s)
        return {"index": index, "status": result["status"], "last_text": result["text"]}
    except Exception as exc:
        return {"index": index, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        await client.disconnect()


async def _run_many(cfg: TestLoadConfig, worker) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(cfg.concurrency)
    results: list[dict[str, Any]] = []

    async def _wrapped(index: int) -> None:
        async with sem:
            result = await worker(cfg, index)
            print(f"[telegram-test-load] result {result}")
            results.append(result)

    await asyncio.gather(*(_wrapped(i) for i in range(cfg.user_count)))
    return sorted(results, key=lambda row: int(row.get("index", 0)))


def _status(cfg: TestLoadConfig) -> None:
    cfg.session_dir.mkdir(parents=True, exist_ok=True)
    sessions = sorted(cfg.session_dir.glob("user_*.session"))
    print(
        "[telegram-test-load] status "
        f"sessions={len(sessions)} expected={cfg.user_count} dir={cfg.session_dir} bot=@{cfg.bot_username}"
    )


def _cleanup(cfg: TestLoadConfig, *, yes: bool) -> None:
    sessions = sorted(cfg.session_dir.glob("user_*.session*"))
    if not yes:
        print(f"[telegram-test-load] would remove {len(sessions)} session files from {cfg.session_dir}; pass --yes")
        return
    for path in sessions:
        path.unlink(missing_ok=True)
    print(f"[telegram-test-load] removed session_files={len(sessions)} dir={cfg.session_dir}")


def _print_summary(rows: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    print(f"[telegram-test-load] summary {counts}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Drive Telegram test-environment users against a test bot.")
    parser.add_argument("action", choices=["provision", "run", "status", "cleanup"])
    parser.add_argument("--env-file", type=Path, default=Path(".env.telegram-test"))
    parser.add_argument("--run-id", default=str(int(time.time())))
    parser.add_argument("--user-count", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=0)
    parser.add_argument("--phone-suffix-start", type=int, default=0)
    parser.add_argument("--yes", action="store_true", help="Confirm cleanup of local Telethon session files.")
    args = parser.parse_args()

    env = _merged_env(args.env_file)
    cfg = TestLoadConfig.from_env(env, run_id=args.run_id, require_scenario=args.action == "run")
    overrides: dict[str, Any] = {}
    if args.user_count:
        overrides["user_count"] = max(1, min(50, int(args.user_count)))
    if args.concurrency:
        overrides["concurrency"] = max(1, min(50, int(args.concurrency)))
    if args.phone_suffix_start:
        overrides["phone_suffix_start"] = int(args.phone_suffix_start)
    if overrides:
        cfg = replace(cfg, **overrides)

    if args.action == "status":
        _status(cfg)
        return
    if args.action == "cleanup":
        _cleanup(cfg, yes=bool(args.yes))
        return
    if args.action == "provision":
        rows = asyncio.run(_run_many(cfg, _provision_one))
        _print_summary(rows)
        return
    if args.action == "run":
        rows = asyncio.run(_run_many(cfg, _run_one))
        _print_summary(rows)
        return


if __name__ == "__main__":
    main()
