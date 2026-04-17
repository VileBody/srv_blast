"""Конфигурация бота."""

import os

TELEGRAM_BOT_TOKEN = os.environ.get("FINANCE_BOT_TOKEN", "8782530243:AAEodzMW1H_Y4C49WwyVc04C0poy5JaoeX4")
GROK_API_KEY = os.environ.get("GROK_API_KEY", "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")
OWNER_TG_ID = int(os.environ.get("OWNER_TG_ID", "975769043"))

# Правила распределения дохода по конвертам (%)
ENVELOPE_RULES = {
    "debts": 50,
    "operations": 20,
    "personal": 15,
    "savings": 15,
}

# Расписание уведомлений
DAILY_PING_HOUR = 23
DAILY_PING_MINUTE = 0
WEEKLY_SUMMARY_DAY = 6  # воскресенье (0=пн, 6=вс)

# Часовой пояс
TIMEZONE = "Europe/Moscow"

# Groq API
GROK_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROK_MODEL = "llama-3.1-8b-instant"
GROQ_PROXY = os.environ.get("GROQ_PROXY", "http://xvGVE8:tQGDZo@177.234.140.100:8000")

# Telegram Bot API proxy (optional, separate from GROQ_PROXY)
TELEGRAM_PROXY = os.environ.get("TELEGRAM_PROXY", "")

# Вебхук-сервер
WEBHOOK_HOST = "0.0.0.0"
WEBHOOK_PORT = 8080

# Логирование
LOG_FILE = "bot.log"

# БД (в Docker монтируется как volume)
DB_PATH = os.environ.get("DB_PATH", "finance.db")

# Начальные долги
INITIAL_DEBTS = [
    {"name": "Кредитка", "amount": 150000, "rate": 60.0, "min_payment": 7700, "deadline_day": 22},
    {"name": "Друг", "amount": 90000, "rate": 0.0, "min_payment": 0, "deadline_day": None},
    {"name": "Кирилл", "amount": 80000, "rate": 0.0, "min_payment": 0, "deadline_day": None},
]

# Названия конвертов на русском
ENVELOPE_NAMES = {
    "debts": "Долги",
    "operations": "Операционка",
    "personal": "Личные",
    "savings": "Подушка",
}
