# -*- coding: utf-8 -*-
"""F6 «Прогрев видео» через НАСТОЯЩУЮ сборку проекта.

Юнит-тесты в test_f6_video.py проверяют функции по отдельности; здесь проверяется
то, что реально уедет на ноду: итоговый payload, порядок слоёв в нём, JSX и
media[]-манифест, собранный из этого же файла. Именно на стыке этих шагов и
жили оба найденных бага — слой уезжал в конец стека, а mp4 уезжал в media/audio.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import project_builder


def _text_layer(name: str, in_p: float, out_p: float) -> dict:
    return {
        "name": name, "type": "text", "in_point": in_p, "out_point": out_p,
        "z_index": 1000, "text": name, "adjustment_layer": False,
        "comp_id": None, "comp_name": None, "source_rect": {}, "props": {},
        "effects": {}, "style_instructions": [],
        "text_data": {"layer_meta": {"comp_name_target": "Текст", "startTime": in_p}},
    }


def _footage_stack() -> list[dict]:
    """Реалистичный стек, как его отдаёт footage_comp: по возрастанию z."""
    def layer(name, z, in_p, out_p, *, audio=False, src=None):
        td: dict = {"layer_meta": {
            "comp_name_target": "Comp 1", "startTime": in_p,
            "enabled": not audio, "audioEnabled": audio,
        }}
        if audio:
            td["audio_envelope"] = {"fade_in_s": 0.5, "fade_out_s": 0.5, "min_db": -48.0}
        if src:
            td["source_footage"] = src
        return {
            "name": name, "type": "footage" if name != "Текст" else "precomp",
            "in_point": in_p, "out_point": out_p, "z_index": z, "text": "",
            "adjustment_layer": False, "comp_id": None,
            "comp_name": "Текст" if name == "Текст" else None,
            "source_rect": {}, "props": {}, "effects": {}, "style_instructions": [],
            "text_data": td,
        }

    return [
        layer("Текст", 1, 0.0, 20.0),
        layer("audio_track", 2, 0.0, 20.0, audio=True),
        layer("clip_a", 100, 0.0, 8.0, src={
            "file_name": "clip_a.mp4", "file_path": "", "remote_url": "s3://pool/clip_a.mp4"}),
        layer("clip_b", 101, 8.0, 20.0, src={
            "file_name": "clip_b.mp4", "file_path": "", "remote_url": "s3://pool/clip_b.mp4"}),
    ]


@pytest.fixture()
def built(tmp_path, monkeypatch):
    monkeypatch.setattr(project_builder, "build_footage_layers", lambda **_: _footage_stack())
    monkeypatch.setattr(
        project_builder, "build_text_layers",
        lambda **_: [_text_layer("под видео", 1.0, 2.0), _text_layer("после", 8.0, 9.0)],
    )

    full_edit_config = tmp_path / "full_edit.json"
    footage_config = tmp_path / "footage_config.json"
    full_edit_config.write_text(json.dumps({
        "composition": {"dur": 20.0, "fps": 25},
        "subtitles_mode": "legacy_blocks",
        # окно прогрева = [0.5, 4.5]: бот подогнал клип под 4-секундную вырезку
        "f6": {
            "video_url": "s3://raw-audio/hooks/interview.mp4",
            "drop_time": 5.0,
            "seed": 12345,
            "source_width": 1920,
            "source_height": 1080,
            "duration": 4.0,
            "has_audio": True,
        },
    }), encoding="utf-8")
    footage_config.write_text(json.dumps({
        "job_id": "f6_full_build_test", "color_grade": "cold",
        "allow_mirror": True, "layers": [],
    }), encoding="utf-8")

    out_json, out_jsx = project_builder.build_full_project(
        repo_root=Path.cwd(),
        full_edit_config_path=full_edit_config,
        footage_config_path=footage_config,
        out_dir=tmp_path / "out",
    )
    return (
        json.loads(out_json.read_text(encoding="utf-8")),
        out_jsx.read_text(encoding="utf-8"),
        out_json,
    )


def test_video_layer_reaches_the_final_payload(built):
    payload, _jsx, _p = built
    layers = payload["footage_layers"]
    f6 = next(L for L in layers if L["name"] == "f6_hook_video")
    assert f6["text_data"]["source_footage"]["remote_url"] == "s3://raw-audio/hooks/interview.mp4"
    assert f6["text_data"]["layer_meta"]["audioEnabled"] is True
    # F6 без искусственного lead pad: окно начинается с нуля и подрезается
    # по фактической длине присланного видео.
    assert f6["in_point"] == pytest.approx(0.0)
    assert f6["out_point"] == pytest.approx(4.0)


def test_video_sits_above_the_footage_in_the_final_payload(built):
    """Порядок массива в payload = порядок стека в AE. Если слой окажется после
    футажа — видео будет за кадром, причём молча."""
    payload, _jsx, _p = built
    names = [L["name"] for L in payload["footage_layers"]]
    i = names.index("f6_hook_video")
    assert names.index("Текст") < i, "субтитр-прекомп должен остаться выше"
    assert i < names.index("clip_a"), "видео обязано быть выше футажа"
    assert i < names.index("clip_b")


def test_track_is_ducked_under_the_warm_up(built):
    payload, _jsx, _p = built
    track = next(L for L in payload["footage_layers"] if L["name"] == "audio_track")
    env = track["text_data"]["audio_envelope"]
    assert env["duck_from_s"] == pytest.approx(0.0)
    assert env["duck_to_s"] == pytest.approx(5.0)
    assert env["duck_from_pct"] == pytest.approx(10.0)
    assert env["duck_ramp_start_s"] == pytest.approx(4.0)
    assert env["duck_curve"] == "soft"


def test_track_subtitles_under_the_video_are_gone(built):
    payload, _jsx, _p = built
    texts = [L["name"] for L in payload["text_layers"]]
    assert "под видео" not in texts
    assert "после" in texts


def test_jsx_carries_the_f6_visual_combo(built):
    _payload, jsx, _p = built
    assert "F6 «Видео» visual combo" in jsx
    assert "DROP: F3 hook_light" not in jsx
    assert "POST-DROP: seeded-random transition per cut" in jsx
    assert "var __f2_seed = 12345" in jsx


def test_media_manifest_built_from_the_real_payload_downloads_the_clip(built):
    """Тот же файл, который уезжает на ноду, прогоняем через сборщик media[]:
    mp4 обязан попасть в media/video/ — JSX ищет видео строго там (по
    расширению), и раньше слой со звуком уезжал в media/audio/."""
    from services.orchestrator.render_manifest import collect_media_urls_from_render_payload

    _payload, _jsx, out_json = built
    media = collect_media_urls_from_render_payload(
        out_json, audio_url="s3://raw-audio/track.mp3",
    )
    by_rel = {m["relpath"]: m["url"] for m in media}
    assert by_rel["media/video/interview.mp4"] == "s3://raw-audio/hooks/interview.mp4"
    # трек по-прежнему едет отдельно и в media/audio/
    assert any(rel.startswith("media/audio/") for rel in by_rel)
    # футаж не потерялся
    assert "media/video/clip_a.mp4" in by_rel


def test_no_f6_block_leaves_the_build_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(project_builder, "build_footage_layers", lambda **_: _footage_stack())
    monkeypatch.setattr(project_builder, "build_text_layers", lambda **_: [])

    full_edit_config = tmp_path / "full_edit.json"
    footage_config = tmp_path / "footage_config.json"
    full_edit_config.write_text(json.dumps({
        "composition": {"dur": 20.0, "fps": 25}, "subtitles_mode": "legacy_blocks",
    }), encoding="utf-8")
    footage_config.write_text(json.dumps({
        "job_id": "f6_absent", "color_grade": "cold", "allow_mirror": True, "layers": [],
    }), encoding="utf-8")

    out_json, out_jsx = project_builder.build_full_project(
        repo_root=Path.cwd(),
        full_edit_config_path=full_edit_config,
        footage_config_path=footage_config,
        out_dir=tmp_path / "out",
    )
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert all(L["name"] != "f6_hook_video" for L in payload["footage_layers"])
    track = next(L for L in payload["footage_layers"] if L["name"] == "audio_track")
    assert "duck_from_s" not in track["text_data"]["audio_envelope"]
    # Токен рендерится пустым: статический try/catch вокруг него в шаблоне
    # остаётся всегда, а вот тело комбо появляться не должно.
    jsx = out_jsx.read_text(encoding="utf-8")
    assert "DROP: F3 hook_light" not in jsx
    assert "__f2_seed" not in jsx
