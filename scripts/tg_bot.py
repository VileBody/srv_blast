#!/usr/bin/env python
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, Optional

import httpx
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.custom import Message

# --- bootstrap: корень проекта + .env ---

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

dotenv_path = ROOT / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)

# --- env config ---

TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
TG_SESSION = os.environ["TG_SESSION"]  # StringSession из get_session.py

# Внутри docker-сети оркестратор доступен как "orchestrator:8000"
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8000").rstrip("/")

TMP_DIR = ROOT / "tmp_tg_audio"
TMP_DIR.mkdir(parents=True, exist_ok=True)

# --- Telethon client (userbot через StringSession) ---

client = TelegramClient(StringSession(TG_SESSION), TG_API_ID, TG_API_HASH)


# --- state per chat: pending файл + активный job ---

class PendingFile:
    def __init__(self, message: Message):
        self.message = message
        self.sender_id = message.sender_id
        self.chat_id = message.chat_id


class ChatState:
    def __init__(self):
        self.pending: Optional[PendingFile] = None
        self.active_job_id: Optional[str] = None
        self.job_in_progress: bool = False


chat_states: Dict[int, ChatState] = {}


def get_state(chat_id: int) -> ChatState:
    state = chat_states.get(chat_id)
    if state is None:
        state = ChatState()
        chat_states[chat_id] = state
    return state


# --- оркестратор API helpers ---

async def start_job(audio_path: Path, name: str = "tg_edit") -> str:
    url = f"{ORCHESTRATOR_URL}/api/v1/jobs"
    async with httpx.AsyncClient(timeout=None) as client_http:
        with audio_path.open("rb") as f:
            files = {"file": (audio_path.name, f, "audio/m4a")}
            data = {"name": name}
            resp = await client_http.post(url, files=files, data=data)
        resp.raise_for_status()
        payload = resp.json()
        return payload["id"]


async def wait_for_job(job_id: str, poll_interval: float = 5.0, timeout: float = 600.0) -> dict:
    url = f"{ORCHESTRATOR_URL}/api/v1/jobs/{job_id}"
    async with httpx.AsyncClient(timeout=None) as client_http:
        start = asyncio.get_event_loop().time()
        while True:
            resp = await client_http.get(url)
            resp.raise_for_status()
            job = resp.json()

            status = job.get("status")
            if status in ("DONE", "FAILED"):
                return job

            now = asyncio.get_event_loop().time()
            if now - start > timeout:
                raise TimeoutError(f"Job {job_id} did not finish within {timeout} seconds")

            await asyncio.sleep(poll_interval)


def is_trigger_message(msg: Message) -> bool:
    if not msg.raw_text:
        return False
    text = msg.raw_text.strip().lower()
    return text == "жопа"


# --- основной handler ---

@client.on(events.NewMessage)
async def handler(event: events.NewMessage.Event):
    msg: Message = event.message

    # 1) работаем ТОЛЬКО с личкой
    if not event.is_private:
        return

    chat_id = msg.chat_id
    state = get_state(chat_id)

    # 2) если это медиа (аудио/документ) — запоминаем pending, НИЧЕГО не отвечаем
    if msg.media:
        state.pending = PendingFile(msg)
        return

    # 3) если это триггер "жопа"
    if is_trigger_message(msg):
        # уже есть активный job — не запускаем новый
        if state.job_in_progress and state.active_job_id:
            await msg.reply(
                f"У тебя уже идёт задача ID `{state.active_job_id}`. "
                f"Дождись результата или попробуй позже.",
                parse_mode="markdown",
            )
            return

        pending = state.pending
        if not pending:
            await msg.reply(
                "Сначала отправь мне аудио, потом напиши `жопа`.",
                parse_mode="markdown",
            )
            return

        if pending.sender_id != msg.sender_id:
            await msg.reply("Файл и триггер должны быть от одного пользователя.")
            return

        # забираем pending и отмечаем job как в процессе
        state.pending = None

        # скачиваем файл
        try:
            await msg.reply("Принял. Скачиваю файл и отправляю в монтаж, подожди немного...")
            media_msg = pending.message
            local_path_str = await media_msg.download_media(file=str(TMP_DIR))
            if not local_path_str:
                await msg.reply("Не удалось скачать файл с серверов Telegram 😿")
                return
            audio_path = Path(local_path_str)
        except Exception as e:
            await msg.reply(f"Ошибка при скачивании файла: `{e!r}`", parse_mode="markdown")
            return

        # стартуем job
        try:
            job_id = await start_job(audio_path, name="tg_edit")
            state.active_job_id = job_id
            state.job_in_progress = True
        except Exception as e:
            await msg.reply(f"Ошибка при создании задания в оркестраторе: `{e!r}`", parse_mode="markdown")
            state.active_job_id = None
            state.job_in_progress = False
            return

        await msg.reply(f"Задача отправлена. ID: `{job_id}`\nЖду результат...", parse_mode="markdown")

        # ждём результат
        try:
            job = await wait_for_job(job_id)
        except TimeoutError:
            await msg.reply("Слишком долго нет ответа от оркестратора. Попробуй позже.")
            state.job_in_progress = False
            state.active_job_id = None
            return
        except Exception as e:
            await msg.reply(f"Ошибка при ожидании результата: `{e!r}`", parse_mode="markdown")
            state.job_in_progress = False
            state.active_job_id = None
            return

        # job завершён (DONE или FAILED)
        state.job_in_progress = False
        state.active_job_id = None

        status = job.get("status")
        error = job.get("error")

        # собираем ссылки: либо из download_urls, либо из segments
        urls = job.get("download_urls") or []
        if not urls:
            segments = job.get("segments") or []
            urls = [
                seg.get("s3_url")
                for seg in segments
                if isinstance(seg, dict) and seg.get("s3_url")
            ]

        if status != "DONE" or not urls:
            text = f"Монтаж завершился со статусом `{status}`."
            if error:
                err_str = str(error)
                if len(err_str) > 500:
                    err_str = err_str[:500] + "…"
                text += f"\n\nОшибка:\n`{err_str}`"
            else:
                text += "\n\nСсылок на файлы нет. Смотри логи сервера."
            await msg.reply(text, parse_mode="markdown")
        else:
            # красивый список ссылок
            lines = [f"Готово! Вот твои ролики ({len(urls)}):"]
            for i, url in enumerate(urls, start=1):
                lines.append(f"{i}. {url}")
            await msg.reply("\n".join(lines))

        try:
            audio_path.unlink(missing_ok=True)
        except Exception:
            pass

        return

    # 4) всё остальное в личке — игнорим (можно добавить /start, /help по желанию)
    # if msg.raw_text and msg.raw_text.strip().lower() in ("/start", "/help"):
    #     await msg.reply("Скинь мне аудио, а затем напиши `жопа`.")


async def main():
    print("Starting Telegram userbot with StringSession...")
    await client.start()
    me = await client.get_me()
    print(f"Logged in as @{me.username or me.id}")
    print("Waiting for private messages...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
