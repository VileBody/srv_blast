"""Public-side regression for the hook flow mirror (Phase A-UX).

The hook flow logic lives in tg_bot_botapi; tg_bot_public mirrors the
state-machine constants, ChatState hook_* fields, and orchestrator-client
kwargs to satisfy the parity gate. The actual user-facing handlers are
NOT wired in public yet — entry is gated by HOOK_FLOW_ENABLED env var
(default off). The orchestrator-client kwargs DO flow into the request
payload regardless of the flag, so a chat state pre-populated with hook
data (e.g. via DB migration) would propagate it cleanly.

These tests pin that contract: the symbols exist, defaults are sane, the
route guard returns False unless the flag is explicitly enabled, and the
orchestrator-client payload includes the new fields.
"""
from __future__ import annotations

import sys
import types


# state_store / app.py need redis at import time; stub it for unit tests.
if "redis.asyncio" not in sys.modules:
    redis_module = types.ModuleType("redis")
    redis_asyncio = types.ModuleType("redis.asyncio")

    class _RedisStub:  # pragma: no cover
        pass

    redis_asyncio.Redis = _RedisStub
    redis_module.asyncio = redis_asyncio
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_asyncio


def test_hook_stage_constants_mirrored() -> None:
    from services.tg_bot_public.state_store import (
        STAGE_WAIT_HOOK_CHOICE,
        STAGE_WAIT_HOOK_DROP,
        STAGE_WAIT_HOOK_DROP_MANUAL,
        STAGE_WAIT_HOOK_TYPE,
    )

    # Stable string values are part of the schema contract — chat states
    # serialized by either bot must round-trip through the other.
    assert STAGE_WAIT_HOOK_CHOICE == "WAIT_HOOK_CHOICE"
    assert STAGE_WAIT_HOOK_DROP == "WAIT_HOOK_DROP"
    assert STAGE_WAIT_HOOK_DROP_MANUAL == "WAIT_HOOK_DROP_MANUAL"
    assert STAGE_WAIT_HOOK_TYPE == "WAIT_HOOK_TYPE"


def test_chatstate_hook_defaults_match_botapi_contract() -> None:
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(chat_id=42)
    # Defaults must exactly match the tg_bot_botapi schema or a chat state
    # written by one bot would not deserialize cleanly in the other.
    assert st.hook_enabled is False
    assert st.hook_drop_t is None
    assert st.hook_type == "standard"
    assert st.hook_analysis_status == ""
    assert st.hook_analysis_audio_path == ""
    assert st.hook_analysis_clip_start == 0.0
    assert st.hook_analysis_clip_end == 0.0
    assert st.hook_drop_candidates == []
    assert st.hook_analysis_bpm == 0.0
    assert st.hook_analysis_error == ""


def test_chatstate_accepts_populated_hook_fields() -> None:
    """The mirror must accept a state pre-populated with the same shape the
    test bot would produce — guard against future drift on field types."""
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(
        chat_id=1,
        hook_enabled=True,
        hook_drop_t=12.5,
        hook_type="standard",
        hook_analysis_status="ready",
        hook_analysis_audio_path="/tmp/audio.mp3",
        hook_analysis_clip_start=0.0,
        hook_analysis_clip_end=22.0,
        hook_drop_candidates=[
            {"t": 12.5, "confidence": 0.94, "snapped_to_beat": True, "source": "rms+flux"},
        ],
    )
    assert st.hook_drop_t == 12.5
    assert st.hook_drop_candidates[0]["confidence"] == 0.94


def test_hook_flow_guard_off_by_default(monkeypatch) -> None:
    """HOOK_FLOW_ENABLED must default to OFF so importing the public app
    never accidentally enables the picker in production."""
    monkeypatch.delenv("HOOK_FLOW_ENABLED", raising=False)
    # Force module reload so the env-derived constant is recomputed.
    sys.modules.pop("services.tg_bot_public.app", None)
    from services.tg_bot_public.app import HOOK_FLOW_ENABLED

    assert HOOK_FLOW_ENABLED is False


