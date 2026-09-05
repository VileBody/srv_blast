"""build_render_job — стор визарда v4 (stageData) → render_job.json.

Реализация spec: backend/docs/RENDER_JOB_SPEC.md §5.
Чистые функции, без внешних зависимостей — вызывается из submit-обработчика.
"""
from __future__ import annotations

from typing import Any

from . import effect_map as em

SCHEMA = "blast.render_job/1"
OUTPUT_DEFAULT = {
    "resolution": [1080, 1920],
    "fps": 23.976,
    "codec": "h264",           # НЕ h265 — TikTok его переколбасит (см. ресёрч)
    "bitrateMbps": 12,
    "audio": "aac_320k",
}


def distribute(keys: list[str], total: int) -> dict[str, int]:
    """Раздать `total` по `keys` как можно ровнее (зеркало SlicePanel.distribute)."""
    if not keys or total <= 0:
        return {}
    base, rem = divmod(total, len(keys))
    return {k: base + (1 if i < rem else 0) for i, k in enumerate(keys)}


def _expand(counts: dict[str, int]) -> list[str]:
    """{'a':2,'b':1} → ['a','a','b'] (порядок ключей сохраняется)."""
    out: list[str] = []
    for key, n in counts.items():
        out.extend([key] * max(0, int(n)))
    return out


def _slice_keys(alloc_slice: dict[str, int], fallback: list[str]) -> list[str]:
    keys = [k for k in alloc_slice.keys()] if alloc_slice else []
    return keys or fallback


def _segment(timing: dict[str, Any] | None) -> dict[str, float] | None:
    if not timing or timing.get("mode") == "ai":
        return None
    frm, to = timing.get("from"), timing.get("to")
    if not frm and not to:
        return None
    return {"from": mmss_seconds(frm), "to": mmss_seconds(to)}


def mmss_seconds(v: str | None) -> float | None:
    """'mm:ss' окно трека → сек.

    Публичная: тот же разбор нужен ручке кандидатов дропа (`main.api_drops`) —
    окно отрывка приходит с фронта в этом же формате, и второй парсер рядом
    гарантированно разъехался бы с этим."""
    if not v:
        return None
    parts = [p for p in str(v).split(":") if p != ""]
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) >= 2:
        return round(nums[0] * 60 + nums[1] + (nums[2] / 100.0 if len(nums) > 2 else 0), 3)
    return float(nums[0]) if nums else None


def _resolve_hook(kind: str | None, cfg: dict[str, Any], bg_glue_id: str | None,
                  bg_style_id: str | None) -> tuple[dict[str, str | None], str | None]:
    """Вернуть (resolved {hook,transition,extra}, family_script). См. spec §5.4.

    Склейка и стилизация настраиваются у КАЖДОГО типа хука (решение продукта: разнообразие
    вариаций), поэтому cfg.effectGlue/effectStyle читаем при любом kind, а не только у
    «Эффектов». Фолбэк — склейка/стиль, заданные на этапе фона.
    """
    resolved = {
        "hook": None,
        "transition": em.map_glue(cfg.get("effectGlue")) or bg_glue_id,
        "extra": em.map_style(cfg.get("effectStyle")) or bg_style_id,
    }
    family_script = None
    if kind == "effects":
        resolved["hook"] = em.map_hook(cfg.get("effectHook"))
    elif kind == "object":
        family_script = em.OBJECT_SCRIPT.get(cfg.get("object"))
    elif kind == "motion":
        family_script = em.MOTION_SCRIPT.get(cfg.get("motion"))
    # sound / thought — отдельные шаги воркера, собственного run_job-хука не дают
    return resolved, family_script


def _split_bg_key(key: str, default_mode: str) -> tuple[str, str]:
    """«footage:Неон» → ("footage", "Неон").

    Ключи allocation.background несут режим префиксом (SlicePanel.backgroundUnits), поэтому
    в батче футаж и фото могут идти вперемешку. Без разбора префикс утекал в render_job
    (`groups: ["footage:Неон"]`) и в подписи чипов W36.
    """
    for m in ("footage", "photo"):
        if key.startswith(f"{m}:"):
            return m, key[len(m) + 1:]
    return ("color" if key == "__color__" else default_mode), key


