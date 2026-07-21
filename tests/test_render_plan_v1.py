from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.render_plan import build_render_plan_v1


def _base_plan(**cfg):
    return build_render_plan_v1(
        main_comp_name="Comp 1",
        subtitles_mode=cfg.pop("subtitles_mode", "impulse_2nd"),
        comps=[{"name": "Comp 1", "w": 1080, "h": 1920, "fps": 24, "dur": 4.0}],
        footage_layers=[
            {
                "name": "clip",
                "type": "footage",
                "in_point": 0.0,
                "out_point": 4.0,
                "z_index": 100,
                "text_data": {
                    "source_footage": {
                        "file_name": "clip.mp4",
                        "remote_url": "s3://footage/clip.mp4",
                    }
                },
            }
        ],
        text_layers=[],
        full_edit_config=cfg,
        f3_media=cfg.get("_f3_media", []),
    )


def test_render_plan_writes_canonical_native_request_and_ae_payload() -> None:
    plan = _base_plan(
        subtitle_flow_plan={
            "segments": [
                {
                    "id": 1,
                    "type": "long",
                    "text": "hello world",
                    "start": 0.0,
                    "end": 1.0,
                    "word_timings": [
                        {"word": "hello", "start": 0.0, "end": 0.4},
                        {"word": "world", "start": 0.4, "end": 1.0},
                    ],
                }
            ]
        }
    )

    native = plan.to_native_request(request_id="job-1")
    assert native["schema"] == "ae-native-renderer.render-request.v1"
    assert native["schemaVersion"] == "render-plan.v1.1"
    assert native["payloadVersion"] == "render-plan.v1"
    assert native["projectSpec"]["mainCompName"] == "Comp 1"
    assert "project" not in native
    assert native["visualOps"][0]["type"] == "subtitle.bot.impulse_2nd.v1"
    assert native["visualOps"][0]["params"]["segments"][0]["words"][0]["word"] == "hello"
    assert native["requirements"]["layer_types"] == ["footage"]
    assert native["styleRegistry"]
    assert native["effectRegistry"]
    assert native["tuningSpec"] == {
        "profile": "builtin:p0p1-readiness",
        "overrides": {},
    }

    ae_payload = plan.to_ae_payload()
    assert ae_payload["project"]["mainCompName"] == "Comp 1"
    assert ae_payload["comps"][0]["name"] == "Comp 1"


def test_trendy_and_brat_keep_word_timings_even_when_text_layers_are_empty() -> None:
    for mode, expected_type in [
        ("trendy_5th", "subtitle.trendy.v1"),
        ("brat_5th", "subtitle.brat.v1"),
    ]:
        plan = _base_plan(
            subtitles_mode=mode,
            subtitles_jsx={
                "mode": mode,
                "bpm": 128,
                "word_timings": [{"word": "брат", "start": 0.1, "end": 0.4}],
            },
        )
        request = plan.to_native_request()
        assert request["text_layers"] == []
        op = request["visualOps"][0]
        assert op["type"] == expected_type
        assert op["params"]["word_timings"][0]["word"] == "брат"
        assert request["goldenRefs"][0]["family"] in {"Trendy", "Brat"}
        if mode == "brat_5th":
            assert op["params"]["bpm"] == 128.0


