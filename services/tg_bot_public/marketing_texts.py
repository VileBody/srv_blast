"""Marketing copy for the public bot — survey, bridge lines, methodology, limits.

Everything user-visible for the post-generation funnel and the free-tier version
picker lives here so marketing can rewrite the wording without touching handler
logic in app.py. The module holds NO I/O and NO aiogram imports — it is pure
data plus tiny pure helpers, which also makes it cheap to unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


# --------------------------------------------------------------------------
# Post-generation survey (first generation only)
# --------------------------------------------------------------------------

# Callback prefix for the inline quick-answer buttons: "pgsurvey:<qid>:<aid>".
# Telegram caps callback_data at 64 bytes — keep qid/aid short and ASCII.
SURVEY_CB_PREFIX = "pgsurvey:"

SURVEY_FIRST_QUESTION_ID = "q1"


@dataclass(frozen=True)
class SurveyOption:
    id: str
    label: str


@dataclass(frozen=True)
class SurveyQuestion:
    id: str
    text: str
    options: Tuple[SurveyOption, ...]
    # Per-answer routing. An answer missing here falls back to `next_default`;
    # an empty next id means "survey finished".
    next_by_answer: Dict[str, str]
    next_default: str


SURVEY_QUESTIONS: Dict[str, SurveyQuestion] = {
    "q1": SurveyQuestion(
        id="q1",
        text="Сколько роликов в месяц у тебя выходит в TikTok?",
        options=(
            SurveyOption("none", "Не выкладываю совсем"),
            SurveyOption("1_10", "1–10"),
            SurveyOption("10_30", "10–30"),
            SurveyOption("30_plus", "30+"),
        ),
        next_by_answer={},
        next_default="q2",
    ),
    "q2": SurveyQuestion(
        id="q2",
        text="Монтируешь как — сам, с чьей-то помощью, или пока вообще не монтируешь?",
        options=(
            SurveyOption("self", "Сам"),
            SurveyOption("helper", "С помощью монтажёра/сервиса"),
            SurveyOption("no_edit", "Не монтирую — ролики не делаю"),
        ),
        # "Сам" → time question, "С помощью" → money question,
        # "Не монтирую" skips both and goes straight to Q3.
        next_by_answer={"self": "q2a", "helper": "q2b", "no_edit": "q3"},
        next_default="q3",
    ),
    "q2a": SurveyQuestion(
        id="q2a",
        text="Сколько времени уходит на один ролик, от исходника до публикации?",
        options=(
            SurveyOption("lt_hour", "Меньше часа"),
            SurveyOption("1_3h", "1–3 часа"),
            SurveyOption("half_day", "Полдня и больше"),
            SurveyOption("a_lot", "Не считал, но точно много"),
        ),
        next_by_answer={},
        next_default="q3",
    ),
    "q2b": SurveyQuestion(
        id="q2b",
        text="Сколько в среднем уходит денег в месяц?",
        options=(
            SurveyOption("zero", "Ничего не трачу"),
            SurveyOption("2_5k", "2 000–5 000₽"),
            SurveyOption("5_10k", "5 000–10 000₽"),
            SurveyOption("10k_plus", "10 000₽+"),
        ),
        next_by_answer={},
        next_default="q3",
    ),
    "q3": SurveyQuestion(
        id="q3",
        text="Что сильнее всего мешает выкладывать чаще?",
        options=(
            SurveyOption("time", "Не хватает времени"),
            SurveyOption("money", "Не хватает денег на монтаж"),
            SurveyOption("ideas", "Не хватает идей/вариантов подачи"),
            SurveyOption("meaning", "В целом не вижу смысла"),
        ),
        # Terminal question — the answer picks the bridge branch below.
        next_by_answer={},
        next_default="",
    ),
}

# Q2 answer → stored branch label (the fork the user took), for segmentation.
SURVEY_Q2_BRANCH_BY_ANSWER: Dict[str, str] = {
    "self": "self",
    "helper": "helper",
    "no_edit": "no_edit",
}

# Q3 answer → bridge branch. Same ids, kept explicit so the copy branch and the
# raw answer id can diverge later without touching handler code.
SURVEY_Q3_BRANCH_BY_ANSWER: Dict[str, str] = {
    "time": "time",
    "money": "money",
    "ideas": "ideas",
    "meaning": "meaning",
}


# --------------------------------------------------------------------------
# Bridge message (sent right after Q3, no buttons)
# --------------------------------------------------------------------------

BRIDGE_TEXT_BY_BRANCH: Dict[str, str] = {
    "time": (
        "Артисты, которые сейчас растут в стримах, выкладывают 1–2 ролика в день.\n"
        "Не потому что сидят в монтаже часами — а потому что нашли способ делать\n"
        "это быстро. Держи — как это устроено 👇"
    ),
    "money": (
        "Артисты, которые сейчас растут в стримах, выкладывают 1–2 ролика в день.\n"
        "Не потому что тратят на монтажёра десятки тысяч — а потому что нашли способ\n"
        "делать это дёшево. Держи — как это устроено 👇"
    ),
    "ideas": (
        "Дело не в нехватке идей — один и тот же трек можно упаковать в десятки\n"
        "разных роликов, и система сама найдёт, какой из них выстрелит. Вот как\n"
        "это работает 👇"
    ),
    "meaning": (
        "Смысл появляется не с одного ролика, а когда система успевает набрать\n"
        "статистику. Вот три примера, где это сработало 👇"
    ),
}

# Fallback used only if a branch id ever falls out of the map above — the
# methodology must still reach the user.
BRIDGE_TEXT_DEFAULT = BRIDGE_TEXT_BY_BRANCH["time"]


def bridge_text_for_branch(branch: str) -> str:
    return BRIDGE_TEXT_BY_BRANCH.get(str(branch or ""), BRIDGE_TEXT_DEFAULT)


# Closing line when the survey is finished but the bridge + methodology are not
# due — i.e. the user became a paying client while answering.
SURVEY_THANKS = "Спасибо — учтём это, когда будем докручивать сервис 🙌"


# --------------------------------------------------------------------------
# Methodology document
# --------------------------------------------------------------------------

# Already uploaded to Telegram — sent by file_id, never re-uploaded from disk.
METHODOLOGY_FILE_ID = (
    "BQACAgIAAxkBAAECeVhqclv0ETyVJrrzHMKlXzJS8AvbaAACZaMAArDMkEuxB7oCr9RDTj0E"
)


# --------------------------------------------------------------------------
# Free-tier version picker (Задача 3)
# --------------------------------------------------------------------------

VERSION_CHOICE_BUTTONS = ["1", "2", "3", "4", "5"]

BTN_VERSIONS_WARN_CONTINUE = "Продолжить"
BTN_VERSIONS_WARN_CHANGE = "Изменить количество"

VERSIONS_PROMPT = "Сколько версий сгенерировать?"
VERSIONS_PROMPT_FREE_SUFFIX = (
    "\n\nВ бесплатном тарифе всего {limit} генераций — каждая версия тратит одну."
)
VERSIONS_INVALID = "Выбери количество версий: 1, 2, 3, 4 или 5."
VERSIONS_WARN_INVALID = (
    "Выбери кнопкой: «{cont}» или «{change}»."
).format(cont=BTN_VERSIONS_WARN_CONTINUE, change=BTN_VERSIONS_WARN_CHANGE)

# Warn once the pick eats at least this share of the free tier. At limit=5 that
# is 4 (80%) and 5 (100%) — exactly the two cases in the spec — and the rule
# keeps holding if the free limit is ever retuned.
VERSIONS_WARN_THRESHOLD_PCT = 80

VERSIONS_WARN_PARTIAL = "⚠️ Это использует {pct}% твоего бесплатного лимита. Продолжить?"
VERSIONS_WARN_FULL = "⚠️ Это использует весь бесплатный лимит (100%). Продолжить?"


def versions_warning_text(versions: int, free_limit: int) -> Optional[str]:
    """Warning for a free-tier version pick, or None when no warning is due.

    `free_limit` is the tariff's free generation quota (INITIAL_CREDITS), not
    the user's current balance — the spec ties the percentages to the tariff.
    """
    n = int(versions)
    limit = int(free_limit)
    if n <= 0 or limit <= 0:
        return None
    pct = int(round(100.0 * n / limit))
    if pct < VERSIONS_WARN_THRESHOLD_PCT:
        return None
    if pct >= 100:
        return VERSIONS_WARN_FULL
    return VERSIONS_WARN_PARTIAL.format(pct=pct)
