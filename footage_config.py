#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.runtime_mode import MODE_DEV, MODE_PROD, get_runtime_mode


@dataclass(frozen=True)
class FootageAssetRow:
    file_name: str
    file_path: str
    src_w: int
    src_h: int
    duration_sec: float
    genre: str
    tag: str
    dominant_color: Optional[str]
    palette_bins: Optional[List[Dict[str, Any]]]


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key, default) or "").strip()


def _s3_bucket_assets() -> str:
    bucket = _env("S3_BUCKET_ASSET_STORAGE")
    if get_runtime_mode() == MODE_PROD and not bucket:
        raise RuntimeError("MODE=prod requires S3_BUCKET_ASSET_STORAGE")
    return bucket


def _s3_assets_prefix() -> str:
    raw = _env("S3_ASSET_PREFIX", "pinterest_collection").strip().strip("/")
    if get_runtime_mode() == MODE_PROD and not raw:
        raise RuntimeError("MODE=prod requires non-empty S3_ASSET_PREFIX")
    return raw


def _s3_preflight_mode() -> str:
    default_mode = "strict" if get_runtime_mode() == MODE_PROD else "off"
    raw = _env("FOOTAGE_S3_PREFLIGHT_MODE", default_mode).strip().lower()
    if raw not in {"off", "strict"}:
        raise RuntimeError(
            "Invalid FOOTAGE_S3_PREFLIGHT_MODE. Expected one of: off | strict. "
            f"Got: {raw!r}"
        )
    return raw


def _s3_asset_key(*, file_name: str, genre: str, tag: str) -> str:
    prefix = _s3_assets_prefix()
    return f"{prefix}/{genre}/{tag}/{file_name}" if prefix else f"{genre}/{tag}/{file_name}"