def test_hook_visual_ops_preserve_ids_timing_assets_and_audio_roles() -> None:
    f3_media = [
        {"url": "s3://fx/sounds/light.wav", "relpath": "media/audio/light.wav"},
        {"url": "s3://fx/logo.png", "relpath": "media/img/logo.png"},
    ]
    plan = _base_plan(
        subtitle_flow_plan={"segments": [{"text": "x", "start": 0.0, "end": 1.0}]},
        f3={
            "hook": "hook_light",
            "transition": "snap_wipe",
            "extra": "analog_glitch",
            "drop_time": 1.25,
            "extra_full": True,
            "hook_extend": "after_drop:3",
        },
        f2={"shape": "star1", "drop_time": 1.25, "seed": 7},
        f4={"device": "tap", "bpm": 130, "drop_time": 1.25},
        f1={"sound_url": "s3://raw/impact.mp3", "drop_time": 1.25, "seed": 9, "text": "boom"},
        f5={
            "audio_url": "s3://tts/voice.wav",
            "drop_rel_sec": 1.25,
            "combo_seed": 11,
            "tts_text": "мысль",
            "audio_duration_ms": 900,
        },
        _f3_media=f3_media,
    )
    ops = {op["type"]: op for op in plan.to_native_request()["visualOps"]}

    f3 = ops["hook.f3.effect.v1"]
    assert f3["params"]["detected_effect_ids"] == ["hook_light", "snap_wipe", "analog_glitch"]
    assert f3["params"]["extra_full"] is True
    assert {asset["role"] for asset in f3["assets"]} == {"audio", "overlay"}

    assert ops["hook.f2.object.v1"]["params"]["shape"] == "star1"
    assert ops["hook.f4.motion.v1"]["params"]["device"] == "tap"
    assert ops["hook.f1.sound.v1"]["assets"][0]["path"] == "media/audio/impact.mp3"
    assert ops["hook.f5.cognition.v1"]["assets"][0]["role"] == "tts_audio"
    request = plan.to_native_request()
    assert sorted(request["requirements"]["asset_roles"]) == ["audio", "tts_audio"]
    assert any(entry["aeMatchName"] == "ANR F3 Stylize" for entry in request["effectRegistry"])

    ae_config = plan.to_ae_overlay_config()
    assert ae_config["f3"] == {
        "hook": "hook_light",
        "transition": "snap_wipe",
        "extra": "analog_glitch",
        "extra_full": True,
        "hook_extend": "after_drop:3",
        "drop_time": 1.25,
        "assets": {},
    }
    assert ae_config["f2"] == {"shape": "star1", "drop_time": 1.25, "seed": 7}
    assert ae_config["f4"] == {"device": "tap", "bpm": 130.0, "drop_time": 1.25}
    assert ae_config["f1"] == {"drop_time": 1.25, "seed": 9}
    assert ae_config["f5"] == {"drop_rel_sec": 1.25, "combo_seed": 11}


def test_ae_overlay_compiler_uses_render_plan_not_mutated_source_config() -> None:
    cfg = {
        "f2": {"shape": "star1", "drop_time": 1.25, "seed": 7},
        "f4": {"device": "tap", "bpm": 130, "drop_time": 1.25},
    }
    plan = _base_plan(**cfg)
    cfg["f2"]["shape"] = "square"
    cfg["f4"]["device"] = "swipe"

    ae_config = plan.to_ae_overlay_config()
    assert ae_config["f2"]["shape"] == "star1"
    assert ae_config["f4"]["device"] == "tap"


def test_semantic_style_is_an_operation_with_a_data_driven_effect_graph() -> None:
    plan = _base_plan(semantic_style={"style_id": "txt_soft_v1", "version": "v1"})
    request = plan.to_native_request()

    operation = next(op for op in request["visualOps"] if op["type"] == "style.semantic.v1")
    assert operation["params"] == {"styleId": "txt_soft_v1", "version": "v1"}
    registry = next(entry for entry in request["styleRegistry"] if entry["styleId"] == "txt_soft_v1")
    assert [effect["matchName"] for effect in registry["effectGraph"]] == [
        "ADBE Glo2",
        "ADBE Geometry2",
    ]
    assert {entry["aeMatchName"] for entry in request["effectRegistry"]} >= {
        "ADBE Glo2",
        "ADBE Geometry2",
    }
    glow = next(entry for entry in request["effectRegistry"] if entry["aeMatchName"] == "ADBE Glo2")
    assert glow["parameterSchema"]["radius"] == {
        "type": "number",
        "default": 10.0,
        "min": 0.0,
        "keyframe": True,
    }
    assert glow["keyframeSupport"] is True
    assert glow["alphaRequirements"].startswith("straight-rgba8")
    assert registry["supportedBackends"] == ["native_approximation"]
    assert registry["tunables"]["ADBE Glo2.radius"] == 28.0
    assert registry["goldenFixtures"] == ["trendy_5th_real_job"]


