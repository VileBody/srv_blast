#!/usr/bin/env python3
"""Scaffold data/footage_collections.json from the folders that were actually uploaded.

Uploading files and making a group SELECTABLE are two different acts. The upload
puts objects in S3; the registry says what the group is called in the bot and
which track themes it suits — editorial decisions no scan can infer. Activation
already reports the mismatch, but with a dozen folders, closing it by hand is
busywork that invites typos in exactly the field (the folder) that must match S3
byte for byte.

So this reads the collection index activation produced and writes the skeleton:
one entry per folder, `label` prefilled from the folder name (they are delivered
human-readable — "бойцовский клуб"), `themes` left empty for you to fill.

Entries you have already edited are never overwritten. A folder that disappeared
from S3 is reported, not deleted — a collection may be intentionally kept while
its files are re-uploaded.

Usage:
  python scripts/sync_collection_registry.py                 # dry run, prints the diff
  python scripts/sync_collection_registry.py --write         # apply
  python scripts/sync_collection_registry.py --write --kind films
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Every label printed below is Cyrillic. On a console that is not UTF-8 (a
# Windows cp1251 shell, or any piped stdout picking up the locale) printing them
# raises UnicodeEncodeError and the run dies after doing its work but before
# saying so. Degrade the glyphs, never the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

from mlcore.footage_collection_catalog import COLLECTION_KINDS  # noqa: E402


def _index_path() -> Path:
    raw = (os.environ.get("COLLECTION_ASSETS_INDEX_JSON") or "").strip()
    return Path(raw) if raw else ROOT / "data" / "collection_assets_index.json"


def _registry_path() -> Path:
    raw = (os.environ.get("FOOTAGE_COLLECTIONS_JSON") or "").strip()
    return Path(raw) if raw else ROOT / "data" / "footage_collections.json"


def _titlecase_ru(text: str) -> str:
    """"бойцовский клуб" -> "Бойцовский клуб". Only the first letter: the rest is
    a film title whose internal casing we have no business guessing."""
    s = " ".join(str(text or "").split())
    return s[:1].upper() + s[1:] if s else s


def folders_from_index(index_path: Path) -> Dict[str, Dict[str, Any]]:
    """{"<kind>__<folder>": {kind, folder, clips}} from the activation index."""
    data = json.loads(index_path.read_text(encoding="utf-8"))
    out: Dict[str, Dict[str, Any]] = {}
    for row in data.get("assets") or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("genre") or "").strip()
        folder = str(row.get("tag") or "").strip()
        if not kind or not folder:
            continue
        slug = f"{kind}__{folder}"
        entry = out.setdefault(slug, {"kind": kind, "folder": folder, "clips": 0})
        entry["clips"] += 1
    return out


def merge(
    existing: List[Dict[str, Any]],
    found: Dict[str, Dict[str, Any]],
    *,
    only_kind: str = "",
) -> tuple[List[Dict[str, Any]], List[str], List[str]]:
    """(merged rows, added slugs, slugs with no files). Existing rows win."""
    by_slug = {
        f"{str(r.get('kind') or '').strip()}__{str(r.get('folder') or '').strip()}": r
        for r in existing
        if isinstance(r, dict)
    }
    added: List[str] = []
    for slug, info in sorted(found.items()):
        if only_kind and info["kind"] != only_kind:
            continue
        if slug in by_slug:
            continue
        by_slug[slug] = {
            "kind": info["kind"],
            "folder": info["folder"],
            "label": _titlecase_ru(info["folder"]),
            "description": "",
            "themes": [],
            "formats": ["wide", "square"],
        }
        added.append(slug)
    empty = [s for s in by_slug if s not in found]
    ordered = [by_slug[s] for s in sorted(by_slug)]
    return ordered, added, sorted(empty)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply (default: dry run)")
    ap.add_argument("--kind", default="", choices=("",) + tuple(COLLECTION_KINDS),
                    help="only scaffold this kind")
    ap.add_argument("--index", default="", help="collection index path override")
    ap.add_argument("--registry", default="", help="registry path override")
    args = ap.parse_args(argv)

    index_path = Path(args.index) if args.index else _index_path()
    registry_path = Path(args.registry) if args.registry else _registry_path()

    if not index_path.exists():
        print(f"[!] collection index not found: {index_path}")
        print("    run activation for media_type=collection first")
        return 2

    found = folders_from_index(index_path)
    print(f"index    : {index_path}")
    print(f"registry : {registry_path}")
    print(f"folders  : {len(found)} in S3\n")

    raw: Dict[str, Any] = {}
    existing: List[Dict[str, Any]] = []
    if registry_path.exists():
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
        existing = [r for r in (raw.get("collections") or []) if isinstance(r, dict)]

    merged, added, empty = merge(existing, found, only_kind=args.kind)

    for row in merged:
        slug = f"{row.get('kind')}__{row.get('folder')}"
        clips = found.get(slug, {}).get("clips", 0)
        mark = "NEW " if slug in added else "    "
        themes = len(row.get("themes") or [])
        note = "" if themes else "  <- themes empty (will rank at the tail)"
        print(f"  {mark}{row.get('label'):<28} clips={clips:<5} themes={themes}{note}")

    if empty:
        print("\n[!] registered but no files in S3 (kept, not deleted):")
        for s in empty:
            print(f"      {s}")

    if not args.write:
        print(f"\ndry run — would add {len(added)} entr{'y' if len(added) == 1 else 'ies'}.")
        print("re-run with --write to apply.")
        return 0

    raw.setdefault("version", "collection-v1")
    raw["collections"] = merged
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = registry_path.with_suffix(registry_path.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, registry_path)
    print(f"\nwrote {len(merged)} collections -> {registry_path}")
    print("next: fill `themes` (and `description`) per collection, then rebuild previews.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