def _make_s3_client():
    try:
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "boto3 is required for FOOTAGE_S3_PREFLIGHT_MODE=strict.\n"
            "Install: pip install boto3\n"
            f"Import error: {e!r}"
        ) from e

    endpoint = _env("S3_ENDPOINT_URL", "") or None
    access_key = _env("S3_ACCESS_KEY_ID", "")
    secret_key = _env("S3_SECRET_ACCESS_KEY", "")
    region = _env("S3_REGION", "ru-1") or "ru-1"

    if bool(access_key) != bool(secret_key):
        raise RuntimeError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be both set or both empty")

    kwargs: Dict[str, Any] = {
        "service_name": "s3",
        "region_name": region,
        "config": Config(signature_version="s3v4"),
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key

    return boto3.client(**kwargs)


def _is_s3_not_found_error(exc: Exception) -> bool:
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str((response.get("Error") or {}).get("Code") or "").strip()
    return code in {"404", "NoSuchKey", "NotFound"}


def _s3_missing_asset_rows(rows: List[Tuple[str, str, str, str]]) -> List[Tuple[str, str, str, str]]:
    """
    rows tuple layout:
      (file_name, genre, tag, s3_key_without_bucket)
    """
    if not rows:
        return []

    bucket = _s3_bucket_assets()
    s3 = _make_s3_client()

    missing: List[Tuple[str, str, str, str]] = []
    for file_name, genre, tag, key in rows:
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except Exception as e:
            if _is_s3_not_found_error(e):
                missing.append((file_name, genre, tag, key))
                continue
            raise RuntimeError(
                "S3 preflight request failed: "
                f"bucket={bucket!r} key={key!r} file_name={file_name!r} err={e!r}"
            ) from e
    return missing


def _read_json(path: Path) -> Dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return obj


def _as_pos_float(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except Exception:
        return None
    if x <= 0:
        return None
    return x


def _s3_locator_for_asset(*, file_name: str, genre: str, tag: str) -> str:
    bucket = _s3_bucket_assets()
    key = _s3_asset_key(file_name=file_name, genre=genre, tag=tag)
    return f"s3://{bucket}/{key}"


def _to_compact_json(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _clean_palette_bins(v: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(v, list):
        return None
    out: List[Dict[str, Any]] = []
    for it in v:
        if not isinstance(it, dict):
            continue
        b = str(it.get("bin") or "").strip()
        w = _as_pos_float(it.get("weight"))
        if not b or w is None:
            continue
        out.append({"bin": b, "weight": float(w)})
    return out or None


def _build_color_meta_map(assets: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(assets, list):
        return out
    for it in assets:
        if not isinstance(it, dict):
            continue
        file_name = str(it.get("file_name") or "").strip()
        if not file_name:
            continue
        dominant_color = str(it.get("dominant_color") or "").strip() or None
        palette_bins = _clean_palette_bins(it.get("palette_bins"))
        if not dominant_color and not palette_bins:
            continue
        out[file_name] = {
            "dominant_color": dominant_color,
            "palette_bins": palette_bins,
        }
    return out


def _normalize_asset_file_name(v: str) -> str:
    return unicodedata.normalize("NFKC", str(v or "").strip())


def _strip_copy_suffix(file_name: str) -> str:
    stem, ext = os.path.splitext(str(file_name or ""))
    for suffix in (" — копия", " - копия", "_copy", " copy"):
        if stem.endswith(suffix):
            return f"{stem[:-len(suffix)]}{ext}"
    return str(file_name or "")


def _require_str(it: Dict[str, Any], key: str) -> str:
    s = str(it.get(key) or "").strip()
    if not s:
        raise RuntimeError(f"Missing required field {key!r} in static assets index row")
    return s


def build_inventory_and_bundle(
    *,
    repo_root: Path,
    footage_dir: Path,
    static_assets_index_path: Path,
    inventory_out_path: Path,
    bundle_out_path: Path,
    adjustment_preset: Optional[Dict[str, Any]] = None,
    max_assets_in_bundle: Optional[int] = None,
) -> Tuple[Path, Path]:
    """
    Build both shared artifacts from static technical index:
      1) inventory: includes deterministic file_path (local path in dev, s3:// in prod)
      2) bundle: technical-only rows for Gemini context

    Source index must be data/static_assets_index.json-like format.
    """
    repo_root = repo_root.resolve()
    footage_dir = footage_dir.resolve()
    static_assets_index_path = static_assets_index_path.resolve()
    inventory_out_path = inventory_out_path.resolve()
    bundle_out_path = bundle_out_path.resolve()

    if not static_assets_index_path.exists():
        raise FileNotFoundError(f"Static assets index missing: {static_assets_index_path}")

    adjustment_preset = adjustment_preset or {
        "id": "ADJ_LAYER_16",
        "name": "Adjustment Layer 16",
        "dump_file": "data/0_4.504505__Adjustment Layer 16__adjustment.json",
        "time_warp_mode": "pin_edges_v1",
    }

    index_obj = _read_json(static_assets_index_path)
    source_assets = index_obj.get("assets")
    if not isinstance(source_assets, list):
        raise RuntimeError(f"Invalid static assets index (missing assets[]): {static_assets_index_path}")

    fallback_meta_path_raw = _env("STATIC_ASSETS_ENRICH_INDEX_JSON", str(repo_root / "data" / "static_assets_index.json"))
    fallback_meta_path = Path(fallback_meta_path_raw).resolve()
    fallback_color_meta: Dict[str, Dict[str, Any]] = {}
    fallback_color_meta_norm: Dict[str, Dict[str, Any]] = {}
    fallback_meta_error: Optional[str] = None
    if fallback_meta_path != static_assets_index_path:
        try:
            if fallback_meta_path.exists():
                fb_obj = _read_json(fallback_meta_path)
                fallback_color_meta = _build_color_meta_map(fb_obj.get("assets"))
                for k, meta in fallback_color_meta.items():
                    kn = _normalize_asset_file_name(k)
                    if kn and kn not in fallback_color_meta_norm:
                        fallback_color_meta_norm[kn] = meta
        except Exception as e:
            fallback_meta_error = str(e)

    runtime_mode = get_runtime_mode()
    mode = "local" if runtime_mode == MODE_DEV else "s3"
    s3_preflight_mode = _s3_preflight_mode() if mode == "s3" else "off"

    assets_map: Dict[str, FootageAssetRow] = {}
    invalid_rows = 0
    color_meta_enriched_rows = 0
    missing_local_files: List[str] = []
    s3_preflight_rows: List[Tuple[str, str, str, str]] = []

    for it in source_assets:
        if not isinstance(it, dict):
            invalid_rows += 1
            continue

        try:
            file_name = _require_str(it, "file_name")
            genre = _require_str(it, "genre")
            tag = _require_str(it, "tag")
            src_w = int(it.get("src_w") or 0)
            src_h = int(it.get("src_h") or 0)
            duration_sec = _as_pos_float(it.get("duration_sec"))
            if src_w <= 0 or src_h <= 0 or duration_sec is None:
                raise RuntimeError("Invalid src size or duration_sec")
        except Exception:
            invalid_rows += 1
            continue

        if file_name in assets_map:
            continue

        if mode == "s3":
            s3_key = _s3_asset_key(file_name=file_name, genre=genre, tag=tag)
            file_path = f"s3://{_s3_bucket_assets()}/{s3_key}"
            s3_preflight_rows.append((file_name, genre, tag, s3_key))
        else:
            local_fp = (footage_dir / file_name).resolve()
            file_path = str(local_fp)
            if not local_fp.exists():
                missing_local_files.append(file_name)

        dominant_color = str(it.get("dominant_color") or "").strip() or None
        palette_bins = _clean_palette_bins(it.get("palette_bins"))
        if (not dominant_color or not palette_bins) and fallback_color_meta:
            meta = fallback_color_meta.get(file_name)
            if not isinstance(meta, dict):
                meta = fallback_color_meta_norm.get(_normalize_asset_file_name(file_name))
            if not isinstance(meta, dict):
                alias = _strip_copy_suffix(file_name)
                if alias != file_name:
                    meta = fallback_color_meta.get(alias)
                    if not isinstance(meta, dict):
                        meta = fallback_color_meta_norm.get(_normalize_asset_file_name(alias))
            if isinstance(meta, dict):
                changed = False
                if not dominant_color:
                    dom2 = str(meta.get("dominant_color") or "").strip() or None
                    if dom2:
                        dominant_color = dom2
                        changed = True
                if not palette_bins:
                    bins2 = _clean_palette_bins(meta.get("palette_bins"))
                    if bins2:
                        palette_bins = bins2
                        changed = True
                if changed:
                    color_meta_enriched_rows += 1

        assets_map[file_name] = FootageAssetRow(
            file_name=file_name,
            file_path=file_path,
            src_w=src_w,
            src_h=src_h,
            duration_sec=float(duration_sec),
            genre=genre,
            tag=tag,
            dominant_color=dominant_color,
            palette_bins=palette_bins,
        )

    if mode == "s3" and s3_preflight_mode == "strict":
        missing_s3_assets = _s3_missing_asset_rows(s3_preflight_rows)
        if missing_s3_assets:
            sample_rows = missing_s3_assets[:20]
            sample_text = "\n".join(
                f"- file_name={file_name!r} genre={genre!r} tag={tag!r} key={key!r}"
                for file_name, genre, tag, key in sample_rows
            )
            raise RuntimeError(
                "missing_s3_assets_in_selected_source\n"
                f"source_index={static_assets_index_path}\n"
                f"s3_bucket={_s3_bucket_assets()}\n"
                f"s3_prefix={_s3_assets_prefix()}\n"
                f"total_missing={len(missing_s3_assets)}\n"
                f"sample_missing:\n{sample_text}"
            )

    assets: List[Dict[str, Any]] = []
    for file_name, row in sorted(assets_map.items(), key=lambda kv: kv[0]):
        obj: Dict[str, Any] = {
            "file_name": row.file_name,
            "file_path": row.file_path,
            "src_w": row.src_w,
            "src_h": row.src_h,
            "duration_sec": row.duration_sec,
            "genre": row.genre,
            "tag": row.tag,
        }
        if row.dominant_color:
            obj["dominant_color"] = row.dominant_color
        if row.palette_bins:
            obj["palette_bins"] = row.palette_bins
        assets.append(obj)

    inv_obj: Dict[str, Any] = {
        "version": "v2",
        "source_static_assets_index": str(static_assets_index_path),
        "footage_dir": str(footage_dir),
        "adjustment_preset": adjustment_preset,
        "assets": assets,
        "warnings": {
            "mode": mode,
            "assets_count": len(assets),
            "invalid_rows": int(invalid_rows),
            "missing_files": sorted(set(missing_local_files)) if mode == "local" else [],
        },
    }
    if fallback_meta_path != static_assets_index_path:
        inv_obj["warnings"]["color_meta_enrich_source"] = str(fallback_meta_path)
        inv_obj["warnings"]["color_meta_enriched_rows"] = int(color_meta_enriched_rows)
        inv_obj["warnings"]["color_meta_enrich_source_rows"] = int(len(fallback_color_meta))
        if fallback_meta_error:
            inv_obj["warnings"]["color_meta_enrich_error"] = fallback_meta_error
    if mode == "s3":
        inv_obj["warnings"]["s3_bucket"] = _s3_bucket_assets()
        inv_obj["warnings"]["s3_preflight_mode"] = s3_preflight_mode

    inventory_out_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_out_path.write_text(json.dumps(inv_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    bundle_rows: List[Dict[str, Any]] = []
    for it in assets:
        row: Dict[str, Any] = {
            "file_name": it["file_name"],
            "src_w": int(it["src_w"]),
            "src_h": int(it["src_h"]),
            "duration_sec": float(it["duration_sec"]),
            "genre": str(it["genre"]),
            "tag": str(it["tag"]),
        }
        if it.get("dominant_color"):
            row["dominant_color"] = it["dominant_color"]
        if it.get("palette_bins"):
            row["palette_bins"] = it["palette_bins"]
        bundle_rows.append(row)

    bundle_rows.sort(key=lambda x: str(x.get("file_name", "")))
    if max_assets_in_bundle is not None:
        bundle_rows = bundle_rows[: int(max_assets_in_bundle)]

    bundle_out_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_out_path.write_text(_to_compact_json(bundle_rows), encoding="utf-8")
    return inventory_out_path, bundle_out_path


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    footage_dir = Path(_env("FOOTAGE_DIR", str(repo_root / "footage"))).resolve()
    static_assets_index_path = Path(
        _env("STATIC_ASSETS_INDEX_JSON", str(repo_root / "data" / "static_assets_index.json"))
    ).resolve()

    inventory_out = Path(
        _env("FOOTAGE_INVENTORY_OUT", str(repo_root / "data" / "footage_inventory.json"))
    ).resolve()
    bundle_out = Path(
        _env("DESCRIPTIONS_BUNDLE_OUT", str(repo_root / "pins" / "descriptions_bundle.json"))
    ).resolve()

    max_assets_env = _env("DESCRIPTIONS_BUNDLE_MAX_ASSETS", "")
    max_assets = int(max_assets_env) if max_assets_env else None

    inv_p, bun_p = build_inventory_and_bundle(
        repo_root=repo_root,
        footage_dir=footage_dir,
        static_assets_index_path=static_assets_index_path,
        inventory_out_path=inventory_out,
        bundle_out_path=bundle_out,
        max_assets_in_bundle=max_assets,
    )

    mode = "s3" if get_runtime_mode() == MODE_PROD else "local"
    print(f"[OK] mode: {mode}")
    if mode == "s3":
        print(f"[OK] s3 asset prefix: {_s3_assets_prefix()}")
    print(f"[OK] static index: {static_assets_index_path}")
    print(f"[OK] inventory: {inv_p}")
    print(f"[OK] bundle: {bun_p}")


if __name__ == "__main__":
    main()
