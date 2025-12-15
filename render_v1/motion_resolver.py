from __future__ import annotations

"""Compatibility wrapper for motion preset resolution.

This module exposes helpers for working with matchName-based property trees
while delegating the core logic to :mod:`render_v1.ae_motion`.
"""

from typing import Any, Dict

from render_v1.ae_motion import (
    expand_procedural,
    normalize_property_tree,
    parse_segment,
    resolve_preset_tree,
    tree_set_value,
)

__all__ = [
    "expand_procedural",
    "normalize_property_tree",
    "parse_segment",
    "resolve_preset_tree",
    "tree_set_value",
]


def _is_value_data_dict(v: Any) -> bool:
    """Preserved for backwards compatibility with legacy imports."""
    return isinstance(v, dict) and (
        "keys" in v or "expression" in v or "value" in v or "procedural" in v
    )


# Backwards-compatible alias
_tree_set_value = tree_set_value

