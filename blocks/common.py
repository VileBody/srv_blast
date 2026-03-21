# blocks/common.py
from __future__ import annotations

from typing import Any, Dict

from app.config import TF_LAYERS
from core.types import LayerBlueprint, PropertyData


def _has_keyframed_opacity(bp: LayerBlueprint) -> bool:
    for _, pd in (bp.props or {}).items():
        try:
            if pd and pd.match_name == "ADBE Opacity" and pd.keyframes and len(pd.keyframes) > 0:
                return True
        except Exception:
            pass
    return False


def apply_tf_from_config(bp: LayerBlueprint, tf_key: str) -> None:
    """
    Unified TF applier for all blocks.
    TF_LAYERS[*] may contain None to mean "do not set".
    If opacity is keyframed in bp.props, this will not set static opacity.
    """
    if tf_key not in TF_LAYERS:
        raise KeyError(f"TF_LAYERS missing key: {tf_key}")

    tf: Dict[str, Any] = TF_LAYERS[tf_key]
    ap = tf.get("anchorPoint")
    pos = tf.get("position")
    sc = tf.get("scale")
    rot = tf.get("rotationZ")
    op = tf.get("opacity")

    if ap is not None:
        bp.props["tf_anchor"] = PropertyData("ADBE Anchor Point", value=ap)
    if pos is not None:
        bp.props["tf_position"] = PropertyData("ADBE Position", value=pos)
    if sc is not None:
        bp.props["tf_scale"] = PropertyData("ADBE Scale", value=sc)
    if rot is not None:
        bp.props["tf_rotation"] = PropertyData("ADBE Rotate Z", value=rot)

    if (op is not None) and (not _has_keyframed_opacity(bp)):
        bp.props["tf_opacity"] = PropertyData("ADBE Opacity", value=op)