def test_hook_flow_guard_route_returns_false_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv("HOOK_FLOW_ENABLED", raising=False)
    sys.modules.pop("services.tg_bot_public.app", None)
    from services.tg_bot_public.app import _should_route_to_hook_flow
    from services.tg_bot_public.state_store import (
        ChatState,
        STAGE_WAIT_HOOK_CHOICE,
        STAGE_WAIT_HOOK_DROP,
    )

    # Even with a chat parked on a hook stage, route must refuse while the
    # flag is off — this is what keeps prod unaffected by the mirror.
    for stage in (STAGE_WAIT_HOOK_CHOICE, STAGE_WAIT_HOOK_DROP):
        st = ChatState(chat_id=1, stage=stage)
        assert _should_route_to_hook_flow(st) is False


def test_hook_flow_guard_route_honors_flag_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("HOOK_FLOW_ENABLED", "1")
    sys.modules.pop("services.tg_bot_public.app", None)
    from services.tg_bot_public.app import _should_route_to_hook_flow
    from services.tg_bot_public.state_store import (
        ChatState,
        STAGE_IDLE,
        STAGE_WAIT_HOOK_DROP,
    )

    # On idle chat the route stays False — entry requires the chat to
    # already be on a hook stage (handlers will land it there in a
    # follow-up PR).
    assert _should_route_to_hook_flow(ChatState(chat_id=1, stage=STAGE_IDLE)) is False
    # Chat resumed mid-flow on a hook stage routes in.
    assert _should_route_to_hook_flow(
        ChatState(chat_id=1, stage=STAGE_WAIT_HOOK_DROP)
    ) is True

    # Cleanup: don't leak the flag into other test files.
    monkeypatch.delenv("HOOK_FLOW_ENABLED", raising=False)
    sys.modules.pop("services.tg_bot_public.app", None)


def test_orchestrator_client_payload_carries_hook_fields() -> None:
    """The send_audio_s3 kwargs must reach the orchestrator payload even
    while HOOK_FLOW_ENABLED is off — chat states pre-populated by DB
    migration or admin tooling should propagate without code changes."""
    import inspect

    from services.tg_bot_public.orchestrator_client import OrchestratorClient

    sig = inspect.signature(OrchestratorClient.send_audio_s3)
    assert "hook_enabled" in sig.parameters
    assert "user_drop_t" in sig.parameters
    assert sig.parameters["hook_enabled"].default is False
    assert sig.parameters["user_drop_t"].default is None
    # F4 «Движение» motion device kwarg mirrored (Phase: AE-FX).
    assert "f4_device" in sig.parameters
    assert sig.parameters["f4_device"].default is None

    # Hook focus-clip analysis is delegated to the orchestrator (librosa lives in
    # the runtime image, not the slim bot image). Both bots expose analyze_hook.
    ah = inspect.signature(OrchestratorClient.analyze_hook)
    assert {"audio_s3_url", "clip_start_sec", "clip_end_sec"}.issubset(ah.parameters)


def test_f4_motion_device_ids_mirrored() -> None:
    """Public bot mirrors the F4 motion device id set so a chat state
    pre-populated with a motion device round-trips through the payload even
    while the picker UX is botapi-only."""
    from services.tg_bot_public.app import (
        F4_MOTION_DEVICE_IDS,
        F4_MOTION_DEVICE_LABELS_RU,
    )

    assert "swipe" in F4_MOTION_DEVICE_IDS
    assert F4_MOTION_DEVICE_IDS == {"swipe", "tap", "pinch", "holdfinger", "head"}
    # RU label map mirrors the botapi picker; values are exactly the id set.
    assert set(F4_MOTION_DEVICE_LABELS_RU.values()) == F4_MOTION_DEVICE_IDS
    assert F4_MOTION_DEVICE_LABELS_RU["Свайп"] == "swipe"

    from services.tg_bot_public.app import F4_REF_BPM

    # Mirrored constant: the motion-hook reframe uses lead_eff =
    # LEAD[device] * F4_REF_BPM / bpm so the overlay cover-end lands on the drop.
    assert F4_REF_BPM == 128.0
