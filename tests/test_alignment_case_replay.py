# -*- coding: utf-8 -*-
"""The replay harness has to be trustworthy before its verdicts mean anything."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import replay_alignment_cases as replay  # noqa: E402

from mlcore.alignment.client import AlignmentServiceError  # noqa: E402


def test_shipped_corpus_parses_and_declares_verdicts() -> None:
    cases = replay.load_cases(replay.DEFAULT_CASES_PATH)

    assert cases, "the corpus must not be empty"
    for case in cases:
        assert case["case_id"]
        assert case["expect"] in {"success", "failure"}
        if case["expect"] == "failure":
            assert case["expect_error_code"], case["case_id"]
        assert float(case["clip_end_abs"]) > float(case["clip_start_abs"])


def test_blank_lines_and_comments_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "# a comment\n\n"
        + json.dumps({"case_id": "one", "expect": "success"})
        + "\n",
        encoding="utf-8",
    )

    assert [case["case_id"] for case in replay.load_cases(path)] == ["one"]


def test_malformed_case_is_an_error_not_a_silent_skip(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")

    with pytest.raises(replay.CaseError):
        replay.load_cases(path)


def test_success_verdict_honours_the_overflow_ceiling() -> None:
    response = SimpleNamespace(
        diagnostics={
            "dynamic_window": {
                "selected_left_window_overflow_sec": 0.0,
                "selected_right_window_overflow_sec": 0.35,
            }
        }
    )

    assert replay._check_success({"expect": "success"}, response) == ""
    assert (
        replay._check_success(
            {"expect": "success", "max_boundary_overflow_sec": 0.4}, response
        )
        == ""
    )
    assert "exceeds the recorded ceiling" in replay._check_success(
        {"expect": "success", "max_boundary_overflow_sec": 0.2}, response
    )


def test_unexpected_success_is_reported() -> None:
    response = SimpleNamespace(diagnostics={})

    problem = replay._check_success(
        {"expect": "failure", "expect_error_code": "ALIGNMENT_WINDOW_MISMATCH"},
        response,
    )

    assert "but alignment succeeded" in problem


def test_failure_verdict_requires_the_recorded_code() -> None:
    exc = AlignmentServiceError("ALIGNMENT_WINDOW_MISMATCH", "no candidates")

    assert (
        replay._check_failure(
            {"expect": "failure", "expect_error_code": "ALIGNMENT_WINDOW_MISMATCH"},
            exc,
        )
        == ""
    )
    assert "expected ALIGNMENT_TEXT_TOO_LONG_FOR_WINDOW" in replay._check_failure(
        {"expect": "failure", "expect_error_code": "ALIGNMENT_TEXT_TOO_LONG_FOR_WINDOW"},
        exc,
    )
    assert "expected success" in replay._check_failure({"expect": "success"}, exc)
