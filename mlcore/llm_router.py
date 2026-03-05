from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import logging
import time
from typing import Callable, Dict, Generic, Optional, TypeVar


T = TypeVar("T")

PROVIDER_MODE_GEMINI = "gemini"
PROVIDER_MODE_OPENROUTER = "openrouter"
PROVIDER_MODE_HEDGED = "hedged"
_ALLOWED_PROVIDER_MODES = {
    PROVIDER_MODE_GEMINI,
    PROVIDER_MODE_OPENROUTER,
    PROVIDER_MODE_HEDGED,
}


def normalize_provider_mode(raw: str) -> str:
    mode = (raw or "").strip().lower()
    if not mode:
        return PROVIDER_MODE_GEMINI
    if mode not in _ALLOWED_PROVIDER_MODES:
        raise RuntimeError(
            "LLM_PROVIDER_MODE must be one of: gemini | openrouter | hedged"
        )
    return mode


@dataclass(frozen=True)
class RoutedCallResult(Generic[T]):
    provider: str
    value: T


def _make_logger(logger: Optional[logging.Logger]) -> logging.Logger:
    return logger or logging.getLogger("mlcore.llm_router")


def _format_exc(e: BaseException) -> str:
    return f"{type(e).__name__}: {e!s}"


def run_routed_call(
    *,
    mode: str,
    stage: str,
    hedge_delay_s: float,
    gemini_call: Callable[[], T],
    openrouter_call: Callable[[], T],
    logger: Optional[logging.Logger] = None,
) -> RoutedCallResult[T]:
    mode_norm = normalize_provider_mode(mode)
    log = _make_logger(logger)

    if mode_norm == PROVIDER_MODE_GEMINI:
        val = gemini_call()
        log.info("llm_routed_winner stage=%s provider=gemini mode=gemini", stage)
        return RoutedCallResult(provider=PROVIDER_MODE_GEMINI, value=val)

    if mode_norm == PROVIDER_MODE_OPENROUTER:
        val = openrouter_call()
        log.info("llm_routed_winner stage=%s provider=openrouter mode=openrouter", stage)
        return RoutedCallResult(provider=PROVIDER_MODE_OPENROUTER, value=val)

    delay = float(hedge_delay_s)
    if delay < 0:
        raise RuntimeError("LLM_HEDGE_DELAY_S must be >= 0")

    errors: Dict[str, BaseException] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm_hedge") as ex:
        futures: Dict[str, Future[T]] = {
            PROVIDER_MODE_GEMINI: ex.submit(gemini_call),
        }
        handled: set[Future[T]] = set()
        started_openrouter = False
        t0 = time.monotonic()

        while True:
            done_now = {f for f in futures.values() if f.done() and f not in handled}
            if done_now:
                done = done_now
            else:
                active = [f for f in futures.values() if not f.done()]
                if not active:
                    break

                timeout: Optional[float] = None
                if not started_openrouter:
                    remaining = delay - (time.monotonic() - t0)
                    timeout = max(0.0, remaining)

                done, _ = wait(set(active), timeout=timeout, return_when=FIRST_COMPLETED)

                if not done and not started_openrouter:
                    futures[PROVIDER_MODE_OPENROUTER] = ex.submit(openrouter_call)
                    started_openrouter = True
                    log.info("llm_hedge_secondary_started stage=%s delay_s=%s", stage, delay)
                    continue

            for fut in done:
                handled.add(fut)
                provider = next((p for p, f in futures.items() if f is fut), "unknown")
                try:
                    val = fut.result()
                    for p, other in futures.items():
                        if p == provider:
                            continue
                        if not other.done():
                            other.cancel()
                    log.info(
                        "llm_routed_winner stage=%s provider=%s mode=hedged",
                        stage,
                        provider,
                    )
                    return RoutedCallResult(provider=provider, value=val)
                except Exception as e:  # noqa: BLE001
                    errors[provider] = e
                    if provider == PROVIDER_MODE_GEMINI and not started_openrouter:
                        futures[PROVIDER_MODE_OPENROUTER] = ex.submit(openrouter_call)
                        started_openrouter = True
                        log.info(
                            "llm_hedge_secondary_started stage=%s reason=primary_failed",
                            stage,
                        )

        ordered = [
            PROVIDER_MODE_GEMINI,
            PROVIDER_MODE_OPENROUTER,
        ]
        detail = "; ".join(
            f"{name}={_format_exc(errors[name])}" for name in ordered if name in errors
        )
        raise RuntimeError(f"llm_hedged_all_failed stage={stage} errors=[{detail}]")
