from __future__ import annotations

import asyncio
import types
from pathlib import Path

from services.tg_bot_public import app as public_app
from services.tg_bot_public.state_store import ChatState, STAGE_ALL_PACKAGES, STAGE_PACKAGE_INFO


class _FakeCreditsDB:
    def __init__(self) -> None:
        self.events: list[tuple[int, str, str]] = []

    async def log_event(self, tg_id: int, event: str, detail: str = "") -> None:
        self.events.append((int(tg_id), str(event), str(detail or "")))


class _FakeStore:
    def __init__(self) -> None:
        self.saved: list[ChatState] = []

    async def set(self, state: ChatState) -> None:
        self.saved.append(state.model_copy(deep=True))


class _FakeS3:
    def __init__(self) -> None:
        self.downloads: list[tuple[str, str, Path]] = []

    def download_file(self, *, bucket: str, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-png")
        self.downloads.append((str(bucket), str(key), dest))
        return dest


class _FakeBot:
    def __init__(self) -> None:
        self.photos: list[tuple[int, object]] = []

    async def send_photo(self, chat_id: int, photo, **_kwargs):
        self.photos.append((int(chat_id), photo))
        return types.SimpleNamespace(message_id=len(self.photos))


class _FakeMessage:
    def __init__(self, text: str = "", *, chat_id: int = 1001) -> None:
        self.text = text
        self.chat = types.SimpleNamespace(id=chat_id)
        self.from_user = types.SimpleNamespace(id=chat_id, username="tester")
        self.answers: list[dict[str, object]] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append({"text": text, **kwargs})
        return types.SimpleNamespace(message_id=len(self.answers))


def _new_app(tmp_path: Path) -> public_app.BlastBotApp:
    app = object.__new__(public_app.BlastBotApp)
    app.settings = types.SimpleNamespace(s3_bucket_asset_storage="asset-bucket", tmp_dir=tmp_path)
    app.credits_db = _FakeCreditsDB()
    app.store = _FakeStore()
    app.s3 = _FakeS3()
    app._bot = _FakeBot()
    return app


def test_package_command_aliases_include_packages_and_typo() -> None:
    assert public_app._is_packages_command_text("/packages")
    assert public_app._is_packages_command_text("/packages@blast808bot")
    assert public_app._is_packages_command_text("/зackages")
    assert public_app._is_packages_command_text("/пакеты")
    assert public_app._is_packages_command_text("/packets")


def test_show_all_packages_is_text_only_without_trial(tmp_path: Path) -> None:
    async def _run() -> None:
        app = _new_app(tmp_path)
        msg = _FakeMessage(text="/packages", chat_id=42)
        st = ChatState(chat_id=42, stage="IDLE")

        await public_app.BlastBotApp._show_all_packages(app, msg, st)

        assert st.stage == STAGE_ALL_PACKAGES
        # Cards are disabled during the tariff refresh -> text-only, no photos.
        assert len(app._bot.photos) == 0
        assert len(app.s3.downloads) == 0
        assert len(msg.answers) == 1
        body = str(msg.answers[0]["text"])
        assert "Вот пул пакетов" in body
        assert "100 роликов" in body
        # Trial is removed from the enumeration.
        assert "Trial" not in body and "Триал" not in body
        assert app.credits_db.events == [(42, "view_packages", "")]

    asyncio.run(_run())


def test_package_detail_is_text_only(tmp_path: Path) -> None:
    async def _run() -> None:
        app = _new_app(tmp_path)
        msg = _FakeMessage(text=public_app.BTN_PKG_BLAST, chat_id=77)
        st = ChatState(chat_id=77, stage=STAGE_ALL_PACKAGES)

        await public_app.BlastBotApp._handle_all_packages(app, msg, st)

        assert st.stage == STAGE_PACKAGE_INFO
        assert st.selected_package == public_app.BTN_PKG_BLAST
        # Cards disabled -> no photo, just the description text.
        assert len(app._bot.photos) == 0
        assert len(msg.answers) == 1
        assert "Бласт — 1 990" in str(msg.answers[0]["text"])
        assert app.credits_db.events == [(77, "select_package", public_app.BTN_PKG_BLAST)]

    asyncio.run(_run())
