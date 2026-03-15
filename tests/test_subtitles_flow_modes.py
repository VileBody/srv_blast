from __future__ import annotations

import logging
import pytest

from app.text_comp import build_text_layers
from mlcore.models.stage1_plan import Stage1PlanPayload
from mlcore.models.subtitles_flow import Impulse2ndRawPayload, Scenes3rdPayload
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
    payload = Impulse2ndRawPayload.model_validate(
        {
            "anchor_in_abs": 10.0,
            "word_timings": [
                {"word": "hello", "start": 0.0, "end": 0.4},
                {"word": "i'm", "start": 0.45, "end": 0.75},
                {"word": "world", "start": 0.8, "end": 1.0},
                {"word": "boom", "start": 1.6, "end": 1.9},
            ],
            "segments": [
                {
                    "text": "Hello, I'm world!",
                    "in": 0.0,
                    "out": 1.5,
                    "type": "long",
                    "word_timings": [
                        {"word": "hello", "start": 0.0, "end": 0.4},
                        {"word": "i'm", "start": 0.45, "end": 0.75},
                        {"word": "world", "start": 0.8, "end": 1.0},
                    ],
                },
                {
                    "text": "boom",
                    "in": 1.6,
                    "out": 2.1,
                    "type": "short",
                    "word_timings": [
                        {"word": "boom", "start": 1.6, "end": 1.9},
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

    short_layer = next(x for x in text_layers if str(x.get("text")) == "boom")
    short_scale_kfs = (((short_layer.get("props") or {}).get("tf_scale") or {}).get("keyframes") or [])
    assert len(short_scale_kfs) >= 4
    # quick-exit keyframe should exist between peak and final collapse
    assert float(short_scale_kfs[2]["t"]) > float(short_scale_kfs[1]["t"])
    assert float(short_scale_kfs[2]["t"]) < float(short_scale_kfs[-1]["t"])
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
                    "words": ["hello", "my", "world"],
                    "start": 10.0,
                    "end": 11.3,
                    "lines": [["hello", "my"], ["world"]],
                    "word_timings": [
                        {"word": "hello", "start": 10.0, "end": 10.3},
                        {"word": "my", "start": 10.35, "end": 10.55},
                        {"word": "world", "start": 10.65, "end": 11.3},
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
                        {"word": "boom", "start": 11.4, "end": 12.0},
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
    adjustments = [x for x in layers if str(x.get("type")) == "adjustment"]
    text_layers = [x for x in layers if str(x.get("type")) == "text"]
    assert len(adjustments) == 1
    assert len(text_layers) == 2
    scene_text = next(x for x in text_layers if str(x.get("name")) != "mine")
    scene_props = scene_text.get("props") or {}
    assert isinstance(scene_props.get("reveal"), dict)
    assert scene_props["reveal"].get("match_name") == "ADBE Text Percent Start"
    assert (scene_text.get("text_data") or {}).get("no_layout_pass") is not True

    mine_layer = next(x for x in text_layers if str(x.get("name")).lower() == "mine")
    mine_td = mine_layer.get("text_data") or {}
    assert mine_td.get("no_text_animator") is True
    assert mine_td.get("no_layout_pass") is True

    precomp_layers = [x for x in layers if str(x.get("type")) == "precomp"]
    precomp_names = {str(x.get("name")) for x in precomp_layers}
    assert 'Текст "Mine"' in precomp_names
    assert 'Текст "Mine" glow' in precomp_names
    adj = adjustments[0]
    g2 = ((adj.get("effects") or {}).get("ADBE Geometry2") or {})
    scale_prop = g2.get("0003") or {}
    kfs = scale_prop.get("keyframes") or []
    assert kfs, "expected Geometry2 scale keyframes on adjustment layer"
    assert max(float(x["t"]) for x in kfs) > float(adj.get("out_point")) + 0.45
    _assert_keyframes_within_bounds(layers)


def test_scenes_reference_postprocess_extends_boundary_from_gap(monkeypatch) -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 1,
                    "type": "TYPE_4",
                    "words": ["boom"],
                    "start": 10.0,
                    "end": 11.0,
                    "lines": [["boom"]],
                    "focus_word": "boom",
                    "focus_style": "red",
                    "word_timings": [
                        {"word": "boom", "start": 10.0, "end": 11.0},
                    ],
                },
                {
                    "id": 2,
                    "type": "TYPE_1",
                    "words": ["stay", "with", "me"],
                    "start": 12.0,
                    "end": 13.0,
                    "lines": [["stay", "with"], ["me"]],
                    "word_timings": [
                        {"word": "stay", "start": 12.0, "end": 12.3},
                        {"word": "with", "start": 12.35, "end": 12.65},
                        {"word": "me", "start": 12.7, "end": 13.0},
                    ],
                },
            ],
        }
    )
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    monkeypatch.setenv("TEXT_LAYER_TIME_SHIFT_S", "0")
    layers = build_text_layers(
        full_edit_config={
            "subtitles_mode": "scenes_3rd",
            "subtitle_flow_plan": flow.model_dump(mode="json"),
            "composition": {"fps": 23.976, "dur": 14.0},
        },
        text_comp_name="Текст",
        mine_comp_name='Текст "Mine"',
    )

    mine_precomp = next(
        x
        for x in layers
        if str(x.get("type")) == "precomp"
        and str(x.get("name")) == 'Текст "Mine"'
        and abs(float(x.get("in_point")) - 10.0) < 1e-6
    )
    # Scene_001 originally ends at 11.0, postprocess should extend it but keep a non-zero gap to scene_002 (12.0).
    assert float(mine_precomp.get("out_point")) > 11.0 + 1e-3
    assert float(mine_precomp.get("out_point")) < 12.0 - (1.0 / 23.976) + 1e-6


def test_scenes_type3_builds_progressive_layers(monkeypatch) -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 1,
                    "type": "TYPE_3",
                    "words": ["we", "will", "away"],
                    "start": 10.0,
                    "end": 11.7,
                    "lines": [["we", "will", "away"]],
                    "word_timings": [
                        {"word": "we", "start": 10.0, "end": 10.3},
                        {"word": "will", "start": 10.35, "end": 10.8},
                        {"word": "away", "start": 11.1, "end": 11.7},
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
    assert names == ["WE", "WE WILL", "WE WILL AWAY"]
    for layer in text_layers:
        props = layer.get("props") or {}
        assert "reveal" not in props
        td = layer.get("text_data") or {}
        assert td.get("no_text_animator") is True
        assert td.get("no_layout_pass") is not True
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
                    "words": ["stay", "with", "me", "now"],
                    "start": 10.0,
                    "end": 13.4,
                    "lines": [["stay", "with"], ["me", "now"]],
                    "word_timings": [
                        {"word": "stay", "start": 10.0, "end": 10.6},
                        {"word": "with", "start": 10.7, "end": 11.4},
                        {"word": "me", "start": 11.5, "end": 12.4},
                        {"word": "now", "start": 12.5, "end": 13.4},
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
    outline = next(x for x in text_layers if str(x.get("name")).endswith(" outline"))
    fill = next(x for x in text_layers if str(x.get("name")) == "STAY WITH ME NOW")

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
        assert td.get("no_layout_pass") is not True
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
                    "words": ["hello", "my", "friend"],
                    "start": 10.0,
                    "end": 11.2,
                    "lines": [["hello", "my"], ["friend"]],
                    "word_timings": [
                        {"word": "hello", "start": 10.0, "end": 10.3},
                        {"word": "my", "start": 10.35, "end": 10.6},
                        {"word": "friend", "start": 10.7, "end": 11.2},
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
    scene_text = next(x for x in layers if str(x.get("type")) == "text")
    assert abs(float(scene_text.get("in_point")) - 10.0) < 1e-6


def test_scenes_type2_builds_focus_italic_styles(monkeypatch) -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 7,
                    "type": "TYPE_2",
                    "words": ["hold", "me", "tight", "now"],
                    "start": 10.0,
                    "end": 12.0,
                    "lines": [["hold", "me"], ["tight", "now"]],
                    "focus_word": "tight",
                    "focus_style": "italic",
                    "word_timings": [
                        {"word": "hold", "start": 10.0, "end": 10.4},
                        {"word": "me", "start": 10.45, "end": 10.8},
                        {"word": "tight", "start": 10.9, "end": 11.4},
                        {"word": "now", "start": 11.5, "end": 12.0},
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
    assert len(text_layers) == 1
    td = text_layers[0].get("text_data") or {}
    assert td.get("no_layout_pass") is not True
    styles = td.get("char_styles_ungrouped") or []
    assert any(bool(s.get("fauxItalic")) for s in styles if isinstance(s, dict))


def test_scenes_type6_builds_adjustment_and_grouped_text(monkeypatch) -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 8,
                    "type": "TYPE_6",
                    "words": ["hold", "on", "now"],
                    "start": 10.0,
                    "end": 11.8,
                    "lines": [["hold", "on"], ["now"]],
                    "word_timings": [
                        {"word": "hold", "start": 10.0, "end": 10.4},
                        {"word": "on", "start": 10.45, "end": 10.9},
                        {"word": "now", "start": 11.0, "end": 11.8},
                    ],
                }
            ],
        }
    )
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    monkeypatch.setenv("TEXT_LAYER_TIME_SHIFT_S", "0")
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
    assert len(text_layers) == 1
    td = text_layers[0].get("text_data") or {}
    assert td.get("no_layout_pass") is not True
    assert isinstance((text_layers[0].get("props") or {}).get("reveal"), dict)


def test_scenes_planner_fail_fast_on_lines_words_mismatch() -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 1,
                    "type": "TYPE_1",
                    "words": ["one", "two", "three"],
                    "start": 10.0,
                    "end": 11.2,
                    "lines": [["one"], ["three", "two"]],
                    "word_timings": [
                        {"word": "one", "start": 10.0, "end": 10.3},
                        {"word": "two", "start": 10.35, "end": 10.7},
                        {"word": "three", "start": 10.8, "end": 11.2},
                    ],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="scene\\.lines must flatten to scene\\.words"):
        planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))


