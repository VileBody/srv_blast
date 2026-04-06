"""Клиент для Grok API (xAI)."""

import json
import logging
import os
import aiohttp

from config import GROK_API_KEY, GROK_API_URL, GROK_MODEL

logger = logging.getLogger(__name__)

# Путь к файлу с промптом
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "grok_prompt.txt")


def _load_prompt() -> str:
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


async def parse_expenses(user_text: str, weekly_budget: int, spent_this_week: int) -> list[dict]:
    """
    Отправить текст пользователя в Grok для парсинга трат.
    Возвращает список: [{"amount": 2000, "category": "еда", "note": "продукты"}, ...]
    """
    remaining = weekly_budget - spent_this_week
    prompt_template = _load_prompt()
    prompt = prompt_template.format(
        weekly_budget=weekly_budget,
        spent_this_week=spent_this_week,
        remaining_budget=remaining,
        user_text=user_text,
    )

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

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GROK_API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Grok API ошибка {resp.status}: {error_text}")
                    return []

                data = await resp.json()
                content = data["choices"][0]["message"]["content"].strip()

                # Убираем возможные markdown-обёртки
                if content.startswith("```"):
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()

                result = json.loads(content)
                expenses = result.get("expenses", [])

                # Валидация
                validated = []
                for exp in expenses:
                    if isinstance(exp.get("amount"), (int, float)) and exp["amount"] > 0:
                        validated.append({
                            "amount": int(exp["amount"]),
                            "category": str(exp.get("category", "другое")),
                            "note": str(exp.get("note", ""))[:100],
                        })

                logger.info(f"Grok распарсил {len(validated)} трат из текста: {user_text[:50]}")
                return validated

    except json.JSONDecodeError as e:
        logger.error(f"Grok вернул невалидный JSON: {e}")
        return []
    except Exception as e:
        logger.error(f"Ошибка при обращении к Grok: {e}")
        return []


async def generate_weekly_summary(
    income_total: int,
    expense_total: int,
    expenses_by_category: dict[str, int],
    envelopes: list[dict],
    debts: list[dict],
) -> str:
    """Попросить Grok сгенерировать рекомендацию к недельной сводке."""
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
            async with session.post(GROK_API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Ошибка генерации недельной сводки: {e}")
        return ""