def build_render_job(batch_id: str, project_id: str | None, user_id: str,
                     stage_data: dict[str, Any], videos_to_generate: int) -> dict[str, Any]:
    bg = stage_data.get("background") or {}
    hooks = stage_data.get("hooks") or {}
    subs = stage_data.get("subtitles") or {}
    alloc = stage_data.get("allocation") or {}
    final = stage_data.get("final") or {}
    track = stage_data.get("track") or {}

    total = max(1, int(alloc.get("total") or videos_to_generate or 1))

    # --- срезы для index-zip ---
    # Ключи allocation.background приходят с фронта с префиксом режима: "footage:Неон" / "photo:Крупный план"
    # (SlicePanel.backgroundUnits). Батч может СМЕШИВАТЬ футаж и фото, поэтому mode берём из ключа
    # per-variation, а не один на батч; имя группы — то, что после префикса.
    mode = bg.get("mode", "footage")
    bg_groups_all = [f"footage:{v}" for v in (bg.get("footage") or [])] + [f"photo:{v}" for v in (bg.get("photo") or [])]
    bg_fallback = (["__uploads__"] if bg.get("uploads") else bg_groups_all) or (["__color__"] if bg.get("color") else ["__default__"])
    sub_fallback = subs.get("pool") or ["Impulse"]
    hook_fallback = [hooks["kind"]] if hooks.get("kind") else []

    bg_keys = _slice_keys(alloc.get("background") or {}, bg_fallback)
    sub_keys = _slice_keys(alloc.get("subtitles") or {}, sub_fallback)
    hook_keys = _slice_keys(alloc.get("hooks") or {}, hook_fallback)

    bg_seq = _expand(alloc.get("background") or distribute(bg_keys, total))
    sub_seq = _expand(alloc.get("subtitles") or distribute(sub_keys, total))
    hook_seq = _expand(alloc.get("hooks") or distribute(hook_keys, total)) if hook_keys else []

    # общие резолвы фона (одни на батч)
    bg_glue_id = em.map_glue(bg.get("glue"))
    bg_style_id = em.map_style(bg.get("photoStyle")) if bg.get("photoEffects") else None
    drop = em.parse_mmssms(hooks.get("dropTime"))
    kind = hooks.get("kind")
    configs = hooks.get("configs") or {}

    variations: list[dict[str, Any]] = []
    for i in range(total):
        group_key = bg_seq[i % len(bg_seq)] if bg_seq else "__default__"
        v_mode, group = _split_bg_key(group_key, mode)
        style = sub_seq[i % len(sub_seq)] if sub_seq else "Impulse"
        v_kind = hook_seq[i % len(hook_seq)] if hook_seq else None
        cfg = configs.get(v_kind) or {} if v_kind else {}
        resolved, family_script = _resolve_hook(v_kind, cfg, bg_glue_id, bg_style_id)

        branding = em.HOOK_BRANDING.get(resolved["hook"], {"enabled": False}) if resolved["hook"] else {"enabled": False}
        variations.append({
            "index": i + 1,
            "subtitle": {
                "style": style,
                "color": final.get("subtitleColor") or subs.get("color") or "#f6f5fd",
                "timingSource": "llm",   # финал; онлайн-превью использует "default"
            },
            "background": {
                "mode": v_mode,
                "groups": [] if group.startswith("__") else [group],
                # тип футажей (Figma W12) — из какой библиотеки берём группы; id из footage-types.json
                "footageType": bg.get("footageType") if v_mode == "footage" else None,
                # свои исходники (Figma W39/W49) — вместо библиотечного футажа
                "uploads": list(bg.get("uploads") or []),
                "sourceAssets": list(bg.get("sourceAssets") or []),
                "sourceFormat": bg.get("sourceFormat"),
                "color": bg.get("color") if v_mode == "color" else None,
                "strobe": bool(bg.get("strobe")),
                "photoStyle": bg.get("photoStyle") if v_mode == "photo" else None,
                "glueId": bg_glue_id,
            },
            "hook": {
                "family": v_kind,
                "dropTime": drop,
                "resolved": resolved,
                "family_script": family_script,
                "config": cfg,
            },
            "branding": {"enabled": bool(branding.get("enabled")),
                          "style": branding.get("style")},
            "sound": {"userSound": (cfg.get("sound") if v_kind in {"sound", "warmup"} else None)},
        })

    return {
        "schema": SCHEMA,
        "batchId": batch_id,
        "projectId": project_id,
        "userId": user_id,
        "idempotencyKey": final.get("idempotencyKey"),
        "track": {
            "s3Key": track.get("s3Key"),
            "durationS": track.get("durationS"),
            "segment": _segment(stage_data.get("timing")),
        },
        "lyrics": {"full": stage_data.get("lyrics") or "", "fragment": stage_data.get("fragment")},
        "output": {**OUTPUT_DEFAULT, "s3Prefix": f"videos/{user_id}/{batch_id}"},
        "variations": variations,
    }


def variation_label(variation: dict[str, Any]) -> dict[str, str]:
    """Короткие подписи для чипов W36 (source / subtitleStyle / hook)."""
    groups = variation["background"]["groups"]
    mode = variation["background"]["mode"]
    # groups уже без префикса режима (_split_bg_key), но старые джобы могли сохранить «photo:Имя»
    source = groups[0].split(":", 1)[-1] if groups else ("Цвет" if mode == "color" else "Футаж")
    hook = variation["hook"]["family"]
    hook_label = {"effects": "Эффекты", "object": "Объект", "motion": "Движение",
                  "sound": "Прогрев", "warmup": "Прогрев", "thought": "Мысль"}.get(hook, "Без хука")
    return {"source": source, "subtitleStyle": variation["subtitle"]["style"], "hook": hook_label}
