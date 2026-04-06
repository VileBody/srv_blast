"""Точка входа — запуск бота с вебхук-сервером и планировщиком."""

import asyncio
import logging
import signal
import sys
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import TELEGRAM_BOT_TOKEN, OWNER_TG_ID, WEBHOOK_HOST, WEBHOOK_PORT, LOG_FILE
from db import init_db, distribute_income
from handlers import router
from scheduler import setup_scheduler

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ── Вебхук-эндпоинт для входящих доходов ──

async def webhook_income(request: web.Request) -> web.Response:
    """POST /webhook/income — приём дохода из внешних систем.
    JSON: {"amount": 4000, "source": "blast", "client": "имя"}
    """
    try:
        data = await request.json()
        amount = int(data.get("amount", 0))
        source = data.get("source", "webhook")
        client = data.get("client", "")

        if amount <= 0:
            return web.json_response({"error": "amount must be > 0"}, status=400)

        note = f"{source}" + (f" ({client})" if client else "")
        distribution = await distribute_income(amount, note)

        # Уведомить в Telegram
        bot: Bot = request.app["bot"]
        lines = "\n".join(f"  {k}: {v}₽" for k, v in distribution.items())
        await bot.send_message(
            OWNER_TG_ID,
            f"💰 Входящий доход (webhook): {amount}₽\nИсточник: {note}\n\nРаспределение:\n{lines}",
        )

        logger.info(f"Webhook income: {amount}₽ от {note}")
        return web.json_response({"ok": True, "distribution": distribution})

    except Exception as e:
        logger.error(f"Webhook income ошибка: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def main():
    # Инициализация БД
    is_first_run = await init_db()

    # Бот
    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher()
    dp.include_router(router)

    # Планировщик
    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Планировщик запущен")

    # Вебхук-сервер (aiohttp)
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/webhook/income", webhook_income)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEBHOOK_HOST, WEBHOOK_PORT)
    await site.start()
    logger.info(f"Webhook-сервер запущен на {WEBHOOK_HOST}:{WEBHOOK_PORT}")

    # Приветствие при первом запуске
    if is_first_run:
        try:
            await bot.send_message(
                OWNER_TG_ID,
                "🚀 Бот запущен! Начальные данные загружены.\nНапиши /start для статуса.",
            )
        except Exception as e:
            logger.error(f"Не удалось отправить приветствие: {e}")

    # Graceful shutdown
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Получен сигнал завершения")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler
            pass

    # Запуск polling в фоне
    polling_task = asyncio.create_task(dp.start_polling(bot))
    logger.info("Бот запущен — polling активен")

    try:
        # На Windows используем KeyboardInterrupt вместо сигналов
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Завершение по KeyboardInterrupt")
    finally:
        logger.info("Остановка бота...")
        scheduler.shutdown(wait=False)
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
        await runner.cleanup()
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