def test_scenes_planner_fail_fast_on_word_timings_words_mismatch() -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 1,
                    "type": "TYPE_1",
                    "words": ["one", "two", "three"],
                    "start": 10.0,
                    "end": 11.2,
                    "lines": [["one", "two"], ["three"]],
                    "word_timings": [
                        {"word": "one", "start": 10.0, "end": 10.3},
                        {"word": "oops", "start": 10.35, "end": 10.7},
                        {"word": "three", "start": 10.8, "end": 11.2},
                    ],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="scene\\.word_timings words mismatch scene\\.words"):
        planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))


@pytest.mark.parametrize(
    ("scene_obj", "match"),
    [
        (
            {
                "id": 4,
                "type": "TYPE_4",
                "words": ["boom", "now"],
                "start": 10.0,
                "end": 10.8,
                "lines": [["boom"], ["now"]],
                "word_timings": [
                    {"word": "boom", "start": 10.0, "end": 10.4},
                    {"word": "now", "start": 10.45, "end": 10.8},
                ],
            },
            "TYPE_4 must stay on one line",
        ),
        (
            {
                "id": 5,
                "type": "TYPE_5",
                "words": ["stay", "with", "me", "now"],
                "start": 10.0,
                "end": 12.9,
                "lines": [["stay", "with"], ["me", "now"]],
                "word_timings": [
                    {"word": "stay", "start": 10.0, "end": 10.6},
                    {"word": "with", "start": 10.7, "end": 11.3},
                    {"word": "me", "start": 11.4, "end": 12.0},
                    {"word": "now", "start": 12.1, "end": 12.9},
                ],
            },
            "TYPE_5 must be >3.0s",
        ),
    ],
)
def test_scenes_planner_fail_fast_on_reference_type_rules(scene_obj: dict, match: str) -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [scene_obj],
        }
    )
    with pytest.raises(ValueError, match=match):
        planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))


