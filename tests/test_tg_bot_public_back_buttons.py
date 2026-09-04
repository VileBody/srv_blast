"""«Назад» на шагах публичного бота, где раньше был тупик.

Палитра субтитров/акцента, режим субтитров, вопрос про хук, рамка, версии и
ввод тайминга: случайный тап на любом из них раньше нельзя было отменить —
шаг спрашивался один раз и назад дороги не было.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from services.tg_bot_public import app as public_app
from services.tg_bot_public.state_store import (
    ChatState,
    STAGE_WAIT_ACCENT_COLOR,
    STAGE_WAIT_BG_MODE,
    STAGE_WAIT_FRAGMENT_TEXT,
    STAGE_WAIT_FRAME,
    STAGE_WAIT_HOOK_CHOICE,
    STAGE_WAIT_SUBTITLE_COLOR,
    STAGE_WAIT_SUBTITLES_MODE,
    STAGE_WAIT_TIMING_INPUT,
    STAGE_WAIT_VISUAL_TRANSITION,
)

BACK = public_app.BTN_BACK


class _Store:
    async def set(self, st: ChatState) -> None:  # noqa: D401 - test stub
        return None


class _Message:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.answers: list[str] = []
        self.markups: list[object] = []

    async def answer(self, text: str = "", **kwargs) -> None:
        self.answers.append(str(text))
        self.markups.append(kwargs.get("reply_markup"))

    async def answer_video(self, *args, **kwargs) -> None:
        return None


def _new_app():
    app = object.__new__(public_app.BlastBotApp)
    app.store = _Store()
    app.settings = SimpleNamespace(
        initial_credits=5,
        rust_gen_enabled=False,
        rust_gen_bot_default_enabled=False,
    )

    async def _no_previews(_message, _keys) -> None:
        return None

    app._send_option_previews = _no_previews
    return app


def _button_texts(markup) -> list[str]:
    return [btn.text for row in markup.keyboard for btn in row]


def test_subtitle_color_keyboard_has_back() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=1, bg_mode="footage")
        msg = _Message()

        await public_app.BlastBotApp._ask_subtitle_color(app, msg, st)

        assert st.stage == STAGE_WAIT_SUBTITLE_COLOR
        assert BACK in _button_texts(msg.markups[-1])

    asyncio.run(_run())


def test_subtitle_color_back_on_solid_returns_to_subtitles_mode() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(
            chat_id=1,
            bg_mode="solid",
            stage=STAGE_WAIT_SUBTITLE_COLOR,
            subtitle_color_hex="#FF0000",
            colors_done=True,
        )

        await public_app.BlastBotApp._handle_wait_subtitle_color(app, _Message(BACK), st)

        assert st.stage == STAGE_WAIT_SUBTITLES_MODE
        # Возврат обязан снять «уже спрошено», иначе шаг молча пропустится.
        assert st.colors_done is False
        assert st.subtitle_color_hex == ""

    asyncio.run(_run())


def test_subtitle_color_back_on_footage_returns_to_hook_question(monkeypatch) -> None:
    monkeypatch.setattr(public_app, "HOOK_FLOW_ENABLED", True)

    async def _run() -> None:
        app = _new_app()
        st = ChatState(
            chat_id=1,
            bg_mode="footage",
            stage=STAGE_WAIT_SUBTITLE_COLOR,
            colors_done=True,
            visuals_done=True,
        )

        await public_app.BlastBotApp._handle_wait_subtitle_color(app, _Message(BACK), st)

        assert st.stage == STAGE_WAIT_HOOK_CHOICE
        assert st.visuals_done is False

    asyncio.run(_run())


def test_subtitle_color_back_without_hook_flow_returns_to_visuals(monkeypatch) -> None:
    monkeypatch.setattr(public_app, "HOOK_FLOW_ENABLED", False)

    async def _run() -> None:
        app = _new_app()
        st = ChatState(
            chat_id=1,
            bg_mode="footage",
            stage=STAGE_WAIT_SUBTITLE_COLOR,
            colors_done=True,
            visuals_done=True,
        )

        await public_app.BlastBotApp._handle_wait_subtitle_color(app, _Message(BACK), st)

        assert st.stage == STAGE_WAIT_VISUAL_TRANSITION

    asyncio.run(_run())


def test_accent_color_back_returns_to_subtitle_color() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(
            chat_id=1,
            bg_mode="footage",
            stage=STAGE_WAIT_ACCENT_COLOR,
            accent_color_hex="#00FF00",
        )
        msg = _Message(BACK)

        await public_app.BlastBotApp._handle_wait_accent_color(app, msg, st)

        assert st.stage == STAGE_WAIT_SUBTITLE_COLOR
        assert st.accent_color_hex == ""
        assert BACK in _button_texts(msg.markups[-1])

    asyncio.run(_run())


def test_accent_color_keyboard_has_back() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=1, bg_mode="footage")
        msg = _Message()

        await public_app.BlastBotApp._ask_accent_color(app, msg, st)

        assert BACK in _button_texts(msg.markups[-1])

    asyncio.run(_run())


def test_subtitles_mode_back_returns_to_background_step() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=1, stage=STAGE_WAIT_SUBTITLES_MODE)
        msg = _Message()

        await public_app.BlastBotApp._ask_subtitles_mode(app, msg, st)
        assert BACK in _button_texts(msg.markups[-1])

        await public_app.BlastBotApp._handle_wait_subtitles_mode(app, _Message(BACK), st)
        assert st.stage == STAGE_WAIT_BG_MODE

    asyncio.run(_run())


def test_hook_choice_back_returns_to_subtitles_mode() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=1, bg_mode="footage")
        msg = _Message()

        await public_app.BlastBotApp._ask_hook_choice(app, msg, st)
        assert st.stage == STAGE_WAIT_HOOK_CHOICE
        assert BACK in _button_texts(msg.markups[-1])

        await public_app.BlastBotApp._handle_wait_hook_choice(app, _Message(BACK), st)
        assert st.stage == STAGE_WAIT_SUBTITLES_MODE

    asyncio.run(_run())


def test_frame_back_returns_to_colors_and_clears_choice(monkeypatch) -> None:
    monkeypatch.setattr(public_app, "HOOK_FLOW_ENABLED", True)

    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=1, bg_mode="footage", stage=STAGE_WAIT_FRAME, frame_id="rounded")
        msg = _Message()

        await public_app.BlastBotApp._ask_frame(app, msg, st)
        assert BACK in _button_texts(msg.markups[-1])

        await public_app.BlastBotApp._handle_wait_frame(app, _Message(BACK), st)
        assert st.stage == STAGE_WAIT_SUBTITLE_COLOR
        # Иначе _ask_versions посчитал бы рамку уже выбранной и пропустил шаг.
        assert st.frame_id == ""

    asyncio.run(_run())


def test_versions_back_returns_to_frame_step() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(chat_id=1, bg_mode="footage", stage="WAIT_VERSIONS", frame_id="none")

        await public_app.BlastBotApp._handle_wait_versions(app, _Message(BACK), st)

        assert st.stage == STAGE_WAIT_FRAME
        assert st.frame_id == ""

    asyncio.run(_run())


def test_timing_input_back_returns_to_fragment_text() -> None:
    async def _run() -> None:
        app = _new_app()
        st = ChatState(
            chat_id=1,
            stage=STAGE_WAIT_TIMING_INPUT,
            target_fragment="старые строки",
            target_fragment_explicit=True,
        )

        await public_app.BlastBotApp._handle_wait_timing_input(app, _Message(BACK), st)

        assert st.stage == STAGE_WAIT_FRAGMENT_TEXT
        assert st.target_fragment == ""
        assert st.target_fragment_explicit is False

    asyncio.run(_run())
