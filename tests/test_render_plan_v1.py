from __future__ import annotations

import json
from pathlib import Path

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
