"""One output geometry per batch, validated before billing or dispatch."""
from __future__ import annotations
from typing import Any

FORMATS = {"vertical": "9:16", "wide": "16:9", "square": "1:1"}


def selected_geometry(stage: dict[str, Any], catalogs: dict[str, dict[str, dict[str, Any]]]) -> str:
    bg = stage.get("background") or {}
    formats: set[str] = set()
    selected: set[str] = set()
    for mode in ("footage", "photo"):
        for name in bg.get(mode) or []:
            if name not in catalogs.get(mode, {}):
                raise ValueError(f"Неизвестный исходник: {name}. Обновите список исходников.")
            item = catalogs[mode][name]
            preset = (item.get("selector") or {}).get("renderPreset", "vertical")
            formats.add("4:3" if mode == "photo" else FORMATS[preset])
            selected.add(f"{mode}:{name}")
    if bg.get("color"):
        formats.add("9:16")
        selected.add("__color__")
    if bg.get("uploads"):
        if not bg.get("sourceFormat"):
            raise ValueError("У загруженных исходников не указан формат")
        formats.add(bg["sourceFormat"])
        selected.add("__uploads__")
        if bg.get("footage") or bg.get("photo") or bg.get("color"):
            raise ValueError("Выберите свои исходники или библиотеку для одного батча")
    if len(formats) != 1:
        raise ValueError("Один батч — один формат. Разделите 9:16, 16:9 и фото 4:3 на разные батчи." if formats else "Не выбран фон")
    for key, count in (stage.get("allocation", {}).get("background") or {}).items():
        if count and key not in selected:
            raise ValueError("Распределение содержит удалённый исходник. Пересоберите пул.")
    return next(iter(formats))
