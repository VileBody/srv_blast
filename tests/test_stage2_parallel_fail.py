from __future__ import annotations

import pytest

from mlcore.gemini_orchestrator import _run_stage2_parallel


def test_stage2_parallel_fail_fast_on_branch_error() -> None:
    def subtitles_ok() -> str:
        return "ok"

    def footage_fail() -> str:
        raise RuntimeError("footage_branch_failed")

    with pytest.raises(RuntimeError, match="footage_branch_failed"):
        _run_stage2_parallel(subtitles_ok, footage_fail)
