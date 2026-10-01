"""Правки монтажного стола визарда → вариации render_job.

Стол правит ролик целиком: хук, переход на каждой склейке, стили по окнам кадров и стиль
субтитров. Фронт шлёт только ролики, которые правили руками
(`stageData.montage.videos[<index с нуля>]`), остальные собираются по варианту FX, как раньше.

Правила (No Fallback — расхождение = явная ошибка, а не тихий пропуск правки):

* правка относится к видео с той же комбинацией (фон · субтитры · вариант FX), под которую
  её делали: подпись `sig` сверяется с раскладкой рендера;
* переход на склейке — как показывает стол: свой на склейке, иначе склейка хука ролика,
  иначе «Без склейки». Склейку фона сюда не подставляем — стол её не показывал;
* склейки — рецепт таймлайна (`timeline.cuts`); время уходит абсолютным, оркестратор
  сверит его с закреплёнными склейками;
* своё видео склеивается встык загруженными клипами, склеек таймлайна у него нет — переход
  там один на весь ролик; у статичного цвета склеек нет вовсе.
"""
from __future__ import annotations

from typing import Any

from . import effect_map as em
from .frames import validate_frame


class MontageError(ValueError):
    """Правки стола нельзя применить как есть — сообщение показывается юзеру."""


def _cuts(timeline: dict[str, Any] | None, segment: dict[str, float] | None) -> list[float] | None:
    cuts = (timeline or {}).get("cuts")
    if cuts is None:
        return None
    if not isinstance(cuts, list):
        raise MontageError("Склейки таймлайна повреждены — открой стол ещё раз")
    points = [float(c) for c in cuts]
    if segment is None:
        raise MontageError("Правки стола есть, а отрывок не выбран — выбери отрывок трека")
    if any(not (segment["from"] < p < segment["to"]) for p in points) or any(b <= a for a, b in zip(points, points[1:])):
        raise MontageError("Склейки собраны для другого отрывка — открой стол, они пересчитаются")
    return points


def apply_montage(
    variations: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    montage: dict[str, Any] | None,
    timeline: dict[str, Any] | None,
    segment: dict[str, float] | None,
    text_by_style: dict[str, Any],
    resolve_hook: Any,
) -> None:
    """Применить правки стола к вариациям на месте.

    slots[i] — раскладка видео i (с нуля): {"sig", "bgKey", "hookAllowed", "mode", "strobe",
    "custom"}; resolve_hook — render_job._resolve_hook (тот же резолв хука, что у вариантов).
    """
    videos = (montage or {}).get("videos") or {}
    if not videos:
        return
    if not isinstance(videos, dict):
        raise MontageError("Правки стола повреждены — открой стол и сохрани их заново")
    cuts = _cuts(timeline, segment)
    for key, entry in videos.items():
        try:
            index = int(key)
        except (TypeError, ValueError) as exc:
            raise MontageError(f"Правки стола повреждены: неизвестный ролик {key!r}") from exc
        if not isinstance(entry, dict):
            raise MontageError(f"Правки стола для видео {index + 1} повреждены")
        if not (0 <= index < len(variations)):
            raise MontageError(f"Правки стола устарели: видео {index + 1} больше нет в батче")
        slot = slots[index]
        if str(entry.get("sig") or "") != slot["sig"]:
            raise MontageError(
                f"Правки стола устарели: у видео {index + 1} поменялись фон, субтитры или FX — "
                "открой стол, чтобы пересобрать их"
            )
        _apply_one(variations[index], slot, entry, index, cuts, segment, text_by_style, resolve_hook)


