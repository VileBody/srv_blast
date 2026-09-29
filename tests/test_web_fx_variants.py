"""Варианты FX (режим вариантов шага FX) → render_job → запрос в оркестратор.

Вариант = тип хука + ОДНА настройка (хук · склейка · стиль). У одного типа их может быть
несколько, «Пул» раздаёт ролики по вариантам (allocation.variants), бэк разворачивает их по
вертикальным видео в порядке списка вариантов.
"""
from __future__ import annotations

import importlib
from copy import deepcopy
from typing import Any

import pytest

from tests.test_web_production_backend import _backend, _config, _module

SLOW = {"id": "v-slow", "kind": "effects", "config": {
    "effectHook": "Слоу-шаттер", "effectHookExtend": "to_end", "effectGlue": "Инверт",
    "effectStyles": ["Старая камера"], "effectStyle": "Старая камера"}}
SLOW_BW = {"id": "v-slow-bw", "kind": "effects", "config": {
    "effectHook": "Слоу-шаттер", "effectGlue": "Инверт", "effectStyles": ["Ч/Б"], "effectStyle": "Ч/Б"}}
SWIPE = {"id": "v-swipe", "kind": "motion", "config": {
    "motion": "Свайп", "effectGlue": "Вспышка", "effectStyles": ["Неон"], "effectStyle": "Неон"}}


def _stage(variants: list[dict[str, Any]], counts: dict[str, int] | None, *, photo: bool = False) -> dict[str, Any]:
    footage = ["Ночной город", "Неон"]
    background = {"footage:Ночной город": 2, "footage:Неон": 1}
    total = 3
    if photo:
        background["photo:Крупный план"] = 1
        total = 4
    return {
        "track": {"id": "t1", "s3Key": "s3://raw-audio/raw/track.mp3", "filename": "t.mp3"},
        "lyrics": "текст", "fragment": "текст",
        "timing": {"from": "00:10", "to": "00:25"},
        "background": {"mode": "footage", "footage": footage, "photo": ["Крупный план"] if photo else [], "uploads": []},
        # режим вариантов: классические конфиги пустые, дроп — общий
        "hooks": {"dropTime": "00:18:000", "configs": {}},
        "fxVariants": deepcopy(variants),
        "subtitles": {"color": "#ffffff", "pool": ["Impulse"]},
        "allocation": {"total": total, "background": background, "subtitles": {"Impulse": total},
                       "hooks": {}, "styles": {}, **({"variants": counts} if counts is not None else {})},
        "final": {"accentColor": "#8b6fe6"},
    }


@pytest.fixture
def rj(monkeypatch: pytest.MonkeyPatch):
    _module(monkeypatch)
    return importlib.import_module("app.render_job")


def test_variants_expand_in_list_order_with_their_own_config(rj) -> None:
    job = rj.build_render_job("b1", "p1", "u1", _stage([SLOW, SLOW_BW, SWIPE], {"v-slow": 1, "v-slow-bw": 1, "v-swipe": 1}), 3)
    hooks = [v["hook"] for v in job["variations"]]
    assert [h["variantId"] for h in hooks] == ["v-slow", "v-slow-bw", "v-swipe"]
    # два варианта ОДНОГО типа — разные стили, чего hooks.configs[kind] выразить не мог
    assert [h["family"] for h in hooks] == ["effects", "effects", "motion"]
    assert hooks[0]["resolved"]["extra"] != hooks[1]["resolved"]["extra"]
    assert hooks[0]["resolved"]["hook"] == "flash_slow_shutter" and hooks[0]["resolved"]["hookExtend"] == "to_end"
    assert hooks[1]["resolved"]["hookExtend"] is None
    assert hooks[2]["family_script"] and "swipe" in hooks[2]["family_script"]


def test_counts_decide_how_many_videos_each_variant_gets(rj) -> None:
    job = rj.build_render_job("b1", "p1", "u1", _stage([SLOW, SWIPE], {"v-slow": 0, "v-swipe": 3}), 3)
    assert [v["hook"]["variantId"] for v in job["variations"]] == ["v-swipe"] * 3


def test_no_counts_means_evenly(rj) -> None:
    job = rj.build_render_job("b1", "p1", "u1", _stage([SLOW, SWIPE], None), 3)
    assert [v["hook"]["variantId"] for v in job["variations"]] == ["v-slow", "v-slow", "v-swipe"]


def test_photo_videos_get_no_variant(rj) -> None:
    stage = _stage([SLOW], {"v-slow": 3}, photo=True)
    stage["allocation"]["subtitles"] = {"Impulse": 4}
    job = rj.build_render_job("b1", "p1", "u1", stage, 4)
    fams = [(v["background"]["mode"], v["hook"]["family"]) for v in job["variations"]]
    assert fams.count(("photo", None)) == 1 and fams.count(("footage", "effects")) == 3


