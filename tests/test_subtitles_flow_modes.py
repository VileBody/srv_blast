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
                    "text": "Hello, I'm world!",
                    "in": 10.0,
                    "out": 11.5,
                    "type": "long",
                    "word_timings": [
                        {"word": "hello", "start": 10.0, "end": 10.4},
                        {"word": "i'm", "start": 10.45, "end": 10.75},
                        {"word": "world", "start": 10.8, "end": 11.0},
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
    text_layers = [x for x in layers if str(x.get("type")) == "text"]
    assert len(text_layers) == 2
    assert str(text_layers[0].get("text")) == "hello i'm world"
    for layer in text_layers:
        props = layer.get("props") or {}
        assert "reveal" not in props
        assert "reveal_end" not in props
        td = layer.get("text_data") or {}
        assert td.get("no_text_animator") is not True
        assert td.get("no_layout_pass") is True
        anim = td.get("text_animator")
        assert isinstance(anim, dict)
        ex = anim.get("expressible_selector") if isinstance(anim, dict) else None
        assert isinstance(ex, dict)
        amount = ex.get("amount") if isinstance(ex, dict) else None
        assert isinstance(amount, dict)
        assert isinstance(amount.get("expression"), str) and "delay" in amount.get("expression")
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
    scene_text = next(x for x in text_layers if str(x.get("name")) == "scene_001")
    scene_props = scene_text.get("props") or {}
    assert isinstance(scene_props.get("reveal"), dict)
    assert scene_props["reveal"].get("match_name") == "ADBE Text Percent Start"

    mine_layer = next(x for x in text_layers if str(x.get("name")).lower() == "mine")
    mine_td = mine_layer.get("text_data") or {}
    assert mine_td.get("no_text_animator") is True

    precomp_layers = [x for x in layers if str(x.get("type")) == "precomp"]
    precomp_names = {str(x.get("name")) for x in precomp_layers}
    assert 'Текст "Mine"' in precomp_names
    assert 'Текст "Mine" glow' in precomp_names
    _assert_keyframes_within_bounds(layers)


def test_scenes_type3_builds_progressive_layers(monkeypatch) -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 1,
                    "type": "TYPE_3",
                    "words": ["we", "will", "go"],
                    "start": 10.0,
                    "end": 11.6,
                    "lines": [["we", "will", "go"]],
                    "word_timings": [
                        {"word": "we", "start": 10.0, "end": 10.3},
                        {"word": "will", "start": 10.4, "end": 10.8},
                        {"word": "go", "start": 10.9, "end": 11.2},
                    ],
                }
            ],
        }
    )
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    monkeypatch.setenv("TEXT_LAYER_TIME_SHIFT_S", "0.3")
    layers = build_text_layers(
        full_edit_config={
            "subtitles_mode": "scenes_3rd",
            "subtitle_flow_plan": flow.model_dump(mode="json"),
            "composition": {"fps": 23.976, "dur": 14.0},
        },
        text_comp_name="Текст",
        mine_comp_name='Текст "Mine"',
    )
    adjustments = [x for x in layers if str(x.get("type")) == "adjustment"]
    text_layers = [x for x in layers if str(x.get("type")) == "text"]
    assert len(adjustments) == 1
    assert len(text_layers) == 3
    names = [str(x.get("name")) for x in text_layers]
    assert names == ["scene_001_01", "scene_001_02", "scene_001_03"]
    for layer in text_layers:
        props = layer.get("props") or {}
        assert "reveal" not in props
        td = layer.get("text_data") or {}
        assert td.get("no_text_animator") is True
    assert "ADBE Box Blur2" in (text_layers[-1].get("effects") or {})
    _assert_keyframes_within_bounds(layers)


