from __future__ import annotations

from typing import Final


LLM_WORKER_TYPE_SDK: Final[str] = "sdk"
LLM_WORKER_TYPE_OPENROUTER: Final[str] = "openrouter"
LLM_WORKER_TYPE_HYBRID: Final[str] = "hybrid"
LLM_WORKER_TYPE_VERTEX_SDK_MIX: Final[str] = "vertex_sdk_mix"

LLM_WORKER_TYPES: Final[tuple[str, ...]] = (
    LLM_WORKER_TYPE_SDK,
    LLM_WORKER_TYPE_OPENROUTER,
    LLM_WORKER_TYPE_HYBRID,
    LLM_WORKER_TYPE_VERTEX_SDK_MIX,
)


def normalize_llm_worker_type(raw: str, *, default: str = LLM_WORKER_TYPE_SDK) -> str:
    v = (raw or "").strip().lower()
    if not v:
        return default
    if v not in LLM_WORKER_TYPES:
        raise RuntimeError(f"LLM worker type must be one of: {', '.join(LLM_WORKER_TYPES)}")
    return v
