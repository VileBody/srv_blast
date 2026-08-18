"""Free-tier version picker: 1..5 for everyone + 80/100% quota warnings."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from services.tg_bot_public import app as public_app
from services.tg_bot_public.config import Settings
from services.tg_bot_public.marketing_texts import (
    BTN_VERSIONS_WARN_CHANGE,
    BTN_VERSIONS_WARN_CONTINUE,
    versions_warning_text,
)
from services.tg_bot_public.state_store import (
    ChatState,
    STAGE_WAIT_CONFIRM,
    STAGE_WAIT_VERSIONS,
    STAGE_WAIT_VERSIONS_WARNING,
)


class _Store:
    def __init__(self) -> None:
        self.saved: list[ChatState] = []

    async def set(self, st: ChatState) -> None:
        self.saved.append(st.model_copy(deep=True))


class _Credits:
    def __init__(self, *, paid: bool = False, balance: int = 5) -> None:
        self.paid = bool(paid)
        self.balance = int(balance)

    async def has_paid(self, _chat_id: int) -> bool:
        return self.paid

    async def get_balance(self, _chat_id: int) -> int:
        return self.balance


class _Message:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.answers: list[str] = []
        self.markups: list[object] = []

    async def answer(self, text: str = "", **kwargs) -> None:
        self.answers.append(str(text))
        self.markups.append(kwargs.get("reply_markup"))


def _new_app(*, paid: bool = False, balance: int = 5, free_limit: int = 5):
    app = object.__new__(public_app.BlastBotApp)
    app.store = _Store()
    app.credits_db = _Credits(paid=paid, balance=balance)
    app.settings = SimpleNamespace(
        initial_credits=free_limit,
        rust_gen_bot_default_enabled=False,
    )
    return app


def _button_texts(markup) -> list[str]:
    return [btn.text for row in markup.keyboard for btn in row]


def test_free_tier_default_limit_is_five() -> None:
    # Task 2: the tariff limit and the UI selector must both mean 5.
    assert Settings().initial_credits == 5


def test_free_user_gets_full_version_picker() -> None:
    async def _run() -> None:
        app = _new_app(paid=False)
        # frame_id="none" = шаг «Рамка» уже пройден: он вклинивается перед
        # версиями, а этот тест — про сам селектор версий.
        st = ChatState(chat_id=7, frame_id="none")
        msg = _Message()

        await public_app.BlastBotApp._proceed_after_render_engine(app, msg, st)

        assert st.stage == STAGE_WAIT_VERSIONS
        assert _button_texts(msg.markups[-1]) == ["1", "2", "3", "4", "5"]
        assert "бесплатном тарифе" in msg.answers[-1]

    asyncio.run(_run())


def test_paid_user_picker_has_no_free_limit_hint() -> None:
    async def _run() -> None:
        app = _new_app(paid=True)
        st = ChatState(chat_id=7, frame_id="none")
        msg = _Message()

        await public_app.BlastBotApp._proceed_after_render_engine(app, msg, st)

        assert st.stage == STAGE_WAIT_VERSIONS
        assert _button_texts(msg.markups[-1]) == ["1", "2", "3", "4", "5"]
        assert "бесплатном тарифе" not in msg.answers[-1]

    asyncio.run(_run())


def test_free_pick_of_three_skips_warning() -> None:
    async def _run() -> None:
        app = _new_app(paid=False)
        st = ChatState(chat_id=7, stage=STAGE_WAIT_VERSIONS)

        await public_app.BlastBotApp._handle_wait_versions(app, _Message("3"), st)

        assert st.versions_count == 3
        assert st.stage == STAGE_WAIT_CONFIRM

    asyncio.run(_run())


def test_free_pick_of_four_warns_about_eighty_percent() -> None:
    async def _run() -> None:
        app = _new_app(paid=False)
        st = ChatState(chat_id=7, stage=STAGE_WAIT_VERSIONS)
        msg = _Message("4")

        await public_app.BlastBotApp._handle_wait_versions(app, msg, st)

        assert st.stage == STAGE_WAIT_VERSIONS_WARNING
        assert "80%" in msg.answers[-1]
        assert _button_texts(msg.markups[-1]) == [
            BTN_VERSIONS_WARN_CONTINUE,
            BTN_VERSIONS_WARN_CHANGE,
        ]

    asyncio.run(_run())


def test_free_pick_of_five_warns_about_full_limit() -> None:
    async def _run() -> None:
        app = _new_app(paid=False)
        st = ChatState(chat_id=7, stage=STAGE_WAIT_VERSIONS)
        msg = _Message("5")

        await public_app.BlastBotApp._handle_wait_versions(app, msg, st)

        assert st.stage == STAGE_WAIT_VERSIONS_WARNING
        assert "100%" in msg.answers[-1]

    asyncio.run(_run())


def test_paid_pick_of_five_has_no_warning() -> None:
    async def _run() -> None:
        app = _new_app(paid=True, balance=50)
        st = ChatState(chat_id=7, stage=STAGE_WAIT_VERSIONS)

        await public_app.BlastBotApp._handle_wait_versions(app, _Message("5"), st)

        assert st.stage == STAGE_WAIT_CONFIRM

    asyncio.run(_run())


def test_warning_continue_reaches_final_confirm() -> None:
    async def _run() -> None:
        app = _new_app(paid=False)
        st = ChatState(
            chat_id=7,
            stage=STAGE_WAIT_VERSIONS_WARNING,
            versions_count=5,
            lyrics_text="hello",
            target_fragment="hello",
        )
        msg = _Message(BTN_VERSIONS_WARN_CONTINUE)

        await public_app.BlastBotApp._handle_wait_versions_warning(app, msg, st)

        assert st.stage == STAGE_WAIT_CONFIRM
        assert st.versions_count == 5
        assert "Запустить генерацию" in msg.answers[-1]

    asyncio.run(_run())


def test_warning_change_returns_to_picker() -> None:
    async def _run() -> None:
        app = _new_app(paid=False)
        st = ChatState(
            chat_id=7, stage=STAGE_WAIT_VERSIONS_WARNING, versions_count=5, frame_id="none"
        )
        msg = _Message(BTN_VERSIONS_WARN_CHANGE)

        await public_app.BlastBotApp._handle_wait_versions_warning(app, msg, st)

        assert st.stage == STAGE_WAIT_VERSIONS
        assert _button_texts(msg.markups[-1]) == ["1", "2", "3", "4", "5"]

    asyncio.run(_run())


def test_warning_rejects_free_text_and_keeps_stage() -> None:
    async def _run() -> None:
        app = _new_app(paid=False)
        st = ChatState(chat_id=7, stage=STAGE_WAIT_VERSIONS_WARNING, versions_count=5)
        msg = _Message("ага")

        await public_app.BlastBotApp._handle_wait_versions_warning(app, msg, st)

        assert st.stage == STAGE_WAIT_VERSIONS_WARNING
        assert BTN_VERSIONS_WARN_CONTINUE in msg.answers[-1]

    asyncio.run(_run())


def test_insufficient_balance_still_blocks_before_warning() -> None:
    async def _run() -> None:
        app = _new_app(paid=False, balance=2)
        st = ChatState(chat_id=7, stage=STAGE_WAIT_VERSIONS)
        msg = _Message("5")

        await public_app.BlastBotApp._handle_wait_versions(app, msg, st)

        assert st.stage == STAGE_WAIT_VERSIONS
        assert "Недостаточно генераций" in msg.answers[-1]

    asyncio.run(_run())


def test_versions_warning_text_scales_with_the_limit() -> None:
    assert versions_warning_text(3, 5) is None
    assert "80%" in (versions_warning_text(4, 5) or "")
    assert "100%" in (versions_warning_text(5, 5) or "")
    # Warning rule is a share of the quota, not a hardcoded 4/5.
    assert versions_warning_text(4, 10) is None
    assert "100%" in (versions_warning_text(10, 10) or "")
