"""Парсинг трат: локальный regex + Groq API как фолбэк."""

import json
import logging
import os
import re
import aiohttp

from config import GROK_API_KEY, GROK_API_URL, GROK_MODEL, GROQ_PROXY

logger = logging.getLogger(__name__)

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "grok_prompt.txt")

# Маппинг ключевых слов → категория
CATEGORY_KEYWORDS = {
    "еда": "еда", "еду": "еда", "продукты": "еда", "продукт": "еда",
    "обед": "еда", "ужин": "еда", "завтрак": "еда", "перекус": "еда",
    "ресторан": "еда", "кафе": "еда", "столовая": "еда", "доставка еды": "еда",
    "транспорт": "транспорт", "такси": "транспорт", "метро": "транспорт",
    "бензин": "транспорт", "заправк": "транспорт", "автобус": "транспорт",
    "машин": "транспорт", "парковк": "транспорт",
    "кофе": "кофе", "кофейн": "кофе",
    "развлечени": "развлечения", "кино": "развлечения", "бар": "развлечения",
    "клуб": "развлечения", "игр": "развлечения", "концерт": "развлечения",
    "здоровь": "здоровье", "аптек": "здоровье", "врач": "здоровье",
    "лекарств": "здоровье", "больниц": "здоровье", "стомат": "здоровье",
    "подар": "подарки",
    "одежд": "одежда", "обувь": "одежда", "шмотк": "одежда",
    "подписк": "подписки", "spotify": "подписки", "youtube": "подписки",
    "связь": "связь", "телефон": "связь", "интернет": "связь", "мобильн": "связь",
    "быт": "бытовое", "хозтовар": "бытовое", "уборк": "бытовое", "дом": "бытовое",
}


def _detect_category(text: str) -> str:
    """Определить категорию по ключевым словам в тексте."""
    text_lower = text.lower()
    for keyword, category in CATEGORY_KEYWORDS.items():
        if keyword in text_lower:
            return category
    return "другое"


def _parse_amount(s: str) -> int | None:
    """Парсит сумму: '5к' → 5000, '2.5к' → 2500, '1500' → 1500."""
    s = s.strip().replace(" ", "")
    m = re.match(r'^(\d+(?:[.,]\d+)?)\s*[кkКK]$', s)
    if m:
        return int(float(m.group(1).replace(",", ".")) * 1000)
    m = re.match(r'^(\d+(?:[.,]\d+)?)$', s)
    if m:
        return int(float(m.group(1).replace(",", ".")))
    return None


def parse_expenses_local(user_text: str) -> list[dict]:
    """
    Локальный парсер трат из свободного текста.
    Поддерживает форматы:
      - "1000 на еду, 2500 на заправку машины, 1500 на развлечения"
      - "я потратил 5к: 1к на еду, 2.5к на заправку"
      - "еда 1000, транспорт 500"
      - "1000 еда, 500 транспорт"
    """
    text = user_text.strip()
    # Убираем вводные фразы
    text = re.sub(r'^(я\s+)?(потратил[аи]?|тратил[аи]?|расход[ыа]?|списал[аи]?)\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^[\d.,]+\s*[кkКK]?\s*[:;—–-]\s*', '', text)  # "5к: ..." → "..."

    expenses = []

    # Паттерн 1: "СУММА на/за ОПИСАНИЕ" или "СУММА КАТЕГОРИЯ"
    # Разделяем по запятым, точкам с запятой, "и", переносам строк
    parts = re.split(r'[,;.\n]+|\s+и\s+', text)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # "1000 на еду" / "2.5к на заправку машины"
        m = re.match(r'(\d+(?:[.,]\d+)?)\s*[кkКK]?\s+(?:на|за)\s+(.+)', part, re.IGNORECASE)
        if m:
            amount = _parse_amount(m.group(1) + ('к' if re.search(r'[кkКK]', part[len(m.group(1)):len(m.group(1))+2]) else ''))
            # Re-parse more carefully
            amount_str = re.match(r'(\d+(?:[.,]\d+)?\s*[кkКK]?)', part).group(1)
            amount = _parse_amount(amount_str)
            if amount and amount > 0:
                note = m.group(2).strip()[:100]
                expenses.append({"amount": amount, "category": _detect_category(note), "note": note})
            continue

        # "1000 еда" / "2.5к транспорт"
        m = re.match(r'(\d+(?:[.,]\d+)?)\s*[кkКK]?\s+(.+)', part, re.IGNORECASE)
        if m:
            amount_str = re.match(r'(\d+(?:[.,]\d+)?\s*[кkКK]?)', part).group(1)
            amount = _parse_amount(amount_str)
            if amount and amount > 0:
                note = m.group(2).strip()[:100]
                expenses.append({"amount": amount, "category": _detect_category(note), "note": note})
            continue

        # "еда 1000" / "транспорт 2.5к"
        m = re.match(r'(.+?)\s+(\d+(?:[.,]\d+)?)\s*[кkКK]?\s*$', part, re.IGNORECASE)
        if m:
            amount_str = re.search(r'(\d+(?:[.,]\d+)?\s*[кkКK]?)\s*$', part).group(1)
            amount = _parse_amount(amount_str)
            if amount and amount > 0:
                note = m.group(1).strip()[:100]
                expenses.append({"amount": amount, "category": _detect_category(note), "note": note})
            continue

        # Просто число — одна трата без категории
        amount = _parse_amount(part)
        if amount and amount > 0:
            expenses.append({"amount": amount, "category": "другое", "note": ""})

    return expenses


