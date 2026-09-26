from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.orchestrator import tasks


class _RetryScheduled(Exception):
    pass


class _Task:
    max_retries = 8

    def __init__(self, retries: int) -> None:
        self.request = SimpleNamespace(retries=retries)
        self.retry_kwargs: dict[str, object] = {}

    def retry(self, **kwargs: object) -> None:
        self.retry_kwargs = kwargs
        raise _RetryScheduled


def test_sosana_transient_uses_provider_retry_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOSANA_MAX_RETRIES", "3")
    task = _Task(retries=2)

    with pytest.raises(_RetryScheduled):
        tasks._maybe_retry_sosana_transient(
            task,
            "sosana_timeout: ReadTimeout('timed out')",
            phase="build_all",
        )

    assert task.retry_kwargs["max_retries"] == 3
    assert task.retry_kwargs["countdown"] == 40.0


def test_sosana_transient_fails_after_provider_retry_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOSANA_MAX_RETRIES", "3")
    task = _Task(retries=3)

    with pytest.raises(RuntimeError, match="sosana_transient_retries_exhausted retries=3/3"):
        tasks._maybe_retry_sosana_transient(
            task,
            "sosana_timeout: ReadTimeout('timed out')",
            phase="build_all",
        )

    assert task.retry_kwargs == {}
