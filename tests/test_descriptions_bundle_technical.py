from __future__ import annotations

import json
from pathlib import Path

from mlcore.descriptions_bundle import build_descriptions_bundle_from_inventory


def test_technical_bundle_contains_no_semantic_text_fields(tmp_path: Path) -> None:
    inv = {
        "assets": [
            {
                "file_name": "clip.mp4",
                "file_path": "/tmp/clip.mp4",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 9.5,
                "genre": "Rock",
                "tag": "dark_forest",
                "dominant_color": "H09_L0",
                "palette_bins": [{"bin": "H09_L0", "weight": 1.0}],
                # semantic junk should not leak into output
                "summary": "foo",
                "tags": ["bar"],
                "objects": ["obj"],
                "camera": {"type": "Static"},
                "visuals": {"style": "Neon"},
                "composition": "Busy",
                "meta": {"summary": "nested"},
            }
        ]
    }
    inv_path = tmp_path / "inventory.json"
    out_path = tmp_path / "bundle.json"
    inv_path.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")

    build_descriptions_bundle_from_inventory(
        inventory_json=inv_path,
        out_path=out_path,
    )

    rows = json.loads(out_path.read_text(encoding="utf-8"))
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]

    assert row["file_name"] == "clip.mp4"
    assert row["genre"] == "Rock"
    assert row["tag"] == "dark_forest"
    assert row["duration_sec"] == 9.5

    forbidden = {"summary", "tags", "objects", "camera", "visuals", "composition", "meta"}
    assert forbidden.isdisjoint(set(row.keys()))
