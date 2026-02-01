from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Tuple

from blocks import (
    IntroDistributor,
    WaltzDistributor,
    PhotoDistributor,
    BabyStrideDistributor,
    GlitchCrescendoDistributor,
    DualTruthDistributor,
    FinaleDistributor,
)

BLOCK_MAP: Dict[str, Any] = {
    "INTRO_ZOOM": IntroDistributor,
    "WALTZ_BRIDGE": WaltzDistributor,
    "SOLO_PHOTO": PhotoDistributor,
    "BABY_BUILD": BabyStrideDistributor,
    "GLITCH_CRESCENDO": GlitchCrescendoDistributor,
    "DUAL_TRUTH": DualTruthDistributor,
    "FINALE": FinaleDistributor,
}


class ProjectOrchestrator:
    """
    Post-patch (ONLY scaling):
      - Keep any \\r decisions upstream (LLM + gemini_client sanitation).
      - Here we only scale long phrases proportionally to fit.

    Additional:
      - Supports special nodes: {"type":"precomp","comp":{...},"layers":[...]}
        These must pass through to JSX. Inner layers are serialized via asdict().
    """

    # tweak knobs
    MAX_WEIGHTED_LINE_CHARS: float = 22.0
    LINE2_WEIGHT: float = 2.0     # line2 is bigger font after \r
    SOFTNESS: float = 0.92
    MIN_SCALE_MULT: float = 0.60
    SKIP_TEXTS_LOWER = {"mine"}

    # do NOT autoscale inside precomp nodes (mine must be 1:1)
    SKIP_AUTOSCALE_NODE_TYPES = {"precomp"}

    def __init__(self, full_data: Dict[str, Any]):
        self.data = full_data
        self.final_stack: List[Dict[str, Any]] = []

    # ============================================================
    # Build / Serialize
    # ============================================================

    def _serialize_layer_like(self, obj: Any) -> Dict[str, Any]:
        """
        Convert:
          - LayerBlueprint dataclass -> dict via asdict
          - raw dict -> dict (copied shallow)
        """
        if isinstance(obj, dict):
            return dict(obj)
        if is_dataclass(obj):
            return asdict(obj)
        # fallback: try to treat as mapping-ish
        try:
            return dict(obj)  # type: ignore[arg-type]
        except Exception as e:
            raise TypeError(f"Unsupported layer object type: {type(obj)}") from e

    def _serialize_precomp_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ensure node has:
          - type="precomp"
          - comp: dict
          - layers: list of serialized layer dicts
        """
        out = dict(node)
        out["type"] = "precomp"

        inner = node.get("layers", [])
        if not isinstance(inner, list):
            inner = []

        inner_serialized: List[Dict[str, Any]] = []
        for it in inner:
            inner_serialized.append(self._serialize_layer_like(it))

        out["layers"] = inner_serialized
        return out

    def build(self) -> None:
        self.final_stack = []

        for block_data in self.data["macro_blocks"]:
            b_id = block_data.get("id", "unknown")
            b_type = block_data["type"]

            if b_type not in BLOCK_MAP:
                raise ValueError(f"Unknown block type '{b_type}' in block '{b_id}'")

            dist = BLOCK_MAP[b_type](block_data)

            for layer in dist.layers:
                # Special node support
                if isinstance(layer, dict) and layer.get("type") == "precomp":
                    self.final_stack.append(self._serialize_precomp_node(layer))
                    continue

                # Normal layer
                self.final_stack.append(self._serialize_layer_like(layer))

        # NEW: autoscale patch (no re-break here)
        self._patch_autoscale_text_layers()

        # Ensure precomps go first (so JSX can create them before any routed layers / video refs)
        self.final_stack.sort(key=self._sort_key)

    def _sort_key(self, x: Dict[str, Any]) -> Tuple[int, int]:
        t = str(x.get("type", ""))
        if t == "precomp":
            return (0, 0)
        z = x.get("z_index")
        try:
            zi = int(z) if z is not None else 0
        except Exception:
            zi = 0
        return (1, zi)

    def save_json(self, path: str) -> None:
        output = {"comp_meta": self.data["composition"], "layers": self.final_stack}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

    # ============================================================
    # Autoscale helpers
    # ============================================================

    @staticmethod
    def _get_text(layer: Dict[str, Any]) -> str:
        t = layer.get("text")
        return t if isinstance(t, str) else ""

    @staticmethod
    def _clean_for_len(s: str) -> str:
        bad = set(" \t\n\r")
        return "".join(ch for ch in s if ch not in bad)

    @classmethod
    def _weighted_max_len(cls, phrase: str, w2: float) -> float:
        lines = phrase.split("\r") if phrase else [""]
        if len(lines) == 1:
            return float(len(cls._clean_for_len(lines[0])))
        l1 = float(len(cls._clean_for_len(lines[0])))
        l2 = float(len(cls._clean_for_len(lines[1])))
        return max(l1, l2 * float(w2))

    def _scale_mult_for_phrase(self, phrase: str) -> float:
        maxw = self._weighted_max_len(phrase, self.LINE2_WEIGHT)
        thr = float(self.MAX_WEIGHTED_LINE_CHARS)
        if maxw <= thr:
            return 1.0
        raw = (thr / maxw) ** float(self.SOFTNESS)
        return max(float(self.MIN_SCALE_MULT), min(1.0, raw))

    @staticmethod
    def _mul_scale_value(v: Any, mult: float) -> Any:
        if isinstance(v, list) and len(v) >= 2:
            out = list(v)
            out[0] = float(out[0]) * mult
            out[1] = float(out[1]) * mult
            if len(out) >= 3:
                out[2] = float(out[2])  # keep Z
            return out
        if isinstance(v, (int, float)):
            return float(v) * mult
        return v

    def _extract_scale_prop(self, props: Dict[str, Any]) -> Tuple[str, Dict[str, Any]] | None:
        if "tf_scale" in props and isinstance(props["tf_scale"], dict) and props["tf_scale"].get("match_name") == "ADBE Scale":
            return "tf_scale", props["tf_scale"]
        for k, pd in props.items():
            if isinstance(pd, dict) and pd.get("match_name") == "ADBE Scale":
                return str(k), pd
        return None

    def _patch_autoscale_text_layers(self) -> None:
        for layer in self.final_stack:
            if not isinstance(layer, dict):
                continue

            # skip special nodes
            if layer.get("type") in self.SKIP_AUTOSCALE_NODE_TYPES:
                continue

            if layer.get("type") != "text":
                continue

            phrase = self._get_text(layer)
            if not phrase:
                continue
            if phrase.strip().lower() in self.SKIP_TEXTS_LOWER:
                continue

            mult = self._scale_mult_for_phrase(phrase)
            if mult >= 0.999999:
                continue

            props = layer.get("props")
            if not isinstance(props, dict):
                layer["props"] = {}
                props = layer["props"]

            found = self._extract_scale_prop(props)
            if found is None:
                props["tf_scale"] = {
                    "match_name": "ADBE Scale",
                    "value": [100.0 * mult, 100.0 * mult, 100.0],
                    "keyframes": [],
                    "expression": None,
                }
                continue

            key, pd = found

            if pd.get("value") is not None:
                pd["value"] = self._mul_scale_value(pd["value"], mult)

            kfs = pd.get("keyframes")
            if isinstance(kfs, list):
                for kf in kfs:
                    if isinstance(kf, dict) and "v" in kf:
                        kf["v"] = self._mul_scale_value(kf["v"], mult)

            props[key] = pd
            layer["props"] = props
