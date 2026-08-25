"""F6 «Видео» visual overlay — то же комбо, что у F1 «Звук».

Pre-drop окно занято видео юзера, поэтому pre-drop визуал-фазы нет: остаются
молния на дропе и seeded-random F3-переход на post-drop склейках. Переиспользуем
сборщик F1 (который, в свою очередь, зовёт f2_object с shape=None), чтобы не
плодить третью копию клея вокруг F3-скриптов и детекции склеек.
"""
from __future__ import annotations

from mlcore.hooks.f1_sound.overlay import build_overlay_jsx as _build_combo_jsx


def build_overlay_jsx(*, drop_time: float, seed: int) -> str:
    """Инъектируемый визуальный JSX-блок F6 (hook_light на дропе + post-drop
    рандомный F3-переход)."""
    return _build_combo_jsx(drop_time=drop_time, seed=seed)
