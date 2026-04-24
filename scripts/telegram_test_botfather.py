#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TOKEN_RE = re.compile(r"\b\d+:[A-Za-z0-9_-]{20,}\b")
TEST_PHONE_PREFIX = "99966"


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
        raise SystemExit(f"{name} is required; fill it in .env or the test env file")
    return value


def _int_env(env: dict[str, str], name: str, default: int) -> int:
    try:
        return int(str(env.get(name) or default).strip())
    except Exception:
        return int(default)


def mask_secret(value: str) -> str:
    raw = str(value or "")
    if len(raw) <= 12:
        return "***"
    return f"{raw[:6]}...{raw[-4:]}"


def _sanitize_username(value: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9_]", "", str(value or "")).strip("_")
    if not raw:
        raw = f"blasttest{int(time.time())}bot"
    if not raw.lower().endswith("bot"):
        raw = f"{raw}bot"
    if len(raw) < 5:
        raw = f"blast{raw}bot"
    return raw[:32]


@dataclass(frozen=True)
class BotFatherConfig:
    api_id: int
    api_hash: str
    session_dir: Path
    dc_id: int
    dc_host: str
    dc_port: int
    code_length: int
    owner_phone_suffix: int
    bot_name: str
    bot_username: str
    owner_session_string: str

    @classmethod
    def from_env(cls, env: dict[str, str], *, bot_name: str, bot_username: str) -> "BotFatherConfig":
        dc_id = _int_env(env, "TG_TEST_DC_ID", 2)
        default_username = f"blasttest{int(time.time())}bot"
        return cls(
            api_id=int(_require(env, "TG_TEST_API_ID")),
            api_hash=_require(env, "TG_TEST_API_HASH"),
            session_dir=Path(str(env.get("TG_TEST_SESSION_DIR") or ".telegram-test-sessions")).expanduser(),
            dc_id=dc_id,
            dc_host=str(env.get("TG_TEST_DC_HOST") or "149.154.167.40").strip(),
            dc_port=_int_env(env, "TG_TEST_DC_PORT", 80),
            code_length=max(5, min(6, _int_env(env, "TG_TEST_CODE_LENGTH", 5))),
            owner_phone_suffix=_int_env(env, "TG_TEST_OWNER_PHONE_SUFFIX", 9000),
            bot_name=str(bot_name or env.get("TG_TEST_BOT_NAME") or "Blast Test Bot").strip(),
            bot_username=_sanitize_username(bot_username or env.get("TG_TEST_BOT_USERNAME_CANDIDATE") or default_username),
            owner_session_string=str(env.get("TG_TEST_OWNER_SESSION_STRING") or "").strip(),
        )

    @property
    def owner_phone(self) -> str:
        return f"99966{self.dc_id}{self.owner_phone_suffix:04d}"

    @property
    def login_code(self) -> str:
        return str(int(self.dc_id)) * int(self.code_length)

    @property
    def login_codes(self) -> list[str]:
        primary = self.login_code
        alternatives = [str(int(self.dc_id)) * 5, str(int(self.dc_id)) * 6]
        seen: set[str] = set()
        codes: list[str] = []
        for code in [primary, *alternatives]:
            if code and code not in seen:
                seen.add(code)
                codes.append(code)
        return codes


