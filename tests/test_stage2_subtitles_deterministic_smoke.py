from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mlcore.models.stage1_plan import Stage1PlanPayload
from mlcore.models.subtitles_flow import SubtitleFlowPlan
from mlcore.models.subtitles_tokens import BlocksTokensPayload
from mlcore.subtitles_deterministic import build_subtitles_deterministic


MODES = (
    "legacy_blocks",
    "impulse_2nd",
    "scenes_3rd",
    "scenes_3rd_single_step",
    "template_4th",
)


def _draft_blocks() -> dict:
    return {
        "block_1": {"phrases": ["alpha beta"]},
        "block_2": {
            "p1": {"phrases": ["gamma delta"]},
            "p2": {"phrases": ["echo foxtrot"]},
        },
        "block_3": {"phrases": ["green horizon"]},
        "block_4": {
            "p1": {"phrases": ["inside journey"]},
            "p2": {"phrases": ["keep listening"]},
        },
        "block_5": {
            "slowly_in": {"phrases": ["long moment"]},
            "fast_reveal": {"phrases": ["moving now"]},
            "glitch_peak": {"phrases": ["night opens"]},
            "mine": {"phrases": ["power"]},
        },
        "block_6": {"phrases": ["quiet road"]},
        "block_7": {
            "part1": {"phrases": ["rise slowly"]},
            "part2": {"phrases": ["zero ending"]},
        },
    }


def _stage1(case: int) -> Stage1PlanPayload:
    base_words = [
        "Alpha,", "beta", "gamma", "delta", "echo", "foxtrot", "green", "horizon",
        "inside", "journey", "keep", "listening", "long", "moment", "moving", "now",
        "night", "opens", "power", "quiet", "road", "rise", "slowly", "zero", "ending",
        "after", "glow", "find", "another", "signal", "under", "silver", "rain",
    ]
    if case == 1:
        base_words[6] = "сильнее"
        base_words[18] = "НАВСЕГДА!"
    elif case == 2:
        base_words[9] = "don't"
        base_words[10] = "U-Haul"
    elif case == 3:
        base_words[12:15] = ["go", "go", "go"]
    elif case == 4:
        base_words[5] = "[ad-lib]"
        base_words[23] = "f**k"

    cursor = 10.05
    transcript = []
    for idx, text in enumerate(base_words):
        if case == 2 and idx in {5, 11, 18, 25}:
            cursor += 0.46
        elif case == 4 and idx in {8, 20}:
            cursor += 0.28
        duration = 0.22 + (idx % 3) * 0.055
        if case in {1, 4} and idx in {6, 18, 28}:
            duration = 0.64
        transcript.append({"text": text, "t_start": cursor, "t_end": cursor + duration})
        cursor += duration + 0.075

    return Stage1PlanPayload.model_validate(
        {
            "audio": {"clip_start_abs": 10.0, "clip_end_abs": cursor + 0.8},
            "transcript_words": transcript,
            "draft_blocks": _draft_blocks(),
        }
    )


def _legacy_segments(payload: BlocksTokensPayload):
    return [
        payload.block_1,
        payload.block_2.p1,
        payload.block_2.p2,
        payload.block_3,
        payload.block_4.p1,
        payload.block_4.p2,
        payload.block_5.slowly_in,
        payload.block_5.fast_reveal,
        payload.block_5.glitch_peak,
        payload.block_5.mine,
        payload.block_6,
        payload.block_7.part1,
        payload.block_7.part2,
    ]


