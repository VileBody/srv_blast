#!/usr/bin/env python3
"""Upgrade the production web preview catalogs without printing env secrets."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.validate_web_preview_catalog_env import read_env, validate
except ModuleNotFoundError:  # direct `python scripts/migrate_...py`
    from validate_web_preview_catalog_env import read_env, validate


FX_FIELDS = {
    "effect_hook": "effectHook",
    "effect_transition": "effectTransition",
    "effect_extra": "effectExtra",
    "motion": "f4Device",
    "shape": "f2Shape",
}


def migrate_values(values: dict[str, str], previews: dict[str, Any]) -> dict[str, str]:
    footage = json.loads(values["WEB_FOOTAGE_CATALOG_JSON"])
    for item in footage:
        if item.get("plane"):
            continue
        item_id = str(item.get("id") or "")
        item["plane"] = (
            "cine16x9" if item_id.startswith("collection:cine16x9__")
            else "films" if item_id.startswith("collection:films__")
            else "vibes"
        )

    fx = json.loads(values["WEB_FX_CATALOG_JSON"])
    by_id = {str(item.get("id") or ""): item for item in fx}
    bucket = values["S3_BUCKET_ASSET_STORAGE"]
    prefix = values["S3_WEB_ASSET_PREFIX"].strip("/")
    for key, record in previews.items():
        category, separator, item_id = str(key).partition(":")
        field = FX_FIELDS.get(category)
        if not separator or not field or not isinstance(record, dict):
            continue
        catalog_id = f"{category}__{item_id}"
        item = by_id.get(catalog_id)
        if item is None:
            item = {
                "id": catalog_id,
                "name": str(record.get("label") or item_id),
                "previewUrl": f"s3://{bucket}/{prefix}/previews/fx/{catalog_id}.mp4",
                "score": 1.0,
            }
            fx.append(item)
            by_id[catalog_id] = item
        item["plane"] = "fx"
        item["selector"] = {field: item_id}

    updated = dict(values)
    updated["WEB_FOOTAGE_CATALOG_JSON"] = json.dumps(footage, ensure_ascii=False, separators=(",", ":"))
    updated["WEB_FX_CATALOG_JSON"] = json.dumps(fx, ensure_ascii=False, separators=(",", ":"))
    validate(updated)
    return updated


def replace_keys(path: Path, replacements: dict[str, str]) -> None:
    original = path.read_text(encoding="utf-8")
    seen: set[str] = set()
    lines = []
    for raw in original.splitlines():
        key = raw.split("=", 1)[0].strip() if "=" in raw else ""
        if key in replacements:
            lines.append(f"{key}={replacements[key]}")
            seen.add(key)
        else:
            lines.append(raw)
    missing = set(replacements) - seen
    if missing:
        raise ValueError("env file is missing keys: " + ", ".join(sorted(missing)))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak-catalog-{stamp}")
    shutil.copy2(path, backup)
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write("\n".join(lines) + "\n")
        temp = Path(handle.name)
    os.chmod(temp, mode)
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    parser.add_argument(
        "--preview-store",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "hook_previews.json",
    )
    args = parser.parse_args()
    values = read_env(args.env_file)
    payload = json.loads(args.preview_store.read_text(encoding="utf-8"))
    replacements = migrate_values(values, dict(payload.get("previews") or {}))
    replace_keys(args.env_file, {
        "WEB_FOOTAGE_CATALOG_JSON": replacements["WEB_FOOTAGE_CATALOG_JSON"],
        "WEB_FX_CATALOG_JSON": replacements["WEB_FX_CATALOG_JSON"],
    })
    print("production preview catalog migration: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
