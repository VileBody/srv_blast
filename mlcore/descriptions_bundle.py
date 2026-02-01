# mlcore/descriptions_bundle.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _extract_meta(asset: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inventory asset example:
      {
        "file_name": "...mp4",
        "file_path": "...",
        "src_w": 720,
        "src_h": 1280,
        "meta": {...}
      }

    We keep ONLY what helps LLM make semantic picks.
    """
    meta = asset.get("meta") if isinstance(asset.get("meta"), dict) else {}
    out: Dict[str, Any] = {
        "file_name": str(asset.get("file_name") or "").strip(),
        "src_w": int(asset.get("src_w") or 0),
        "src_h": int(asset.get("src_h") or 0),
        "summary": meta.get("summary"),
        "tags": meta.get("tags"),
        "objects": meta.get("objects"),
        "camera": meta.get("camera"),
        "visuals": meta.get("visuals"),
        "composition": meta.get("composition"),
    }

    # drop null/empty to keep file smaller
    cleaned: Dict[str, Any] = {}
    for k, v in out.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        cleaned[k] = v
    return cleaned


def build_descriptions_bundle_from_inventory(
    *,
    inventory_json: Path,
    out_path: Path,
    max_assets: Optional[int] = None,
) -> Path:
    """
    Build ONE JSON file containing description-like metadata for ALL assets.

    Output format: JSON array of objects:
      [
        {"file_name": "...", "src_w":..., "src_h":..., "summary":..., "tags":[...], ...},
        ...
      ]

    This file is meant to be attached to Gemini as a single context document.
    """
    inventory_json = inventory_json.resolve()
    out_path = out_path.resolve()

    inv = json.loads(inventory_json.read_text(encoding="utf-8"))
    assets = inv.get("assets")

    if not isinstance(assets, list):
        raise ValueError(f"Inventory JSON must contain 'assets': {inventory_json}")

    rows: List[Dict[str, Any]] = []
    for it in assets:
        if not isinstance(it, dict):
            continue
        fn = str(it.get("file_name") or "").strip()
        if not fn:
            continue

        row = _extract_meta(it)
        if not row.get("file_name"):
            continue
        if int(row.get("src_w") or 0) <= 0 or int(row.get("src_h") or 0) <= 0:
            continue

        rows.append(row)

        if max_assets is not None and len(rows) >= int(max_assets):
            break

    if not rows:
        raise RuntimeError(f"No valid assets found in inventory: {inventory_json}")

    # deterministic order
    rows.sort(key=lambda x: str(x.get("file_name", "")))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # compact JSON, but still UTF-8
    out_path.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return out_path
