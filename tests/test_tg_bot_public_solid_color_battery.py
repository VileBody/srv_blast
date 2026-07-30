from __future__ import annotations

import asyncio
import inspect

from services.tg_bot_public import app as public_app
from services.tg_bot_public.state_store import (
    ChatState,
    STAGE_WAIT_CONFIRM,
    STAGE_WAIT_SUBTITLE_COLOR,
)


class _Store:
    async def set(self, st):
        self.state = st


class _Message:
    def __init__(self, text=""):
        self.text = text
        self.answers = []

    async def answer(self, text="", reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return self


class _App:
    def __init__(self):
        self.store = _Store()
        self.subtitle_color_calls = 0
        self.proceed_calls = 0

    _color_kb = public_app.BlastBotApp._color_kb

    async def _ask_subtitle_color(self, message, st):
        self.subtitle_color_calls += 1
        await public_app.BlastBotApp._ask_subtitle_color(self, message, st)

    async def _proceed_to_versions_or_confirm(self, message, st):
        self.proceed_calls += 1


def _labels(markup):
    return [button.text for row in markup.keyboard for button in row]


def test_footage_stylization_has_no_skip_button_and_rejects_skip():
    source = inspect.getsource(public_app.BlastBotApp._ask_visual_style)
    assert "[BTN_FX_SKIP]" not in source
    handler = inspect.getsource(public_app.BlastBotApp._handle_wait_visual_style)
    assert "text == BTN_FX_SKIP" not in handler
    assert "Выбери стилизацию кнопкой ниже." in handler


def test_solid_confirm_skips_visual_choices_and_opens_subtitle_color():
    async def run():
        app = _App()
        st = ChatState(
            chat_id=1,
            bg_mode="solid",
            visual_transition="snap_wipe",
            visual_style="xerox",
        )
        msg = _Message(public_app.BTN_CONFIRM_YES)
        await public_app.BlastBotApp._handle_wait_confirm_mode(app, msg, st)
        assert app.subtitle_color_calls == 1
        assert st.stage == STAGE_WAIT_SUBTITLE_COLOR
        assert st.visuals_done is True
        assert st.visual_transition == ""
        assert st.visual_style == ""
        assert public_app.BTN_COLOR_BATTERY in _labels(msg.answers[-1][1])

    asyncio.run(run())


def test_solid_color_battery_builds_five_distinct_video_cases():
    async def run():
        app = _App()
        st = ChatState(chat_id=1, bg_mode="solid", bg_solid_color="black")
        msg = _Message(public_app.BTN_COLOR_BATTERY)
        await public_app.BlastBotApp._handle_wait_subtitle_color(app, msg, st)
        colors = [case["subtitle_color_hex"] for case in st.battery_cases]
        assert st.stage == STAGE_WAIT_CONFIRM
        assert st.battery_mode is True
        assert st.versions_count == 5
        assert len(colors) == len(set(colors)) == 5
        assert st.subtitle_color_hex == colors[0]
        assert st.accent_color_hex == ""
        assert "5 видео" in msg.answers[-1][0]

    asyncio.run(run())


def test_white_background_battery_excludes_white_subtitles():
    async def run():
        app = _App()
        st = ChatState(chat_id=1, bg_mode="solid", bg_solid_color="white")
        msg = _Message(public_app.BTN_COLOR_BATTERY)
        await public_app.BlastBotApp._handle_wait_subtitle_color(app, msg, st)
        colors = {case["subtitle_color_hex"] for case in st.battery_cases}
        assert "#FFFFFF" not in colors
        assert len(colors) == 5

    asyncio.run(run())


def test_regular_solid_color_skips_accent_picker():
    async def run():
        app = _App()
        st = ChatState(chat_id=1, bg_mode="solid")
        msg = _Message("Красный")
        await public_app.BlastBotApp._handle_wait_subtitle_color(app, msg, st)
        assert st.subtitle_color_hex == "#FF2D55"
        assert st.accent_color_hex == ""
        assert st.colors_done is True
        assert app.proceed_calls == 1

    asyncio.run(run())


def test_enqueue_uses_battery_case_color_for_each_version():
    source = inspect.getsource(public_app.BlastBotApp._enqueue_batch_version)
    assert "cases[case_index].get(\"subtitle_color_hex\")" in source
    assert "subtitle_color_hex=(None if st.bg_mode == \"solid_strobe\" else (subtitle_color_for_version or None))" in source
    assert "case count must match versions_total" in source