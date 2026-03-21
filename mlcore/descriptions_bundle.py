# mlcore/descriptions_bundle.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except Exception:
        return None
    if x <= 0:
        return None
    return x


def _extract_technical_row(asset: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inventory asset contract (technical-only):
      {
        "file_name": "...mp4",
        "file_path": "...",            # ignored in bundle
        "src_w": 720,
        "src_h": 1280,
        "duration_sec": 12.34,
        "genre": "Rock",
        "tag": "dark_forest",
        "dominant_color": "H09_L0",    # optional
        "palette_bins": [...]          # optional
      }
    """
    file_name = str(asset.get("file_name") or "").strip()
    src_w = int(asset.get("src_w") or 0)
    src_h = int(asset.get("src_h") or 0)
    genre = str(asset.get("genre") or "").strip()
    tag = str(asset.get("tag") or "").strip()
    duration_sec = _as_float(asset.get("duration_sec"))

    out: Dict[str, Any] = {
        "file_name": file_name,
        "src_w": src_w,
        "src_h": src_h,
        "duration_sec": duration_sec,
        "genre": genre,
        "tag": tag,
        "dominant_color": asset.get("dominant_color"),
        "palette_bins": asset.get("palette_bins"),
    }

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
    Build one global technical bundle from inventory.

    Output format (JSON array):
      [
        {
          "file_name":"...",
          "src_w":720,
          "src_h":1280,
          "duration_sec":12.34,
          "genre":"Rock",
          "tag":"dark_forest",
          "dominant_color":"H09_L0",
          "palette_bins":[...]
        },
        ...
      ]
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
        row = _extract_technical_row(it)
        if not row.get("file_name"):
            continue
        if int(row.get("src_w") or 0) <= 0 or int(row.get("src_h") or 0) <= 0:
            continue
        if not row.get("genre") or not row.get("tag"):
            continue
        if row.get("duration_sec") is None:
            continue

        rows.append(row)
        if max_assets is not None and len(rows) >= int(max_assets):
            break

    if not rows:
        raise RuntimeError(f"No valid assets found in inventory: {inventory_json}")

    rows.sort(key=lambda x: str(x.get("file_name", "")))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return out_path
