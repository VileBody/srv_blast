#!/usr/bin/env python3
"""
Build AE ExtendScript template.

- Input parts:   render_templates/jsx_src/parts/*.jsxinc
- Order file:    render_templates/jsx_src/parts_order.json
- Output file:   render_templates/job_template.jsx

This keeps the runtime template single-file (AE node receives one script),
while allowing us to edit the template in smaller modules.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ORDER_PATH = ROOT / "render_templates" / "jsx_src" / "parts_order.json"
PARTS_DIR = ROOT / "render_templates" / "jsx_src" / "parts"


def main() -> None:
    if not ORDER_PATH.is_file():
        raise SystemExit(f"Order file not found: {ORDER_PATH}")

    order = json.loads(ORDER_PATH.read_text(encoding="utf-8"))
    out_path = ROOT / order["output"]

    parts = order["parts"]
    if not parts:
        raise SystemExit("No parts in parts_order.json")

    chunks: list[str] = []
    for name in parts:
        p = PARTS_DIR / name
        if not p.is_file():
            raise SystemExit(f"Missing part: {p}")
        chunks.append(p.read_text(encoding="utf-8"))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(chunks), encoding="utf-8")
    print(f"[build_job_template] Wrote: {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
