# app/project_builder.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader

from app.project_config import AE_PROJECT
from app.footage_comp import build_footage_layers, resolve_text_duration_sec
from app.text_comp import build_text_layers
from core.subtitles_mode import (
    SUBTITLES_MODE_JSX_5TH,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    normalize_subtitles_mode,
)

LOGGER = logging.getLogger("app.project_builder")


def _apply_comp_duration_overrides(
    *,
    comps: list[Dict[str, Any]],
    main_comp_name: str,
    text_comp_name: str,
    mine_comp_name: str = "",
    comp_dur: float,
) -> list[Dict[str, Any]]:
    comp_dur = float(comp_dur)
    if comp_dur <= 0:
        return comps

    out: list[Dict[str, Any]] = []
    for c in comps:
        if not isinstance(c, dict):
            continue
        cc = dict(c)
        name = str(cc.get("name") or "")

        if name == text_comp_name:
            cc["dur"] = comp_dur
            cc["workAreaDuration"] = comp_dur
            cc.setdefault("workAreaStart", 0.0)
            cc.setdefault("displayStartTime", 0.0)

        if name == main_comp_name:
            # Keep main comp timing strictly aligned with the actual built text/footage duration.
            cc["dur"] = comp_dur
            cc["workAreaDuration"] = comp_dur
            cc.setdefault("workAreaStart", 0.0)
            cc.setdefault("displayStartTime", 0.0)

        if mine_comp_name and name == mine_comp_name:
            # Mine comp must be at least as long as the main comp so TYPE_4 layers
            # placed at absolute time t (e.g. 13s) fit inside the comp timeline.
            cc["dur"] = comp_dur
            cc["workAreaDuration"] = comp_dur
            cc.setdefault("workAreaStart", 0.0)
            cc.setdefault("displayStartTime", 0.0)

        out.append(cc)

    return out


def _parse_hex_color_rgb01(s: str) -> Optional[List[float]]:
    raw = str(s or "").strip().lstrip("#")
    if len(raw) != 6:
        return None
    try:
        r = int(raw[0:2], 16)
        g = int(raw[2:4], 16)
        b = int(raw[4:6], 16)
    except ValueError:
        return None
    return [r / 255.0, g / 255.0, b / 255.0]


