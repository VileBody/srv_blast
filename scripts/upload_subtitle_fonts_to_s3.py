#!/usr/bin/env python3
"""Заливка шрифтов субтитров для превью сайта (и как единый набор для рендер-ноды).

Превью субтитров на сайте рисует ТОЛЬКО настоящими шрифтами каталога — их файлы
сайт раздаёт сам (web_app/backend/app/subtitle_fonts.py). Этот скрипт собирает их:

    python scripts/upload_subtitle_fonts_to_s3.py --src "C:/.../fonts" --dry-run
    python scripts/upload_subtitle_fonts_to_s3.py --src "C:/.../fonts"
    python scripts/upload_subtitle_fonts_to_s3.py --src "C:/.../fonts" --out-dir web_app/backend/.fonts   # мок/локально

Что делает:
  * берёт из --src все .ttf/.otf/.woff/.woff2 (вложенные папки тоже), читает
    PostScript-имя ИЗ ФАЙЛА (name ID 6) — имя файла не важно;
  * оставляет только шрифты, которые нужны превью (каталог + стандартные шрифты стилей
    + Arial Narrow brat), и пережимает их в woff2;
  * кладёт в s3://$FX_ASSETS_S3_BUCKET/$FX_ASSETS_S3_PREFIX/fonts/subtitles/<PS>.woff2
    (или в --out-dir), идемпотентно: совпадающий по размеру объект пропускается;
  * в конце печатает, каких шрифтов не хватает, и возвращает 1, если список не полный.

Нужны fonttools и brotli:  pip install fonttools brotli
Креды S3 — из .env репозитория или окружения (как у upload_fx_assets_to_s3.py):
S3_ENDPOINT_URL, S3_REGION, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, FX_ASSETS_S3_BUCKET,
FX_ASSETS_S3_PREFIX. Если FX_ASSETS_S3_BUCKET пуст — берётся S3_BUCKET_ASSET_STORAGE
(так бакет ассетов выбирает и сайт).

Лицензии: файл шрифта уходит в браузер человеку — это распространение. До заливки
проверь, что лицензия шрифта разрешает веб-встраивание.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG = REPO_ROOT / "config" / "styles" / "subtitle_font_catalog.json"
FONT_SUFFIXES = {".ttf", ".otf", ".woff", ".woff2"}

# Сверх каталога: стандартные шрифты стилей (app/subtitle_font_layout.STYLE_DEFAULT_FONTS)
# и шрифт brat с курсивами фокус-слова (BRAT_FONT / BRAT_FOCUS_STYLES). Модуль движка
# не импортируем (скрипт должен работать и на старом чекауте) — совпадение списков
# держит тест tests/test_web_subtitle_fonts.py.
EXTRA_FONTS = (
    "Point-SemiBold", "Point-Light", "Montserrat-BoldItalic", "Montserrat-Bold",
    "ArialNarrow", "ArialNarrow-Italic", "ArialNarrow-BoldItalic",
)


def required_fonts() -> List[str]:
    raw = json.loads(CATALOG.read_text(encoding="utf-8"))
    names = {str(row["ps"]) for row in raw.get("fonts") or [] if row.get("roles")}
    names.update(EXTRA_FONTS)
    return sorted(names)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k:
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def make_client() -> Any:
    import boto3  # type: ignore
    from botocore.config import Config  # type: ignore

    kwargs: Dict[str, Any] = {
        "service_name": "s3",
        "region_name": (os.environ.get("S3_REGION") or "ru-1").strip() or "ru-1",
        "config": Config(signature_version="s3v4", proxies={}),
    }
    endpoint = (os.environ.get("S3_ENDPOINT_URL") or "").strip()
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    key, secret = (os.environ.get("S3_ACCESS_KEY_ID") or "").strip(), (os.environ.get("S3_SECRET_ACCESS_KEY") or "").strip()
    if key and secret:
        kwargs["aws_access_key_id"], kwargs["aws_secret_access_key"] = key, secret
    return boto3.client(**kwargs)


def postscript_names(path: Path) -> List[Tuple[str, Any]]:
    """(PostScript-имя, TTFont) для файла; у .ttc — для каждого шрифта коллекции."""
    from fontTools.ttLib import TTCollection, TTFont  # type: ignore

    fonts = TTCollection(str(path)).fonts if path.suffix.lower() == ".ttc" else [TTFont(str(path))]
    out = []
    for font in fonts:
        name = font["name"].getDebugName(6)
        if name:
            out.append((name.strip(), font))
    return out


def to_woff2(font: Any) -> bytes:
    font.flavor = "woff2"
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def head_size(s3: Any, bucket: str, key: str) -> Optional[int]:
    try:
        return int(s3.head_object(Bucket=bucket, Key=key)["ContentLength"])
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="папка с файлами шрифтов (.ttf/.otf/.woff/.woff2)")
    ap.add_argument("--out-dir", help="положить woff2 в папку вместо S3 (мок / локальная разработка)")
    ap.add_argument("--dry-run", action="store_true", help="только показать план")
    ap.add_argument("--force", action="store_true", help="перезалить даже совпадающие по размеру")
    args = ap.parse_args()

    src = Path(args.src).expanduser().resolve()
    if not src.is_dir():
        raise SystemExit(f"--src не папка: {src}")
    try:
        import fontTools  # noqa: F401  # type: ignore
        import brotli  # noqa: F401  # type: ignore
    except ImportError as exc:
        raise SystemExit(f"нужны fonttools и brotli: pip install fonttools brotli ({exc})")

    need = set(required_fonts())
    found: Dict[str, Tuple[Path, Any]] = {}
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in FONT_SUFFIXES | {".ttc"}:
            continue
        try:
            names = postscript_names(path)
        except Exception as exc:  # битый файл — сказать и идти дальше
            print(f"SKIP  не читается: {path.name} ({exc})")
            continue
        for ps, font in names:
            if ps in need and ps not in found:
                found[ps] = (path, font)
            elif ps not in need:
                print(f"SKIP  не из каталога: {ps}  ({path.name})")

    load_env_file(REPO_ROOT / ".env")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else None
    s3 = bucket = prefix = None
    if not out_dir:
        bucket = (os.environ.get("FX_ASSETS_S3_BUCKET") or "").strip() or (os.environ.get("S3_BUCKET_ASSET_STORAGE") or "").strip()
        if not bucket:
            raise SystemExit("Missing env: FX_ASSETS_S3_BUCKET (или S3_BUCKET_ASSET_STORAGE)")
        base = (os.environ.get("FX_ASSETS_S3_PREFIX") or "fx_assets/").strip().strip("/")
        prefix = f"{base}/fonts/subtitles" if base else "fonts/subtitles"
        if not args.dry_run:
            s3 = make_client()
    elif not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    put = skipped = 0
    for ps in sorted(found):
        path, font = found[ps]
        data = to_woff2(font)
        target = (out_dir / f"{ps}.woff2") if out_dir else f"s3://{bucket}/{prefix}/{ps}.woff2"
        if args.dry_run:
            print(f"PLAN  {len(data)/1024:7.0f}KB  {ps:32s} <- {path.name}  -> {target}")
            continue
        if out_dir:
            dest = out_dir / f"{ps}.woff2"
            if not args.force and dest.exists() and dest.stat().st_size == len(data):
                skipped += 1
                continue
            dest.write_bytes(data)
        else:
            key = f"{prefix}/{ps}.woff2"
            if not args.force and head_size(s3, bucket, key) == len(data):
                print(f"SKIP  (уже есть)  {key}")
                skipped += 1
                continue
            s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType="font/woff2")
            if head_size(s3, bucket, key) != len(data):
                raise SystemExit(f"не подтвердился в бакете: s3://{bucket}/{key}")
        print(f"PUT   {len(data)/1024:7.0f}KB  {ps:32s} <- {path.name}")
        put += 1

    missing = sorted(need - set(found))
    print(f"\n{'план' if args.dry_run else 'готово'}: найдено {len(found)} из {len(need)}, залито {put}, пропущено {skipped}")
    if missing:
        print("НЕ ХВАТАЕТ (превью этими шрифтами покажет «шрифт не загружен»):")
        for ps in missing:
            print(f"  {ps}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
