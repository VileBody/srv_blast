#!/usr/bin/env python
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

# --- bootstrap: .env и sys.path ---

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

dotenv_path = ROOT / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)


async def main():
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")

    if not api_id or not api_hash:
        print("Ошибка: в .env должны быть TG_API_ID и TG_API_HASH")
        return

    api_id_int = int(api_id)

    print("=== Генерация StringSession для Telethon ===")
    print("Сейчас Telethon попросит номер телефона (в формате +7999...) и код из Telegram.")
    print("Если включён 2FA-пароль — тоже спросит.")
    print()

    # пустая временная сессия — нужно только для логина
    async with TelegramClient(StringSession(), api_id_int, api_hash) as client:
        session_str = client.session.save()
        print("\n=== ГОТОВО ===")
        print("Вот твой StringSession (TG_SESSION):\n")
        print(session_str)
        print("\nСкопируй это значение в .env как строку:")
        print("TG_SESSION=" + session_str)
        print("\nИ больше этот скрипт можно почти не трогать 🙂")


if __name__ == "__main__":
    asyncio.run(main())