def test_scenes_planner_warns_on_short_type4_duration(caplog) -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 6,
                    "type": "TYPE_4",
                    "words": ["boom"],
                    "start": 10.0,
                    "end": 10.3,
                    "lines": [["boom"]],
                    "word_timings": [
                        {"word": "boom", "start": 10.0, "end": 10.3},
                    ],
                }
            ],
        }
    )
    caplog.set_level(logging.WARNING, logger="test")
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    assert len(flow.segments) == 1
    assert str(flow.segments[0].style_tag) == "TYPE_4"
    msgs = [r.message for r in caplog.records]
    assert any("reason=type4_short_duration" in m for m in msgs)


def test_scenes_planner_fallback_type3_last_gap_to_type1(caplog) -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 1,
                    "type": "TYPE_3",
                    "words": ["we", "will", "away"],
                    "start": 10.0,
                    "end": 11.2,
                    "lines": [["we", "will", "away"]],
                    "word_timings": [
                        {"word": "we", "start": 10.0, "end": 10.3},
                        {"word": "will", "start": 10.35, "end": 10.7},
                        {"word": "away", "start": 10.7, "end": 11.2},  # last_gap=0.0 -> fallback
                    ],
                }
            ],
        }
    )
    caplog.set_level(logging.WARNING, logger="test")
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    assert len(flow.segments) == 1
    assert str(flow.segments[0].style_tag) == "TYPE_1"
    msgs = [r.message for r in caplog.records]
    assert any("reason=type3_last_gap_fallback_type1" in m for m in msgs)


