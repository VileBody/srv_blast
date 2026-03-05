from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

import httpx
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, KeyboardButton, Message, ReplyKeyboardMarkup

from .audio_prepare import AudioPrepareResult, prepare_audio_best_effort
from .config import SETTINGS, Settings
from .orchestrator_client import OrchestratorClient
from .s3_client import S3Client, make_s3_url
from .state_store import (
    ChatState,
    RedisChatStateStore,
    STAGE_IDLE,
    STAGE_PROCESSING,
    STAGE_WAIT_AUDIO,
    STAGE_WAIT_CONFIRM,
    STAGE_WAIT_LYRICS_CHOICE,
    STAGE_WAIT_LYRICS_TEXT,
    STAGE_WAIT_NEXT,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] tg_bot: %(message)s",
)
log = logging.getLogger("tg_bot")


BTN_SEND_TRACK = "Отправить трек"
BTN_SEND_LYRICS = "Отправить текст"
BTN_SKIP_LYRICS = "Не присылать текст"
BTN_LAUNCH = "Запустить"
BTN_NEXT = "Сделать следующий"


_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


def _kb(*rows: list[str]) -> ReplyKeyboardMarkup:
    keyboard = []
    for row in rows:
        keyboard.append([KeyboardButton(text=str(x)) for x in row])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def _safe_name(name: str) -> str:
    out = []
    for ch in str(name or ""):
        if ch.isalnum() or ch in {"-", "_", "."}:
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_")
    return s or "audio.bin"


def _now_tag() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime())


def _extract_audio_spec(message: Message) -> Optional[Tuple[str, str]]:
    if message.audio:
        file_id = str(message.audio.file_id)
        file_name = str(message.audio.file_name or "audio.mp3")
        return file_id, file_name

    if message.document:
        file_id = str(message.document.file_id)
        mime = str(message.document.mime_type or "").lower()
        file_name = str(message.document.file_name or "audio.bin")
        ext = Path(file_name).suffix.lower()
        if mime.startswith("audio/") or ext in _AUDIO_EXTS:
            return file_id, file_name

    return None


def _resolve_job_video_source(job: dict[str, Any], settings: Settings) -> str:
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

    bucket = str(settings.s3_bucket_output_video or "").strip()
    job_id = str(job.get("job_id") or "").strip()
    if bucket and job_id:
        return make_s3_url(bucket, f"renders/{job_id}/output.mp4")

    return ""