def _is_pure_white_rgb(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 3:
        return False
    try:
        return all(float(c) >= 0.999 for c in value)
    except (TypeError, ValueError):
        return False


def _override_white_fill_colors(node: Any, target_rgb01: List[float]) -> int:
    """In-place replace any fillColor == [1,1,1] (pure white) with target.
    Returns number of replacements. Other colors (e.g. red accent) untouched.
    """
    replaced = 0
    if isinstance(node, dict):
        for key, val in list(node.items()):
            if key == "fillColor" and _is_pure_white_rgb(val):
                node[key] = list(target_rgb01)
                replaced += 1
            else:
                replaced += _override_white_fill_colors(val, target_rgb01)
    elif isinstance(node, list):
        for item in node:
            replaced += _override_white_fill_colors(item, target_rgb01)
    return replaced


def _tojson_filter(v: Any) -> str:
    """
    Stable JSON for embedding into JSX.
    - keep utf-8 (ensure_ascii=False)
    - compact (separators) to reduce JSX size
    """
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _apply_f5_if_present(
    *,
    full_edit_config: Dict[str, Any],
    footage_layers: List[Dict[str, Any]],
    text_layers: List[Dict[str, Any]],
    main_comp_name: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Если в full_edit_config есть блок "f5" — применяет F5-инжекторы.

    Блок "f5" формируется оркестратором: F5Response.to_config_block(
        focal_start_ms=..., audio_url="s3://...").

    Импорт mlcore.hooks делаем лениво внутри функции, чтобы project_builder
    не тянул ML-зависимости когда хука нет.
    """
    f5_block = full_edit_config.get("f5") if isinstance(full_edit_config, dict) else None
    if not f5_block or not isinstance(f5_block, dict):
        return footage_layers, text_layers

    try:
        from mlcore.hooks.f5_cognition.inject import apply_f5
        from mlcore.hooks.f5_cognition.models import F5Response
    except Exception as e:  # noqa: BLE001
        LOGGER.error("f5 block present but mlcore.hooks import failed: %s", e)
        raise

    f5_resp = F5Response.from_config_block(f5_block)
    focal_start_ms = int(f5_block.get("focal_start_ms", 0))
    audio_url = f5_block.get("audio_url")
    drop_rel_raw = f5_block.get("drop_rel_sec")
    drop_rel_sec = float(drop_rel_raw) if drop_rel_raw is not None else None

    LOGGER.info(
        "f5 hook present device=%s focal_ms=%d audio_url=%s drop_rel=%s tts=%r",
        f5_resp.chosen_device.value, focal_start_ms, bool(audio_url),
        drop_rel_sec, f5_resp.tts_text,
    )

    return apply_f5(
        footage_layers=footage_layers,
        text_layers=text_layers,
        f5=f5_resp,
        focal_start_ms=focal_start_ms,
        tts_remote_url=audio_url,
        target_comp_name=main_comp_name,
        drop_rel_sec=drop_rel_sec,
    )


def _build_f4_overlay_js(full_edit_config: Dict[str, Any]) -> str:
    """
    Если в full_edit_config есть блок "f4" — собирает инъектируемый JSX-блок
    оверлея выбранного приёма («Движение»). Блок встраивается в шаблон сырым
    куском (после addFlashOnCuts, до сохранения проекта).

    Блок "f4" формируется оркестратором: {"device": "...", "bpm": <float>}.
    Нет блока => пустая строка => ноль влияния на обычные джобы.

    Импорт mlcore.hooks делаем лениво, чтобы project_builder не тянул
    ML-зависимости когда хука нет.
    """
    f4_block = full_edit_config.get("f4") if isinstance(full_edit_config, dict) else None
    if not f4_block or not isinstance(f4_block, dict):
        return ""

    device = str(f4_block.get("device") or "").strip().lower()
    if not device:
        raise RuntimeError("f4 block present but 'device' is empty")
    bpm = f4_block.get("bpm")
    if bpm is None:
        raise RuntimeError("f4 block present but 'bpm' is missing")
    drop_raw = f4_block.get("drop_time")
    drop_time = float(drop_raw) if drop_raw is not None else None

    from mlcore.hooks.f4_motion.overlay import build_overlay_jsx

    overlay = build_overlay_jsx(device=device, bpm=float(bpm), drop_time=drop_time)
    LOGGER.info(
        "f4 hook present device=%s bpm=%s drop_time=%s js_len=%d",
        device, bpm, drop_time, len(overlay),
    )
    return overlay


def _apply_f1_if_present(
    *,
    full_edit_config: Dict[str, Any],
    footage_layers: List[Dict[str, Any]],
    text_layers: List[Dict[str, Any]],
    main_comp_name: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Если в full_edit_config есть блок "f1" — добавляет audio-слой с загруженным
    пользователем звуком в окне [0.5, drop−0.5], приглушает ТРЕК под звук (как
    F5: 25%→100% к дропу) и, если юзер приложил текст, кладёт субтитр того же
    типа, что трек (как F5). Визуал (hook_light + post-drop random) идёт отдельно
    через токен f1_overlay_js. Нет блока => без изменений.
    """
    f1_block = full_edit_config.get("f1") if isinstance(full_edit_config, dict) else None
    if not f1_block or not isinstance(f1_block, dict):
        return footage_layers, text_layers

    sound_url = str(f1_block.get("sound_url") or "").strip()
    if not sound_url:
        raise RuntimeError("f1 block present but 'sound_url' is empty")
    drop_time = f1_block.get("drop_time")
    if drop_time is None:
        raise RuntimeError("f1 block present but 'drop_time' is missing")
    drop_time = float(drop_time)

    from mlcore.hooks.f1_sound.inject import (
        f1_audio_window,
        inject_f1_audio,
        inject_f1_subtitle,
    )
    from mlcore.hooks.f5_cognition.inject import inject_track_duck

    # 1) Duck the TRACK under the user's sound (before adding the f1 sound layer
    #    so the duck only touches the real track audio — same as F5). Window =
    #    [sound start .. drop], volume 25%→100% returning to full at the drop.
    sound_in, _sound_out = f1_audio_window(drop_time)
    footage_layers = inject_track_duck(
        footage_layers, duck_from_sec=sound_in, duck_to_sec=drop_time,
    )

    # 2) User's pre-drop sound as an audio layer.
    footage_layers = inject_f1_audio(
        footage_layers,
        sound_url=sound_url,
        drop_time=drop_time,
        target_comp_name=main_comp_name,
    )

    # 3) Optional subtitle — only if the user attached text for the sound.
    f1_text = str(f1_block.get("text") or "").strip()
    if f1_text:
        text_layers = inject_f1_subtitle(
            text_layers, text=f1_text, drop_time=drop_time,
        )

    LOGGER.info(
        "f1 present sound=%s drop_time=%s subtitle=%s",
        sound_url[:80], drop_time, bool(f1_text),
    )
    return footage_layers, text_layers


def _build_f5_overlay_js(full_edit_config: Dict[str, Any]) -> str:
    """
    Визуал-combo для F5 «Мысль» — тот же, что у F1/F2 без шейпов: hook_light на
    дропе + seeded-random F3-переход на post-drop склейках. Берётся из блока
    "f5" (drop_rel_sec + combo_seed, проставлены оркестратором). Нет дропа =>
    пустая строка (только голос, без визуала).
    """
    f5_block = full_edit_config.get("f5") if isinstance(full_edit_config, dict) else None
    if not f5_block or not isinstance(f5_block, dict):
        return ""
    drop_raw = f5_block.get("drop_rel_sec")
    if drop_raw is None:
        return ""
    seed = f5_block.get("combo_seed")
    if seed is None:
        return ""

    from mlcore.hooks.f1_sound.overlay import build_overlay_jsx

    overlay = build_overlay_jsx(drop_time=float(drop_raw), seed=int(seed))
    LOGGER.info("f5 overlay present drop_time=%s seed=%s js_len=%d", drop_raw, seed, len(overlay))
    return overlay


def _build_f1_overlay_js(full_edit_config: Dict[str, Any]) -> str:
    """
    Если в full_edit_config есть блок "f1" — собирает визуальный JSX combo
    (hook_light на дропе + рандомный F3-переход на post-drop склейках; pre-drop
    шейпов нет — там играет звук). Нет блока => пустая строка => ноль влияния.
    """
    f1_block = full_edit_config.get("f1") if isinstance(full_edit_config, dict) else None
    if not f1_block or not isinstance(f1_block, dict):
        return ""

    drop_time = f1_block.get("drop_time")
    if drop_time is None:
        raise RuntimeError("f1 block present but 'drop_time' is missing")
    seed = f1_block.get("seed")
    if seed is None:
        raise RuntimeError("f1 block present but 'seed' is missing")

    from mlcore.hooks.f1_sound.overlay import build_overlay_jsx

    overlay = build_overlay_jsx(drop_time=float(drop_time), seed=int(seed))
    LOGGER.info("f1 overlay present drop_time=%s seed=%s js_len=%d", drop_time, seed, len(overlay))
    return overlay


def _build_jsx_subtitles_js(full_edit_config: Dict[str, Any]) -> str:
    """5th-template JSX subtitle generator (trendy/brat).

    Orchestrator emits full_edit_config["subtitles_jsx"] = {mode, word_timings,
    bpm} for these modes; we inline it into the chosen script (read by the
    template token, after the hook overlays, before save). Absent => "".
    """
    block = full_edit_config.get("subtitles_jsx") if isinstance(full_edit_config, dict) else None
    if not block or not isinstance(block, dict):
        return ""

    from app.jsx_subtitles_builder import build_jsx_subtitles_overlay

    mode = str(block.get("mode") or "").strip()
    word_timings = block.get("word_timings") or []
    bpm = block.get("bpm")
    # Subtitle color: same env that recolors Python subtitle fills
    # (SUBTITLES_FORCE_FILL_HEX) — apply it to the trendy/brat text too.
    fill_hex = str(os.environ.get("SUBTITLES_FORCE_FILL_HEX") or "").strip() or None
    overlay = build_jsx_subtitles_overlay(
        mode=mode,
        word_timings=list(word_timings),
        bpm=(float(bpm) if bpm is not None else None),
        fill_hex=fill_hex,
    )
    LOGGER.info(
        "jsx subtitles present mode=%s words=%d bpm=%s js_len=%d",
        mode, len(word_timings), bpm, len(overlay),
    )
    return overlay


def _build_f2_overlay_js(full_edit_config: Dict[str, Any]) -> str:
    """
    Если в full_edit_config есть блок "f2" — собирает инъектируемый JSX-блок
    F2 «Объект» packaged-combo (shape на pre-drop склейках + hook_light на
    дропе + рандомный F3-переход на post-drop склейках). Блок встраивается в
    шаблон сырым куском (рядом с f3/f4 блоками, до сохранения проекта).

    Блок "f2" формируется оркестратором: {"shape": <id>, "drop_time": <float>,
    "seed": <int>}. Нет блока => пустая строка => ноль влияния.

    Импорт mlcore.hooks делаем лениво, чтобы project_builder не тянул лишнее.
    """
    f2_block = full_edit_config.get("f2") if isinstance(full_edit_config, dict) else None
    if not f2_block or not isinstance(f2_block, dict):
        return ""

    shape = str(f2_block.get("shape") or "").strip().lower()
    if not shape:
        raise RuntimeError("f2 block present but 'shape' is empty")
    drop_time = f2_block.get("drop_time")
    if drop_time is None:
        raise RuntimeError("f2 block present but 'drop_time' is missing")
    seed = f2_block.get("seed")
    if seed is None:
        raise RuntimeError("f2 block present but 'seed' is missing")

    from mlcore.hooks.f2_object.overlay import build_overlay_jsx

    # Custom shape color (F2 «Объект» customization). Absent → script default.
    shape_fill_hex = str(os.environ.get("F2_SHAPE_COLOR_HEX") or "").strip() or None
    overlay = build_overlay_jsx(
        shape=shape,
        drop_time=float(drop_time),
        seed=int(seed),
        shape_fill_hex=shape_fill_hex,
    )
    LOGGER.info(
        "f2 combo present shape=%s drop_time=%s seed=%s color=%s js_len=%d",
        shape, drop_time, seed, shape_fill_hex, len(overlay),
    )
    return overlay


def _build_f3_overlay_js(full_edit_config: Dict[str, Any]) -> str:
    """
    Если в full_edit_config есть блок "f3" — собирает инъектируемый JSX-блок
    эффектов («Эффект»: хук + переход + грейд + звук + лого). Блок встраивается
    в шаблон сырым куском (рядом с f4-блоком, до сохранения проекта).

    Блок "f3" формируется оркестратором:
      {"hook": <id|null>, "transition": <id|null>, "extra": <id|null>,
       "hook_extend": <"to_end"|"after_drop:N"|null>, "drop_time": <float>,
       "assets": {"hook_sound": "media/audio/..", "transition_sound": ..,
                  "extra_sound": .., "logo": "media/img/.."}}.
    Нет блока / пустой выбор => пустая строка => ноль влияния.

    Импорт mlcore.hooks делаем лениво, чтобы project_builder не тянул лишнее,
    когда эффектов нет.
    """
    f3_block = full_edit_config.get("f3") if isinstance(full_edit_config, dict) else None
    if not f3_block or not isinstance(f3_block, dict):
        return ""

    hook = (str(f3_block.get("hook") or "").strip() or None)
    transition = (str(f3_block.get("transition") or "").strip() or None)
    extra = (str(f3_block.get("extra") or "").strip() or None)
    if not (hook or transition or extra):
        return ""

    drop_time = f3_block.get("drop_time")
    if drop_time is None:
        raise RuntimeError("f3 block present but 'drop_time' is missing")
    hook_extend = (str(f3_block.get("hook_extend") or "").strip() or None)
    assets = f3_block.get("assets") if isinstance(f3_block.get("assets"), dict) else {}

    from mlcore.hooks.f3_effect.overlay import build_overlay_jsx

    overlay = build_overlay_jsx(
        hook=hook,
        transition=transition,
        extra=extra,
        hook_extend=hook_extend,
        drop_time=float(drop_time),
        assets=assets,
    )
    LOGGER.info(
        "f3 fx present hook=%s trans=%s extra=%s extend=%s js_len=%d",
        hook, transition, extra, hook_extend, len(overlay),
    )
    return overlay


def _extract_f3_media(full_edit_config: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Извлекает f3.assets download list (S3-URL + relpath) из full_edit_config,
    отфильтровывая мусор. Возвращает [{url, relpath}, ...] для записи в payload
    под ключом "f3_media" — render_manifest.collect_media_urls_from_render_payload
    подцепит и положит рядом с футажом в Windows-payload.media[].
    Нет блока / пустой список => [].
    """
    f3 = full_edit_config.get("f3") if isinstance(full_edit_config, dict) else None
    if not isinstance(f3, dict):
        return []
    raw = f3.get("_media")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for it in raw:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or "").strip()
        rel = str(it.get("relpath") or "").strip().strip("/")
        if not url or not rel:
            continue
        if rel in seen:
            continue
        seen.add(rel)
        out.append({"url": url, "relpath": rel})
    return out


def build_full_project(
    *,
    repo_root: Path,
    full_edit_config_path: Path,
    footage_config_path: Path,
    out_dir: Path,
) -> Tuple[Path, Path]:
    repo_root = repo_root.resolve()
    full_edit_config_path = full_edit_config_path.resolve()
    footage_config_path = footage_config_path.resolve()
    out_dir = out_dir.resolve()

    if not full_edit_config_path.exists():
        raise FileNotFoundError(str(full_edit_config_path))
    if not footage_config_path.exists():
        raise FileNotFoundError(str(footage_config_path))

    full_edit_config = json.loads(full_edit_config_path.read_text(encoding="utf-8"))
    footage_cfg = json.loads(footage_config_path.read_text(encoding="utf-8"))
    subtitles_mode = normalize_subtitles_mode(
        str(full_edit_config.get("subtitles_mode") or ""),
        default=SUBTITLES_MODE_LEGACY_BLOCKS,
    )

    main_comp = dict(AE_PROJECT["main_comp"])
    text_comp = dict(AE_PROJECT["text_comp"])
    mine_comp = dict(AE_PROJECT["mine_comp"])

    main_name = str(main_comp["name"])
    text_name = str(text_comp["name"])
    mine_name = str(mine_comp["name"])

    # ----------------------------------------------------------
    # Resolve factual composition duration (explicit + logged fallbacks).
    # ----------------------------------------------------------
    comp_meta = full_edit_config.get("composition") if isinstance(full_edit_config, dict) else None
    composition_dur = None
    if isinstance(comp_meta, dict):
        d = comp_meta.get("dur")
        if d is not None:
            try:
                composition_dur = float(d)
            except Exception:
                composition_dur = None
                LOGGER.warning("composition.dur is present but invalid: %r", d)

    layers_cfg = list(footage_cfg.get("layers") or [])
    comp_dur = resolve_text_duration_sec(
        composition_dur=composition_dur,
        footage_cfg=footage_cfg,
        layers_cfg=layers_cfg,
    )

    comps_list = [main_comp, text_comp, mine_comp]
    comps_list = _apply_comp_duration_overrides(
        comps=comps_list,
        main_comp_name=main_name,
        text_comp_name=text_name,
        mine_comp_name=mine_name,
        comp_dur=float(comp_dur),
    )

    main_comp = next((c for c in comps_list if c.get("name") == main_name), main_comp)
    text_comp = next((c for c in comps_list if c.get("name") == text_name), text_comp)
    mine_comp = next((c for c in comps_list if c.get("name") == mine_name), mine_comp)

    # 1) Footage layers
    footage_layers = build_footage_layers(
        repo_root=repo_root,
        footage_cfg=footage_cfg,
        main_comp_name=main_name,
        text_comp_name=text_name,
        composition_dur=comp_dur,
        precomp_z_index=int(AE_PROJECT.get("root_precomp_z_index", 9999)),
        precomp_placement=AE_PROJECT.get("root_precomp_placement"),
        subtitles_mode=subtitles_mode,
    )

    # 2) Text layers. In 5th-template JSX subtitle modes (trendy/brat) the
    #    injected script generates the subtitle layers itself from word-timings,
    #    so we skip the normal Python text_layers entirely.
    if subtitles_mode in SUBTITLES_MODE_JSX_5TH:
        text_layers = []
        LOGGER.info("subtitles_mode=%s → JSX-generated subtitles (text_layers skipped)", subtitles_mode)
    else:
        text_layers = build_text_layers(
            full_edit_config=full_edit_config,
            text_comp_name=text_name,
            mine_comp_name=mine_name,
        )

    # 2.5) F5 Cognition hook («Мысль»): если в config есть блок "f5" — добавляем
    #      TTS audio-слой + TTS subtitle-слой и вырезаем перекрытые трек-субтитры.
    #      Если блока нет — zero impact, обычные job'ы не затрагиваются.
    footage_layers, text_layers = _apply_f5_if_present(
        full_edit_config=full_edit_config,
        footage_layers=footage_layers,
        text_layers=text_layers,
        main_comp_name=main_name,
    )

    # 2.6) F1 «Звук» hook: если в config есть блок "f1" — добавляем audio-слой с
    #      загруженным пользователем звуком в окне [0.5, drop−0.5]. Визуальная
    #      часть (hook_light + post-drop random) идёт через токен f1_overlay_js.
    footage_layers, text_layers = _apply_f1_if_present(
        full_edit_config=full_edit_config,
        footage_layers=footage_layers,
        text_layers=text_layers,
        main_comp_name=main_name,
    )

    payload: Dict[str, Any] = {
        "project": {"mainCompName": main_name, "subtitlesMode": subtitles_mode},
        "comps": [main_comp, text_comp, mine_comp],
        "footage_layers": footage_layers,
        "text_layers": text_layers,
    }

    force_fill_hex = str(os.environ.get("SUBTITLES_FORCE_FILL_HEX") or "").strip()
    if force_fill_hex:
        target = _parse_hex_color_rgb01(force_fill_hex)
        if target is None:
            raise RuntimeError(
                f"SUBTITLES_FORCE_FILL_HEX is set but invalid: {force_fill_hex!r}"
            )
        replaced = _override_white_fill_colors(payload, target)
        LOGGER.info(
            "subtitles_fill_override hex=%s rgb01=%s replacements=%d",
            force_fill_hex,
            target,
            replaced,
        )

    # F4 «Движение» motion-hook overlay. If full_edit_config carries an "f4"
    # block ({device, bpm}) we build the chosen device's injectable JSX and pass
    # it to the template as a raw block (rendered after addFlashOnCuts, before
    # save). Absent block => empty string => zero impact on regular jobs.
    f4_overlay_js = _build_f4_overlay_js(full_edit_config)
    # F3 «Эффект» overlay (hook/transition/extra + sound + logo). Absent => "".
    f3_overlay_js = _build_f3_overlay_js(full_edit_config)
    # F2 «Объект» packaged-combo overlay (shape pre-drop + hook_light + random
    # F3 transition post-drop). Absent block => empty string => zero impact.
    f2_overlay_js = _build_f2_overlay_js(full_edit_config)
    # F1 «Звук» visual combo (hook_light + post-drop random; no pre-drop shapes,
    # the user's sound plays there). Absent block => "".
    f1_overlay_js = _build_f1_overlay_js(full_edit_config)
    # F5 «Мысль» visual combo (hook_light at drop + post-drop random F3). Voice
    # is injected separately (_apply_f5_if_present). Absent drop => "".
    f5_overlay_js = _build_f5_overlay_js(full_edit_config)
    # 5th-template JSX subtitles (trendy/brat). Injected over the main comp; the
    # script builds the subtitle layers from word-timings. Absent block => "".
    jsx_subtitles_js = _build_jsx_subtitles_js(full_edit_config)
    # F3 ассет-download list (sound/logo S3-URL'ы + relpath под __APP_DIR/media).
    # render_manifest.collect_media_urls_from_render_payload подцепит и положит
    # в Windows-payload.media[] рядом с футажом. Пусто => без звука/лого.
    f3_media = _extract_f3_media(full_edit_config)
    if f3_media:
        payload["f3_media"] = f3_media

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)

    out_json = out_dir / "final_render_instructions_full.json"
    out_jsx = out_dir / "render_full.jsx"

    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ✅ IMPORTANT: add tojson filter so templates can safely embed JSON into JSX
    env = Environment(loader=FileSystemLoader(str(repo_root / "templates")), autoescape=False)
    env.filters["tojson"] = _tojson_filter

    tpl = env.get_template("project_template.j2")
    jsx = tpl.render(
        **payload,
        f4_overlay_js=f4_overlay_js,
        f3_overlay_js=f3_overlay_js,
        f2_overlay_js=f2_overlay_js,
        f1_overlay_js=f1_overlay_js,
        f5_overlay_js=f5_overlay_js,
        jsx_subtitles_js=jsx_subtitles_js,
    )
    out_jsx.write_text(jsx, encoding="utf-8")

    return out_json, out_jsx
