"""Validate background selections while preserving geometry per variation."""
from __future__ import annotations
from typing import Any

FORMATS = {"vertical": "9:16", "wide": "16:9", "square": "1:1"}


def selected_geometry(stage: dict[str, Any], catalogs: dict[str, dict[str, dict[str, Any]]]) -> set[str]:
    bg = stage.get("background") or {}
    formats: set[str] = set()
    selected: set[str] = set()
    for mode in ("footage", "photo"):
        for name in bg.get(mode) or []:
            if name not in catalogs.get(mode, {}):
                raise ValueError(f"Неизвестный исходник: {name}. Обновите список исходников.")
            item = catalogs[mode][name]
            preset = (item.get("selector") or {}).get("renderPreset", "vertical")
            if mode == "photo":
                formats.add("4:3")
            else:
                if preset not in FORMATS:
                    raise ValueError(f"Неизвестная геометрия исходника: {preset!r}. Обновите каталог.")
                formats.add(FORMATS[preset])
            selected.add(f"{mode}:{name}")
    if bg.get("color"):
        formats.add("9:16")
        selected.add("__color__")
    for plan in bg.get("sourceVideos") or []:
        if plan.get("format") not in {"9:16", "16:9"} or not plan.get("sourceIds"):
            raise ValueError("Личное видео должно содержать исходники одного поддерживаемого формата")
        formats.add(plan["format"])
        selected.add(f"upload:{plan.get('id')}")
    # Старый клиент/сохранённый черновик мог прислать только uploads + sourceFormat.
    # Сервер submit мигрирует такую форму в sourceVideos, но validate_stage также
    # вызывается напрямую из production adapter и должен принимать её сам.
    if not (bg.get("sourceVideos") or []) and bg.get("uploads"):
        source_format = bg.get("sourceFormat")
        if source_format not in {"9:16", "16:9"}:
            raise ValueError("У загруженных исходников не указан поддерживаемый формат")
        formats.add(source_format)
        selected.update({"__uploads__", "upload:source-video-legacy"})
    if not formats:
        raise ValueError("Не выбран фон")
    for key, count in (stage.get("allocation", {}).get("background") or {}).items():
        if count and key not in selected:
            raise ValueError("Распределение содержит удалённый исходник. Пересоберите пул.")
    return formats