def test_scenes_planner_fallback_type3_word_count_to_type1(caplog) -> None:
    planner = SubtitlesPlannerFactory.create("scenes_3rd")
    payload = Scenes3rdPayload.model_validate(
        {
            "clip": {"start": 10.0, "end": 24.0},
            "scenes": [
                {
                    "id": 9,
                    "type": "TYPE_3",
                    "words": ["we", "will", "run", "away", "tonight"],  # 5 words -> fallback
                    "start": 10.0,
                    "end": 12.2,
                    "lines": [["we", "will", "run", "away", "tonight"]],
                    "word_timings": [
                        {"word": "we", "start": 10.0, "end": 10.3},
                        {"word": "will", "start": 10.35, "end": 10.7},
                        {"word": "run", "start": 10.75, "end": 11.0},
                        {"word": "away", "start": 11.05, "end": 11.6},
                        {"word": "tonight", "start": 11.9, "end": 12.2},
                    ],
                }
            ],
        }
    )
    caplog.set_level(logging.WARNING, logger="test")
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    assert len(flow.segments) == 1
    assert str(flow.segments[0].style_tag) == "TYPE_1"
    msgs = [r.message for r in caplog.records]
    assert any("reason=type3_word_count_fallback_type1" in m for m in msgs)


def test_impulse_mode_ignores_global_text_shift_and_keeps_drop_shadows(monkeypatch) -> None:
    planner = SubtitlesPlannerFactory.create("impulse_2nd")
    payload = Impulse2ndRawPayload.model_validate(
        {
            "anchor_in_abs": 10.0,
            "word_timings": [
                {"word": "long", "start": 0.0, "end": 0.4},
                {"word": "phrase", "start": 0.5, "end": 1.0},
            ],
            "segments": [
                {
                    "text": "Long phrase",
                    "in": 0.0,
                    "out": 1.3,
                    "type": "long",
                    "word_timings": [
                        {"word": "long", "start": 0.0, "end": 0.4},
                        {"word": "phrase", "start": 0.5, "end": 1.0},
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


def test_impulse_raw_anchor_adapter_converts_to_absolute() -> None:
    planner = SubtitlesPlannerFactory.create("impulse_2nd")
    payload = Impulse2ndRawPayload.model_validate(
        {
            "anchor_in_abs": 12.5,
            "word_timings": [{"word": "hello", "start": 0.3, "end": 0.6}],
            "segments": [
                {
                    "text": "hello",
                    "in": 0.3,
                    "out": 0.9,
                    "type": "long",
                    "word_timings": [{"word": "hello", "start": 0.3, "end": 0.6}],
                }
            ],
        }
    )
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    seg = flow.segments[0]
    assert abs(float(seg.in_point) - 12.8) < 1e-6
    assert abs(float(seg.out_point) - 13.4) < 1e-6
    tok = seg.tokens[0]
    assert abs(float(tok.t_start) - 12.8) < 1e-6
    assert abs(float(tok.t_end) - 13.1) < 1e-6


def test_impulse_adapter_warns_and_repairs_minor_issues(caplog) -> None:
    planner = SubtitlesPlannerFactory.create("impulse_2nd")
    payload = Impulse2ndRawPayload.model_validate(
        {
            "anchor_in_abs": 10.0,
            "word_timings": [
                {"word": "go", "start": -0.02, "end": 0.10},
                {"word": "away", "start": 0.33, "end": 0.60},
            ],
            "segments": [
                {"text": "go go", "in": -0.02, "out": 0.30, "type": "long", "word_timings": []},
                {"text": "away", "in": 0.31, "out": 0.70, "type": "short", "word_timings": []},
            ],
        }
    )
    caplog.set_level(logging.WARNING, logger="test")
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    assert len(flow.segments) == 2
    msgs = [r.message for r in caplog.records]
    assert any("reason=segment_in_out_of_clip" in m for m in msgs)
    assert any("reason=segment_tokens_from_global_word_timings" in m for m in msgs)
    assert any("reason=repeated_word" in m for m in msgs)
    assert any("reason=close_boundary" in m for m in msgs)


def test_impulse_adapter_extends_clip_for_last_segment_tail_pad(caplog) -> None:
    planner = SubtitlesPlannerFactory.create("impulse_2nd")
    stage1 = Stage1PlanPayload.model_validate(
        {
            "audio": {"clip_start_abs": 3.0, "clip_end_abs": 17.0},
            "transcript_words": [{"text": "пролог", "t_start": 16.3, "t_end": 17.0}],
            "draft_blocks": _draft_blocks(),
        }
    )
    payload = Impulse2ndRawPayload.model_validate(
        {
            "anchor_in_abs": 6.2,
            "word_timings": [{"word": "пролог", "start": 10.1, "end": 10.8}],
            "segments": [
                {
                    "text": "пролог",
                    "in": 10.1,
                    "out": 11.3,  # template rule: last out = last word end + 0.5
                    "type": "short",
                    "word_timings": [{"word": "пролог", "start": 10.1, "end": 10.8}],
                }
            ],
        }
    )
    caplog.set_level(logging.WARNING, logger="test")
    flow = planner.normalize_payload(payload=payload, stage1=stage1, logger=logging.getLogger("test"))
    assert len(flow.segments) == 1
    assert abs(float(flow.segments[0].out_point) - 17.5) < 1e-6
    assert abs(float(flow.clip.end) - 17.5) < 1e-6
    msgs = [r.message for r in caplog.records]
    assert any("reason=segment_out_tail_pad_extend_clip" in m for m in msgs)


def test_impulse_adapter_allows_effective_clip_over_max_for_tail_pad(caplog) -> None:
    planner = SubtitlesPlannerFactory.create("impulse_2nd")
    stage1 = Stage1PlanPayload.model_validate(
        {
            "audio": {"clip_start_abs": 155.0, "clip_end_abs": 185.0},  # exactly 30.0s
            "transcript_words": [{"text": "финал", "t_start": 184.4, "t_end": 185.0}],
            "draft_blocks": _draft_blocks(),
        }
    )
    payload = Impulse2ndRawPayload.model_validate(
        {
            "anchor_in_abs": 155.0,
            "word_timings": [{"word": "финал", "start": 29.4, "end": 30.0}],
            "segments": [
                {
                    "text": "финал",
                    "in": 29.4,
                    "out": 30.31,
                    "type": "short",
                    "word_timings": [{"word": "финал", "start": 29.4, "end": 30.0}],
                }
            ],
        }
    )
    caplog.set_level(logging.WARNING, logger="test")
    flow = planner.normalize_payload(payload=payload, stage1=stage1, logger=logging.getLogger("test"))
    assert abs(float(flow.clip.start) - 155.0) < 1e-6
    assert abs(float(flow.clip.end) - 185.31) < 1e-6
    assert abs(float(flow.segments[0].out_point) - 185.31) < 1e-6
    msgs = [r.message for r in caplog.records]
    assert any("reason=segment_out_tail_pad_extend_clip" in m for m in msgs)
    assert any("reason=clip_duration_over_max" in m for m in msgs)


def test_impulse_adapter_fail_fast_cases() -> None:
    planner = SubtitlesPlannerFactory.create("impulse_2nd")

    with pytest.raises(Exception):
        Impulse2ndRawPayload.model_validate(
            {
                "segments": [{"text": "x", "in": 0.0, "out": 0.3, "type": "long"}],
            }
        )

    payload_overlap = Impulse2ndRawPayload.model_validate(
        {
            "anchor_in_abs": 10.0,
            "word_timings": [],
            "segments": [
                {"text": "one", "in": 0.0, "out": 1.0, "type": "long"},
                {"text": "two", "in": 0.5, "out": 1.1, "type": "long"},
            ],
        }
    )
    with pytest.raises(ValueError):
        planner.normalize_payload(payload=payload_overlap, stage1=_stage1(), logger=logging.getLogger("test"))

    with pytest.raises(Exception):
        Impulse2ndRawPayload.model_validate(
            {
                "anchor_in_abs": 10.0,
                "segments": [{"text": "bad", "in": 1.0, "out": 0.4, "type": "long"}],
            }
        )

    stage1 = Stage1PlanPayload.model_validate(
        {
            "audio": {"clip_start_abs": 3.0, "clip_end_abs": 17.0},
            "transcript_words": [{"text": "x", "t_start": 10.0, "t_end": 10.2}],
            "draft_blocks": _draft_blocks(),
        }
    )
    payload_far = Impulse2ndRawPayload.model_validate(
        {
            "anchor_in_abs": 6.2,
            "segments": [{"text": "far", "in": 10.1, "out": 12.5, "type": "short"}],
        }
    )
    with pytest.raises(ValueError):
        planner.normalize_payload(payload=payload_far, stage1=stage1, logger=logging.getLogger("test"))


def test_impulse_renderer_keeps_template_prev_out_quirk(monkeypatch) -> None:
    planner = SubtitlesPlannerFactory.create("impulse_2nd")
    payload = Impulse2ndRawPayload.model_validate(
        {
            "anchor_in_abs": 10.0,
            "segments": [
                {
                    "text": "alpha beta gamma",
                    "in": 0.0,
                    "out": 1.0,
                    "type": "long",
                    "word_timings": [{"word": "alpha", "start": 0.0, "end": 0.4}],
                },
                {
                    "text": "delta echo",
                    "in": 1.02,
                    "out": 1.6,
                    "type": "long",
                    "word_timings": [{"word": "delta", "start": 1.02, "end": 1.3}],
                },
            ],
        }
    )
    flow = planner.normalize_payload(payload=payload, stage1=_stage1(), logger=logging.getLogger("test"))
    monkeypatch.setenv("TEXT_LAYER_TIME_SHIFT_S", "0")
    layers = build_text_layers(
        full_edit_config={
            "subtitles_mode": "impulse_2nd",
            "subtitle_flow_plan": flow.model_dump(mode="json"),
            "composition": {"fps": 23.976, "dur": 14.0},
        },
        text_comp_name="Текст",
        mine_comp_name='Текст "Mine"',
    )
    text_layers = [x for x in layers if str(x.get("type")) == "text"]
    assert len(text_layers) == 2
    first = next(x for x in text_layers if str(x.get("text")) == "alpha beta gamma")
    second = next(x for x in text_layers if str(x.get("text")) == "delta echo")
    assert float(second.get("in_point")) < float(first.get("out_point"))