def _extract_project_archive_source(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""

    direct_candidates = [
        payload.get("project_archive_url"),
        payload.get("artifacts_s3_uri"),
        payload.get("artifacts_s3_url"),
        payload.get("artifacts_url"),
    ]
    for raw in direct_candidates:
        u = str(raw or "").strip()
        if u.startswith("s3://") or u.startswith("http://") or u.startswith("https://"):
            return u

    msg = str(payload.get("message") or "").strip()
    if not msg:
        return ""
    m = re.search(r"artifacts=(s3://[^;\s]+|https?://[^;\s]+)", msg, flags=re.IGNORECASE)
    if not m:
        return ""
    u = str(m.group(1) or "").strip().rstrip(".,;")
    if u.startswith("s3://") or u.startswith("http://") or u.startswith("https://"):
        return u
    return ""


def _resolve_job_project_archive_source(job: dict[str, Any]) -> str:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    windows = result.get("windows") if isinstance(result.get("windows"), dict) else {}

    candidates = [
        _extract_project_archive_source(result),
        _extract_project_archive_source(windows),
    ]
    for u in candidates:
        if u:
            return u
    return ""


def _archive_suffix_from_source(source: str) -> str:
    src = str(source or "").strip()
    if not src:
        return ".bin"

    name = ""
    if src.startswith("s3://"):
        tail = src[5:]
        if "/" in tail:
            name = tail.split("/", 1)[1].split("/")[-1]
    elif src.startswith("http://") or src.startswith("https://"):
        name = Path(urlparse(src).path).name

    low = str(name or "").lower()
    if low.endswith(".tar.gz"):
        return ".tar.gz"
    if low.endswith(".tgz"):
        return ".tgz"
    if low.endswith(".zip"):
        return ".zip"
    if low.endswith(".tar"):
        return ".tar"
    suffix = Path(low).suffix
    return suffix or ".bin"


class BlastBotApp:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = RedisChatStateStore(settings)
        self.s3 = S3Client(settings)
        self.orchestrator = OrchestratorClient(base_url=settings.orchestrator_public_url, timeout_s=60.0)

        self.dp = Dispatcher()
        self.router = Router()
        self.dp.include_router(self.router)

        self._processing_task: asyncio.Task[None] | None = None
        self._bot: Bot | None = None

        self._register_handlers()
        self.dp.startup.register(self._on_startup)
        self.dp.shutdown.register(self._on_shutdown)

    def _register_handlers(self) -> None:
        @self.router.message(CommandStart())
        async def _on_start(message: Message) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            st = await self.store.get(chat_id)
            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе, подожди завершения.")
                return
            await self._move_to_wait_audio(chat_id, message)

        @self.router.message()
        async def _on_any_message(message: Message) -> None:
            if message.chat is None:
                return
            chat_id = int(message.chat.id)
            st = await self.store.get(chat_id)

            if st.stage == STAGE_PROCESSING:
                await message.answer("Трек в процессе, подожди завершения.")
                return

            if st.stage in {STAGE_IDLE, ""}:
                await self._move_to_wait_audio(chat_id, message)
                return

            if st.stage == STAGE_WAIT_AUDIO:
                await self._handle_wait_audio(message, st)
                return

            if st.stage == STAGE_WAIT_LYRICS_CHOICE:
                await self._handle_wait_lyrics_choice(message, st)
                return

            if st.stage == STAGE_WAIT_LYRICS_TEXT:
                await self._handle_wait_lyrics_text(message, st)
                return

            if st.stage == STAGE_WAIT_CONFIRM:
                await self._handle_wait_confirm(message, st)
                return

            if st.stage == STAGE_WAIT_NEXT:
                await self._handle_wait_next(message, st)
                return

            # Unknown stage -> reset deterministically.
            await self._move_to_wait_audio(chat_id, message)

    async def _on_startup(self, bot: Bot) -> None:
        self._bot = bot
        if not self.settings.tg_bot_token:
            raise RuntimeError("TG_BOT_TOKEN is empty")

        self.s3.validate_core()

        if not self.settings.s3_bucket_raw_audio:
            raise RuntimeError("S3_BUCKET_RAW_AUDIO is empty")

        self.settings.tmp_dir.mkdir(parents=True, exist_ok=True)

        self._processing_task = asyncio.create_task(self._processing_loop(), name="tg_bot_processing_loop")
        log.info("startup complete: polling loop started")

    async def _on_shutdown(self, bot: Bot) -> None:
        del bot
        if self._processing_task is not None:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass

        await self.orchestrator.close()
        await self.store.close()
        self._bot = None
        log.info("shutdown complete")

    async def _move_to_wait_audio(self, chat_id: int, message: Message) -> None:
        await self.store.reset_to_wait_audio(chat_id)
        await message.answer(
            "Привет. Отправь трек аудио-файлом, и я соберу клип.",
            reply_markup=_kb([BTN_SEND_TRACK]),
        )
        await message.answer("Пришли аудио (audio/document).")

    async def _handle_wait_audio(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_SEND_TRACK:
            await message.answer("Жду аудио-файл.")
            return

        spec = _extract_audio_spec(message)
        if spec is None:
            await message.answer("Нужен аудио-файл. Нажми «Отправить трек» и пришли файл.")
            return

        if message.chat is None:
            return

        chat_id = int(message.chat.id)
        file_id, original_name = spec

        incoming_dir = self.settings.tmp_dir / str(chat_id) / "incoming"
        prepared_dir = self.settings.tmp_dir / str(chat_id) / "prepared"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        prepared_dir.mkdir(parents=True, exist_ok=True)

        src_name = f"{_now_tag()}_{uuid.uuid4().hex[:8]}_{_safe_name(original_name)}"
        src_path = incoming_dir / src_name

        try:
            await message.answer("Скачиваю файл и готовлю mp3…")
            tg_file = await message.bot.get_file(file_id)
            with open(src_path, "wb") as f:
                await message.bot.download_file(tg_file.file_path, destination=f)

            prep: AudioPrepareResult = await asyncio.to_thread(
                prepare_audio_best_effort,
                src=src_path,
                work_dir=prepared_dir,
                ffmpeg_bin=self.settings.ffmpeg_bin,
                max_audio_mb=self.settings.bot_max_audio_mb,
            )
        except Exception as e:
            await message.answer(f"Не удалось подготовить аудио: {e}")
            return

        st.pending_audio_file_id = file_id
        st.pending_audio_filename = _safe_name(original_name)
        st.prepared_audio_local_path = str(prep.output_path)
        st.lyrics_text = ""
        st.stage = STAGE_WAIT_LYRICS_CHOICE
        await self.store.set(st)

        size_mb = prep.size_bytes / (1024 * 1024)
        limit_note = "<= лимита" if prep.under_limit else "> лимита (best-effort)"
        await message.answer(
            f"Трек готов: mp3 {prep.bitrate}, {size_mb:.2f}MB ({limit_note}).\n"
            "Хочешь прислать текст песни для субтитров?",
            reply_markup=_kb([BTN_SEND_LYRICS, BTN_SKIP_LYRICS]),
        )

    async def _handle_wait_lyrics_choice(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text == BTN_SEND_LYRICS:
            st.stage = STAGE_WAIT_LYRICS_TEXT
            await self.store.set(st)
            await message.answer("Пришли текст песни одним или несколькими сообщениями (сохраняю последнее).")
            return

        if text == BTN_SKIP_LYRICS:
            st.lyrics_text = ""
            st.stage = STAGE_WAIT_CONFIRM
            await self.store.set(st)
            await message.answer("Запустить генерацию?", reply_markup=_kb([BTN_LAUNCH]))
            return

        await message.answer("Выбери кнопку: «Отправить текст» или «Не присылать текст».")

    async def _handle_wait_lyrics_text(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if not text:
            await message.answer("Жду текст песни сообщением.")
            return

        st.lyrics_text = text
        st.stage = STAGE_WAIT_CONFIRM
        await self.store.set(st)
        await message.answer("Текст получил. Запустить генерацию?", reply_markup=_kb([BTN_LAUNCH]))

    async def _handle_wait_confirm(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text != BTN_LAUNCH:
            await message.answer("Нажми «Запустить», когда будешь готов.")
            return

        if message.chat is None:
            return

        chat_id = int(message.chat.id)
        prepared_path = Path(st.prepared_audio_local_path).expanduser().resolve()
        if not prepared_path.exists():
            await message.answer("Подготовленный mp3 не найден. Пришли трек заново.")
            await self._move_to_wait_audio(chat_id, message)
            return

        key = self._build_raw_audio_key(chat_id=chat_id, file_name=prepared_path.name)
        try:
            await message.answer("Заливаю аудио в S3 и ставлю задачу в очередь…")
            audio_s3_url = await asyncio.to_thread(
                self.s3.upload_file,
                path=prepared_path,
                bucket=self.settings.s3_bucket_raw_audio,
                key=key,
                content_type="audio/mpeg",
            )

            idem = f"tg-{chat_id}-{uuid.uuid4().hex[:12]}"
            enqueue = await self.orchestrator.send_audio_s3(
                audio_s3_url=audio_s3_url,
                mode="with_gemini",
                lyrics_text=st.lyrics_text,
                idempotency_key=idem,
                project_id=None,
            )
            job_id = str(enqueue.get("job_id") or "").strip()
            if not job_id:
                raise RuntimeError(f"enqueue response has no job_id: {enqueue}")

            st.stage = STAGE_PROCESSING
            st.active_job_id = job_id
            st.active_job_started_at = time.time()
            st.last_status_msg_at = time.time()
            st.last_result_url = ""
            await self.store.set(st)

            await message.answer(
                f"Запустил. job_id={job_id}\n"
                "Трек в процессе, напишу когда видео будет готово."
            )
        except Exception as e:
            await message.answer(f"Не удалось запустить задачу: {e}")

    async def _handle_wait_next(self, message: Message, st: ChatState) -> None:
        text = str(message.text or "").strip()
        if text != BTN_NEXT:
            await message.answer("Если хочешь новый ролик, нажми «Сделать следующий».", reply_markup=_kb([BTN_NEXT]))
            return

        if message.chat is None:
            return

        await self._move_to_wait_audio(int(message.chat.id), message)

    def _build_raw_audio_key(self, *, chat_id: int, file_name: str) -> str:
        safe = _safe_name(file_name)
        return f"{self.settings.s3_raw_audio_prefix.strip('/')}/{chat_id}/{_now_tag()}_{uuid.uuid4().hex[:10]}_{safe}"

    async def _processing_loop(self) -> None:
        while True:
            try:
                states = await self.store.list_processing()
                for st in states:
                    try:
                        await self._process_chat_job(st)
                    except Exception as e:
                        log.warning("processing loop chat=%s err=%r", st.chat_id, e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("processing loop iteration error=%r", e)

            await asyncio.sleep(max(1.0, float(self.settings.bot_poll_interval_s)))

    async def _process_chat_job(self, st: ChatState) -> None:
        job_id = str(st.active_job_id or "").strip()
        if not job_id:
            st.stage = STAGE_WAIT_NEXT
            await self.store.set(st)
            return

        job = await self.orchestrator.get_job(job_id)
        status = str(job.get("status") or "").upper()

        if status not in {"SUCCEEDED", "FAILED"}:
            return

        bot = self._require_bot()

        if status == "FAILED":
            error_text = str(job.get("error") or "").strip()
            tail = error_text[-1000:] if error_text else ""
            await bot.send_message(
                st.chat_id,
                "Задача завершилась с ошибкой.\n"
                + (f"Детали: {tail}" if tail else "Без деталей."),
                reply_markup=_kb([BTN_NEXT]),
            )
            st.stage = STAGE_WAIT_NEXT
            st.active_job_id = ""
            st.active_job_started_at = 0.0
            await self.store.set(st)
            return

        source = _resolve_job_video_source(job, self.settings)
        if not source:
            await bot.send_message(
                st.chat_id,
                "Готово, но не нашёл ссылку на видео в ответе оркестратора.",
                reply_markup=_kb([BTN_NEXT]),
            )
            st.stage = STAGE_WAIT_NEXT
            st.active_job_id = ""
            st.active_job_started_at = 0.0
            await self.store.set(st)
            return

        st.last_result_url = source
        await self.store.set(st)

        video_path = self.settings.tmp_dir / str(st.chat_id) / "result" / f"{job_id}.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)

        file_sent = False
        send_file_error = ""
        archive_path: Path | None = None

        try:
            await self._download_result_video(source=source, dest=video_path)
            await bot.send_document(
                chat_id=st.chat_id,
                document=FSInputFile(str(video_path)),
                caption="Вот твой трек.",
            )
            file_sent = True
        except Exception as e:
            send_file_error = str(e)
            log.warning("send file failed chat=%s job=%s err=%s", st.chat_id, job_id, send_file_error)

        if not file_sent:
            fallback_link = await self._build_fallback_link(source)
            msg = "Не смог отправить файл видео."
            if fallback_link:
                msg += f"\nСсылка: {fallback_link}"
            if send_file_error:
                msg += f"\nОшибка: {send_file_error}"
            await bot.send_message(st.chat_id, msg)

        if self.settings.tg_send_project_archive:
            archive_source = _resolve_job_project_archive_source(job)
            if archive_source:
                archive_suffix = _archive_suffix_from_source(archive_source)
                archive_path = self.settings.tmp_dir / str(st.chat_id) / "result" / f"{job_id}_project{archive_suffix}"
                archive_sent = False
                archive_error = ""
                try:
                    await self._download_result_video(source=archive_source, dest=archive_path)
                    await bot.send_document(
                        chat_id=st.chat_id,
                        document=FSInputFile(str(archive_path)),
                        caption="Архив проекта (AEP + ресурсы).",
                    )
                    archive_sent = True
                except Exception as e:
                    archive_error = str(e)
                    log.warning("send archive failed chat=%s job=%s err=%s", st.chat_id, job_id, archive_error)

                if not archive_sent:
                    archive_link = await self._build_fallback_link(archive_source)
                    msg = "Не смог отправить архив проекта."
                    if archive_link:
                        msg += f"\nСсылка: {archive_link}"
                    if archive_error:
                        msg += f"\nОшибка: {archive_error}"
                    await bot.send_message(st.chat_id, msg)
            else:
                await bot.send_message(
                    st.chat_id,
                    "Видео готово, но ссылка на архив проекта в ответе рендера не найдена.",
                )

        await bot.send_message(
            st.chat_id,
            "Сделать следующий?",
            reply_markup=_kb([BTN_NEXT]),
        )

        st.stage = STAGE_WAIT_NEXT
        st.active_job_id = ""
        st.active_job_started_at = 0.0
        st.prepared_audio_local_path = ""
        st.pending_audio_file_id = ""
        st.pending_audio_filename = ""
        st.lyrics_text = ""
        await self.store.set(st)

        try:
            if video_path.exists():
                video_path.unlink()
        except Exception:
            pass
        try:
            if archive_path is not None and archive_path.exists():
                archive_path.unlink()
        except Exception:
            pass

    async def _download_result_video(self, *, source: str, dest: Path) -> None:
        src = str(source or "").strip()
        if not src:
            raise RuntimeError("empty output source")

        if src.startswith("s3://"):
            await asyncio.to_thread(self.s3.download_s3_url, s3_url=src, dest=dest)
            return

        if src.startswith("http://") or src.startswith("https://"):
            await self._download_http(url=src, dest=dest)
            return

        raise RuntimeError(f"unsupported output source: {src!r}")

    async def _download_http(self, *, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        timeout = httpx.Timeout(600.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 300:
                    raise RuntimeError(f"http download failed status={resp.status_code} url={url}")
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

    async def _build_fallback_link(self, source: str) -> str:
        src = str(source or "").strip()
        if not src:
            return ""
        if src.startswith("http://") or src.startswith("https://"):
            return src
        if src.startswith("s3://"):
            try:
                return await asyncio.to_thread(self.s3.generate_presigned_for_s3_url, s3_url=src, expires_s=None)
            except Exception:
                return src
        return src

    def _require_bot(self) -> Bot:
        if self._bot is None:
            raise RuntimeError("bot instance is not ready")
        return self._bot

    async def run(self) -> None:
        bot = Bot(token=self.settings.tg_bot_token)
        await self.dp.start_polling(bot)


def main() -> None:
    app = BlastBotApp(SETTINGS)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
