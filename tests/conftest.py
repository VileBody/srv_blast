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
    aiogram_client_telegram_mod = types.ModuleType("aiogram.client.telegram")
    aiogram_client_session_mod = types.ModuleType("aiogram.client.session")
    aiogram_client_session_aiohttp_mod = types.ModuleType("aiogram.client.session.aiohttp")

    class _Dummy:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

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

        async def send_photo(self, *args, **kwargs):
            _ = (args, kwargs)
            return None

        async def send_video(self, *args, **kwargs):
            _ = (args, kwargs)
            return None

        async def send_animation(self, *args, **kwargs):
            _ = (args, kwargs)
            return None

        async def send_document(self, *args, **kwargs):
            _ = (args, kwargs)
            return None

        async def set_my_commands(self, *args, **kwargs):
            _ = (args, kwargs)
            return None

    class TelegramBadRequest(Exception):
        pass

    class TelegramAPIServer:
        def __init__(self, base: str, file: str) -> None:
            self.base = base
            self.file = file

        @classmethod
        def from_base(cls, base: str) -> "TelegramAPIServer":
            base = base.rstrip("/")
            return cls(
                base=f"{base}/bot{{token}}/{{method}}",
                file=f"{base}/file/bot{{token}}/{{path}}",
            )

    TelegramAPIServer.PRODUCTION = TelegramAPIServer(
        base="https://api.telegram.org/bot{token}/{method}",
        file="https://api.telegram.org/file/bot{token}/{path}",
    )
    TelegramAPIServer.TEST = TelegramAPIServer(
        base="https://api.telegram.org/bot{token}/test/{method}",
        file="https://api.telegram.org/file/bot{token}/test/{path}",
    )

    class TelegramForbiddenError(Exception):
        pass

    class TelegramRetryAfter(Exception):
        def __init__(self, *args, retry_after: float = 1.0, **kwargs) -> None:
            super().__init__(*args)
            _ = kwargs
            self.retry_after = float(retry_after)

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
    aiogram_client_telegram_mod.TelegramAPIServer = TelegramAPIServer
    aiogram_client_session_aiohttp_mod.AiohttpSession = _Dummy

    sys.modules["aiogram"] = aiogram_mod
    sys.modules["aiogram.exceptions"] = aiogram_exceptions_mod
    sys.modules["aiogram.filters"] = aiogram_filters_mod
    sys.modules["aiogram.types"] = aiogram_types_mod
    sys.modules["aiogram.client"] = aiogram_client_mod
    sys.modules["aiogram.client.telegram"] = aiogram_client_telegram_mod
    sys.modules["aiogram.client.session"] = aiogram_client_session_mod
    sys.modules["aiogram.client.session.aiohttp"] = aiogram_client_session_aiohttp_mod


def _install_redis_stub() -> None:
    redis_mod = types.ModuleType("redis")
    redis_asyncio_mod = types.ModuleType("redis.asyncio")

    class _RedisError(Exception):
        pass

    class _FakeRedis:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

    redis_mod.Redis = _FakeRedis
    redis_asyncio_mod.Redis = _FakeRedis
    redis_mod.exceptions = SimpleNamespace(
        ConnectionError=_RedisError,
        TimeoutError=_RedisError,
    )
    redis_asyncio_mod.exceptions = redis_mod.exceptions
    redis_mod.asyncio = redis_asyncio_mod
    sys.modules["redis"] = redis_mod
    sys.modules["redis.asyncio"] = redis_asyncio_mod


def _install_asyncpg_stub() -> None:
    asyncpg_mod = types.ModuleType("asyncpg")

    class _DummyPool:
        async def close(self):
            return None

    class _DummyConnection:
        pass

    async def _create_pool(*args, **kwargs):
        _ = (args, kwargs)
        return _DummyPool()

    asyncpg_mod.Pool = _DummyPool
    asyncpg_mod.Connection = _DummyConnection
    asyncpg_mod.create_pool = _create_pool
    sys.modules["asyncpg"] = asyncpg_mod


def _install_aiohttp_stub() -> None:
    aiohttp_mod = types.ModuleType("aiohttp")
    aiohttp_web_mod = types.ModuleType("aiohttp.web")

    class _DummyRequest:
        async def read(self):
            return b""

        async def text(self):
            return ""

        async def json(self):
            return {}

    class _DummyResponse:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

    class _DummyHTTPError(Exception):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args)
            _ = kwargs

    class _DummyApplication:
        def add_routes(self, *_args, **_kwargs):
            return None

    class _DummyAppRunner:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

        async def setup(self):
            return None

        async def cleanup(self):
            return None

    class _DummyTCPSite:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

        async def start(self):
            return None

    def _json_response(*args, **kwargs):
        return _DummyResponse(*args, **kwargs)

    aiohttp_web_mod.Request = _DummyRequest
    aiohttp_web_mod.Response = _DummyResponse
    aiohttp_web_mod.Application = _DummyApplication
    aiohttp_web_mod.AppRunner = _DummyAppRunner
    aiohttp_web_mod.TCPSite = _DummyTCPSite
    aiohttp_web_mod.HTTPForbidden = _DummyHTTPError
    aiohttp_web_mod.HTTPBadRequest = _DummyHTTPError
    aiohttp_web_mod.json_response = _json_response

    aiohttp_mod.web = aiohttp_web_mod
    sys.modules["aiohttp"] = aiohttp_mod
    sys.modules["aiohttp.web"] = aiohttp_web_mod


if importlib.util.find_spec("celery") is None:
    _install_celery_stub()

if importlib.util.find_spec("aiogram") is None:
    _install_aiogram_stub()

if importlib.util.find_spec("redis") is None:
    _install_redis_stub()

if importlib.util.find_spec("asyncpg") is None:
    _install_asyncpg_stub()

if importlib.util.find_spec("aiohttp") is None:
    _install_aiohttp_stub()
