#!/usr/bin/env python3
"""Fail a web production deploy when a preview plane silently disappeared."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def validate(values: dict[str, str]) -> None:
    try:
        footage = json.loads(values["WEB_FOOTAGE_CATALOG_JSON"])
        fx = json.loads(values["WEB_FX_CATALOG_JSON"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"preview catalog JSON is missing or invalid: {exc}") from exc
    if not isinstance(footage, list) or not isinstance(fx, list):
        raise ValueError("preview catalogs must be arrays")

    planes = {str(item.get("plane") or "") for item in footage if isinstance(item, dict)}
    missing_planes = sorted({"vibes", "cine16x9", "films"} - planes)
    if missing_planes:
        raise ValueError("footage catalog lacks explicit planes: " + ", ".join(missing_planes))

    ids = [str(item.get("id") or "") for item in fx if isinstance(item, dict)]
    prefixes = {"effect_hook__", "effect_transition__", "effect_extra__", "motion__", "shape__"}
    missing_fx = sorted(prefix for prefix in prefixes if not any(item.startswith(prefix) for item in ids))
    if missing_fx:
        raise ValueError("FX catalog lacks preview groups: " + ", ".join(missing_fx))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_web_preview_catalog_env.py ENV_FILE", file=sys.stderr)
        return 2
    try:
        validate(read_env(Path(sys.argv[1])))
    except (OSError, ValueError) as exc:
        print(f"production preview catalog validation failed: {exc}", file=sys.stderr)
        return 1
    print("production preview catalog validation: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