async def _connect_owner(cfg: BotFatherConfig):
    from telethon import TelegramClient
    from telethon import errors, functions
    from telethon.sessions import StringSession

    cfg.session_dir.mkdir(parents=True, exist_ok=True)
    if cfg.owner_session_string:
        client = TelegramClient(StringSession(cfg.owner_session_string), cfg.api_id, cfg.api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError("TG_TEST_OWNER_SESSION_STRING is not authorized")
        me = await client.get_me()
        phone = str(getattr(me, "phone", "") or "")
        if not phone.startswith(TEST_PHONE_PREFIX):
            await client.disconnect()
            raise RuntimeError(
                "TG_TEST_OWNER_SESSION_STRING must belong to a Telegram test account "
                f"with phone prefix {TEST_PHONE_PREFIX}"
            )
        print(
            "[telegram-test-botfather] owner_session "
            f"user_id={int(getattr(me, 'id', 0) or 0)} phone={phone}"
        )
        return client

    session_path = cfg.session_dir / "botfather_owner"
    client = TelegramClient(str(session_path), cfg.api_id, cfg.api_hash)
    client.session.set_dc(cfg.dc_id, cfg.dc_host, cfg.dc_port)
    await client.connect()
    if not await client.is_user_authorized():
        print(f"[telegram-test-botfather] login owner_phone={cfg.owner_phone}")
        sent = await client.send_code_request(cfg.owner_phone)
        last_error: Exception | None = None
        for code in cfg.login_codes:
            try:
                await client(functions.auth.SignInRequest(cfg.owner_phone, sent.phone_code_hash, code))
                break
            except errors.PhoneNumberUnoccupiedError:
                await client(
                    functions.auth.SignUpRequest(
                        phone_number=cfg.owner_phone,
                        phone_code_hash=sent.phone_code_hash,
                        first_name="Blast Test",
                        last_name="",
                    )
                )
                break
            except errors.PhoneCodeInvalidError as exc:
                last_error = exc
                continue
        else:
            raise RuntimeError(
                f"Could not authorize Telegram test user {cfg.owner_phone}; tried codes={cfg.login_codes}"
            ) from last_error
    return client


async def _latest_text(client: Any, botfather: Any, *, limit: int = 5) -> str:
    texts: list[str] = []
    async for msg in client.iter_messages(botfather, limit=limit):
        text = str(getattr(msg, "raw_text", "") or "")
        if text:
            texts.append(text)
    return "\n\n".join(texts)


async def _send_and_wait(client: Any, botfather: Any, text: str, *, wait_s: float = 2.0) -> str:
    from telethon.errors import FloodWaitError

    try:
        await client.send_message(botfather, text)
    except FloodWaitError as exc:
        delay = int(getattr(exc, "seconds", 0) or 0) + 1
        print(f"[telegram-test-botfather] flood_wait seconds={delay}")
        await asyncio.sleep(delay)
        await client.send_message(botfather, text)
    await asyncio.sleep(wait_s)
    return await _latest_text(client, botfather)


async def create_bot(cfg: BotFatherConfig) -> dict[str, str]:
    client = await _connect_owner(cfg)
    try:
        botfather = await client.get_entity("BotFather")
        await _send_and_wait(client, botfather, "/cancel", wait_s=1.0)
        first = await _send_and_wait(client, botfather, "/newbot", wait_s=1.5)
        if "too many" in first.lower():
            raise RuntimeError(first[:500])
        await _send_and_wait(client, botfather, cfg.bot_name, wait_s=1.5)
        final = await _send_and_wait(client, botfather, cfg.bot_username, wait_s=2.5)
        token_match = TOKEN_RE.search(final)
        if not token_match:
            raise RuntimeError(
                "BotFather did not return a token. "
                f"username={cfg.bot_username!r} latest={final[:1200]!r}"
            )
        return {"token": token_match.group(0), "username": cfg.bot_username, "name": cfg.bot_name}
    finally:
        await client.disconnect()


async def status(cfg: BotFatherConfig) -> None:
    client = await _connect_owner(cfg)
    try:
        me = await client.get_me()
        print(
            "[telegram-test-botfather] owner "
            f"user_id={int(getattr(me, 'id', 0) or 0)} phone={cfg.owner_phone} "
            f"session_dir={cfg.session_dir}"
        )
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Telegram test-environment bot through test BotFather.")
    parser.add_argument("action", choices=["create-bot", "status"])
    parser.add_argument("--env-file", type=Path, default=Path(".env.telegram-test"))
    parser.add_argument("--bot-name", default="")
    parser.add_argument("--bot-username", default="")
    parser.add_argument("--print-token", action="store_true", help="Print the full token. Avoid in CI logs.")
    args = parser.parse_args()

    cfg = BotFatherConfig.from_env(_merged_env(args.env_file), bot_name=args.bot_name, bot_username=args.bot_username)
    if args.action == "status":
        asyncio.run(status(cfg))
        return

    result = asyncio.run(create_bot(cfg))
    print("[telegram-test-botfather] created")
    token = result["token"] if args.print_token else mask_secret(result["token"])
    print(f"TG_TEST_BOT_TOKEN={token}")
    print(f"TG_TEST_BOT_USERNAME={result['username']}")


if __name__ == "__main__":
    main()
