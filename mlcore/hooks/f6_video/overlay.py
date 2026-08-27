"""F6 «Видео» visual overlay — то же комбо, что у F1 «Звук».

Pre-drop окно занято видео юзера, поэтому pre-drop визуал-фазы нет. Молния на
дропе для F6 также не нужна: остаются только seeded-random F3-переходы на
post-drop склейках.
"""
from __future__ import annotations

from mlcore.hooks.f2_object.overlay import build_overlay_jsx as _build_combo_jsx


def build_overlay_jsx(*, drop_time: float, seed: int) -> str:
    """Инъектируемый F6 JSX: только post-drop рандомные F3-переходы."""
    return _build_combo_jsx(
        shape=None,
        drop_time=drop_time,
        seed=seed,
        include_drop_hook=False,
    )