def test_native_plugin_approximations_do_not_require_a_plugin_worker() -> None:
    plan = build_render_plan_v1(
        main_comp_name="Comp 1",
        subtitles_mode="impulse_2nd",
        comps=[{"name": "Comp 1", "w": 64, "h": 64, "fps": 24, "dur": 1}],
        footage_layers=[{
            "name": "adjustment",
            "type": "adjustment",
            "in_point": 0,
            "out_point": 1,
            "z_index": 1,
            "effects": {
                "S_DropShadow": {"0052": {"value": 60}},
                "S_BlurMotion": {"0051": {"value": 12}},
            },
        }],
        text_layers=[],
        full_edit_config={},
        f3_media=[],
    )
    request = plan.to_native_request()

    assert request["requirements"]["plugins"] == []
    by_name = {entry["aeMatchName"]: entry for entry in request["effectRegistry"]}
    assert by_name["S_DropShadow"]["backend"] == "native_approximation"
    assert by_name["S_BlurMotion"]["backend"] == "native_approximation"
    assert by_name["S_BlurMotion"]["pluginIdentifier"] == "sapphire:S_BlurMotion"


def test_render_plan_rejects_unknown_comp_layer_and_schema_fields() -> None:
    with pytest.raises(ValidationError, match="unexpectedCompField"):
        build_render_plan_v1(
            main_comp_name="Comp 1",
            subtitles_mode="impulse_2nd",
            comps=[{
                "name": "Comp 1", "w": 1080, "h": 1920, "fps": 24, "dur": 1,
                "unexpectedCompField": True,
            }],
            footage_layers=[],
            text_layers=[],
            full_edit_config={},
            f3_media=[],
        )

    with pytest.raises(ValidationError, match="unexpected_layer_field"):
        build_render_plan_v1(
            main_comp_name="Comp 1",
            subtitles_mode="impulse_2nd",
            comps=[{"name": "Comp 1", "w": 1080, "h": 1920, "fps": 24, "dur": 1}],
            footage_layers=[{
                "name": "clip", "type": "footage", "in_point": 0, "out_point": 1,
                "z_index": 1, "unexpected_layer_field": True,
            }],
            text_layers=[],
            full_edit_config={},
            f3_media=[],
        )


def test_project_builder_emits_native_request_json(tmp_path: Path, monkeypatch) -> None:
    from app import project_builder

    monkeypatch.setattr(project_builder, "build_footage_layers", lambda **_: [])
    monkeypatch.setattr(project_builder, "build_text_layers", lambda **_: [])

    full_edit_config = tmp_path / "full_edit.json"
    footage_config = tmp_path / "footage_config.json"
    out_dir = tmp_path / "out"
    full_edit_config.write_text(
        json.dumps(
            {
                "job_id": "job-native",
                "composition": {"dur": 2.0},
                "subtitles_mode": "brat_5th",
                "subtitles_jsx": {
                    "mode": "brat_5th",
                    "word_timings": [{"word": "ok", "start": 0.0, "end": 0.5}],
                },
            }
        ),
        encoding="utf-8",
    )
    footage_config.write_text(json.dumps({"layers": []}), encoding="utf-8")

    out_json, _out_jsx = project_builder.build_full_project(
        repo_root=Path.cwd(),
        full_edit_config_path=full_edit_config,
        footage_config_path=footage_config,
        out_dir=out_dir,
    )
    request = json.loads(out_json.read_text(encoding="utf-8"))
    assert request["schema"] == "ae-native-renderer.render-request.v1"
    assert request["requestId"] == "job-native"
    assert request["visualOps"][0]["type"] == "subtitle.brat.v1"
    assert request["tuningSpec"]["profile"] == "builtin:p0p1-readiness"


def test_render_plan_preserves_per_job_native_tuning_overrides() -> None:
    plan = _base_plan(
        subtitle_flow_plan={"segments": [{"text": "x", "start": 0.0, "end": 1.0}]},
        native_tuning={
            "profile": "builtin:p0p1-readiness",
            "overrides": {
                "effects": {"glow": {"radius_multiplier": 0.8}},
            },
        },
    )

    request = plan.to_native_request()
    assert request["tuningSpec"]["overrides"] == {
        "effects": {"glow": {"radius_multiplier": 0.8}},
    }
    assert "tuningSpec" not in plan.to_ae_payload()
