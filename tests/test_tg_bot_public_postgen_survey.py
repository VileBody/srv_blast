"""Post-generation survey + methodology delivery (public bot)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from services.tg_bot_public import app as public_app
from services.tg_bot_public.marketing_texts import (
    BRIDGE_TEXT_BY_BRANCH,
    METHODOLOGY_FILE_ID,
    SURVEY_CB_PREFIX,
    SURVEY_QUESTIONS,
    SURVEY_THANKS,
)
from services.tg_bot_public.state_store import ChatState


class _Credits:
    """In-memory stand-in for the survey/activity tables."""

    def __init__(self, *, generations_started: int = 1, paid: bool = False) -> None:
        self.generations_started = int(generations_started)
        self.paid = bool(paid)
        self.survey: dict[int, dict] = {}
        self.saved_calls: list[dict] = []

    async def has_paid(self, _tg_id: int) -> bool:
        return self.paid

    async def count_events(self, _tg_id: int, event: str) -> int:
        return self.generations_started if event == "generation_started" else 0

    async def get_survey_response(self, tg_id: int):
        return self.survey.get(int(tg_id))

    async def save_survey_answer(
        self,
        tg_id: int,
        *,
        question_id: str,
        answer_id: str,
        answer_label: str,
        branch_q2: str = "",
        branch_q3: str = "",
        completed: bool = False,
    ) -> None:
        self.saved_calls.append({
            "tg_id": int(tg_id),
            "question_id": question_id,
            "answer_id": answer_id,
            "answer_label": answer_label,
            "branch_q2": branch_q2,
            "branch_q3": branch_q3,
            "completed": completed,
        })
        row = self.survey.setdefault(
            int(tg_id), {"answers": {}, "branch_q2": "", "branch_q3": "", "completed_at": ""}
        )
        row["answers"][question_id] = {"id": answer_id, "label": answer_label}
        if branch_q2:
            row["branch_q2"] = branch_q2
        if branch_q3:
            row["branch_q3"] = branch_q3


class _Message:
    def __init__(self, *, chat_id: int = 7, text: str = "") -> None:
        self.text = text
        self.chat = SimpleNamespace(id=chat_id)
        self.answers: list[str] = []
        self.markups: list[object] = []
        self.documents: list[str] = []
        self.edits: list[str] = []

    async def answer(self, text: str = "", **kwargs) -> None:
        self.answers.append(str(text))
        self.markups.append(kwargs.get("reply_markup"))

    async def answer_document(self, document: str, **_kwargs) -> None:
        self.documents.append(str(document))

    async def edit_text(self, text: str, **_kwargs) -> None:
        self.edits.append(str(text))


class _Callback:
    def __init__(self, data: str, message: _Message) -> None:
        self.data = data
        self.message = message
        self.acks: list[str] = []

    async def answer(self, text: str = "", **_kwargs) -> None:
        self.acks.append(str(text))


def _new_app(
    *,
    generations_started: int = 1,
    resend_every: bool = False,
    paid: bool = False,
    force_free_chat_ids: frozenset[int] = frozenset(),
):
    app = object.__new__(public_app.BlastBotApp)
    app.credits_db = _Credits(generations_started=generations_started, paid=paid)
    app.settings = SimpleNamespace(
        resend_methodology_every_generation=resend_every,
        tg_force_free_funnel_chat_ids=force_free_chat_ids,
    )
    return app


def _cb_data(question_id: str, answer_id: str) -> str:
    return f"{SURVEY_CB_PREFIX}{question_id}:{answer_id}"


def _labels(markup) -> list[str]:
    return [btn.text for row in markup.inline_keyboard for btn in row]


# --- entry point -----------------------------------------------------------


def test_first_generation_starts_the_survey() -> None:
    async def _run() -> None:
        app = _new_app(generations_started=1)
        msg = _Message()

        await public_app.BlastBotApp._start_postgen_marketing_flow(
            app, message=msg, st=ChatState(chat_id=7)
        )

        assert msg.answers[-1] == SURVEY_QUESTIONS["q1"].text
        assert _labels(msg.markups[-1]) == [o.label for o in SURVEY_QUESTIONS["q1"].options]
        # No document yet — it arrives after Q3.
        assert msg.documents == []

    asyncio.run(_run())


def test_second_generation_sends_only_the_methodology() -> None:
    async def _run() -> None:
        app = _new_app(generations_started=2)
        msg = _Message()

        await public_app.BlastBotApp._start_postgen_marketing_flow(
            app, message=msg, st=ChatState(chat_id=7)
        )

        assert msg.documents == [METHODOLOGY_FILE_ID]
        assert msg.answers == []

    asyncio.run(_run())


def test_third_generation_is_silent_by_default() -> None:
    async def _run() -> None:
        app = _new_app(generations_started=3)
        msg = _Message()

        await public_app.BlastBotApp._start_postgen_marketing_flow(
            app, message=msg, st=ChatState(chat_id=7)
        )

        assert msg.documents == []
        assert msg.answers == []

    asyncio.run(_run())


def test_resend_flag_repeats_methodology_on_every_generation() -> None:
    async def _run() -> None:
        app = _new_app(generations_started=7, resend_every=True)
        msg = _Message()

        await public_app.BlastBotApp._start_postgen_marketing_flow(
            app, message=msg, st=ChatState(chat_id=7)
        )

        assert msg.documents == [METHODOLOGY_FILE_ID]

    asyncio.run(_run())


def test_paying_client_gets_neither_survey_nor_methodology() -> None:
    async def _run() -> None:
        # Both the first generation and a later one: the funnel is conversion
        # material, clients must never see it.
        for started in (1, 2):
            app = _new_app(generations_started=started, paid=True)
            msg = _Message()

            await public_app.BlastBotApp._start_postgen_marketing_flow(
                app, message=msg, st=ChatState(chat_id=7)
            )

            assert msg.answers == []
            assert msg.documents == []

    asyncio.run(_run())


def test_force_free_funnel_override_still_reaches_a_paid_chat() -> None:
    async def _run() -> None:
        app = _new_app(paid=True, force_free_chat_ids=frozenset({7}))
        msg = _Message()

        await public_app.BlastBotApp._start_postgen_marketing_flow(
            app, message=msg, st=ChatState(chat_id=7)
        )

        assert msg.answers[-1] == SURVEY_QUESTIONS["q1"].text

    asyncio.run(_run())


def test_paid_check_failure_stays_silent() -> None:
    async def _run() -> None:
        app = _new_app()

        async def _boom(*_a, **_kw):
            raise RuntimeError("db down")

        app.credits_db.has_paid = _boom
        msg = _Message()

        await public_app.BlastBotApp._start_postgen_marketing_flow(
            app, message=msg, st=ChatState(chat_id=7)
        )

        assert msg.answers == []
        assert msg.documents == []

    asyncio.run(_run())


def test_counter_failure_never_breaks_the_generation() -> None:
    async def _run() -> None:
        app = _new_app()

        async def _boom(*_a, **_kw):
            raise RuntimeError("db down")

        app.credits_db.count_events = _boom
        msg = _Message()

        await public_app.BlastBotApp._start_postgen_marketing_flow(
            app, message=msg, st=ChatState(chat_id=7)
        )

        assert msg.answers == []
        assert msg.documents == []

    asyncio.run(_run())


# --- branching -------------------------------------------------------------


async def _answer(app, msg: _Message, question_id: str, answer_id: str) -> _Callback:
    cb = _Callback(_cb_data(question_id, answer_id), msg)
    await public_app.BlastBotApp._handle_postgen_survey_callback(app, cb)
    return cb


def test_self_branch_asks_the_time_question() -> None:
    async def _run() -> None:
        app = _new_app()
        msg = _Message()

        await _answer(app, msg, "q1", "1_10")
        assert msg.answers[-1] == SURVEY_QUESTIONS["q2"].text

        await _answer(app, msg, "q2", "self")
        assert msg.answers[-1] == SURVEY_QUESTIONS["q2a"].text

        await _answer(app, msg, "q2a", "1_3h")
        assert msg.answers[-1] == SURVEY_QUESTIONS["q3"].text

    asyncio.run(_run())


def test_helper_branch_asks_the_money_question() -> None:
    async def _run() -> None:
        app = _new_app()
        msg = _Message()

        await _answer(app, msg, "q2", "helper")
        assert msg.answers[-1] == SURVEY_QUESTIONS["q2b"].text

        await _answer(app, msg, "q2b", "5_10k")
        assert msg.answers[-1] == SURVEY_QUESTIONS["q3"].text

    asyncio.run(_run())


def test_no_edit_branch_skips_straight_to_q3() -> None:
    async def _run() -> None:
        app = _new_app()
        msg = _Message()

        await _answer(app, msg, "q2", "no_edit")

        assert msg.answers[-1] == SURVEY_QUESTIONS["q3"].text
        assert SURVEY_QUESTIONS["q2a"].text not in msg.answers
        assert SURVEY_QUESTIONS["q2b"].text not in msg.answers

    asyncio.run(_run())


def test_q3_sends_matching_bridge_then_methodology() -> None:
    async def _run() -> None:
        for answer_id, branch in (
            ("time", "time"),
            ("money", "money"),
            ("ideas", "ideas"),
            ("meaning", "meaning"),
        ):
            app = _new_app()
            msg = _Message()

            await _answer(app, msg, "q3", answer_id)

            assert msg.answers[-1] == BRIDGE_TEXT_BY_BRANCH[branch]
            assert msg.documents == [METHODOLOGY_FILE_ID]

    asyncio.run(_run())


# --- persistence & robustness ---------------------------------------------


def test_answers_and_branches_are_persisted() -> None:
    async def _run() -> None:
        app = _new_app()
        msg = _Message()

        await _answer(app, msg, "q1", "30_plus")
        await _answer(app, msg, "q2", "helper")
        await _answer(app, msg, "q2b", "10k_plus")
        await _answer(app, msg, "q3", "money")

        row = app.credits_db.survey[7]
        assert set(row["answers"]) == {"q1", "q2", "q2b", "q3"}
        assert row["answers"]["q1"]["id"] == "30_plus"
        assert row["branch_q2"] == "helper"
        assert row["branch_q3"] == "money"
        assert app.credits_db.saved_calls[-1]["completed"] is True

    asyncio.run(_run())


def test_repeated_click_does_not_resend_the_branch() -> None:
    async def _run() -> None:
        app = _new_app()
        msg = _Message()

        await _answer(app, msg, "q3", "time")
        assert msg.documents == [METHODOLOGY_FILE_ID]

        cb = await _answer(app, msg, "q3", "time")

        assert msg.documents == [METHODOLOGY_FILE_ID]
        assert cb.acks == ["Уже ответил."]

    asyncio.run(_run())


def test_client_who_paid_mid_survey_keeps_answer_but_gets_no_document() -> None:
    async def _run() -> None:
        app = _new_app()
        msg = _Message()

        await _answer(app, msg, "q1", "1_10")
        # Bought a package while the survey was open.
        app.credits_db.paid = True
        await _answer(app, msg, "q3", "time")

        assert "q3" in app.credits_db.survey[7]["answers"]
        assert msg.documents == []
        assert msg.answers[-1] == SURVEY_THANKS
        assert BRIDGE_TEXT_BY_BRANCH["time"] not in msg.answers

    asyncio.run(_run())


def test_unknown_callback_payload_is_ignored() -> None:
    async def _run() -> None:
        app = _new_app()
        msg = _Message()

        for data in (f"{SURVEY_CB_PREFIX}nope:whatever", f"{SURVEY_CB_PREFIX}q1:bogus", SURVEY_CB_PREFIX):
            cb = _Callback(data, msg)
            await public_app.BlastBotApp._handle_postgen_survey_callback(app, cb)

        assert msg.answers == []
        assert msg.documents == []

    asyncio.run(_run())


def test_save_failure_still_advances_the_survey() -> None:
    async def _run() -> None:
        app = _new_app()

        async def _boom(*_a, **_kw):
            raise RuntimeError("db down")

        app.credits_db.save_survey_answer = _boom
        msg = _Message()

        await _answer(app, msg, "q1", "none")

        assert msg.answers[-1] == SURVEY_QUESTIONS["q2"].text

    asyncio.run(_run())


def test_callback_data_fits_telegram_limit() -> None:
    for question in SURVEY_QUESTIONS.values():
        for opt in question.options:
            payload = _cb_data(question.id, opt.id)
            assert len(payload.encode("utf-8")) <= 64, payload
