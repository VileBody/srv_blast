"""«Докрутить на сайте»: ролики батча из бота → черновик визарда, открытый на монтажном столе.

Решение продукта: ссылка переносит ВСЁ и сразу — трек, окно, текст и те же слова ASR,
стиль и цвета субтитров, вайб и ровно те же склейки и клипы, переход, стилизацию, хук
и рамку. Источник — оркестратор (`GET /jobs/{id}/edit_state` на каждый ролик батча):
склейки и клипы джоба хранит в resume_state (`stage2_footage_plan`), настройки — в
запросе джобы. Если запрос уже вычищен TTL стора, берётся снимок настроек, который бот
положил в ссылку (`payload.settings`).

Обратные карты — от того же реестра эффектов, что и прямые (`effect_map`), так что
id бота и подписи сайта не разъедутся молча: неизвестный id = видимая пометка, а не
тихая подмена.

No Fallback: что нельзя перенести точно, перечислено в `notes` (их видит человек), а то,
без чего стол собрать нельзя (разные окна или склейки у роликов, вайб не из каталога
сайта, стиль субтитров без пары на сайте), — `BotImportError`. Тогда сайт открывает
визард по-старому (трек, окно, текст) и говорит об этом вслух.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import effect_map as em
from .frames import FRAMES

_TOL = 1e-3

# id прошлой версии плана (`mlcore.storyboard_plan.build_plan`)
_PLAN_VERSION = 1

# Подписи шагов «Объект»/«Движение» живут в путях скриптов (как их читает production_backend).
_SHAPE_TOKENS = ("elipse", "square", "rhomb", "star1", "star2")
_MOTION_TOKENS = ("swipe", "tap", "pinch", "holdfinger", "head")

# Мок-режим: подписи стилей субтитров — как в .env.production.example (WEB_SUBTITLE_MODE_MAP_JSON).
MOCK_SUBTITLE_MODES: dict[str, str] = {
    "Brat": "brat_5th", "Jakson": "scenes_3rd", "Impulse": "impulse_2nd", "Tape": "template_4th",
    "Trendy": "trendy_5th", "Duo": "kant_two_frames", "Bubble": "kant_gum", "Code": "kant_matrix",
    "Novel": "kant_edit", "Retro": "kant_vhs", "Scribble": "kant_lani_style",
}

# Плоскость каталога футажа → id типа футажей визарда (data/footage-types.json)
_FOOTAGE_TYPE_BY_PLANE = {"vibes": "vertical", "cine16x9": "cine16x9", "films": "films"}

# Сплошной фон бота → цвет палитры сайта (production_backend переводит его обратно)
_SOLID_COLORS = {"white": "#ffffff", "black": "#000000", "green": "#00ff00"}


class BotImportError(ValueError):
    """Ролики бота нельзя открыть на столе как есть — сообщение показывается человеку."""


@dataclass(frozen=True)
class ImportCatalog:
    """Что сайт знает о каталогах: подпись стиля субтитров → режим рендера, подпись вайба/фото →
    точный слот (theme, tags_group), геометрия и плоскость каталога."""
    subtitle_modes: dict[str, str]
    footage: dict[str, dict[str, Any]] = field(default_factory=dict)
    photo: dict[str, dict[str, Any]] = field(default_factory=dict)


# ── обратные карты реестра эффектов ───────────────────────────────────────────

def _reverse(group: str) -> dict[str, str]:
    """manifestId → подпись (первая по реестру: у склеек есть ещё altId, он не подпись)."""
    out: dict[str, str] = {}
    for entry in em._REG.get(group, []):
        out.setdefault(str(entry["manifestId"]), str(entry["label"]))
    return out


HOOK_LABEL_BY_ID: dict[str, str] = _reverse("hook")
GLUE_LABEL_BY_ID: dict[str, str] = _reverse("glue")
STYLE_LABEL_BY_ID: dict[str, str] = _reverse("style")
THOUGHT_LABEL_BY_DEVICE: dict[str, str] = {device: label for label, device in em.THOUGHT_DEVICE.items()}


def _label_by_token(scripts: dict[str, str], tokens: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for label, script in scripts.items():
        for token in tokens:
            if f"_{token}.jsx" in script:
                out[token] = label
    return out


OBJECT_LABEL_BY_SHAPE: dict[str, str] = _label_by_token(em.OBJECT_SCRIPT, _SHAPE_TOKENS)
MOTION_LABEL_BY_DEVICE: dict[str, str] = _label_by_token(em.MOTION_SCRIPT, _MOTION_TOKENS)


# ── время ─────────────────────────────────────────────────────────────────────

def seconds_to_timing(value: float) -> str:
    """секунды → «мм:сс:сс» (поля тайминга визарда, render_job.mmss_seconds)."""
    cs = int(round(max(0.0, float(value)) * 100))
    return f"{cs // 6000:02d}:{(cs % 6000) // 100:02d}:{cs % 100:02d}"


def _floor_cs(value: float) -> float:
    return math.floor(round(float(value) * 100, 6)) / 100.0


def _ceil_cs(value: float) -> float:
    return math.ceil(round(float(value) * 100, 6)) / 100.0


def _same_points(a: list[float], b: list[float]) -> bool:
    return len(a) == len(b) and all(abs(float(x) - float(y)) <= _TOL for x, y in zip(a, b))


# ── настройки ролика: запрос джобы, иначе снимок из ссылки бота ────────────────

_SNAPSHOT_TO_REQUEST = {
    "subtitlesMode": "subtitles_mode",
    "visualTransition": "effect_transition",
    "visualStyle": "effect_extra",
    "hookEnabled": "hook_enabled",
    "f2Shape": "f2_shape",
    "frameId": "frame_id",
    "bgMode": "bg_mode",
    "bgSolidColor": "bg_solid_color",
    "subtitleColorHex": "subtitle_color_hex",
    "accentColorHex": "accent_color_hex",
    "hookDropT": "user_drop_t",
}


def _request_from_snapshot(snapshot: dict[str, Any], position: int) -> dict[str, Any]:
    """Тот же запрос, что собрал бы бот (`_enqueue_batch_version`), из снимка ChatState."""
    req: dict[str, Any] = {}
    for src, dst in _SNAPSHOT_TO_REQUEST.items():
        value = snapshot.get(src)
        if value not in (None, ""):
            req[dst] = value
    if req.get("frame_id") == "none":
        req.pop("frame_id")
    if req.get("effect_extra"):
        req["effect_extra_full"] = True
    category = str(snapshot.get("hookCategory") or "")
    hooked = bool(snapshot.get("hookEnabled"))
    if hooked and category == "effect" and snapshot.get("effectHook"):
        req["effect_hook"] = snapshot["effectHook"]
        if snapshot.get("effectHookExtend"):
            req["effect_hook_extend"] = snapshot["effectHookExtend"]
    if hooked and category == "thought" and snapshot.get("hookDevice"):
        req["hook_device"] = snapshot["hookDevice"]
    if hooked and category == "motion" and snapshot.get("hookDevice"):
        req["f4_device"] = snapshot["hookDevice"]
    if not (hooked and category == "object"):
        req.pop("f2_shape", None)
    # вайб видео i — как раздаёт бот: selected[i % K], id = "theme:tags_group"
    vibes = [str(v) for v in snapshot.get("vibeSelectedIds") or [] if ":" in str(v)]
    if vibes:
        theme, group = vibes[position % len(vibes)].split(":", 1)
        req["rotation_theme"], req["rotation_tags_group"] = theme, group
    return req


def _hex(value: Any) -> str | None:
    raw = str(value or "").strip().lstrip("#").lower()
    if len(raw) == 6 and all(c in "0123456789abcdef" for c in raw):
        return f"#{raw}"
    return None


# ── хук ролика → вариант FX визарда ───────────────────────────────────────────

def _variant_config(req: dict[str, Any], notes: list[str]) -> tuple[str, dict[str, Any]]:
    """(kind, config) варианта FX — зеркало `render_job._resolve_hook` + `production_backend`."""
    cfg: dict[str, Any] = {}
    kind = "none"
    if req.get("f6_video_url"):
        kind = "warmup"
        cfg.update({
            "warmupKind": "video", "sound": "Видео из бота",
            "videoUrl": str(req["f6_video_url"]),
            "videoWidth": req.get("f6_video_width"), "videoHeight": req.get("f6_video_height"),
            "videoDuration": req.get("f6_video_duration"),
            "videoHasAudio": bool(req.get("f6_video_has_audio", True)),
        })
    elif req.get("f1_sound_url"):
        kind = "warmup"
        # Длительность звука бот не хранит, а прогрев на сайте её требует: рендер явно
        # попросит загрузить звук заново (production_backend), тихо без звука не уйдёт.
        cfg.update({"warmupKind": "audio", "sound": "Звук из бота", "soundUrl": str(req["f1_sound_url"])})
        notes.append("Звук прогрева из бота загрузи на сайте заново: без этого генерация не начнётся.")
    elif req.get("f2_shape"):
        label = OBJECT_LABEL_BY_SHAPE.get(str(req["f2_shape"]))
        if label is None:
            raise BotImportError(f"Фигуры «{req['f2_shape']}» на сайте нет")
        kind, cfg["object"] = "object", label
    elif req.get("f4_device"):
        label = MOTION_LABEL_BY_DEVICE.get(str(req["f4_device"]))
        if label is None:
            raise BotImportError(f"Движения «{req['f4_device']}» на сайте нет")
        kind, cfg["motion"] = "motion", label
    elif req.get("hook_device"):
        label = THOUGHT_LABEL_BY_DEVICE.get(str(req["hook_device"]))
        if label is None:
            raise BotImportError(f"Приёма «{req['hook_device']}» на сайте нет")
        kind, cfg["thought"] = "thought", label
    elif req.get("effect_hook"):
        label = HOOK_LABEL_BY_ID.get(str(req["effect_hook"]))
        if label is None:
            raise BotImportError(f"Хука «{req['effect_hook']}» на сайте нет")
        kind, cfg["effectHook"] = "effects", label
        extend = str(req.get("effect_hook_extend") or "")
        if extend in {"to_end", "after_drop:3"}:
            cfg["effectHookExtend"] = extend
        elif extend:
            notes.append(f"Длина шлейфа «{extend}» из бота на сайте не настраивается, стоит штатная.")

    transition = str(req.get("effect_transition") or "")
    glue = GLUE_LABEL_BY_ID.get(transition) if transition else None
    if transition and glue is None:
        notes.append(f"Переход «{transition}» есть только в боте: на сайте ролик пойдёт без него, выбери другой на столе.")
    cfg["effectGlue"] = glue or em.NO_GLUE_LABEL

    extra = str(req.get("effect_extra") or "")
    style = STYLE_LABEL_BY_ID.get(extra) if extra else None
    if extra and style is None:
        notes.append(f"Стилизации «{extra}» на сайте нет: ролик пойдёт без неё, выбери другую на столе.")
    cfg["effectStyle"] = style or em.NO_STYLE_LABEL
    cfg["effectStyles"] = [cfg["effectStyle"]]
    if style and kind != "none":
        cfg["effectStyleFull"] = bool(req.get("effect_extra_full"))
    return kind, cfg


# ── фон ролика ────────────────────────────────────────────────────────────────

@dataclass
class _Background:
    key: str            # ключ allocation.background ("footage:<имя>", "photo:<имя>", "__color__")
    mode: str           # footage | photo | color
    name: str = ""
    plane: str = ""
    fmt: str = "9:16"
    color: str | None = None
    strobe: bool = False

    @property
    def hook_allowed(self) -> bool:
        return self.mode == "footage" and self.fmt != "16:9"


def _find_slot(items: dict[str, dict[str, Any]], theme: str, group: str) -> tuple[str, dict[str, Any]] | None:
    for name, sel in items.items():
        if str(sel.get("rotationTheme") or "") == theme and str(sel.get("rotationTagsGroup") or "") == group:
            return name, sel
    return None


def _background(req: dict[str, Any], catalog: ImportCatalog) -> _Background:
    bg_mode = str(req.get("bg_mode") or "footage")
    if bg_mode in {"solid", "solid_strobe"}:
        raw = str(req.get("bg_solid_color") or "white").lower()
        color = _SOLID_COLORS.get(raw) or _hex(raw)
        if color is None:
            raise BotImportError(f"Цвет фона «{raw}» сайт не знает")
        return _Background(key="__color__", mode="color", color=color, strobe=bg_mode == "solid_strobe")
    theme = str(req.get("rotation_theme") or "")
    group = str(req.get("rotation_tags_group") or "")
    if not theme or not group:
        raise BotImportError("Футаж ролика подобран по артисту, а не по вайбу: на сайте такого выбора нет")
    if bg_mode == "photo":
        found = _find_slot(catalog.photo, theme, group)
        if found is None:
            raise BotImportError(f"Фото-вайба {theme}:{group} нет в каталоге сайта")
        return _Background(key=f"photo:{found[0]}", mode="photo", name=found[0])
    found = _find_slot(catalog.footage, theme, group)
    if found is None:
        raise BotImportError(f"Вайба {theme}:{group} нет в каталоге сайта")
    name, sel = found
    fmt = "16:9" if str(sel.get("renderPreset") or "") == "wide" else "9:16"
    return _Background(key=f"footage:{name}", mode="footage", name=name, plane=str(sel.get("plane") or "vibes"), fmt=fmt)


# ── план раскадровки под окно визарда ─────────────────────────────────────────

def _reframe_clip(clip: dict[str, Any], *, new_in: float | None = None, new_out: float | None = None) -> dict[str, Any]:
    """Сдвинуть край крайнего кадра, не трогая, какие кадры исходника идут на экране."""
    out = dict(clip)
    start_time = float(clip["start_time"])
    if new_in is not None:
        offset = new_in - start_time
        if offset < 0:
            # исходник раньше не начинается: кадр стартует с начала файла (сдвиг ≤ окна округления)
            start_time, offset = new_in, 0.0
        out.update({"in_point": new_in, "start_time": start_time, "source_offset_sec": offset})
    if new_out is not None:
        out["out_point"] = new_out
    return out


def fit_plan(plan: dict[str, Any], *, start: float, end: float, cuts: list[float]) -> dict[str, Any]:
    """План бот-джобы → окно визарда [start, end] (окно ASR, округлённое наружу до сотых).

    Окно плана бывает уже окна слов (субтитры прибивают окно к фразам), и визард сверяет
    план с отрывком до тысячной (`storyboard.attach_to_variations`). Поэтому крайние кадры
    дотягиваются до краёв отрывка, внутренние склейки и клипы не меняются. Рендер сам
    подрежет края обратно под своё окно (`storyboard_plan.validate_plan`).
    """
    if int(plan.get("version") or 0) != _PLAN_VERSION:
        raise BotImportError("План монтажа ролика в неизвестном формате")
    if not _same_points([float(p) for p in plan.get("switch_points_abs") or []], cuts):
        raise BotImportError("Склейки плана не совпадают со склейками ролика")
    clips = sorted((dict(c) for c in plan.get("clips") or []), key=lambda c: float(c["in_point"]))
    if len(clips) != len(cuts) + 1:
        raise BotImportError("В плане монтажа ролика не хватает кадров")
    if not (start < (cuts[0] if cuts else end) and (cuts[-1] if cuts else start) < end):
        raise BotImportError("Склейки ролика выходят за отрывок")
    clips[0] = _reframe_clip(clips[0], new_in=start)
    clips[-1] = _reframe_clip(clips[-1], new_out=end)
    return {
        "version": _PLAN_VERSION,
        "clip_start_abs": start,
        "clip_end_abs": end,
        "switch_points_abs": list(cuts),
        "clips": clips,
    }


# ── сборка ────────────────────────────────────────────────────────────────────

def build_wizard_import(
    edit_states: list[dict[str, Any]],
    catalog: ImportCatalog,
    *,
    snapshot: dict[str, Any] | None = None,
    draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ролики батча (ответы `/jobs/{id}/edit_state` в порядке бота) → импорт визарда.

    Возвращает camelCase-блок для фронта (`wizardStore.importEdit`): тайминг, текст, дроп,
    фон, субтитры, варианты FX, распределение «Пула», склейки таймлайна, раскадровку
    (планы без превью: превью подтягивает вызывающий через подбор с закреплёнными клипами),
    рамки роликов и окно для примерки субтитров. `notes` — что перенести точно не вышло.
    """
    snapshot = dict(snapshot or {})
    notes: list[str] = []
    versions: list[dict[str, Any]] = []
    for position, state in enumerate(edit_states):
        status = state.get("status")
        if status not in (None, "SUCCEEDED"):
            notes.append(f"Ролик {position + 1} в боте не собрался, на стол он не попал.")
            continue
        req = dict(state.get("request") or {}) or _request_from_snapshot(snapshot, position)
        versions.append({"position": position, "state": state, "req": req})
    if not versions:
        raise BotImportError("Ни один ролик батча не собрался в боте")

    first = versions[0]
    # ── окно: слова ASR лежат в рабочем окне выравнивателя — оно и есть отрывок ──
    def window_of(v: dict[str, Any]) -> tuple[float, float]:
        w = v["state"].get("window") or {}
        if w.get("clip_start_abs") is not None and w.get("clip_end_abs") is not None:
            return float(w["clip_start_abs"]), float(w["clip_end_abs"])
        plan = v["state"].get("footage_plan") or {}
        if plan.get("clip_start_abs") is not None:
            return float(plan["clip_start_abs"]), float(plan["clip_end_abs"])
        req = v["req"]
        if req.get("user_clip_start_sec") is not None and req.get("user_clip_end_sec") is not None:
            return float(req["user_clip_start_sec"]), float(req["user_clip_end_sec"])
        if draft and float(draft.get("clipEnd") or 0) > float(draft.get("clipStart") or 0):
            return float(draft["clipStart"]), float(draft["clipEnd"])
        raise BotImportError("У ролика нет отрывка трека")

    w_start, w_end = window_of(first)
    for v in versions[1:]:
        s, e = window_of(v)
        if abs(s - w_start) > _TOL or abs(e - w_end) > _TOL:
            raise BotImportError("Ролики батча сняты на разные отрывки трека: на одном столе их не собрать")
    start, end = _floor_cs(w_start), _ceil_cs(w_end)

    # ── склейки: общие на батч (как рецепт таймлайна) ──
    def cuts_of(v: dict[str, Any]) -> list[float] | None:
        plan = v["state"].get("footage_plan") or {}
        points = plan.get("switch_points_abs") if plan else None
        if points is None:
            points = v["state"].get("switch_points_abs")
        return [float(p) for p in points] if points is not None else None

    cuts = cuts_of(first)
    if cuts is None:
        raise BotImportError("Склейки ролика не сохранились: ролик собран до того, как их начали хранить")
    for v in versions[1:]:
        other = cuts_of(v)
        if other is None or not _same_points(other, cuts):
            raise BotImportError("Склейки у роликов батча разные: на одном столе их не собрать")
    cuts = [p for p in cuts if start + _TOL < p < end - _TOL]

    # ── дроп ──
    drop_raw = first["req"].get("user_drop_t")
    drop = float(drop_raw) if drop_raw is not None else None
    if drop is not None and not (start <= drop <= end):
        notes.append("Дроп ролика лежит вне отрывка, на сайте выбери его заново.")
        drop = None

    # ── фон, субтитры, хук по роликам ──
    for v in versions:
        v["bg"] = _background(v["req"], catalog)
    modes = {str(v["req"].get("subtitles_mode") or "") for v in versions}
    if len(modes) != 1 or not next(iter(modes)):
        raise BotImportError("Стиль субтитров роликов не определён")
    mode = next(iter(modes))
    style = next((name for name, m in catalog.subtitle_modes.items() if m == mode), None)
    if style is None:
        raise BotImportError(f"Стиля субтитров «{mode}» на сайте нет")

    variant_notes: list[str] = []
    configs = [_variant_config(v["req"], variant_notes) for v in versions]
    for note in variant_notes:
        if note not in notes:
            notes.append(note)
    if len({json.dumps(c, sort_keys=True, ensure_ascii=False) for c in configs}) != 1:
        raise BotImportError("Хуки у роликов батча разные: на одном столе их не собрать")
    kind, config = configs[0]
    hooked = kind != "none"
    if kind in {"object", "warmup", "thought"}:
        # Комбо этих хуков ставит после дропа случайные переходы с сидом от id джобы
        notes.append("Переходы после дропа на сайте могут выпасть другие, чем в боте.")
    if hooked and drop is None:
        raise BotImportError("Для хука ролика нужен дроп, а его нет")

    # Порядок роликов на сайте: «Пул» раскладывает фоны группами (combinationAt), поэтому
    # ролики одного вайба идут подряд — в порядке первого появления в батче бота.
    order: list[str] = []
    for v in versions:
        if v["bg"].key not in order:
            order.append(v["bg"].key)
    color_versions = [v for v in versions if v["bg"].mode == "color"]
    if len(color_versions) > 1:
        notes.append("На сайте цветной фон бывает только у одного ролика: перенесён первый из цветных.")
    rest = [v for v in versions if v["bg"].mode != "color"]
    rest.sort(key=lambda v: order.index(v["bg"].key))
    ordered = rest + color_versions[:1]
    if [v["position"] for v in ordered] != [v["position"] for v in versions]:
        notes.append("Ролики одного вайба на сайте стоят рядом, поэтому их номера могли поменяться.")

    footage_names: list[str] = []
    photo_names: list[str] = []
    formats: dict[str, str] = {}
    planes: set[str] = set()
    alloc_bg: dict[str, int] = {}
    color_bg = color_versions[0]["bg"] if color_versions else None
    for v in rest:
        bg = v["bg"]
        alloc_bg[bg.key] = alloc_bg.get(bg.key, 0) + 1
        if bg.mode == "footage" and bg.name not in footage_names:
            footage_names.append(bg.name)
            formats[bg.name] = bg.fmt
            planes.add(bg.plane)
        if bg.mode == "photo" and bg.name not in photo_names:
            photo_names.append(bg.name)
    if len(planes) > 1:
        raise BotImportError("Ролики батча из разных библиотек футажа: на сайте батч берёт одну")
    plane = next(iter(planes), "vibes")
    hook_target = sum(1 for v in rest if v["bg"].hook_allowed)
    if hooked and hook_target < len(ordered):
        notes.append("Хук сайт ставит только на вертикальное видео: на фото, цвете и 16:9 его не будет.")

    variant_id = "v-bot-1"
    background: dict[str, Any] = {
        "mode": "footage" if footage_names else ("photo" if photo_names else "color"),
        "footage": footage_names,
        "footageFormats": formats,
        "footageType": _FOOTAGE_TYPE_BY_PLANE.get(plane, "vertical"),
        "photo": photo_names,
        "photoEffects": False,
        "strobe": bool(color_bg and color_bg.strobe),
    }
    if color_bg is not None:
        background["color"] = color_bg.color
    # Фото, цвет и 16:9 хука не получают — переход и стиль им даёт фон (render_job._resolve_hook).
    if hook_target < len(ordered):
        if config.get("effectGlue") != em.NO_GLUE_LABEL:
            background["glue"] = config["effectGlue"]
        if photo_names and config.get("effectStyle") != em.NO_STYLE_LABEL:
            background.update({"photoEffects": True, "photoStyle": config["effectStyle"]})

    subtitle_color = _hex(first["req"].get("subtitle_color_hex"))
    accent = _hex(first["req"].get("accent_color_hex"))
    subtitles: dict[str, Any] = {"pool": [style], "textTab": style, "textByStyle": {}}
    if subtitle_color:
        subtitles["color"] = subtitle_color
    if accent:
        subtitles["textByStyle"] = {style: {"accentColor": accent}}

    allocation = {
        "total": len(ordered),
        "background": alloc_bg,
        "subtitles": {style: len(rest)} if rest else {},
        "hooks": {},
        "styles": {},
        "variants": {variant_id: hook_target},
        "seeded": True,
    }
    if color_bg is not None:
        allocation["strobeFont" if color_bg.strobe else "colorFont"] = style

    # ── раскадровка: точные клипы — только у вертикальных вайбов с сохранённым планом ──
    storyboard: list[dict[str, Any]] = []
    missing_plan = False
    for index, v in enumerate(ordered):
        bg = v["bg"]
        if bg.mode != "footage" or bg.plane != "vibes":
            continue
        plan = v["state"].get("footage_plan")
        meta = v["state"].get("footage_plan_meta") or {}
        if not plan or meta.get("bg_mode", "footage") != "footage" or meta.get("exact_slot") is False:
            missing_plan = True
            continue
        storyboard.append({"index": index + 1, "group": bg.name, "plan": fit_plan(plan, start=start, end=end, cuts=cuts)})
    if missing_plan:
        notes.append("Клипы части роликов не сохранились в боте: склейки те же, клипы подберутся заново.")
    if any(v["bg"].mode == "footage" and v["bg"].plane != "vibes" for v in ordered):
        notes.append("Коллекции и фильмы подбираются при генерации: склейки те же, кадры могут отличаться.")
    if any(v["bg"].mode == "photo" for v in ordered):
        notes.append("Фото подбираются при генерации: склейки те же, кадры могут отличаться.")

    # ── рамка: на столе она у ролика, а не у батча ──
    frames: dict[int, str] = {}
    for index, v in enumerate(ordered):
        frame = str(v["req"].get("frame_id") or "")
        if not frame or frame == "none":
            continue
        if frame not in FRAMES:
            notes.append(f"Рамки «{frame}» на сайте нет.")
            continue
        if v["bg"].fmt == "16:9":
            continue
        frames[index] = frame

    lyrics = str((first["state"].get("asr") or {}).get("reference_text") or first["req"].get("target_fragment")
                 or (draft or {}).get("lyrics") or "")
    words = [
        {"text": str(w["text"]), "tStart": float(w["t_start"]), "tEnd": float(w["t_end"])}
        for w in first["state"].get("words") or []
    ]
    return {
        "timing": {"from": seconds_to_timing(start), "to": seconds_to_timing(end)},
        "window": {"start": start, "end": end},
        "lyrics": lyrics,
        "dropTime": seconds_to_timing(drop) if drop is not None else None,
        "background": background,
        "subtitles": subtitles,
        "fxVariants": [{"id": variant_id, "kind": kind, "config": config}],
        "allocation": allocation,
        "timeline": {"pace": "auto", "cuts": cuts},
        "storyboard": storyboard,
        "frames": frames,
        "words": words,
        "asrSourceJobId": str(first["state"].get("job_id") or ""),
        "asrAvailable": bool((first["state"].get("asr") or {}).get("available"))
        and str((first["state"].get("asr") or {}).get("mode") or "") == "local_ctc",
        "jobOrder": [str(v["state"].get("job_id") or "") for v in ordered],
        "notes": notes,
    }