def test_scenes_type5_builds_outline_and_fill(monkeypatch) -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 1,
                    "type": "TYPE_5",
                    "words": ["stay", "with", "me"],
                    "start": 10.0,
                    "end": 11.2,
                    "lines": [["stay", "with", "me"]],
                    "word_timings": [
                        {"word": "stay", "start": 10.0, "end": 10.2},
                        {"word": "with", "start": 10.3, "end": 10.6},
                        {"word": "me", "start": 10.7, "end": 11.0},
                    ],
                }
            ],
        }
    )
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    monkeypatch.setenv("TEXT_LAYER_TIME_SHIFT_S", "0.3")
    layers = build_text_layers(
        full_edit_config={
            "subtitles_mode": "scenes_3rd",
            "subtitle_flow_plan": flow.model_dump(mode="json"),
            "composition": {"fps": 23.976, "dur": 14.0},
        },
        text_comp_name="Текст",
        mine_comp_name='Текст "Mine"',
    )
    adjustments = [x for x in layers if str(x.get("type")) == "adjustment"]
    text_layers = [x for x in layers if str(x.get("type")) == "text"]
    assert len(adjustments) == 1
    assert len(text_layers) == 2
    outline = next(x for x in text_layers if str(x.get("name")).endswith("_outline"))
    fill = next(x for x in text_layers if str(x.get("name")) == "scene_001")

    outline_props = outline.get("props") or {}
    assert "reveal_end" in outline_props
    assert "reveal" not in outline_props
    fill_props = fill.get("props") or {}
    assert "reveal" in fill_props
    assert "ADBE Box Blur2" in (fill.get("effects") or {})
    for layer in (outline, fill):
        td = layer.get("text_data") or {}
        assert isinstance(td.get("text_animator"), dict)
        assert td.get("no_text_animator") is not True
    _assert_keyframes_within_bounds(layers)


def test_flow_modes_ignore_global_text_shift(monkeypatch) -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 1,
                    "type": "TYPE_1",
                    "words": ["hello"],
                    "start": 10.0,
                    "end": 10.8,
                    "lines": [["hello"]],
                    "word_timings": [{"word": "hello", "start": 10.0, "end": 10.6}],
                }
            ],
        }
    )
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    monkeypatch.setenv("TEXT_LAYER_TIME_SHIFT_S", "0.3")
    layers = build_text_layers(
        full_edit_config={
            "subtitles_mode": "scenes_3rd",
            "subtitle_flow_plan": flow.model_dump(mode="json"),
            "composition": {"fps": 23.976, "dur": 14.0},
        },
        text_comp_name="Текст",
        mine_comp_name='Текст "Mine"',
    )
    scene_text = next(x for x in layers if str(x.get("name")) == "scene_001")
    assert abs(float(scene_text.get("in_point")) - 10.0) < 1e-6


def test_impulse_mode_ignores_global_text_shift_and_keeps_drop_shadows(monkeypatch) -> None:
    planner = SubtitlesPlannerFactory.create("impulse_2nd")
    payload = Impulse2ndPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "segments": [
                {
                    "text": "Long phrase",
                    "in": 10.0,
                    "out": 11.3,
                    "type": "long",
                    "word_timings": [
                        {"word": "long", "start": 10.0, "end": 10.4},
                        {"word": "phrase", "start": 10.5, "end": 11.0},
                    ],
                }
            ],
        }
    )
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    monkeypatch.setenv("TEXT_LAYER_TIME_SHIFT_S", "0.3")
    layers_shifted = build_text_layers(
        full_edit_config={
            "subtitles_mode": "impulse_2nd",
            "subtitle_flow_plan": flow.model_dump(mode="json"),
            "composition": {"fps": 23.976, "dur": 14.0},
        },
        text_comp_name="Текст",
        mine_comp_name='Текст "Mine"',
    )

    monkeypatch.setenv("TEXT_LAYER_TIME_SHIFT_S", "0")
    layers_plain = build_text_layers(
        full_edit_config={
            "subtitles_mode": "impulse_2nd",
            "subtitle_flow_plan": flow.model_dump(mode="json"),
            "composition": {"fps": 23.976, "dur": 14.0},
        },
        text_comp_name="Текст",
        mine_comp_name='Текст "Mine"',
    )

    layer = next(x for x in layers_shifted if str(x.get("type")) == "text")
    layer_plain = next(x for x in layers_plain if str(x.get("type")) == "text")
    assert abs(float(layer.get("in_point")) - float(layer_plain.get("in_point"))) < 1e-6
    effects = layer.get("effects") or {}
    keys = set(effects.keys())
    assert "0001:ADBE Drop Shadow" in keys
    assert "0002:ADBE Drop Shadow" in keys
    assert "0003:ADBE Drop Shadow" in keys
