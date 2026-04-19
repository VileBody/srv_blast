from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace


# Keep local test runs reproducible without manual `PYTHONPATH=.`
_REPO_ROOT = Path(__file__).resolve().parents[1]
_ROOT_STR = str(_REPO_ROOT)
if _ROOT_STR not in sys.path:
    sys.path.insert(0, _ROOT_STR)


def _install_celery_stub() -> None:
    celery_mod = types.ModuleType("celery")

    class _FakeTask:
        abstract = False
        name = ""

        def __init__(self) -> None:
            self.request = SimpleNamespace(retries=0, eta=None)

        def retry(self, *args, **kwargs):  # pragma: no cover - explicit override in tests
            raise RuntimeError("fake_celery_retry_not_configured")

    class _FakeTaskWrapper(_FakeTask):
        def __init__(self, fn, *, bind: bool, name: str | None) -> None:
            super().__init__()
            self._fn = fn
            self._bind = bool(bind)
            self.name = str(name or fn.__name__)

        def run(self, *args, **kwargs):
            if self._bind:
                return self._fn(self, *args, **kwargs)
            return self._fn(*args, **kwargs)

        def __call__(self, *args, **kwargs):
            return self.run(*args, **kwargs)

        def delay(self, *args, **kwargs):
            return self.run(*args, **kwargs)

        def apply_async(self, args=None, kwargs=None, **_):
            return self.run(*tuple(args or ()), **dict(kwargs or {}))

    class _FakeInspect:
        def active(self):
            return {}

        def reserved(self):
            return {}

    class _FakeControl:
        def inspect(self, timeout: float = 1.0):
            _ = timeout
            return _FakeInspect()

    class _FakeConf(dict):
        def update(self, *args, **kwargs):
            super().update(*args, **kwargs)

    class _FakeCelery:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)
            self.conf = _FakeConf()
            self.control = _FakeControl()
            self.Task = _FakeTask

        def task(self, *dargs, **dkwargs):
            def _decorate(fn):
                return _FakeTaskWrapper(
                    fn,
                    bind=bool(dkwargs.get("bind", False)),
                    name=dkwargs.get("name"),
                )

            if dargs and callable(dargs[0]) and len(dargs) == 1 and not dkwargs:
                return _decorate(dargs[0])
            return _decorate

    celery_mod.Celery = _FakeCelery
    celery_mod.Task = _FakeTask

    celery_utils_mod = types.ModuleType("celery.utils")
    celery_utils_log_mod = types.ModuleType("celery.utils.log")
    celery_utils_log_mod.get_task_logger = logging.getLogger

    sys.modules["celery"] = celery_mod
    sys.modules["celery.utils"] = celery_utils_mod
    sys.modules["celery.utils.log"] = celery_utils_log_mod


def _install_aiogram_stub() -> None:
    aiogram_mod = types.ModuleType("aiogram")
    aiogram_exceptions_mod = types.ModuleType("aiogram.exceptions")
    aiogram_filters_mod = types.ModuleType("aiogram.filters")
    aiogram_types_mod = types.ModuleType("aiogram.types")
    aiogram_client_mod = types.ModuleType("aiogram.client")
    aiogram_client_session_mod = types.ModuleType("aiogram.client.session")
    aiogram_client_session_aiohttp_mod = types.ModuleType("aiogram.client.session.aiohttp")

    class _Dummy:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

        def __call__(self, *args, **kwargs):
            _ = (args, kwargs)
            return self

        def __getattr__(self, _name: str):
            return self

        async def answer(self, *args, **kwargs):
            _ = (args, kwargs)
            return None

    class _DummyRouter(_Dummy):
        def message(self, *args, **kwargs):
            _ = (args, kwargs)

            def _decorator(fn):
                return fn

            return _decorator

        def callback_query(self, *args, **kwargs):
            _ = (args, kwargs)

            def _decorator(fn):
                return fn

            return _decorator

    class _DummyDispatcher(_Dummy):
        def include_router(self, *args, **kwargs):
            _ = (args, kwargs)
            return None

        async def start_polling(self, *args, **kwargs):
            _ = (args, kwargs)
            return None

    class _DummyBot(_Dummy):
        async def send_message(self, *args, **kwargs):
            _ = (args, kwargs)
            return None

        async def send_video(self, *args, **kwargs):
            _ = (args, kwargs)
            return None

    class TelegramBadRequest(Exception):
        pass

    class TelegramForbiddenError(Exception):
        pass

    class TelegramRetryAfter(Exception):
        def __init__(self, *args, retry_after: float = 1.0, **kwargs) -> None:
            super().__init__(*args)
            _ = kwargs
            self.retry_after = retry_after

    aiogram_mod.Bot = _DummyBot
    aiogram_mod.Dispatcher = _DummyDispatcher
    aiogram_mod.Router = _DummyRouter
    aiogram_exceptions_mod.TelegramBadRequest = TelegramBadRequest
    aiogram_exceptions_mod.TelegramForbiddenError = TelegramForbiddenError
    aiogram_exceptions_mod.TelegramRetryAfter = TelegramRetryAfter
    aiogram_filters_mod.CommandStart = _Dummy
    aiogram_filters_mod.Command = _Dummy
    aiogram_types_mod.FSInputFile = _Dummy
    aiogram_types_mod.BotCommand = _Dummy
    aiogram_types_mod.CallbackQuery = _Dummy
    aiogram_types_mod.ChatMemberUpdated = _Dummy
    aiogram_types_mod.KeyboardButton = _Dummy
    aiogram_types_mod.Message = _Dummy
    aiogram_types_mod.ReplyKeyboardMarkup = _Dummy
    aiogram_types_mod.ReplyKeyboardRemove = _Dummy
    aiogram_types_mod.InlineKeyboardMarkup = _Dummy
    aiogram_types_mod.InlineKeyboardButton = _Dummy
    aiogram_types_mod.Update = _Dummy
    aiogram_client_session_aiohttp_mod.AiohttpSession = _Dummy

    sys.modules["aiogram"] = aiogram_mod
    sys.modules["aiogram.exceptions"] = aiogram_exceptions_mod
    sys.modules["aiogram.filters"] = aiogram_filters_mod
    sys.modules["aiogram.types"] = aiogram_types_mod
    sys.modules["aiogram.client"] = aiogram_client_mod
    sys.modules["aiogram.client.session"] = aiogram_client_session_mod
    sys.modules["aiogram.client.session.aiohttp"] = aiogram_client_session_aiohttp_mod


def _install_redis_stub() -> None:
    redis_mod = types.ModuleType("redis")

    class _RedisError(Exception):
        pass

    class _FakeRedis:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

    redis_mod.Redis = _FakeRedis
    redis_mod.exceptions = SimpleNamespace(
        ConnectionError=_RedisError,
        TimeoutError=_RedisError,
    )
    sys.modules["redis"] = redis_mod


if importlib.util.find_spec("celery") is None:
    _install_celery_stub()

if importlib.util.find_spec("aiogram") is None:
    _install_aiogram_stub()

if importlib.util.find_spec("redis") is None:
    _install_redis_stub()
