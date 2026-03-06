from __future__ import annotations

from mlcore.prompts.assemble import build_stage1b_scenario_user_prompt


def _asr_json() -> dict:
    return {
        "transcript_words": [
            {"text": "BILLIE", "t_start": 0.1, "t_end": 0.3},
            {"text": "JEAN", "t_start": 0.31, "t_end": 0.55},
        ]
    }


def test_stage1b_prompt_default_branch_without_target_fragment() -> None:
    prompt = build_stage1b_scenario_user_prompt(
        asr_json=_asr_json(),
        target_fragment="",
        schema_name="Stage1ScenarioPayload",
    )
    assert "USER_TARGET_FRAGMENT_BRANCH=ON" not in prompt
    assert "UNIVERSAL_RULES_FOR_TARGET_FRAGMENT" not in prompt


def test_stage1b_prompt_includes_target_fragment_branch_rules() -> None:
    prompt = build_stage1b_scenario_user_prompt(
        asr_json=_asr_json(),
        target_fragment="SHE IS NOT MY LOVER",
        schema_name="Stage1ScenarioPayload",
    )
    assert "USER_TARGET_FRAGMENT_BRANCH=ON" in prompt
    assert "USER_TARGET_FRAGMENT:" in prompt
    assert "SHE IS NOT MY LOVER" in prompt
    assert "Working audio window MUST remain 13..18 seconds." in prompt
    assert "Maximize overlap of the selected working window with USER_TARGET_FRAGMENT." in prompt
