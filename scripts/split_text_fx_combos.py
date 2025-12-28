#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _must_dict(x: Any, where: str) -> Dict[str, Any]:
    if not isinstance(x, dict):
        raise TypeError(f"{where}: expected dict, got {type(x).__name__}")
    return x


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    fx_path = repo_root / "config" / "styles" / "text" / "text_fx_combos.json"
    motion_path = repo_root / "config" / "styles" / "text" / "text_motion_library.json"

    if not fx_path.is_file():
        raise FileNotFoundError(f"Missing source combos file: {fx_path}")

    src = json.loads(fx_path.read_text(encoding="utf-8"))
    src = _must_dict(src, "text_fx_combos.json")

    combos = _must_dict(src.get("combos"), "text_fx_combos.json.combos")
    default_id = src.get("defaultComboId")

    motion_out: Dict[str, Any] = {
        "schemaVersion": "text_motion_library.v1",
        "defaultComboId": default_id,
        "combos": {},
    }
    fx_out: Dict[str, Any] = {
        "schemaVersion": "text_fx_effects_library.v1",
        "defaultComboId": default_id,
        "combos": {},
    }

    for cid, cdata in combos.items():
        cdata = _must_dict(cdata, f"combo[{cid}]")

        raw_template = cdata.get("template", cdata)
        raw_template = _must_dict(raw_template, f"combo[{cid}].template")

        motion_template: Dict[str, Any] = {}
        for k in ("threeD", "textAnimators", "textMoreOptions"):
            if k in raw_template:
                motion_template[k] = raw_template[k]

        fx_template: Dict[str, Any] = {}
        if "effects" in raw_template:
            fx_template["effects"] = raw_template["effects"]

        exposed_map = cdata.get("exposedMap") or {}
        exposed_map = _must_dict(exposed_map, f"combo[{cid}].exposedMap")

        motion_map: Dict[str, Any] = {}
        fx_map: Dict[str, Any] = {}
        for k, path in exposed_map.items():
            if isinstance(path, list) and path:
                head = path[0]
                if head in ("textAnimators", "textMoreOptions", "threeD"):
                    motion_map[k] = path
                elif head in ("effects", "effectStack"):
                    fx_map[k] = path
                else:
                    fx_map[k] = path
            else:
                fx_map[k] = path

        defaults = cdata.get("defaults") or {}
        defaults = _must_dict(defaults, f"combo[{cid}].defaults")

        motion_defaults = {k: defaults[k] for k in motion_map.keys() if k in defaults}
        fx_defaults = {k: defaults[k] for k in fx_map.keys() if k in defaults}

        base = {
            "name": cdata.get("name", ""),
            "description": cdata.get("description", ""),
        }

        motion_entry: Dict[str, Any] = {**base, "template": motion_template}
        if motion_defaults:
            motion_entry["defaults"] = motion_defaults
        if motion_map:
            motion_entry["exposedMap"] = motion_map

        fx_entry: Dict[str, Any] = {**base, "template": fx_template}
        if fx_defaults:
            fx_entry["defaults"] = fx_defaults
        if fx_map:
            fx_entry["exposedMap"] = fx_map

        motion_out["combos"][cid] = motion_entry
        fx_out["combos"][cid] = fx_entry

    motion_path.write_text(json.dumps(motion_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fx_path.write_text(json.dumps(fx_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[OK] Wrote motion library: {motion_path}")
    print(f"[OK] Wrote effects library: {fx_path}")


if __name__ == "__main__":
    main()
