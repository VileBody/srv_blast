"""F1 «Звук» visual overlay = F2 combo without the pre-drop shapes.

F1's pre-drop region is the user's uploaded sound (audio, see inject.py), so
there is no pre-drop visual transition. The drop hook_light and the post-drop
seeded-random F3 transition are identical to F2 — we reuse f2_object's builder
with shape=None to avoid duplicating the F3-script loading / cut-detection /
PRNG glue.
"""
from __future__ import annotations

from mlcore.hooks.f2_object.overlay import (
    F2_POST_DROP_TRANSITION_POOL as F1_POST_DROP_TRANSITION_POOL,
)
from mlcore.hooks.f2_object.overlay import build_overlay_jsx as _build_combo_jsx


def build_overlay_jsx(*, drop_time: float, seed: int) -> str:
    """Return the injectable F1 visual JSX block (hook_light @ drop + post-drop
    random F3 transition). Pre-drop visual phase is skipped (shape=None)."""
    return _build_combo_jsx(shape=None, drop_time=drop_time, seed=seed)
