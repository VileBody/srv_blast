"""RU-лейблы визарда (стор v4) → id эффектов AE-манифеста.

Источник истины — ЕДИНЫЙ реестр `frontend/src/data/effects-registry.json`
(его же читает фронт для чипов). Добавил эффект в реестр → и чип в визарде,
и резолв здесь появляются автоматически (по одним правилам).

См. spec: backend/docs/RENDER_JOB_SPEC.md §4.
"""
from __future__ import annotations

import json
from pathlib import Path

_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "data" / "effects-registry.json"


def _load_registry() -> dict:
    try:
        registry = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"effects registry is unavailable: {_REGISTRY_PATH}") from exc
    if not isinstance(registry, dict) or not all(registry.get(group) for group in ("hook", "glue", "style")):
        raise RuntimeError(f"effects registry has no required hook/glue/style groups: {_REGISTRY_PATH}")
    return registry


_REG = _load_registry()


def _build_map(group: str, *, include_alt: bool = False) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in _REG.get(group, []):
        out[e["label"]] = e["manifestId"]
        if include_alt and e.get("altId"):
            out[e["altId"]] = e["manifestId"]
    return out


# label/altId → manifestId (из реестра)
HOOK_MAP: dict[str, str] = _build_map("hook")
GLUE_MAP: dict[str, str] = _build_map("glue", include_alt=True)   # + GLUE_TYPES dashed-id
STYLE_MAP: dict[str, str] = _build_map("style")

# branding по manifestId хука (из поля registry.hook[].branding)
HOOK_BRANDING: dict[str, dict] = {}
for _e in _REG.get("hook", []):
    _b = _e.get("branding")
    if _b in (True, "built_in", "stamp_flash"):
        HOOK_BRANDING[_e["manifestId"]] = {"enabled": True, "style": _b if isinstance(_b, str) else "stamp_flash"}
    else:
        HOOK_BRANDING[_e["manifestId"]] = {"enabled": False}

# object/motion — не идут в run_job, зовутся отдельными скриптами (spec §4.4)
OBJECT_SCRIPT: dict[str, str] = {
    "Круг": "Хуки/Лого и шейпы/Шейпы/rebuild_shape_elipse.jsx",
    "Квадрат": "Хуки/Лого и шейпы/Шейпы/rebuild_shape_square.jsx",
    "Ромб": "Хуки/Лого и шейпы/Шейпы/rebuild_shape_rhomb.jsx",
    "Звезда-5": "Хуки/Лого и шейпы/Шейпы/rebuild_shape_star1.jsx",
    "Звезда-10": "Хуки/Лого и шейпы/Шейпы/rebuild_shape_star2.jsx",
}
MOTION_SCRIPT: dict[str, str] = {
    "Свайп": "Хуки/Движение/ш3/rebuild_swipe.jsx",
    "Тап": "Хуки/Движение/ш2/rebuild_tap.jsx",
    "Зум": "Хуки/Движение/ш4/rebuild_pinch.jsx",
    "Задержи": "Хуки/Движение/ш1/rebuild_holdfinger.jsx",
    "Голова": "Хуки/Движение/ш5/rebuild_head.jsx",
}


def map_hook(label: str | None) -> str | None:
    return HOOK_MAP.get(label) if label else None


def map_glue(label: str | None) -> str | None:
    return GLUE_MAP.get(label) if label else None


def map_style(label: str | None) -> str | None:
    return STYLE_MAP.get(label) if label else None


def parse_mmssms(value: str | None) -> float | None:
    """'mm:ss:ms' → секунды (float). Пусто/битое → None (воркер возьмёт последнюю склейку)."""
    if not value:
        return None
    parts = value.split(":")
    try:
        nums = [int(p) for p in parts if p != ""]
    except ValueError:
        return None
    if not nums:
        return None
    if len(nums) == 3:
        mm, ss, ms = nums
    elif len(nums) == 2:
        mm, ss, ms = 0, nums[0], nums[1]
    else:
        mm, ss, ms = 0, nums[0], 0
    return round(mm * 60 + ss + ms / 100.0, 3)
