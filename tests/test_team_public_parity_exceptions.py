from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_team_public_parity import (
    TEAM_ONLY_EXCEPTIONS_PATH,
    _team_only_exceptions,
)


def _write_manifest(root: Path, payload: dict) -> None:
    path = root / TEAM_ONLY_EXCEPTIONS_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_team_only_exception_requires_manifest_in_diff(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        {
            "exceptions": [
                {
                    "name": "team_local_ctc_alignment",
                    "reason": "Team-only product contract",
                    "team_files": ["services/tg_bot_botapi/app.py"],
                    "required_tests": ["tests/test_tg_bot_botapi_alignment_flow.py"],
                }
            ]
        },
    )

    waived, labels = _team_only_exceptions(
        tmp_path,
        changed_set={
            "services/tg_bot_botapi/app.py",
            "tests/test_tg_bot_botapi_alignment_flow.py",
        },
    )

    assert waived == set()
    assert labels == []


def test_team_only_exception_requires_changed_evidence_test(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        {
            "exceptions": [
                {
                    "name": "team_local_ctc_alignment",
                    "reason": "Team-only product contract",
                    "team_files": ["services/tg_bot_botapi/app.py"],
                    "required_tests": ["tests/test_tg_bot_botapi_alignment_flow.py"],
                }
            ]
        },
    )

    with pytest.raises(RuntimeError, match="missing changed evidence tests"):
        _team_only_exceptions(
            tmp_path,
            changed_set={
                TEAM_ONLY_EXCEPTIONS_PATH,
                "services/tg_bot_botapi/app.py",
            },
        )


def test_team_only_exception_waives_only_declared_changed_files(
    tmp_path: Path,
) -> None:
    _write_manifest(
        tmp_path,
        {
            "exceptions": [
                {
                    "name": "team_local_ctc_alignment",
                    "reason": "Team-only product contract",
                    "team_files": [
                        "services/tg_bot_botapi/app.py",
                        "services/tg_bot_botapi/state_store.py",
                    ],
                    "required_tests": ["tests/test_tg_bot_botapi_alignment_flow.py"],
                }
            ]
        },
    )

    waived, labels = _team_only_exceptions(
        tmp_path,
        changed_set={
            TEAM_ONLY_EXCEPTIONS_PATH,
            "services/tg_bot_botapi/app.py",
            "tests/test_tg_bot_botapi_alignment_flow.py",
        },
    )

    assert waived == {"services/tg_bot_botapi/app.py"}
    assert labels == ["team_local_ctc_alignment"]