@pytest.mark.parametrize("case", range(5), ids=lambda value: f"case_{value + 1}")
def test_legacy_blocks_deterministic_smoke(case: int) -> None:
    stage1 = _stage1(case)
    result = build_subtitles_deterministic(
        stage1=stage1,
        subtitles_mode="legacy_blocks",
        logger=logging.getLogger("test"),
    )
    assert isinstance(result, BlocksTokensPayload)
    segments = _legacy_segments(result)
    assert len(segments) == 13
    assert len(result.block_5.mine.tokens) == 1
    flattened = [token for segment in segments for token in segment.tokens]
    assert len(flattened) == len(stage1.transcript_words)
    assert [(t.t_start, t.t_end) for t in flattened] == [
        (w.t_start, w.t_end) for w in stage1.transcript_words
    ]
    again = build_subtitles_deterministic(
        stage1=stage1,
        subtitles_mode="legacy_blocks",
        logger=logging.getLogger("test"),
    )
    assert result.model_dump(mode="json") == again.model_dump(mode="json")


@pytest.mark.parametrize("mode", MODES[1:])
@pytest.mark.parametrize("case", range(5), ids=lambda value: f"case_{value + 1}")
def test_flow_mode_deterministic_smoke(mode: str, case: int) -> None:
    stage1 = _stage1(case)
    result = build_subtitles_deterministic(
        stage1=stage1,
        subtitles_mode=mode,
        logger=logging.getLogger("test"),
    )
    assert isinstance(result, SubtitleFlowPlan)
    assert result.mode == mode
    assert result.segments
    assert all(segment.out_point > segment.in_point for segment in result.segments)
    assert all(
        result.segments[i].in_point >= result.segments[i - 1].out_point - 1e-6
        for i in range(1, len(result.segments))
    )
    again = build_subtitles_deterministic(
        stage1=stage1,
        subtitles_mode=mode,
        logger=logging.getLogger("test"),
    )
    assert result.model_dump(mode="json") == again.model_dump(mode="json")


def test_template4_focus_is_present_in_every_subtitle_pair() -> None:
    result = build_subtitles_deterministic(
        stage1=_stage1(1),
        subtitles_mode="template_4th",
        logger=logging.getLogger("test"),
    )
    assert isinstance(result, SubtitleFlowPlan)
    for start in range(0, len(result.segments), 2):
        pair = result.segments[start : start + 2]
        assert any(token.focus for segment in pair for token in segment.tokens)


def test_template4_uses_lyrics_context_only_as_focus_tie_break() -> None:
    stage1 = _stage1(0)
    baseline = build_subtitles_deterministic(
        stage1=stage1,
        subtitles_mode="template_4th",
        logger=logging.getLogger("test"),
    )
    contextual = build_subtitles_deterministic(
        stage1=stage1,
        subtitles_mode="template_4th",
        logger=logging.getLogger("test"),
        lyrics_text="alpha beta\nalpha beta\ngamma delta",
        target_fragment="beta",
    )
    assert isinstance(baseline, SubtitleFlowPlan)
    assert isinstance(contextual, SubtitleFlowPlan)
    assert [segment.text for segment in contextual.segments] == [
        segment.text for segment in baseline.segments
    ]
    baseline_focus = [
        token.text for segment in baseline.segments for token in segment.tokens if token.focus
    ]
    contextual_focus = [
        token.text for segment in contextual.segments for token in segment.tokens if token.focus
    ]
    assert baseline_focus[0] == "gamma"
    assert contextual_focus[0] == "beta"


@pytest.mark.parametrize("mode", ["trendy_5th", "brat_5th"])
def test_jsx_modes_do_not_enter_deterministic_stage2(mode: str) -> None:
    with pytest.raises(RuntimeError, match="without this stage"):
        build_subtitles_deterministic(
            stage1=_stage1(0),
            subtitles_mode=mode,
            logger=logging.getLogger("test"),
        )


def test_orchestrator_stage2_branch_contains_no_subtitle_llm_call() -> None:
    source = Path("mlcore/gemini_orchestrator.py").read_text(encoding="utf-8")
    start = source.index("    def _run_subtitles_once()")
    end = source.index("    style_raw_payload:", start)
    branch = source[start:end]
    assert "build_subtitles_deterministic(" in branch
    assert "call_subtitles_plan_once(" not in branch
    assert "call_subtitles_plan_model_once(" not in branch