def test_counts_must_match_the_vertical_videos(rj) -> None:
    with pytest.raises(ValueError, match="вариантов FX"):
        rj.build_render_job("b1", "p1", "u1", _stage([SLOW, SWIPE], {"v-slow": 2, "v-swipe": 2}), 3)


def test_deleted_variant_in_allocation_is_explicit(rj) -> None:
    with pytest.raises(ValueError, match="удалённый вариант"):
        rj.build_render_job("b1", "p1", "u1", _stage([SLOW], {"v-slow": 3, "v-gone": 1}), 3)


def test_unfinished_variant_is_explicit(rj) -> None:
    broken = {"id": "v-x", "kind": "effects", "config": {"effectGlue": "Инверт", "effectStyles": ["Ч/Б"]}}
    with pytest.raises(ValueError, match="не настроен"):
        rj.build_render_job("b1", "p1", "u1", _stage([broken], {"v-x": 3}), 3)
    no_style = {"id": "v-y", "kind": "motion", "config": {"motion": "Свайп", "effectGlue": "Вспышка"}}
    with pytest.raises(ValueError, match="не настроен"):
        rj.build_render_job("b1", "p1", "u1", _stage([no_style], {"v-y": 3}), 3)


def test_legacy_hooks_are_ignored_in_variants_mode(rj) -> None:
    """Режим вариантов — единственный источник хуков: старые hooks.configs/allocation.hooks
    из черновика не должны развернуться вторым слоем."""
    stage = _stage([SWIPE], {"v-swipe": 3})
    stage["hooks"]["kind"] = "object"
    stage["hooks"]["configs"] = {"object": {"object": "Круг", "effectGlue": "Щелчок", "effectStyle": "Ч/Б"}}
    stage["allocation"]["hooks"] = {"object": 3}
    job = rj.build_render_job("b1", "p1", "u1", stage, 3)
    assert {v["hook"]["family"] for v in job["variations"]} == {"motion"}


def test_classic_mode_is_unchanged(rj) -> None:
    stage = _stage([], None)
    stage.pop("fxVariants")
    stage["hooks"] = {"dropTime": "00:18:000", "kind": "effects", "configs": {
        "effects": {"effectHook": "Молния", "effectGlue": "Инверт", "effectStyles": ["Ч/Б"], "effectStyle": "Ч/Б"}}}
    stage["allocation"]["hooks"] = {"effects": 3}
    stage["allocation"]["styles"] = {"Ч/Б": 3}
    job = rj.build_render_job("b1", "p1", "u1", stage, 3)
    assert {v["hook"]["family"] for v in job["variations"]} == {"effects"}
    assert all(v["hook"]["variantId"] is None for v in job["variations"])


def test_selected_hook_configs_follow_the_mode(rj) -> None:
    stage = _stage([SLOW, SWIPE], {"v-slow": 3, "v-swipe": 0})
    assert [fam for fam, _ in rj.selected_hook_configs(stage)] == ["effects"]
    assert rj.selected_hook_families(stage) == {"effects"}
    # ссылки на словари stage_data: submit дописывает в них метаданные файлов прогрева
    _, cfg = rj.selected_hook_configs(stage)[0]
    cfg["marker"] = 1
    assert stage["fxVariants"][0]["config"]["marker"] == 1


def test_each_video_reaches_the_orchestrator_with_its_variant(monkeypatch) -> None:
    module = _module(monkeypatch)
    rj = importlib.import_module("app.render_job")
    stage = _stage([SLOW, SLOW_BW, SWIPE], {"v-slow": 1, "v-slow-bw": 1, "v-swipe": 1})
    # в тестовом каталоге оркестратора точный селектор есть у «Неона»
    stage["background"]["footage"] = ["Неон"]
    stage["allocation"]["background"] = {"footage:Неон": 3}
    job = rj.build_render_job("b1", "p1", "u1", stage, 3)
    backend = _backend(module, _config(module))
    web_job = {"id": "web-job", "projectId": "p1", "stageData": stage, "renderJob": job}
    payloads = [backend._request_payload(job=web_job, variation=v, index=i + 1, total=3, master_id=None)
                for i, v in enumerate(job["variations"])]
    assert payloads[0]["effect_hook"] == payloads[1]["effect_hook"] == "flash_slow_shutter"
    assert payloads[0]["effect_extra"] != payloads[1]["effect_extra"]
    assert payloads[0]["effect_transition"] == payloads[1]["effect_transition"]
    assert payloads[2]["f4_device"] == "swipe" and payloads[2].get("effect_hook") is None
    assert payloads[2]["effect_transition"] != payloads[0]["effect_transition"]
