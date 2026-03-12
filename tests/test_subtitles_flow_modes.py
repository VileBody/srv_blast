from __future__ import annotations

import logging

from app.text_comp import build_text_layers
from mlcore.models.stage1_plan import Stage1PlanPayload
from mlcore.models.subtitles_flow import Impulse2ndPayload, Scenes3rdPayload
from mlcore.subtitles_flow import SubtitlesPlannerFactory


def _draft_blocks() -> dict:
    return {
        "block_1": {"phrases": ["a"]},
        "block_2": {"p1": {"phrases": ["b"]}, "p2": {"phrases": ["c"]}},
        "block_3": {"phrases": ["d"]},
        "block_4": {"p1": {"phrases": ["e"]}, "p2": {"phrases": ["f"]}},
        "block_5": {
            "slowly_in": {"phrases": ["g"]},
            "fast_reveal": {"phrases": ["h"]},
            "glitch_peak": {"phrases": ["i"]},
            "mine": {"phrases": ["j"]},
        },
        "block_6": {"phrases": ["k"]},
        "block_7": {"part1": {"phrases": ["l"]}, "part2": {"phrases": ["m"]}},
    }


def _stage1() -> Stage1PlanPayload:
    return Stage1PlanPayload.model_validate(
        {
            "audio": {"clip_start_abs": 10.0, "clip_end_abs": 24.0},
            "transcript_words": [
                {"text": "hello", "t_start": 10.0, "t_end": 10.4},
                {"text": "world", "t_start": 10.5, "t_end": 11.0},
            ],
            "draft_blocks": _draft_blocks(),
        }
    )


def _assert_keyframes_within_bounds(layers: list[dict]) -> None:
    allowed_interp = {"6612", "6613", "6614", None}
    for layer in layers:
        if str(layer.get("type")) != "text":
            continue
        in_point = float(layer.get("in_point"))
        out_point = float(layer.get("out_point"))
        for prop in (layer.get("props") or {}).values():
            if not isinstance(prop, dict):
                continue
            kfs = prop.get("keyframes")
            if not isinstance(kfs, list):
                continue
            prev_t = None
            for kf in kfs:
                t = float(kf.get("t"))
                assert in_point - 1e-6 <= t <= out_point + 1e-6
                if prev_t is not None:
                    assert t >= prev_t - 1e-9
                prev_t = t
                assert kf.get("iit") in allowed_interp
                assert kf.get("oit") in allowed_interp


def test_impulse_mode_planner_and_renderer(monkeypatch) -> None:
    planner = SubtitlesPlannerFactory.create("impulse_2nd")
    payload = Impulse2ndPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "segments": [
                {
                    "text": "hello world",
                    "in": 10.0,
                    "out": 11.5,
                    "type": "long",
                    "word_timings": [
                        {"word": "hello", "start": 10.0, "end": 10.4},
                        {"word": "world", "start": 10.5, "end": 11.0},
                    ],
                },
                {
                    "text": "boom",
                    "in": 11.6,
                    "out": 12.1,
                    "type": "short",
                    "word_timings": [
                        {"word": "boom", "start": 11.6, "end": 11.9},
                    ],
                },
            ],
        }
    )
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    assert flow.mode == "impulse_2nd"
    assert len(flow.segments) == 2

    monkeypatch.setenv("TEXT_LAYER_TIME_SHIFT_S", "0")
    full_edit = {
        "subtitles_mode": "impulse_2nd",
        "subtitle_flow_plan": flow.model_dump(mode="json"),
        "composition": {"fps": 23.976, "dur": 14.0},
    }
    layers = build_text_layers(
        full_edit_config=full_edit,
        text_comp_name="Текст",
        mine_comp_name='Текст "Mine"',
    )
    assert len([x for x in layers if str(x.get("type")) == "text"]) == 2
    _assert_keyframes_within_bounds(layers)


def test_scenes_mode_planner_and_renderer(monkeypatch) -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 1,
                    "type": "TYPE_1",
                    "words": ["hello", "world"],
                    "start": 10.0,
                    "end": 11.2,
                    "lines": [["hello"], ["world"]],
                    "word_timings": [
                        {"word": "hello", "start": 10.0, "end": 10.4},
                        {"word": "world", "start": 10.5, "end": 11.0},
                    ],
                },
                {
                    "id": 2,
                    "type": "TYPE_4",
                    "words": ["boom"],
                    "start": 11.4,
                    "end": 12.0,
                    "lines": [["boom"]],
                    "focus_word": "boom",
                    "focus_style": "red",
                    "word_timings": [
                        {"word": "boom", "start": 11.4, "end": 11.8},
                    ],
                },
            ],
        }
    )
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    assert flow.mode == "scenes_3rd"
    assert len(flow.segments) == 2

    monkeypatch.setenv("TEXT_LAYER_TIME_SHIFT_S", "0")
    full_edit = {
        "subtitles_mode": "scenes_3rd",
        "subtitle_flow_plan": flow.model_dump(mode="json"),
        "composition": {"fps": 23.976, "dur": 14.0},
    }
    layers = build_text_layers(
        full_edit_config=full_edit,
        text_comp_name="Текст",
        mine_comp_name='Текст "Mine"',
    )
    text_layers = [x for x in layers if str(x.get("type")) == "text"]
    assert len(text_layers) == 2
    _assert_keyframes_within_bounds(layers)
