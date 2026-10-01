#!/usr/bin/env python3
"""Новые превью эффектов и субтитров → S3 → каталоги сайта в .env.production (с бэкапом).

Запускает workflow «Publish Web Previews» на runner'е деплоя сайта: только он читает
.env.production (права 600). Превью заранее кладутся в папку на том же сервере:

    <stage>/manifest.json   [{plane: "fx", category, id, file, name, in_registry}
                             | {plane: "subtitle", mode, file, name, new_style}]
    <stage>/previews/*.mp4

    python3 scripts/publish_web_previews.py <.env.production> --stage <dir> [--with-new-subtitle-styles]

Ключи объектов — с номером волны (по умолчанию v2), чтобы браузеры не отдавали старые
ролики по старому адресу:
    <S3_WEB_ASSET_PREFIX>/previews/fx/<wave>/<category>__<id>.mp4
    <S3_WEB_ASSET_PREFIX>/previews/subtitle/<wave>/<mode>.mp4

Каталоги:
  WEB_FX_CATALOG_JSON — у <category>__<id> меняется previewUrl; записи нет и эффект есть в
    реестре сайта (in_registry) — добавляется. Эффект не на сайте — пропускается.
  WEB_SUBTITLE_CATALOG_JSON — у стиля с selector.subtitlesMode == mode меняется previewUrl.
    Новые стили (new_style) добавляются в каталог и WEB_SUBTITLE_MODE_MAP_JSON только с
    --with-new-subtitle-styles: без задеплоенного кода стиля его выбор уронил бы заказ.

Порядок: каталоги собираются и проходят validate ДО заливки, env пишется ПОСЛЕ неё — упавший
шаг ничего не оставляет наполовину. Ключи S3 берутся из того же env и не печатаются.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.validate_web_preview_catalog_env import read_env, validate
except ModuleNotFoundError:  # прямой запуск `python scripts/publish_web_previews.py`
    from validate_web_preview_catalog_env import read_env, validate

FX_FIELDS = {"effect_hook": "effectHook", "effect_transition": "effectTransition", "effect_extra": "effectExtra"}


def s3_client(env: dict[str, str]):
    import boto3  # type: ignore
    from botocore.config import Config  # type: ignore

    kw: dict[str, Any] = {"service_name": "s3", "region_name": env.get("S3_REGION") or "ru-1",
                          "config": Config(signature_version="s3v4", proxies={})}
    if env.get("S3_ENDPOINT_URL"):
        kw["endpoint_url"] = env["S3_ENDPOINT_URL"]
    if env.get("S3_ACCESS_KEY_ID") and env.get("S3_SECRET_ACCESS_KEY"):
        kw["aws_access_key_id"] = env["S3_ACCESS_KEY_ID"]
        kw["aws_secret_access_key"] = env["S3_SECRET_ACCESS_KEY"]
    return boto3.client(**kw)


def plan(env: dict[str, str], manifest: list[dict], stage: Path, *, wave: str, new_styles: bool) -> dict:
    """Новые значения каталогов + список заливок. Ничего не пишет и не льёт."""
    bucket = env["S3_BUCKET_ASSET_STORAGE"]
    prefix = env["S3_WEB_ASSET_PREFIX"].strip("/")
    fx = json.loads(env["WEB_FX_CATALOG_JSON"])
    subs = json.loads(env["WEB_SUBTITLE_CATALOG_JSON"])
    mode_map = json.loads(env.get("WEB_SUBTITLE_MODE_MAP_JSON") or "{}")
    fx_by_id = {str(i.get("id")): i for i in fx}
    sub_by_mode = {str((i.get("selector") or {}).get("subtitlesMode")): i for i in subs}
    uploads: list[tuple[Path, str]] = []
    stats: dict[str, Any] = {"fx_updated": 0, "fx_added": 0, "fx_skipped": [],
                             "sub_updated": 0, "sub_added": 0, "sub_skipped": []}

    def url(src: Path, key: str) -> str:
        if not src.is_file():
            raise SystemExit(f"нет файла превью: {src}")
        uploads.append((src, key))
        return f"s3://{bucket}/{key}"

    for item in manifest:
        src = stage / "previews" / item["file"]
        if item["plane"] == "fx":
            cid = f"{item['category']}__{item['id']}"
            entry = fx_by_id.get(cid)
            if entry is None and not item.get("in_registry"):
                stats["fx_skipped"].append(cid)
                continue
            preview = url(src, f"{prefix}/previews/fx/{wave}/{cid}.mp4")
            if entry is None:
                entry = {"id": cid, "name": item["name"], "score": 1.0}
                fx.append(entry)
                fx_by_id[cid] = entry
                stats["fx_added"] += 1
            else:
                stats["fx_updated"] += 1
            entry.update(previewUrl=preview, plane="fx", selector={FX_FIELDS[item["category"]]: item["id"]})
        elif item["plane"] == "subtitle":
            mode = item["mode"]
            entry = sub_by_mode.get(mode)
            if entry is None and not (item.get("new_style") and new_styles):
                stats["sub_skipped"].append(mode)
                continue
            preview = url(src, f"{prefix}/previews/subtitle/{wave}/{mode}.mp4")
            if entry is None:
                name = item["name"]
                if name in mode_map and mode_map[name] != mode:
                    raise SystemExit(f"имя стиля {name!r} уже занято режимом {mode_map[name]!r}")
                entry = {"id": mode, "name": name, "plane": "subtitle",
                         "selector": {"subtitlesMode": mode}, "score": 1.0}
                subs.append(entry)
                sub_by_mode[mode] = entry
                mode_map[name] = mode
                stats["sub_added"] += 1
            else:
                stats["sub_updated"] += 1
            entry["previewUrl"] = preview
        else:
            raise SystemExit(f"неизвестный plane в манифесте: {item!r}")

    values = {
        "WEB_FX_CATALOG_JSON": json.dumps(fx, ensure_ascii=False, separators=(",", ":")),
        "WEB_SUBTITLE_CATALOG_JSON": json.dumps(subs, ensure_ascii=False, separators=(",", ":")),
    }
    if stats["sub_added"]:
        values["WEB_SUBTITLE_MODE_MAP_JSON"] = json.dumps(mode_map, ensure_ascii=False, separators=(",", ":"))
    validate({**env, **values})
    return {"values": values, "uploads": uploads, "stats": stats, "bucket": bucket}


def replace_keys(path: Path, replacements: dict[str, str]) -> Path:
    """Бэкап рядом, атомарная замена; права и владелец файла сохраняются (пишет тот же uid)."""
    lines, seen = [], set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        key = raw.split("=", 1)[0].strip() if "=" in raw else ""
        if key in replacements:
            lines.append(f"{key}={replacements[key]}")
            seen.add(key)
        else:
            lines.append(raw)
    missing = set(replacements) - seen
    if missing:
        raise SystemExit("в env нет ключей: " + ", ".join(sorted(missing)))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak-previews-{stamp}")
    shutil.copy2(path, backup)
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write("\n".join(lines) + "\n")
        temp = Path(handle.name)
    os.chmod(temp, mode)
    temp.replace(path)
    return backup


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("env_file", type=Path)
    ap.add_argument("--stage", type=Path, required=True)
    ap.add_argument("--wave", default="v2")
    ap.add_argument("--with-new-subtitle-styles", action="store_true")
    args = ap.parse_args(argv)

    env = read_env(args.env_file)
    manifest = json.loads((args.stage / "manifest.json").read_text(encoding="utf-8"))
    result = plan(env, manifest, args.stage, wave=args.wave, new_styles=args.with_new_subtitle_styles)

    s3 = s3_client(env)
    for src, key in result["uploads"]:
        s3.put_object(Bucket=result["bucket"], Key=key, Body=src.read_bytes(), ContentType="video/mp4")
        s3.head_object(Bucket=result["bucket"], Key=key)  # объект на месте
    backup = replace_keys(args.env_file, result["values"])

    st = result["stats"]
    print(f"S3: залито и проверено {len(result['uploads'])} превью (волна {args.wave})")
    print(f"эффекты: обновлено {st['fx_updated']}, добавлено {st['fx_added']}, пропущено {st['fx_skipped']}")
    print(f"субтитры: обновлено {st['sub_updated']}, добавлено {st['sub_added']}, пропущено {st['sub_skipped']}")
    print(f"бэкап env: {backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
