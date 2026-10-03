"""Раскадровка визарда: склейки по темпу трека и реальные исходники вайба.

Сайт — тонкий клиент ручек оркестратора `/storyboard/*` (там тот же
детерминированный подбор, что делает рендер: `mlcore.storyboard_plan`). Здесь:

* форма запросов/ответов для фронта (camelCase);
* mock-режим: склейки по сетке битов и «клипы» из превью-роликов вайбов
  каталога — чтобы экран жил локально без оркестратора;
* привязка сохранённой раскладки к вариациям рендера (`attach_to_variations`).

Раскладка хранится в черновике визарда (`stageData.storyboard`): для каждого
видео батча — группа футажа и закреплённый план. В рендер план уходит как
`footage_plan`, и джоба ставит ровно эти клипы на ровно эти склейки.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable

PACES = ("sparse", "auto", "dense")
_MOCK_BPM = 128.0
_MOCK_BEATS_PER_CUT = {"sparse": 8, "auto": 4, "dense": 2}
_MOCK_POOL = 40


class StoryboardError(ValueError):
    """Раскадровку нельзя применить как есть — сообщение показывается юзеру."""


# ── mock ──────────────────────────────────────────────────────────────────────

def _grid(start: float, end: float, bpm: float, anchor: float | None) -> list[float]:
    beat = 60.0 / bpm
    origin = anchor if anchor is not None else start
    k0 = int((start - origin) // beat) - 1
    out = []
    k = k0
    while True:
        t = origin + k * beat
        if t > end + 1e-6:
            break
        if t >= start - 1e-6:
            out.append(round(t, 3))
        k += 1
    return out


def mock_cuts(*, start: float, end: float, drop: float | None) -> dict[str, Any]:
    beats = _grid(start, end, _MOCK_BPM, drop)
    cuts: dict[str, list[float]] = {}
    for pace in PACES:
        step = _MOCK_BEATS_PER_CUT[pace]
        pts = [b for i, b in enumerate(beats) if i % step == 0 and start + 0.3 < b < end - 0.3]
        if drop is not None and start + 0.3 < drop < end - 0.3 and round(drop, 3) not in pts:
            pts = sorted(pts + [round(drop, 3)])
        cuts[pace] = pts
    return {"bpm": _MOCK_BPM, "dropT": drop, "beats": beats, "cuts": cuts}


def _mock_clip_names(vibe_id: str) -> list[str]:
    return [f"{vibe_id}-{k:03d}.mp4" for k in range(_MOCK_POOL)]


def _rank(names: Iterable[str], seed: str, salt: str) -> list[str]:
    return sorted(names, key=lambda n: hashlib.sha256(f"{seed}:{salt}:{n}".encode()).hexdigest())


def _mock_view(vibe: dict[str, Any], name: str, *, start: float | None = None, end: float | None = None) -> dict[str, Any]:
    k = int(name.rsplit("-", 1)[-1].split(".")[0])
    view = {
        "fileName": name,
        "previewUrl": vibe.get("previewUrl"),
        # у превью-ролика вайба ~10–20 с разных сцен: разные смещения = разные «клипы»
        "previewOffset": round((k * 1.7) % 9.0, 2),
        "tags": [],
    }
    if start is not None and end is not None:
        view.update({"inPoint": start, "outPoint": end})
    return view


def _plan(start: float, end: float, cuts: list[float], names: list[str]) -> dict[str, Any]:
    bounds = [start, *cuts, end]
    return {
        "version": 1,
        "clip_start_abs": start,
        "clip_end_abs": end,
        "switch_points_abs": list(cuts),
        "clips": [
            {"file_name": n, "fit_mode": "cover", "in_point": bounds[i], "out_point": bounds[i + 1],
             "start_time": bounds[i], "source_offset_sec": 0.0}
            for i, n in enumerate(names)
        ],
    }


def mock_pick(*, vibes: list[dict[str, Any]], start: float, end: float, cuts: list[float],
              videos: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {str(v.get("name")): v for v in vibes}
    bounds = [start, *cuts, end]
    shots = len(bounds) - 1
    used: dict[str, set[str]] = {}
    out = []
    for video in videos:
        vibe = by_name.get(str(video.get("group") or ""))
        if vibe is None:
            raise StoryboardError(f"Вайб «{video.get('group')}» не найден в каталоге")
        taken = used.setdefault(vibe["name"], set())
        pins = {int(k): str(v) for k, v in (video.get("pins") or {}).items()}
        order = [n for n in _rank(_mock_clip_names(str(vibe["id"])), str(video.get("seedKey")), "pick") if n not in taken and n not in pins.values()]
        names, repeats = [], []
        for i in range(shots):
            if i in pins:
                names.append(pins[i])
            elif order:
                names.append(order.pop(0))
            else:
                names.append(_rank(_mock_clip_names(str(vibe["id"])), str(video.get("seedKey")), f"r{i}")[0])
                repeats.append(i)
        taken.update(names)
        out.append({
            "index": video.get("index"),
            "group": vibe["name"],
            "clips": [_mock_view(vibe, n, start=bounds[i], end=bounds[i + 1]) for i, n in enumerate(names)],
            "repeats": repeats,
            "plan": _plan(start, end, cuts, names),
        })
    return {"videos": out}


def mock_alternatives(*, vibes: list[dict[str, Any]], group: str, seed_key: str, shot: int,
                      exclude: Iterable[str], limit: int) -> dict[str, Any]:
    vibe = next((v for v in vibes if str(v.get("name")) == group), None)
    if vibe is None:
        raise StoryboardError(f"Вайб «{group}» не найден в каталоге")
    excluded = set(exclude)
    names = [n for n in _rank(_mock_clip_names(str(vibe["id"])), seed_key, f"alt{shot}") if n not in excluded]
    return {"candidates": [_mock_view(vibe, n) for n in names[:limit]]}


# ── production: orchestrator shapes → camelCase ───────────────────────────────

def clip_view(clip: dict[str, Any]) -> dict[str, Any]:
    return {
        "fileName": clip.get("file_name"),
        "inPoint": clip.get("in_point"),
        "outPoint": clip.get("out_point"),
        "previewUrl": clip.get("preview_url"),
        "previewOffset": clip.get("preview_offset_sec") or 0.0,
        "tags": list(clip.get("tags") or []),
    }


# ── render ────────────────────────────────────────────────────────────────────

def recipe_cuts(timeline: dict[str, Any] | None, segment: dict[str, float] | None) -> dict[str, Any] | None:
    """Склейки рецепта таймлайна для видео, клипы которых рендер подбирает сам
    (фото, коллекции, строб, вайб без раскадровки).

    «Авто» без ручных правок — это и есть разбиение рендера, его не шлём. Выбранный
    темп или сдвинутые руками склейки обязаны дойти до рендера: если их нет или
    они от другого отрывка — это явная ошибка, а не тихий откат на «авто».
    """
    if not timeline:
        return None
    pace = str(timeline.get("pace") or "auto")
    if pace == "auto" and not timeline.get("edited"):
        return None
    cuts = timeline.get("cuts")
    if not isinstance(cuts, list):
        raise StoryboardError("Склейки таймлайна ещё считаются — подожди пару секунд и нажми снова")
    if segment is None:
        raise StoryboardError("Склейки таймлайна есть, а отрывок не выбран — выбери отрывок трека")
    points = [float(c) for c in cuts]
    if any(not (segment["from"] < p < segment["to"]) for p in points) or any(b <= a for a, b in zip(points, points[1:])):
        raise StoryboardError("Склейки собраны для другого отрывка — открой таймлайн, они пересчитаются")
    return {"pace": pace, "clipStartAbs": segment["from"], "clipEndAbs": segment["to"], "switchPointsAbs": points}


def attach_to_variations(variations: list[dict[str, Any]], storyboard: dict[str, Any] | None,
                         segment: dict[str, float] | None, timeline: dict[str, Any] | None = None) -> None:
    """Приклеить закреплённые планы к вариациям рендера.

    Раскадровка собирается на «Пуле» по той же раскладке вариаций и по склейкам
    рецепта таймлайна. Если с тех пор поменялись окно, склейки или фон конкретного
    видео, план описывал бы другое видео — это явная ошибка, а не тихий перебор клипов.
    """
    if not storyboard:
        return
    recipe_cuts = (timeline or {}).get("cuts")
    entries = storyboard.get("videos") or []
    if not isinstance(entries, list):
        raise StoryboardError("Раскадровка повреждена — соберите исходники заново")
    by_index = {int(v.get("index") or 0): v for v in variations}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("plan"):
            continue
        index = int(entry.get("index") or 0)
        variation = by_index.get(index)
        if variation is None:
            raise StoryboardError(f"Раскадровка устарела: видео {index} больше нет в батче")
        bg = variation.get("background") or {}
        groups = list(bg.get("groups") or [])
        if bg.get("mode") != "footage" or bg.get("sourceAssets") or not groups or groups[0] != entry.get("group"):
            raise StoryboardError(
                f"Раскадровка устарела: у видео {index} поменялся фон — соберите исходники заново на шаге «Пул»"
            )
        plane = bg.get("footagePlane")
        if plane is not None and plane != "vibes":
            # раскадровку «Пул» собирает только вайбам; у фильма/коллекции другой пул
            # клипов — план оттуда не про этот футаж (черновики без плана — как раньше)
            raise StoryboardError(
                f"Раскадровка есть только у вайбов, а видео {index} — из подборки {plane!r}: соберите исходники заново на шаге «Пул»"
            )
        plan = entry["plan"]
        if segment is None or abs(float(plan.get("clip_start_abs", -1)) - segment["from"]) > 1e-3 \
                or abs(float(plan.get("clip_end_abs", -1)) - segment["to"]) > 1e-3:
            raise StoryboardError("Раскадровка собрана для другого отрывка — соберите исходники заново")
        plan_cuts = [float(p) for p in plan.get("switch_points_abs") or []]
        if not isinstance(recipe_cuts, list) or len(recipe_cuts) != len(plan_cuts)                 or any(abs(float(a) - b) > 1e-3 for a, b in zip(recipe_cuts, plan_cuts)):
            raise StoryboardError("Склейки на таймлайне поменялись — дождитесь новой раскадровки на шаге «Пул»")
        bg["footagePlan"] = plan
