"""Static + rendered copy for the season flow.

Long strings are kept here so handlers stay slim. Templates accept a
`PhaseSnapshot` plus user-state fields and return ready-to-send text.
"""
from __future__ import annotations

from typing import Iterable, Optional

from core.season_phase import PhaseSnapshot, SeasonPhase


# --------------------------------------------------------------------------- #
# Onboarding (3 intro messages + consent + welcome)
# --------------------------------------------------------------------------- #

INTRO_1 = (
    "Почему 99% видео под треки артистов никогда не залетают в рекомендации?\n\n"
    "Мы задались этим вопросом — и создали <b>Blast</b>, co-pilot для продвижения "
    "музыки. Это AI-агент на базе Telegram-бота: помогает артистам создавать "
    "контент — генерировать идеи и видео с нуля.\n\n"
    "Загружаешь трек → выбираешь параметры → получаешь готовый ролик для "
    "TT, Reels, Shorts.\n\n"
    "Дальше расскажу, что мы делаем прямо сейчас."
)

INTRO_2 = (
    "Мы задались целью разобраться, как работает вирусный контент, и найти "
    "формулу залетевшего ролика.\n\n"
    "В формуле участвует огромное количество факторов: от структуры ролика "
    "до VPN, через который ты постишь. Поэтому мы разбиваем её на сезоны "
    "и копаем по одной части за раз.\n\n"
    "<b>Сезон работает так:</b> 4 недели мы показываем прогресс в разработке "
    "и соц-сетях, потом 2 недели вы тестите результат на своих треках.\n\n"
    "Прямо сейчас начинаем разбирать <b>хук</b>. 21 формат, лучшие 5-7 попадут "
    "в бота через ~3 недели. Когда появятся — у тебя 2 бесплатные генерации "
    "в окне, чтобы протестить на своём треке."
)

CONSENT = (
    "Хочешь получать апдейты по сезону?\n\n"
    "🔥 <b>Да, всё</b> — тесты, разборы, открытие окна. Плюс первым узнаёшь, "
    "как получить генерации <b>ещё до окна</b> — через рефералку, бета-доступ, "
    "спецофферы.\n\n"
    "✨ <b>Только финалы</b> — итоги сезонов и открытие окон, ничего лишнего."
)


def WELCOME(snap: PhaseSnapshot) -> str:
    days = snap.days_until_window
    if days > 0:
        when = f"Через {days} {_plural_days(days)} окно общей генерации"
    else:
        when = "Окно общей генерации открывается"
    return (
        f"Готово. Это твоё меню — здесь вся информация о сезоне, генерация "
        f"для клиентов, тарифы, примеры. {when} — обязательно отпишу."
    )


# --------------------------------------------------------------------------- #
# Menu screens
# --------------------------------------------------------------------------- #

MENU_HEADER = "Меню сезона"


def render_generation_screen(snap: PhaseSnapshot, *, account_status: str) -> str:
    """Phase-aware generation screen for free / churned users.

    Paid_active users never see this screen — they go through the legacy flow.
    """
    days = snap.days_until_window
    hours = snap.hours_until_window

    if snap.phase in (SeasonPhase.DEV_EARLY, SeasonPhase.DEV_LATE):
        return (
            f"Окно открывается через {days} {_plural_days(days)}.\n\n"
            "Сейчас тестируем формулу хука — разбираем 21 формат, в окно "
            "попадут лучшие 5-7. Хочешь оказаться в waitlist'е?"
        )
    if snap.phase == SeasonPhase.PRE_LAUNCH:
        return (
            f"Окно открывается через {days} {_plural_days(days)}.\n\n"
            "Реферал = ранний доступ. Поделись ссылкой с друзьями — за "
            "приглашённого получишь доступ к окну раньше всех."
        )
    if snap.phase == SeasonPhase.WINDOW_OPEN:
        if account_status == "exhausted_free":
            return (
                "Лимит бесплатных генераций исчерпан 🚫\n\n"
                "Можешь получить +1 генерацию за каждого приглашённого друга — "
                "или купить тариф, чтобы снять лимит."
            )
        return (
            "Окно открыто 🎉\n\n"
            "У тебя 2 бесплатные генерации в этом сезоне. "
            "Загрузи трек — и я соберу ролик с хуком."
        )
    if snap.phase == SeasonPhase.WINDOW_CLOSING:
        when = f"{hours}ч" if hours > 0 else "скоро"
        return (
            f"Окно закрывается через {when} ⏳\n\n"
            "Успей загрузить трек — или забери тариф со спецоффером, "
            "чтобы остаться в безлимите."
        )
    return "Окно скоро откроется — следи за апдейтами."


def render_about_season(snap: PhaseSnapshot, *, tt_link: str = "", tg_link: str = "") -> str:
    lines = [
        f"<b>Сезон №{snap.season_number} · {snap.season_theme}</b>",
        f"Неделя {snap.week} из 6 · фаза {snap.phase_label}",
    ]
    if snap.next_window_at > 0:
        days = snap.days_until_window
        if days > 0:
            lines.append(f"Открытие окна: через {days} {_plural_days(days)}")
        else:
            lines.append("Открытие окна: уже скоро")
    if tt_link:
        lines.append(f"\nTT: {tt_link}")
    if tg_link:
        lines.append(f"TG: {tg_link}")
    return "\n".join(lines)


def render_invite_screen(
    *,
    referral_link: str,
    referrals_count: int,
    tier: int,
) -> str:
    progress = _progress_bar(referrals_count, target=5)
    return (
        f"Твоя ссылка: <code>{referral_link}</code>\n\n"
        f"Прогресс: {progress} {referrals_count}/5\n\n"
        f"<b>Бонусы по тирам:</b>\n"
        f"1 друг → ранний доступ за 24ч + 1 бонусная генерация\n"
        f"3 друга → за 48ч + 2 генерации + showcase priority\n"
        f"5 друзей → за 3 дня + 3 генерации + гарантированный спот в showcase\n\n"
        f"Текущий тир: <b>{tier}</b>"
    )


def render_pricing_screen(*, tariff_lines: Iterable[str] = ()) -> str:
    lines = list(tariff_lines)
    body = "\n".join(lines) if lines else "Текущие тарифы будут доступны здесь."
    return (
        f"<b>Текущие тарифы:</b>\n\n{body}\n\n"
        "В окне платный = безлимит на новую фичу + ранний доступ за 24-48ч."
    )


def render_examples_screen(*, brand_account_link: str = "") -> str:
    footer = f"\n\nВсе примеры: {brand_account_link}" if brand_account_link else ""
    return (
        "Последние работы скоро появятся здесь — следи за апдейтами по сезону."
        f"{footer}"
    )


def render_history_screen(*, total_gens: int) -> str:
    if total_gens <= 0:
        return (
            "Пока ни одной генерации. Когда окно откроется — твои ролики "
            "появятся здесь."
        )
    return f"У тебя {total_gens} {_plural_gens(total_gens)} в этом сезоне."


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _plural_days(n: int) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "дня"
    return "дней"


def _plural_gens(n: int) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "генерация"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "генерации"
    return "генераций"


def _progress_bar(value: int, *, target: int, width: int = 5) -> str:
    if target <= 0:
        return ""
    filled = min(width, int(round(width * value / target)))
    return "▰" * filled + "▱" * (width - filled)
