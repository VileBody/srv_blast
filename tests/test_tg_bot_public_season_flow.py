"""Public-side regression for the season flow mirror (Hooks S1).

The season flow logic lives in tg_bot_botapi; tg_bot_public mirrors the
state-machine constants and ChatState fields to satisfy the parity gate.
The actual user-facing handlers are NOT wired in public yet — entry is
gated by SEASON_FLOW_ENABLED env var (default off).

These tests pin that contract: the symbols exist, defaults are sane, and
the route guard returns False unless the flag is explicitly enabled.
"""
from __future__ import annotations

import os
import sys
import types

# state_store needs redis at import time; stub it for unit tests.
if "redis.asyncio" not in sys.modules:
    redis_module = types.ModuleType("redis")
    redis_asyncio = types.ModuleType("redis.asyncio")

    class _RedisStub:  # pragma: no cover
        pass

    redis_asyncio.Redis = _RedisStub
    redis_module.asyncio = redis_asyncio
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_asyncio


def test_season_stage_constants_mirrored() -> None:
    from services.tg_bot_public.state_store import (
        SEASON_STAGES,
        STAGE_SEASON_CONSENT,
        STAGE_SEASON_INTRO_1,
        STAGE_SEASON_INTRO_2,
        STAGE_SEASON_MENU,
    )

    assert STAGE_SEASON_INTRO_1 == "SEASON_INTRO_1"
    assert STAGE_SEASON_INTRO_2 == "SEASON_INTRO_2"
    assert STAGE_SEASON_CONSENT == "SEASON_CONSENT"
    assert STAGE_SEASON_MENU == "SEASON_MENU"
    assert SEASON_STAGES == frozenset({
        STAGE_SEASON_INTRO_1,
        STAGE_SEASON_INTRO_2,
        STAGE_SEASON_CONSENT,
        STAGE_SEASON_MENU,
    })


def test_chatstate_defaults_match_botapi_contract() -> None:
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(chat_id=42)
    # New free user — onboarding has not begun and notifications default to
    # the conservative "finals_only" tier per TZ §1.3.
    assert st.season_intro_step == 0
    assert st.season_intro_completed is False
    assert st.season_update_frequency == "finals_only"
    assert st.season_account_status == "new_free"
    assert st.season_waitlist is False
    assert st.season_referrer_tier == 0
    assert st.season_referrals_count == 0


def test_kill_switch_env_var_name_is_shared(monkeypatch) -> None:
    """SEASON_FLOW_ENABLED is the single env var shared by both bots; if
    someone renames it here, the botapi gate will silently diverge."""
    monkeypatch.delenv("SEASON_FLOW_ENABLED", raising=False)
    if "services.tg_bot_public.app" in sys.modules:
        del sys.modules["services.tg_bot_public.app"]
    from services.tg_bot_public import app as public_app

    assert public_app.SEASON_FLOW_ENABLED is False


def test_route_guard_off_by_default(monkeypatch) -> None:
    """Without SEASON_FLOW_ENABLED the guard must short-circuit to False
    even for a chat that looks like a season participant."""
    monkeypatch.delenv("SEASON_FLOW_ENABLED", raising=False)
    # Reload the module so the gate constant picks up the cleared env.
    if "services.tg_bot_public.app" in sys.modules:
        del sys.modules["services.tg_bot_public.app"]
    from services.tg_bot_public.app import _should_route_to_season
    from services.tg_bot_public.state_store import (
        ChatState,
        STAGE_SEASON_MENU,
    )

    in_season = ChatState(chat_id=1, stage=STAGE_SEASON_MENU,
                          season_intro_completed=True)
    assert _should_route_to_season(in_season) is False


def test_route_guard_on_routes_season_users(monkeypatch) -> None:
    """With the flag on, a chat already in a season stage or that finished
    intro should be picked up by the guard."""
    monkeypatch.setenv("SEASON_FLOW_ENABLED", "1")
    # Reload so the module-level constant reflects the patched env.
    if "services.tg_bot_public.app" in sys.modules:
        del sys.modules["services.tg_bot_public.app"]
    from services.tg_bot_public.app import _should_route_to_season
    from services.tg_bot_public.state_store import (
        ChatState,
        STAGE_IDLE,
        STAGE_SEASON_INTRO_1,
    )

    new_user = ChatState(chat_id=2, stage=STAGE_IDLE)
    assert _should_route_to_season(new_user) is False

    mid_intro = ChatState(chat_id=3, stage=STAGE_SEASON_INTRO_1)
    assert _should_route_to_season(mid_intro) is True

    finished_intro = ChatState(chat_id=4, stage=STAGE_IDLE,
                               season_intro_completed=True)
    assert _should_route_to_season(finished_intro) is True

    # Cleanup so other tests in the session see the default-off behavior.
    monkeypatch.delenv("SEASON_FLOW_ENABLED", raising=False)
    if "services.tg_bot_public.app" in sys.modules:
        del sys.modules["services.tg_bot_public.app"]