async def parse_expenses(user_text: str, weekly_budget: int, spent_this_week: int) -> list[dict]:
    """Парсинг трат: сначала локально, если не получилось — через Groq API."""
    # Если текст типа "ничего", "0", "нет" — пустой список
    clean = user_text.strip().lower()
    if clean in ("ничего", "нет", "0", "ничего не тратил", "ничего не тратила", "-"):
        return []

    # Пробуем локальный парсер
    result = parse_expenses_local(user_text)
    if result:
        logger.info(f"Локальный парсер: {len(result)} трат из '{user_text[:50]}'")
        return result

    # Фолбэк на Groq API
    logger.info(f"Локальный парсер не справился, пробуем Groq API: '{user_text[:50]}'")
    try:
        return await _parse_expenses_api(user_text, weekly_budget, spent_this_week)
    except Exception as e:
        logger.warning(f"Groq API недоступен: {e}")
        return []


async def _parse_expenses_api(user_text: str, weekly_budget: int, spent_this_week: int) -> list[dict]:
    """Отправить текст в Groq API для парсинга."""
    remaining = weekly_budget - spent_this_week

    def _load_prompt() -> str:
        with open(PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read()

    prompt_template = _load_prompt()
    prompt = (prompt_template
              .replace("{weekly_budget}", str(weekly_budget))
              .replace("{spent_this_week}", str(spent_this_week))
              .replace("{remaining_budget}", str(remaining))
              .replace("{user_text}", user_text))

    payload = {
        "model": GROK_MODEL,
        "messages": [
            {"role": "system", "content": "Ты финансовый парсер. Отвечай ТОЛЬКО валидным JSON, без markdown."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1000,
    }

    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(GROK_API_URL, json=payload, headers=headers, proxy=GROQ_PROXY, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"Groq API ошибка {resp.status}: {error_text[:200]}")
                return []

            data = await resp.json()
            content = data["choices"][0]["message"]["content"].strip()

            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

            result = json.loads(content)
            expenses = result.get("expenses", [])

            validated = []
            for exp in expenses:
                if isinstance(exp.get("amount"), (int, float)) and exp["amount"] > 0:
                    validated.append({
                        "amount": int(exp["amount"]),
                        "category": str(exp.get("category", "другое")),
                        "note": str(exp.get("note", ""))[:100],
                    })

            return validated


async def generate_weekly_summary(
    income_total: int,
    expense_total: int,
    expenses_by_category: dict[str, int],
    envelopes: list[dict],
    debts: list[dict],
) -> str:
    """Попросить Groq сгенерировать рекомендацию к недельной сводке."""
    cat_str = ", ".join(f"{k} {v}₽" for k, v in expenses_by_category.items())
    env_str = ", ".join(f"{e['name']} {e['balance']}₽" for e in envelopes)
    debt_str = ", ".join(f"{d['name']} {d['amount']}₽" for d in debts)
    summary_text = (
        f"Доход за неделю: {income_total}₽\n"
        f"Расходы за неделю: {expense_total}₽\n"
        f"По категориям: {cat_str}\n"
        f"Конверты: {env_str}\n"
        f"Долги: {debt_str}\n"
    )

    payload = {
        "model": GROK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — финансовый советник. Дай краткую рекомендацию (2-3 предложения) "
                    "на основе недельной финансовой сводки пользователя. "
                    "Будь конкретным, дружелюбным, на русском языке. Обращайся на ты."
                ),
            },
            {"role": "user", "content": summary_text},
        ],
        "temperature": 0.7,
        "max_tokens": 500,
    }

    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GROK_API_URL, json=payload, headers=headers, proxy=GROQ_PROXY, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Ошибка генерации недельной сводки: {e}")
        return ""