def _apply_one(variation, slot, entry, index, cuts, segment, text_by_style, resolve_hook) -> None:
    n = index + 1
    kind = str(entry.get("kind") or "none")
    cfg = dict(entry.get("config") or {})
    if kind != "none" and not slot["hookAllowed"]:
        raise MontageError(f"Видео {n}: хук ставится только на вертикальное видео — сними его на столе")
    if kind != "none" and variation["hook"].get("dropTime") is None:
        raise MontageError(f"Видео {n}: для хука нужен дроп — выбери его на шаге FX")

    resolved, family_script = resolve_hook(kind if kind != "none" else None, cfg, None, None)

    # ── переходы по склейкам (как transitionAt стола) ──
    transitions = entry.get("transitions") or {}
    if not isinstance(transitions, dict):
        raise MontageError(f"Видео {n}: переходы на склейках повреждены")
    default_label = cfg.get("effectGlue") or em.NO_GLUE_LABEL
    per_cut: list[dict[str, Any]] = []
    labels: list[str] = []
    for i, t in enumerate(cuts or []):
        label = str(transitions.get(str(i), transitions.get(i)) or default_label)
        labels.append(label)
        if label == em.NO_GLUE_LABEL:
            continue
        tid = em.map_glue(label)
        if not tid:
            raise MontageError(f"Видео {n}: неизвестный переход «{label}»")
        per_cut.append({"tAbs": t, "transition": tid})
    if cuts is None and (transitions or cfg.get("effectGlue") not in (None, em.NO_GLUE_LABEL)):
        raise MontageError("Склейки таймлайна ещё считаются — подожди пару секунд и нажми снова")

    hook = variation["hook"]
    hook["family"] = kind if kind != "none" else None
    hook["config"] = cfg
    hook["family_script"] = family_script
    hook["montage"] = True
    resolved["transition"] = None
    resolved["extra"] = None
    resolved["extraFull"] = False

    if slot["custom"]:
        # своё видео: склейки — стыки загруженных клипов; переход один на весь ролик
        distinct = {label for label in labels}
        if len(distinct) > 1:
            raise MontageError(f"Видео {n}: у своего видео переход один на весь ролик — выбери один на столе")
        only = next(iter(distinct), default_label)
        resolved["transition"] = em.map_glue(only) if only != em.NO_GLUE_LABEL else None
    elif slot["mode"] == "color" and not slot["strobe"]:
        if per_cut:
            raise MontageError(f"Видео {n}: у статичного цвета нет склеек — переходы на нём не встанут")
        hook["cutTransitions"] = []
    else:
        hook["cutTransitions"] = per_cut
        hook["cutsAbs"] = list(cuts or [])

    # ── стили по окнам кадров ──
    styles = entry.get("styles") or []
    if not isinstance(styles, list):
        raise MontageError(f"Видео {n}: стили повреждены")
    ranges: list[dict[str, Any]] = []
    if styles:
        if segment is None:
            raise MontageError("Правки стола есть, а отрывок не выбран — выбери отрывок трека")
        bounds = [segment["from"], *(cuts or []), segment["to"]]
        shots = len(bounds) - 1
        for item in styles:
            label = str((item or {}).get("style") or "")
            try:
                a, b = int(item.get("a")), int(item.get("b"))
            except (TypeError, ValueError) as exc:
                raise MontageError(f"Видео {n}: окно стиля «{label}» повреждено") from exc
            if not (0 <= a < b <= shots):
                raise MontageError(f"Видео {n}: стиль «{label}» выходит за кадры ролика — поправь его на столе")
            eid = em.map_style(label)
            if not eid:
                raise MontageError(f"Видео {n}: неизвестная стилизация «{label}»")
            ranges.append({"extra": eid, "startAbs": bounds[a], "endAbs": bounds[b]})
    hook["extraRanges"] = ranges
    hook["resolved"] = resolved
    # хук мог смениться на столе — брендинг и свой звук считаем от нового
    branding = em.HOOK_BRANDING.get(resolved["hook"], {"enabled": False}) if resolved.get("hook") else {"enabled": False}
    variation["branding"] = {"enabled": bool(branding.get("enabled")), "style": branding.get("style")}
    variation["sound"] = {"userSound": cfg.get("sound") if kind in {"sound", "warmup"} else None}

    # ── рамка ролика (PNG-маска поверх всех слоёв, как шаг «Рамка» в боте) ──
    try:
        variation["frame"] = validate_frame(entry.get("frame"))
    except ValueError as exc:
        raise MontageError(f"Видео {n}: {exc}") from exc
    # маска нарисована под 9:16 и ложится cover-масштабом — на 16:9 её обрезало бы
    if variation["frame"] and str((variation.get("background") or {}).get("sourceFormat") or "9:16") == "16:9":
        raise MontageError(f"Видео {n}: рамка ставится только на вертикальное видео — сними её на столе")

    # ── стиль субтитров ролика ──
    sub = entry.get("sub")
    if sub:
        variation["subtitle"]["style"] = str(sub)
        variation["subtitle"]["text"] = dict(text_by_style.get(str(sub)) or {})
